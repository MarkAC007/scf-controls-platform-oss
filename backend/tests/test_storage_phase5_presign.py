"""Phase 5 — the presign response says which verb it signed (ISC 43, ISC 44).

The browser used to choose between a presigned POST and a raw PUT by asking
whether the ``fields`` dictionary was empty. That was a stand-in for "this is
Azure", and it is only ever right by coincidence: a presigned POST that needs no
*extra* form fields is a legal reply from an S3-compatible store, and the
browser would have sent it with the wrong verb and an Azure header on it.

So the response carries the verb explicitly. Three properties are pinned here:

1. The field exists, is required, and is a **closed vocabulary** — a value the
   client does not know about is refused at the boundary rather than coerced.
   ``PUT`` is in that vocabulary today because Azure signs one; the point of
   making it a vocabulary rather than a boolean is that a future S3 preset which
   signs a ``PUT`` is representable without the client guessing again.
2. The S3 driver — which serves all four presets — says ``POST`` and names the
   provider it resolved, rather than leaving either to be re-derived downstream.
3. The route carries both through untouched.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schemas import EvidenceFileUploadUrlResponse  # noqa: E402
from services.storage_config import (  # noqa: E402
    PROVIDER_MINIO,
    SOURCE_ORG,
    SSE_NONE,
    ResolvedStorageConfig,
)


def _minio_config() -> ResolvedStorageConfig:
    """An organisation's own MinIO — path-style, no SSE, its own endpoint."""
    return ResolvedStorageConfig(
        config_id="cfg-1",
        source=SOURCE_ORG,
        provider=PROVIDER_MINIO,
        bucket="acme-evidence",
        region="eu-west-1",
        endpoint_url="https://objects.example.com",
        path_style=True,
        sse_mode=SSE_NONE,
        url_expiry=900,
        max_file_size=50 * 1024 * 1024,
    )


def _response(**overrides):
    payload = {
        "method": "POST",
        "provider": "minio",
        "url": "https://objects.example.com/acme-evidence",
        "fields": {},
        "s3_key": "evidence/org-1/2026/09/abc_test.pdf",
        "expires_in": 900,
        "upload_ticket": "ticket",
    }
    payload.update(overrides)
    return EvidenceFileUploadUrlResponse(**payload)


class TestTheResponseStatesItsVerb:
    def test_the_schema_carries_a_method_and_a_provider(self):
        model = _response()
        assert model.method == "POST"
        assert model.provider == "minio"
        assert {"method", "provider"} <= set(
            EvidenceFileUploadUrlResponse.model_fields
        )

    def test_the_method_is_required(self):
        # No default. A driver that does not say which verb it signed must be a
        # failure to find, not a POST to assume — which is exactly the guess the
        # empty-fields sniff was making.
        with pytest.raises(ValidationError) as exc:
            EvidenceFileUploadUrlResponse(
                provider="minio",
                url="https://objects.example.com/acme-evidence",
                fields={},
                s3_key="evidence/org-1/2026/09/abc_test.pdf",
                expires_in=900,
                upload_ticket=None,
            )
        assert "method" in str(exc.value)

    def test_a_put_preset_is_representable(self):
        # The reason this is a vocabulary and not `is_post: bool`.
        assert _response(method="PUT", provider="azure_blob").method == "PUT"

    def test_an_unknown_verb_is_refused_at_the_boundary(self):
        with pytest.raises(ValidationError):
            _response(method="PATCH")

    def test_an_empty_fields_dict_is_still_a_post(self):
        # The case the old client got wrong. Nothing about an empty `fields`
        # changes the verb.
        model = _response(fields={})
        assert model.fields == {}
        assert model.method == "POST"


class TestTheDriverSaysWhatItSigned:
    @patch("services.s3_service._presign_client")
    def test_the_s3_driver_returns_post_and_the_resolved_provider(self, mock_client):
        import services.s3_service as s3_service

        client = MagicMock()
        client.generate_presigned_post.return_value = {
            "url": "https://objects.example.com/acme-evidence",
            "fields": {"key": "evidence/org-1/2026/09/abc_test.pdf"},
        }
        mock_client.return_value = client

        result = s3_service.generate_upload_presigned_post(
            org_id="org-1",
            filename="test.pdf",
            content_type="application/pdf",
            config=_minio_config(),
        )

        assert result["method"] == "POST"
        # The provider the configuration resolved to, not a constant: the same
        # driver serves all four presets.
        assert result["provider"] == PROVIDER_MINIO

    def test_the_azure_driver_says_put(self):
        # Retired by D13 and still present; while it is present it must state
        # its verb like every other signer, because nothing infers one now.
        import inspect

        from services import azure_blob_service

        source = inspect.getsource(azure_blob_service.generate_upload_presigned_post)
        assert '"method": "PUT"' in source
        assert '"provider": "azure_blob"' in source


class TestTheRouteCarriesItThrough:
    @pytest.mark.asyncio
    async def test_the_upload_url_route_passes_the_verb_to_the_client(self):
        from types import SimpleNamespace
        from uuid import uuid4

        from api import evidence_files
        from schemas import EvidenceFileUploadUrlRequest

        org_id = uuid4()
        membership = SimpleNamespace(
            organization_id=org_id,
            user=SimpleNamespace(db_id=str(uuid4()), email="admin@example.com"),
        )

        signed = {
            "url": "https://objects.example.com/acme-evidence",
            "fields": {},
            "object_key": "evidence/org-1/2026/09/abc_test.pdf",
            "expires_in": 900,
            "method": "POST",
            "provider": "minio",
        }

        with patch.object(
            evidence_files, "generate_upload_presigned_post", return_value=signed
        ), patch.object(evidence_files, "mint_upload_ticket", return_value="ticket"):
            result = await evidence_files.get_upload_url(
                org_id=org_id,
                evidence_id="ERL-001",
                request=EvidenceFileUploadUrlRequest(
                    filename="test.pdf",
                    content_type="application/pdf",
                    file_size_bytes=1024,
                ),
                membership=membership,
            )

        assert result.method == "POST"
        assert result.provider == "minio"
        # Empty fields, and still a POST: the response does not let the client
        # re-derive the verb from them.
        assert result.fields == {}
