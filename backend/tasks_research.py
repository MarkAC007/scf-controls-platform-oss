"""
Celery tasks for AI-powered vendor research (Issue #59).

Orchestrates parallel research across four external sources:
    - HIBP (Have I Been Pwned) — domain breach history
    - CISA KEV — Known Exploited Vulnerabilities catalogue
    - CVE/NVD — Common Vulnerabilities and Exposures
    - Regulatory — regulatory actions and sanctions

Uses a Celery chord: four source tasks run in parallel, then an aggregator
callback merges results, computes an overall risk signal, persists to the
database, and caches in Redis.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from celery import chord, shared_task
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from services.outbound_rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync database session (Celery tasks run outside the async event loop)
# Lazy initialisation avoids import-time failure if psycopg2 is absent
# (the web process should never import this module directly).
# ---------------------------------------------------------------------------
_SYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"
).replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")

_sync_engine = None
SyncSession = None


def _get_sync_session():
    """Lazily create the sync engine and session factory on first use."""
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()

# ---------------------------------------------------------------------------
# Sync Redis helper (Celery context is synchronous)
# ---------------------------------------------------------------------------
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

def _get_sync_redis():
    """Return a synchronous Redis client for use inside Celery tasks."""
    import redis as sync_redis
    return sync_redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        socket_keepalive=True,
        retry_on_timeout=True,
        health_check_interval=30,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RESEARCH_CACHE_TTL = int(timedelta(days=30).total_seconds())  # 30 days
CACHE_KEY_PREFIX = "scf:cache:v1:vendor_research"

REQUEST_TIMEOUT = 20  # seconds per external HTTP call

# Source task names (used for routing)
TASK_PREFIX = "tasks_research"

# ---------------------------------------------------------------------------
# 1. Orchestrator
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_vendor_orchestrator")
def research_vendor_orchestrator(
    self,
    vendor_id: str,
    job_id: str,
    domain: str,
    vendor_name: str,
) -> Dict[str, Any]:
    """
    Create a chord of four source research tasks plus an aggregator callback.

    Called by the service layer after creating the VendorResearchResult row
    with status='pending'.
    """
    task_id = self.request.id
    logger.info(
        f"research_vendor_orchestrator[{task_id}] starting for "
        f"vendor={vendor_id} job={job_id} domain={domain}"
    )

    # Mark job as running -------------------------------------------------
    try:
        from models import VendorResearchResult
        session = _get_sync_session()
        try:
            session.execute(
                update(VendorResearchResult)
                .where(VendorResearchResult.job_id == job_id)
                .values(
                    status="running",
                    started_at=datetime.utcnow(),
                    source_statuses={
                        "hibp": "pending",
                        "cisa_kev": "pending",
                        "cve_nvd": "pending",
                        "regulatory": "pending",
                    },
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"Failed to mark job {job_id} as running: {exc}")

    # Dispatch chord -------------------------------------------------------
    header = [
        research_hibp.s(domain, vendor_name),
        research_cisa_kev.s(vendor_name),
        research_cve_nvd.s(vendor_name),
        research_regulatory.s(vendor_name),
    ]
    callback = research_aggregator.s(job_id=job_id, vendor_id=vendor_id)

    result = chord(header)(callback)
    logger.info(f"Chord dispatched for job {job_id}, chord_id={result.id}")

    return {"job_id": job_id, "chord_id": result.id, "status": "dispatched"}


# ---------------------------------------------------------------------------
# 2. HIBP source task
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_hibp")
def research_hibp(self, domain: str, vendor_name: str) -> Dict[str, Any]:
    """Query Have I Been Pwned for domain breaches."""
    source = "hibp"
    logger.info(f"research_hibp starting for domain={domain}")

    api_key = os.getenv("HIBP_API_KEY", "")
    if not api_key:
        return _source_error(source, "HIBP_API_KEY not configured")

    try:
        rate_limiter.wait(source)

        resp = requests.get(
            f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}",
            headers={
                "hibp-api-key": api_key,
                "User-Agent": "SCF-Controls-Platform-Research",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code == 404:
            # No breaches found — good news
            return _source_success(source, {
                "breaches": [],
                "breach_count": 0,
                "domain": domain,
                "signal": "low",
            })

        if resp.status_code == 429:
            return _source_error(source, "Rate limited by HIBP API")

        resp.raise_for_status()
        breaches = resp.json()

        # Extract key signals
        total_pwned = sum(b.get("PwnCount", 0) for b in breaches)
        sensitive_breaches = [
            b["Name"] for b in breaches if b.get("IsSensitive", False)
        ]
        recent_breaches = [
            b["Name"] for b in breaches
            if b.get("BreachDate", "") >= (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
        ]

        signal = "low"
        if len(breaches) > 5 or total_pwned > 1_000_000:
            signal = "high"
        elif len(breaches) > 2 or total_pwned > 100_000:
            signal = "medium"

        return _source_success(source, {
            "breaches": [
                {
                    "name": b.get("Name"),
                    "date": b.get("BreachDate"),
                    "pwn_count": b.get("PwnCount", 0),
                    "data_classes": b.get("DataClasses", []),
                    "is_sensitive": b.get("IsSensitive", False),
                    "description": (b.get("Description") or "")[:500],
                    "is_verified": b.get("IsVerified", False),
                    "modified_date": b.get("ModifiedDate"),
                    "is_fabricated": b.get("IsFabricated", False),
                    "is_retired": b.get("IsRetired", False),
                }
                for b in breaches[:20]  # cap to 20 for storage
            ],
            "breach_count": len(breaches),
            "total_pwned_accounts": total_pwned,
            "sensitive_breaches": sensitive_breaches,
            "recent_breaches": recent_breaches,
            "domain": domain,
            "signal": signal,
        })

    except requests.RequestException as exc:
        return _source_error(source, f"HTTP error: {exc}")
    except Exception as exc:
        logger.exception(f"research_hibp unexpected error: {exc}")
        return _source_error(source, f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# 3. CISA KEV source task
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_cisa_kev")
def research_cisa_kev(self, vendor_name: str) -> Dict[str, Any]:
    """Query CISA Known Exploited Vulnerabilities catalogue."""
    source = "cisa_kev"
    logger.info(f"research_cisa_kev starting for vendor={vendor_name}")

    try:
        rate_limiter.wait(source)

        resp = requests.get(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        # Filter vulnerabilities by vendor name (case-insensitive)
        vendor_lower = vendor_name.lower()
        matches = [
            v for v in data.get("vulnerabilities", [])
            if vendor_lower in v.get("vendorProject", "").lower()
        ]

        overdue = [
            v for v in matches
            if v.get("dueDate", "") < datetime.utcnow().strftime("%Y-%m-%d")
        ]

        signal = "low"
        if len(matches) > 10 or len(overdue) > 5:
            signal = "high"
        elif len(matches) > 3 or len(overdue) > 0:
            signal = "medium"

        return _source_success(source, {
            "vulnerabilities": [
                {
                    "cve_id": v.get("cveID"),
                    "vendor_project": v.get("vendorProject"),
                    "product": v.get("product"),
                    "vulnerability_name": v.get("vulnerabilityName"),
                    "date_added": v.get("dateAdded"),
                    "due_date": v.get("dueDate"),
                    "known_ransomware_use": v.get("knownRansomwareCampaignUse", "Unknown"),
                    "short_description": (v.get("shortDescription") or "")[:500],
                    "required_action": (v.get("requiredAction") or "")[:500],
                    "notes": (v.get("notes") or "")[:500],
                }
                for v in matches[:30]
            ],
            "total_matches": len(matches),
            "overdue_count": len(overdue),
            "vendor_searched": vendor_name,
            "catalogue_count": data.get("count", 0),
            "signal": signal,
        })

    except requests.RequestException as exc:
        return _source_error(source, f"HTTP error: {exc}")
    except Exception as exc:
        logger.exception(f"research_cisa_kev unexpected error: {exc}")
        return _source_error(source, f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# 4. CVE/NVD source task
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_cve_nvd")
def research_cve_nvd(self, vendor_name: str) -> Dict[str, Any]:
    """Query NIST NVD for CVEs related to the vendor."""
    source = "nvd"
    logger.info(f"research_cve_nvd starting for vendor={vendor_name}")

    try:
        rate_limiter.wait("nvd")

        headers = {"User-Agent": "SCF-Controls-Platform-Research"}
        nvd_api_key = os.getenv("NVD_API_KEY", "")
        if nvd_api_key:
            headers["apiKey"] = nvd_api_key

        resp = requests.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={
                "keywordSearch": vendor_name,
                "resultsPerPage": 50,
            },
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code == 403:
            return _source_error(source, "NVD API returned 403 Forbidden")

        resp.raise_for_status()
        data = resp.json()

        vulns = data.get("vulnerabilities", [])
        total_results = data.get("totalResults", 0)

        # Severity breakdown
        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        processed = []
        for item in vulns:
            cve = item.get("cve", {})
            cve_id = cve.get("id", "")

            # Extract CVSS score (prefer v3.1, fallback v3.0, then v2)
            metrics = cve.get("metrics", {})
            cvss_score = None
            severity = None
            for version_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                metric_list = metrics.get(version_key, [])
                if metric_list:
                    cvss_data = metric_list[0].get("cvssData", {})
                    cvss_score = cvss_data.get("baseScore")
                    severity = cvss_data.get("baseSeverity", "").upper()
                    break

            if severity in severity_counts:
                severity_counts[severity] += 1

            descriptions = cve.get("descriptions", [])
            desc = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")

            # Extract CVSS vector, exploitability score, and CWE IDs (DPSIA Enhancement)
            cvss_vector = None
            exploitability_score = None
            for version_key in ("cvssMetricV31", "cvssMetricV30"):
                metric_list = metrics.get(version_key, [])
                if metric_list:
                    cvss_data = metric_list[0].get("cvssData", {})
                    cvss_vector = cvss_data.get("vectorString")
                    exploitability_score = metric_list[0].get("exploitabilityScore")
                    break

            weaknesses = cve.get("weaknesses", [])
            cwe_ids = []
            for w in weaknesses:
                for wd in w.get("description", []):
                    if wd.get("value", "").startswith("CWE-"):
                        cwe_ids.append(wd["value"])

            references = cve.get("references", [])
            ref_urls = [r.get("url") for r in references[:5] if r.get("url")]
            patch_urls = [
                r.get("url") for r in references
                if r.get("url") and any(t in (r.get("tags") or []) for t in ["Patch", "Vendor Advisory"])
            ][:3]

            processed.append({
                "cve_id": cve_id,
                "description": desc[:500],
                "cvss_score": cvss_score,
                "severity": severity,
                "published": cve.get("published"),
                "cvss_vector": cvss_vector,
                "exploitability_score": exploitability_score,
                "cwe_ids": cwe_ids,
                "reference_urls": ref_urls,
                "patch_urls": patch_urls,
            })

        signal = "low"
        if severity_counts["CRITICAL"] > 3 or severity_counts["HIGH"] > 10:
            signal = "high"
        elif severity_counts["CRITICAL"] > 0 or severity_counts["HIGH"] > 3:
            signal = "medium"

        return _source_success(source, {
            "cves": processed,
            "total_results": total_results,
            "severity_counts": severity_counts,
            "vendor_searched": vendor_name,
            "signal": signal,
        })

    except requests.RequestException as exc:
        return _source_error(source, f"HTTP error: {exc}")
    except Exception as exc:
        logger.exception(f"research_cve_nvd unexpected error: {exc}")
        return _source_error(source, f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# 5. Regulatory source task
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_regulatory")
def research_regulatory(self, vendor_name: str) -> Dict[str, Any]:
    """Search for regulatory actions, sanctions, and compliance issues."""
    source = "regulatory"
    logger.info(f"research_regulatory starting for vendor={vendor_name}")

    findings: List[Dict[str, Any]] = []

    # --- OFAC / SDN sanctions check (US Treasury) -------------------------
    try:
        rate_limiter.wait(source)
        resp = requests.get(
            "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            content = resp.text.lower()
            if vendor_name.lower() in content:
                findings.append({
                    "type": "ofac_sdn",
                    "description": f"Potential match for '{vendor_name}' in OFAC SDN list",
                    "severity": "critical",
                    "source_url": "https://sanctionslistservice.ofac.treas.gov",
                })
    except requests.RequestException as exc:
        logger.warning(f"OFAC check failed: {exc}")

    # --- SEC EDGAR filings check ------------------------------------------
    try:
        rate_limiter.wait(source)
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": f'"{vendor_name}"', "dateRange": "custom", "startdt": "2020-01-01"},
            headers={"User-Agent": "SCF-Controls-Platform research@scfcontrols.com"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            try:
                sec_data = resp.json()
                total_hits = sec_data.get("hits", {}).get("total", {}).get("value", 0)
                if total_hits > 0:
                    findings.append({
                        "type": "sec_filings",
                        "description": f"Found {total_hits} SEC filing(s) mentioning '{vendor_name}'",
                        "severity": "info",
                        "total_hits": total_hits,
                        "source_url": "https://efts.sec.gov",
                    })
            except (ValueError, KeyError):
                pass
    except requests.RequestException as exc:
        logger.warning(f"SEC check failed: {exc}")

    # --- GDPR Enforcement Tracker (enforcementtracker.com) -----------------
    try:
        rate_limiter.wait(source)
        resp = requests.get(
            "https://www.enforcementtracker.com/statistics.html",
            headers={"User-Agent": "SCF-Controls-Platform research@scfcontrols.com"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            content = resp.text.lower()
            if vendor_name.lower() in content:
                findings.append({
                    "type": "gdpr_enforcement",
                    "description": f"Potential GDPR enforcement reference for '{vendor_name}' on enforcementtracker.com",
                    "severity": "high",
                    "source_url": "https://www.enforcementtracker.com",
                })
    except requests.RequestException as exc:
        logger.warning(f"GDPR enforcement tracker check failed: {exc}")

    signal = "low"
    critical_findings = [f for f in findings if f.get("severity") == "critical"]
    if critical_findings:
        signal = "high"
    elif len(findings) > 3:
        signal = "medium"

    return _source_success(source, {
        "findings": findings,
        "finding_count": len(findings),
        "critical_count": len(critical_findings),
        "vendor_searched": vendor_name,
        "signal": signal,
    })


# ---------------------------------------------------------------------------
# 6. Aggregator (chord callback)
# ---------------------------------------------------------------------------
@shared_task(bind=True, name=f"{TASK_PREFIX}.research_aggregator")
def research_aggregator(
    self,
    source_results: List[Dict[str, Any]],
    job_id: str,
    vendor_id: str,
) -> Dict[str, Any]:
    """
    Merge source results, compute overall risk signal, persist to DB, and cache.

    Receives the list of return values from the four source tasks.
    """
    logger.info(f"research_aggregator starting for job={job_id}")

    # Map results by source ------------------------------------------------
    results_map: Dict[str, Dict[str, Any]] = {}
    source_statuses: Dict[str, str] = {}
    errors: List[Dict[str, str]] = []
    risk_signals: Dict[str, str] = {}

    for result in source_results:
        src = result.get("source", "unknown")
        status = result.get("status", "unknown")
        results_map[src] = result.get("data", {})
        source_statuses[src] = status

        if status == "failed":
            errors.append({"source": src, "error": result.get("error", "Unknown error")})
        else:
            signal = result.get("data", {}).get("signal", "low")
            risk_signals[src] = signal

    # Calculate overall risk signal ----------------------------------------
    overall = _compute_overall_risk(risk_signals)

    # Determine job status -------------------------------------------------
    succeeded = sum(1 for s in source_statuses.values() if s == "success")
    total = len(source_statuses)
    if succeeded == total:
        job_status = "completed"
    elif succeeded > 0:
        job_status = "partial"
    else:
        job_status = "failed"

    # Build summary text ---------------------------------------------------
    summary_parts = []
    if "hibp" in results_map:
        bc = results_map["hibp"].get("breach_count", 0)
        summary_parts.append(f"{bc} breach(es) found via HIBP")
    if "cisa_kev" in results_map:
        mc = results_map["cisa_kev"].get("total_matches", 0)
        summary_parts.append(f"{mc} CISA KEV match(es)")
    if "nvd" in results_map:
        tr = results_map["nvd"].get("total_results", 0)
        summary_parts.append(f"{tr} CVE(s) in NVD")
    if "regulatory" in results_map:
        fc = results_map["regulatory"].get("finding_count", 0)
        summary_parts.append(f"{fc} regulatory finding(s)")
    summary = "; ".join(summary_parts) if summary_parts else "No results available."

    # Persist to database --------------------------------------------------
    now = datetime.utcnow()
    try:
        from models import VendorResearchResult
        session = _get_sync_session()
        try:
            session.execute(
                update(VendorResearchResult)
                .where(VendorResearchResult.job_id == job_id)
                .values(
                    status=job_status,
                    hibp_results=results_map.get("hibp", {}),
                    cisa_kev_results=results_map.get("cisa_kev", {}),
                    cve_nvd_results=results_map.get("nvd", {}),
                    regulatory_results=results_map.get("regulatory", {}),
                    summary=summary,
                    risk_indicators=risk_signals,
                    overall_risk_signal=overall,
                    source_statuses=source_statuses,
                    errors=errors,
                    completed_at=now,
                )
            )
            session.commit()
            logger.info(f"Persisted results for job {job_id} with status={job_status}")
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"Failed to persist results for job {job_id}: {exc}")

    # Cache in Redis -------------------------------------------------------
    cache_payload = {
        "job_id": job_id,
        "vendor_id": vendor_id,
        "status": job_status,
        "hibp_results": results_map.get("hibp", {}),
        "cisa_kev_results": results_map.get("cisa_kev", {}),
        "cve_nvd_results": results_map.get("nvd", {}),
        "regulatory_results": results_map.get("regulatory", {}),
        "summary": summary,
        "risk_indicators": risk_signals,
        "overall_risk_signal": overall,
        "source_statuses": source_statuses,
        "errors": errors,
        "completed_at": now.isoformat(),
    }
    try:
        r = _get_sync_redis()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        r.setex(cache_key, RESEARCH_CACHE_TTL, json.dumps(cache_payload, default=str))
        # Also cache as "latest" for the vendor
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        r.setex(latest_key, RESEARCH_CACHE_TTL, json.dumps(cache_payload, default=str))
        logger.info(f"Cached results for job {job_id} with 30-day TTL")
    except Exception as exc:
        logger.warning(f"Failed to cache results for job {job_id}: {exc}")

    return {
        "job_id": job_id,
        "status": job_status,
        "overall_risk_signal": overall,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_success(source: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a successful source result."""
    return {
        "source": source,
        "status": "success",
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _source_error(source: str, error: str) -> Dict[str, Any]:
    """Wrap a failed source result (never raises — chord callback always fires)."""
    logger.warning(f"Source {source} failed: {error}")
    return {
        "source": source,
        "status": "failed",
        "error": error,
        "data": {},
        "timestamp": datetime.utcnow().isoformat(),
    }


def _compute_overall_risk(signals: Dict[str, str]) -> str:
    """
    Compute overall risk from per-source signals.

    Rules:
        - Any 'high' → overall 'high'
        - 2+ 'medium' → overall 'high'
        - 1 'medium' → overall 'medium'
        - All 'low' → overall 'low'
        - No signals → 'unknown'
    """
    if not signals:
        return "unknown"

    values = list(signals.values())
    high_count = values.count("high")
    medium_count = values.count("medium")

    if high_count > 0:
        return "high"
    if medium_count >= 2:
        return "high"
    if medium_count == 1:
        return "medium"
    return "low"
