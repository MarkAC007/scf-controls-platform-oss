"""
Evidence Inbox — webhook ingestion endpoint (Issue #214).

External systems POST evidence payloads to per-org webhook URLs.
This endpoint validates HMAC-SHA256 signatures, logs every delivery,
and creates EvidenceFile records from JSON or multipart payloads.

Endpoint:
  POST /organizations/{org_id}/evidence/{evidence_id}/inbox

Authentication: HMAC-SHA256 (not standard bearer token).
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import WebhookEndpoint, WebhookDelivery, EvidenceFile, EvidenceTracking
from schemas import WebhookIngestResponse
from services.storage_service import ALLOWED_CONTENT_TYPES
from services.storage_service import move_to_quarantine, write_inbox_payload
from services.audit_service import create_audit_entry
from services.validation_service import run_validation
from services.malware_scan_service import get_scan_service
from rate_limiting import rate_limit_inbox

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-inbox"])

# Size limits
MAX_JSON_PAYLOAD_BYTES = 1 * 1024 * 1024    # 1 MB for JSON-only
MAX_FILE_PAYLOAD_BYTES = 50 * 1024 * 1024   # 50 MB for file uploads

# Replay attack prevention: reject requests with timestamps older than this
TIMESTAMP_TOLERANCE_SECONDS = 300  # 5 minutes


def _compute_declaration_origins(
    payload_json,
    artifact_type_header,
    collector_id_header,
):
    """Classify origin of artifact_type and collector_id declarations.

    Returns (artifact_type_origin, collector_id_origin) where each is one of
    {"body", "header", "both", "none"}. Called BEFORE header splice so the
    values reflect what the client actually sent, not the merged payload.

    M2 PR 1.1 (#572 §3): feeds the `webhook.intake` cutover-signal metric.
    """
    body_has_artifact_type = isinstance(payload_json, dict) and bool(payload_json.get("artifact_type"))
    body_has_collector_id = isinstance(payload_json, dict) and bool(payload_json.get("collector_id"))
    if body_has_artifact_type and artifact_type_header:
        artifact_type_origin = "both"
    elif body_has_artifact_type:
        artifact_type_origin = "body"
    elif artifact_type_header:
        artifact_type_origin = "header"
    else:
        artifact_type_origin = "none"
    if body_has_collector_id and collector_id_header:
        collector_id_origin = "both"
    elif body_has_collector_id:
        collector_id_origin = "body"
    elif collector_id_header:
        collector_id_origin = "header"
    else:
        collector_id_origin = "none"
    return artifact_type_origin, collector_id_origin


# ---------------------------------------------------------------------------
# HMAC verification helpers
# ---------------------------------------------------------------------------

def _verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """Verify HMAC-SHA256 signature.

    Expected header format: ``sha256=<hex_digest>``
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request (respects X-Forwarded-For)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Ingest endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/inbox",
    response_model=WebhookIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest evidence via webhook",
    description="""
    Receive evidence from an external system via HMAC-authenticated webhook.

    **Authentication:** HMAC-SHA256 signature (not standard bearer token).

    **Required headers:**
    - `X-SCF-Webhook-Id` — endpoint UUID
    - `X-SCF-Signature` — `sha256=<hmac_hex_digest>`

    **Optional headers:**
    - `X-SCF-Event-Id` — idempotency key (duplicate events return existing delivery)
    - `X-SCF-Timestamp` — unix epoch for replay prevention (5-min tolerance)

    **Payload:** JSON (`application/json`, max 1 MB) or binary file (max 50 MB).

    Returns a `202 Accepted` with the delivery ID and processing status.

    ---

    ## Code Examples

    ### cURL
    ```bash
    PAYLOAD='{"collected_at":"2024-01-15T10:00:00Z","source":"aws-config","data":{"status":"compliant"}}'
    SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "YOUR_WEBHOOK_SECRET" | awk '{print "sha256="$2}')

    curl -X POST "https://your-platform.example.com/organizations/{org_id}/evidence/{evidence_id}/inbox" \\
      -H "Content-Type: application/json" \\
      -H "X-SCF-Webhook-Id: YOUR_WEBHOOK_ENDPOINT_UUID" \\
      -H "X-SCF-Signature: $SIGNATURE" \\
      -H "X-SCF-Timestamp: $(date +%s)" \\
      -d "$PAYLOAD"
    ```

    ### Python
    ```python
    import hashlib
    import hmac
    import json
    import time
    import requests

    WEBHOOK_SECRET = "YOUR_WEBHOOK_SECRET"
    WEBHOOK_ID = "YOUR_WEBHOOK_ENDPOINT_UUID"
    BASE_URL = "https://your-platform.example.com"

    def send_evidence(org_id: str, evidence_id: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

        response = requests.post(
            f"{BASE_URL}/organizations/{org_id}/evidence/{evidence_id}/inbox",
            headers={
                "Content-Type": "application/json",
                "X-SCF-Webhook-Id": WEBHOOK_ID,
                "X-SCF-Signature": signature,
                "X-SCF-Timestamp": str(int(time.time())),
            },
            data=body,
        )
        response.raise_for_status()
        return response.json()

    result = send_evidence(
        org_id="YOUR_ORG_UUID",
        evidence_id="ERL-IAM-001",
        payload={
            "collected_at": "2024-01-15T10:00:00Z",
            "source": "aws-config",
            "data": {"status": "compliant", "resource_count": 42},
        },
    )
    print(result)  # {"delivery_id": "...", "status": "accepted"}
    ```

    ### TypeScript
    ```typescript
    import crypto from 'crypto';

    const WEBHOOK_SECRET = 'YOUR_WEBHOOK_SECRET';
    const WEBHOOK_ID = 'YOUR_WEBHOOK_ENDPOINT_UUID';
    const BASE_URL = 'https://your-platform.example.com';

    async function sendEvidence(
      orgId: string,
      evidenceId: string,
      payload: Record<string, unknown>
    ): Promise<{ delivery_id: string; status: string }> {
      const body = JSON.stringify(payload);
      const signature =
        'sha256=' +
        crypto.createHmac('sha256', WEBHOOK_SECRET).update(body).digest('hex');

      const response = await fetch(
        `${BASE_URL}/organizations/${orgId}/evidence/${evidenceId}/inbox`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-SCF-Webhook-Id': WEBHOOK_ID,
            'X-SCF-Signature': signature,
            'X-SCF-Timestamp': String(Math.floor(Date.now() / 1000)),
          },
          body,
        }
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }

    const result = await sendEvidence('YOUR_ORG_UUID', 'ERL-IAM-001', {
      collected_at: new Date().toISOString(),
      source: 'aws-config',
      data: { status: 'compliant', resource_count: 42 },
    });
    console.log(result); // { delivery_id: '...', status: 'accepted' }
    ```
    """,
)
@rate_limit_inbox
async def ingest_evidence(
    request: Request,
    response: Response,
    org_id: UUID,
    evidence_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive evidence from an external system via webhook.

    No standard auth — uses HMAC-SHA256 signature validation.
    Required headers:
      - X-SCF-Webhook-Id: endpoint UUID
      - X-SCF-Signature: sha256=<hmac_hex_digest>
    Optional headers:
      - X-SCF-Event-Id: idempotency key
      - X-SCF-Timestamp: unix epoch (replay prevention, 5-min tolerance)
    """
    # --- Extract headers ---------------------------------------------------
    webhook_id_str = request.headers.get("X-SCF-Webhook-Id")
    signature_header = request.headers.get("X-SCF-Signature")
    event_id = request.headers.get("X-SCF-Event-Id")
    timestamp_header = request.headers.get("X-SCF-Timestamp")
    content_type = request.headers.get("Content-Type", "")
    user_agent = request.headers.get("User-Agent", "")[:500]
    client_ip = _get_client_ip(request)
    # M2 (#572): optional declarations from multipart/non-JSON collectors.
    # JSON bodies can carry the same info in `artifact_type` / `collector_id`.
    artifact_type_header = request.headers.get("X-SCF-Artifact-Type")
    collector_id_header = request.headers.get("X-SCF-Collector-Id")

    if not webhook_id_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-SCF-Webhook-Id header",
        )
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-SCF-Signature header",
        )

    # Parse webhook ID
    try:
        webhook_id = UUID(webhook_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-SCF-Webhook-Id format",
        )

    # --- Replay attack prevention (timestamp check) -------------------------
    if timestamp_header:
        try:
            ts = int(timestamp_header)
            now = int(time.time())
            if abs(now - ts) > TIMESTAMP_TOLERANCE_SECONDS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Request timestamp too old or too far in the future (tolerance: {TIMESTAMP_TOLERANCE_SECONDS}s)",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-SCF-Timestamp format (expected unix epoch integer)",
            )

    # --- Look up endpoint --------------------------------------------------
    result = await db.execute(
        select(WebhookEndpoint).where(
            and_(
                WebhookEndpoint.id == webhook_id,
                WebhookEndpoint.organization_id == org_id,
            )
        )
    )
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    if not endpoint.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook endpoint is revoked",
        )

    # --- Check allowed_evidence_ids ----------------------------------------
    if endpoint.allowed_evidence_ids is not None:
        if evidence_id not in endpoint.allowed_evidence_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Evidence ID '{evidence_id}' is not allowed for this webhook endpoint",
            )

    # --- Read raw body -----------------------------------------------------
    body = await request.body()

    # Enforce size limits
    is_json = "application/json" in content_type
    max_size = MAX_JSON_PAYLOAD_BYTES if is_json else MAX_FILE_PAYLOAD_BYTES
    if len(body) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Payload exceeds maximum size ({max_size // (1024 * 1024)}MB)",
        )

    # --- Verify HMAC signature ---------------------------------------------
    sig_valid = _verify_signature(endpoint.secret, body, signature_header)

    if not sig_valid:
        # Log the failed attempt before rejecting
        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            organization_id=org_id,
            evidence_id=evidence_id,
            event_id=event_id,
            content_type=content_type,
            signature_valid=False,
            status="rejected",
            error_message="Invalid HMAC signature",
            ip_address=client_ip,
            user_agent=user_agent,
        )
        db.add(delivery)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # --- Idempotency check -------------------------------------------------
    if event_id:
        existing = await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.event_id == event_id,
            )
        )
        existing_delivery = existing.scalar_one_or_none()
        if existing_delivery:
            return WebhookIngestResponse(
                delivery_id=existing_delivery.id,
                status=existing_delivery.status,
                message="Duplicate event — returning existing delivery",
            )

    # --- Create delivery record (status=received) --------------------------
    payload_json = None
    if is_json:
        try:
            payload_json = json.loads(body)
        except json.JSONDecodeError:
            delivery = WebhookDelivery(
                webhook_endpoint_id=endpoint.id,
                organization_id=org_id,
                evidence_id=evidence_id,
                event_id=event_id,
                content_type=content_type,
                signature_valid=True,
                status="failed",
                error_message="Invalid JSON payload",
                ip_address=client_ip,
                user_agent=user_agent,
            )
            db.add(delivery)
            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            )

    # M2 PR 1.1 (#572 §3): capture origin of artifact_type / collector_id BEFORE
    # the splice below, so the cutover-signal metric can distinguish body vs
    # header declarations. Origin is not persisted — emitted as stdout metric.
    artifact_type_origin, collector_id_origin = _compute_declaration_origins(
        payload_json, artifact_type_header, collector_id_header,
    )
    logger.info(
        "webhook.intake evidence_id=%s artifact_type_origin=%s collector_id_origin=%s",
        evidence_id, artifact_type_origin, collector_id_origin,
    )

    # M2 (#572): splice header-carried declarations into payload_json so the
    # window assessment service finds them alongside JSON-body declarations.
    # JSON-body values win when both are present.
    if payload_json is None and (artifact_type_header or collector_id_header):
        payload_json = {}
    if isinstance(payload_json, dict):
        if artifact_type_header and not payload_json.get("artifact_type"):
            header_types = [t.strip() for t in artifact_type_header.split(",") if t.strip()]
            if len(header_types) == 1:
                payload_json["artifact_type"] = header_types[0]
            elif header_types:
                payload_json["artifact_type"] = header_types
        if collector_id_header and not payload_json.get("collector_id"):
            payload_json["collector_id"] = collector_id_header

    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        organization_id=org_id,
        evidence_id=evidence_id,
        event_id=event_id,
        payload_json=payload_json,
        content_type=content_type,
        signature_valid=True,
        status="received",
        ip_address=client_ip,
        user_agent=user_agent,
    )
    db.add(delivery)
    await db.flush()

    # --- Process payload ---------------------------------------------------
    try:
        evidence_file = None

        if is_json and payload_json:
            # JSON payload — extract metadata and create an EvidenceFile record
            # that represents the ingested data (stored as JSON evidence).
            source = payload_json.get("source", "webhook")
            data = payload_json.get("data", {})
            description = data.get("description", f"Webhook evidence from {source}")
            filename = data.get("filename", f"webhook_{source}_{delivery.id}.json")

            # Store the JSON payload as an evidence file record
            evidence_file = EvidenceFile(
                organization_id=org_id,
                evidence_id=evidence_id,
                filename=filename,
                s3_key=f"evidence/{org_id}/inbox/{delivery.id}.json",
                content_type="application/json",
                file_size_bytes=len(body),
                # No uploaded_by_user_id — this is system-ingested
            )
            db.add(evidence_file)
            await db.flush()

            # --- Write raw payload to S3 (Issue #400 fix) ----------------------------
            write_inbox_payload(
                s3_key=evidence_file.s3_key,
                body=body,
                org_id=str(org_id),
            )

            # --- Malware scan ---
            scan_service = get_scan_service()
            scan_result = await scan_service.scan_bytes(
                data=body,
                filename=filename,
                claimed_content_type=content_type,
            )
            evidence_file.scan_status = scan_result.status
            evidence_file.scan_details = scan_result.details

            if scan_result.status == "infected":
                # Quarantine the file and reject
                move_to_quarantine(evidence_file.s3_key, str(org_id))
                evidence_file.s3_key = f"quarantine/{org_id}/{evidence_file.id}_{filename}"
                delivery.evidence_file_id = evidence_file.id
                delivery.status = "rejected"
                delivery.error_message = f"Malware detected: {scan_result.details.get('message', 'unknown threat')}"
                delivery.processed_at = datetime.utcnow()
                await db.commit()

                return WebhookIngestResponse(
                    delivery_id=delivery.id,
                    status="rejected",
                    message=f"File rejected: malware detected",
                )

            delivery.evidence_file_id = evidence_file.id

            # Run validation (never raises — results stored in DB)
            await run_validation(
                db=db,
                evidence_file=evidence_file,
                payload_json=payload_json,
                validation_source="webhook",
            )

        # --- Update evidence tracker (link webhook evidence) ----------------
        tracker_result = await db.execute(
            select(EvidenceTracking).where(
                and_(
                    EvidenceTracking.organization_id == org_id,
                    EvidenceTracking.evidence_id == evidence_id,
                )
            )
        )
        tracker = tracker_result.scalar_one_or_none()
        if tracker:
            tracker.last_collection_date = datetime.utcnow().date()
            logger.info(
                "Updated evidence tracker last_collection_date: evidence_id=%s, org=%s",
                evidence_id, org_id,
            )
        else:
            logger.warning(
                "No evidence tracking record for evidence_id=%s in org=%s — "
                "webhook evidence file created but not linked to tracker",
                evidence_id, org_id,
            )

        # Update delivery status
        delivery.status = "processed"
        delivery.processed_at = datetime.utcnow()

        # Update endpoint stats
        endpoint.last_delivery_at = datetime.utcnow()
        endpoint.delivery_count = (endpoint.delivery_count or 0) + 1

        await db.commit()
        await db.refresh(delivery)

        logger.info(
            "Webhook delivery processed: delivery_id=%s, endpoint=%s, evidence=%s, org=%s",
            delivery.id, endpoint.id, evidence_id, org_id,
        )

        return WebhookIngestResponse(
            delivery_id=delivery.id,
            status="processed",
            message="Evidence ingested successfully",
        )

    except Exception as e:
        logger.error(
            "Webhook delivery failed: endpoint=%s, evidence=%s, error=%s",
            endpoint.id, evidence_id, str(e), exc_info=True,
        )

        delivery.status = "failed"
        delivery.error_message = str(e)[:2000]
        delivery.processed_at = datetime.utcnow()
        await db.commit()

        return WebhookIngestResponse(
            delivery_id=delivery.id,
            status="failed",
            message=f"Processing failed: {str(e)[:200]}",
        )
