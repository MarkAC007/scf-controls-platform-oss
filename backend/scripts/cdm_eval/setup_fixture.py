"""Lifecycle CLI for the local CDM evaluation corpus.

Usage (from backend/):
    python scripts/cdm_eval/setup_fixture.py --stage
    python scripts/cdm_eval/setup_fixture.py --ingest
    python scripts/cdm_eval/setup_fixture.py --verify
    python scripts/cdm_eval/setup_fixture.py --drop

The CDM evaluation harness is only useful when every run measures the same
corpus. A silent thirteenth document, a stale container mount, or a partial
ingest changes the denominator under the metrics and turns a retrieval result
into a story about fixture drift. This script makes that state explicit.

``--stage`` fingerprints the host-side fixture directory into a gitignored
manifest. ``--ingest`` runs the real CDM ingest path inside the backend
container, but tags each document so the fixture can be identified and removed
exactly. ``--verify`` is the read-only preflight imported by the evaluator.
``--drop`` tears down only the tagged rows, including dependent mappings so
foreign keys do not leave a half-removed corpus behind.

No mode computes or persists mappings. The harness evaluates ranking against
parsed documents and stored chunks; writing mapping proposals during setup
would make setup part of the behaviour under test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import string
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

FIXTURE_DIR = BACKEND_ROOT / "fixtures-local" / "policies"
MANIFEST_PATH = BACKEND_ROOT / "fixtures-local" / "manifest.json"
EXPECTED_DOCUMENT_COUNT = 12
INGEST_TIMEOUT_SECONDS = 300
TAG = "realpolicy::"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME_TYPE = "application/pdf"
ALLOWED_SUFFIXES = frozenset({".docx", ".pdf"})


@dataclass(frozen=True)
class ManifestEntry:
    filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class IngestResult:
    filename: str
    ingest_status: str
    word_count: int
    chunk_count: int
    extraction_backend: str | None
    ingest_error: str | None


@dataclass(frozen=True)
class FixtureStatus:
    ok: bool
    n_documents: int
    n_chunks: int
    total_words: int
    fingerprint: str
    failures: tuple[str, ...]


def strip_tag(original_filename: str) -> str:
    if original_filename.startswith(TAG):
        return original_filename[len(TAG):]
    return original_filename


def open_session() -> Session:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for DB-backed fixture modes")

    url = database_url.replace("+asyncpg", "")
    return sessionmaker(bind=create_engine(url))()


def resolve_org_id(session: Session) -> UUID:
    org_id = session.execute(
        text("SELECT id FROM organizations WHERE slug='default'")
    ).scalar_one_or_none()
    if org_id is None:
        raise RuntimeError("No organization found with slug='default'")
    if isinstance(org_id, UUID):
        return org_id
    try:
        return UUID(str(org_id))
    except ValueError as exc:
        raise RuntimeError(f"Organization slug='default' has invalid UUID {org_id!r}") from exc


def corpus_fingerprint(sha256s: Sequence[str]) -> str:
    joined = "\n".join(sorted(sha256s))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def verify_fixture(session: Session, org_id: UUID) -> FixtureStatus:
    rows = session.execute(
        text(
            "SELECT d.id, d.original_filename, d.ingest_status, d.word_count, "
            "d.sha256, count(c.id) AS chunk_count "
            "FROM cdm_documents d "
            "LEFT JOIN cdm_document_chunks c ON c.cdm_document_id=d.id "
            "WHERE d.organization_id=:o AND d.original_filename LIKE :t "
            "GROUP BY d.id, d.original_filename, d.ingest_status, d.word_count, d.sha256 "
            "ORDER BY d.original_filename"
        ),
        {"o": org_id, "t": TAG + "%"},
    ).all()

    failures: list[str] = []
    if len(rows) != EXPECTED_DOCUMENT_COUNT:
        failures.append(
            f"expected {EXPECTED_DOCUMENT_COUNT} tagged documents, found {len(rows)}"
        )

    total_words = 0
    total_chunks = 0
    sha256s: list[str] = []

    for row in rows:
        filename = strip_tag(str(row.original_filename))
        word_count = _int_or_zero(row.word_count)
        chunk_count = _int_or_zero(row.chunk_count)
        total_words += word_count
        total_chunks += chunk_count
        sha256s.append(str(row.sha256))

        if row.ingest_status != "parsed":
            failures.append(
                f"{filename}: ingest_status={row.ingest_status!r}, expected 'parsed'"
            )
        if word_count <= 0:
            failures.append(f"{filename}: word_count={row.word_count!r}, expected > 0")
        if chunk_count <= 0:
            failures.append(f"{filename}: chunks={chunk_count}, expected > 0")

    return FixtureStatus(
        ok=not failures,
        n_documents=len(rows),
        n_chunks=total_chunks,
        total_words=total_words,
        fingerprint=corpus_fingerprint(sha256s),
        failures=tuple(failures),
    )


def _int_or_zero(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _is_valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in string.hexdigits for char in value)


def _fixture_path(filename: str) -> Path:
    candidate = FIXTURE_DIR / filename
    try:
        candidate.resolve().relative_to(FIXTURE_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Manifest filename escapes fixture directory: {filename!r}") from exc
    return candidate


def _mime_type(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".docx":
        return DOCX_MIME_TYPE
    if suffix == ".pdf":
        return PDF_MIME_TYPE
    raise RuntimeError(f"Unsupported fixture document type for {filename!r}")


def _load_existing_manifest_names() -> set[str] | None:
    if not MANIFEST_PATH.exists():
        return None
    manifest = _load_manifest(MANIFEST_PATH)
    return {entry.filename for entry in manifest}


def _load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        raise RuntimeError(f"Manifest not found at {path}; run --stage first")
    if not path.is_file():
        raise RuntimeError(f"Manifest path is not a file: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest is not valid JSON at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Manifest root must be an object: {path}")
    if not isinstance(raw.get("generated_at"), str):
        raise RuntimeError(f"Manifest missing string generated_at: {path}")
    if not isinstance(raw.get("source"), str):
        raise RuntimeError(f"Manifest missing string source: {path}")

    documents = raw.get("documents")
    if not isinstance(documents, list):
        raise RuntimeError(f"Manifest missing documents list: {path}")

    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(documents):
        if not isinstance(item, dict):
            raise RuntimeError(f"Manifest document #{index + 1} must be an object")
        filename = item.get("filename")
        sha256 = item.get("sha256")
        size_bytes = item.get("size_bytes")

        if not isinstance(filename, str) or not filename:
            raise RuntimeError(f"Manifest document #{index + 1} has invalid filename")
        if Path(filename).name != filename or Path(filename).is_absolute():
            raise RuntimeError(f"Manifest filename must be a local basename: {filename!r}")
        if Path(filename).suffix.casefold() not in ALLOWED_SUFFIXES:
            raise RuntimeError(f"Manifest filename has unsupported suffix: {filename!r}")
        if filename in seen:
            raise RuntimeError(f"Manifest contains duplicate filename: {filename!r}")
        if not isinstance(sha256, str) or not _is_valid_sha256(sha256):
            raise RuntimeError(f"Manifest document {filename!r} has invalid sha256")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise RuntimeError(f"Manifest document {filename!r} has invalid size_bytes")

        seen.add(filename)
        entries.append(ManifestEntry(filename=filename, sha256=sha256, size_bytes=size_bytes))

    if len(entries) != EXPECTED_DOCUMENT_COUNT:
        raise RuntimeError(
            f"Manifest contains {len(entries)} documents; expected {EXPECTED_DOCUMENT_COUNT}"
        )
    return sorted(entries, key=lambda entry: entry.filename)


def _enumerate_fixture_documents() -> list[Path]:
    if not FIXTURE_DIR.exists():
        raise RuntimeError(f"Fixture directory does not exist: {FIXTURE_DIR}")
    if not FIXTURE_DIR.is_dir():
        raise RuntimeError(f"Fixture path is not a directory: {FIXTURE_DIR}")

    paths = [
        path
        for path in FIXTURE_DIR.iterdir()
        if path.is_file() and path.suffix.casefold() in ALLOWED_SUFFIXES
    ]
    return sorted(paths, key=lambda path: path.name)


def stage_manifest() -> int:
    paths = _enumerate_fixture_documents()
    filenames = [path.name for path in paths]

    if len(paths) != EXPECTED_DOCUMENT_COUNT:
        print(
            f"Expected {EXPECTED_DOCUMENT_COUNT} fixture documents, found {len(paths)}.",
            file=sys.stderr,
        )
        print("Found files:", file=sys.stderr)
        for filename in filenames:
            print(f"  - {filename}", file=sys.stderr)

        existing_names = _load_existing_manifest_names()
        if existing_names is None:
            print(
                "Missing/extra comparison unavailable because no existing manifest was found.",
                file=sys.stderr,
            )
        else:
            missing = sorted(existing_names - set(filenames))
            extra = sorted(set(filenames) - existing_names)
            print("Missing relative to existing manifest:", file=sys.stderr)
            for filename in missing:
                print(f"  - {filename}", file=sys.stderr)
            print("Extra relative to existing manifest:", file=sys.stderr)
            for filename in extra:
                print(f"  - {filename}", file=sys.stderr)
            if not missing:
                print("  - none", file=sys.stderr)
            if not extra:
                print("  - none", file=sys.stderr)
        return 1

    entries: list[ManifestEntry] = []
    for path in paths:
        blob = path.read_bytes()
        entries.append(
            ManifestEntry(
                filename=path.name,
                sha256=_sha256(blob),
                size_bytes=len(blob),
            )
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(FIXTURE_DIR),
        "documents": [
            {
                "filename": entry.filename,
                "sha256": entry.sha256,
                "size_bytes": entry.size_bytes,
            }
            for entry in sorted(entries, key=lambda entry: entry.filename)
        ],
    }

    if not MANIFEST_PATH.parent.is_dir():
        raise RuntimeError(f"Manifest parent directory does not exist: {MANIFEST_PATH.parent}")
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    fingerprint = corpus_fingerprint([entry.sha256 for entry in entries])
    print(f"wrote manifest: {MANIFEST_PATH}")
    print(f"documents: {len(entries)}")
    print(f"corpus fingerprint: {fingerprint}")
    return 0


def ingest_fixture() -> int:
    entries = _load_manifest(MANIFEST_PATH)
    session = open_session()
    try:
        org_id = resolve_org_id(session)
        existing = session.execute(
            text(
                "SELECT count(*) FROM cdm_documents "
                "WHERE organization_id=:o AND original_filename LIKE :t"
            ),
            {"o": org_id, "t": TAG + "%"},
        ).scalar_one()
        if int(existing) > 0:
            print(
                f"Found {existing} tagged fixture documents. Run --drop before --ingest.",
                file=sys.stderr,
            )
            return 1

        from services import cdm_storage
        import tasks_cdm

        print(f"ingesting {len(entries)} fixture documents")
        print(
            f"{'filename':<64} {'status':<10} {'words':>8} {'chunks':>8} {'backend':<16} error"
        )

        results: list[IngestResult] = []
        for entry in entries:
            try:
                result = _ingest_one(session, org_id, entry, cdm_storage, tasks_cdm)
            except Exception as exc:
                session.rollback()
                print(f"ERROR ingesting {entry.filename}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            results.append(result)
            _print_ingest_result(result)

        bad = [
            result
            for result in results
            if result.ingest_status != "parsed"
            or result.word_count <= 0
            or result.chunk_count <= 0
        ]
        if bad:
            print("Fixture ingest completed with invalid document state:", file=sys.stderr)
            for result in bad:
                print(
                    f"  - {result.filename}: status={result.ingest_status!r}, "
                    f"words={result.word_count}, chunks={result.chunk_count}, "
                    f"error={result.ingest_error!r}",
                    file=sys.stderr,
                )
            return 1

        print(f"corpus fingerprint: {corpus_fingerprint([entry.sha256 for entry in entries])}")
        return 0
    finally:
        session.close()


def _ingest_one(
    session: Session,
    org_id: UUID,
    entry: ManifestEntry,
    cdm_storage: Any,
    tasks_cdm: Any,
) -> IngestResult:
    path = _fixture_path(entry.filename)
    if not path.exists():
        raise RuntimeError(f"Fixture file missing in container/host path: {path}")
    if not path.is_file():
        raise RuntimeError(f"Fixture path is not a file: {path}")

    blob = path.read_bytes()
    actual_sha256 = _sha256(blob)
    if actual_sha256 != entry.sha256:
        raise RuntimeError(
            f"sha256 mismatch for {entry.filename}: manifest={entry.sha256}, actual={actual_sha256}"
        )
    if len(blob) != entry.size_bytes:
        raise RuntimeError(
            f"size mismatch for {entry.filename}: manifest={entry.size_bytes}, actual={len(blob)}"
        )

    doc_id = uuid.uuid4()
    stored_name = TAG + entry.filename
    session.execute(
        text(
            "INSERT INTO cdm_documents (id, organization_id, original_filename, "
            "mime_type, sha256, size_bytes, ingest_status, created_at) "
            "VALUES (:i,:o,:f,:m,:h,:sz,'pending',now())"
        ),
        {
            "i": doc_id,
            "o": org_id,
            "f": stored_name,
            "m": _mime_type(entry.filename),
            "h": entry.sha256,
            "sz": entry.size_bytes,
        },
    )
    session.commit()

    key = cdm_storage.build_cdm_object_key(org_id, doc_id, stored_name)
    if not isinstance(key, str) or not key:
        raise RuntimeError(f"Storage key builder returned invalid key for {entry.filename!r}")
    cdm_storage.write_cdm_payload(key, blob, str(org_id))

    task_result = tasks_cdm.ingest_cdm_document.delay(str(doc_id)).get(
        timeout=INGEST_TIMEOUT_SECONDS
    )
    _check_task_result(task_result, doc_id, entry.filename)
    session.expire_all()

    row = session.execute(
        text(
            "SELECT ingest_status, word_count, extraction_backend, ingest_error "
            "FROM cdm_documents WHERE id=:i"
        ),
        {"i": doc_id},
    ).one_or_none()
    if row is None:
        raise RuntimeError(f"Inserted document row disappeared after ingest: {entry.filename}")

    chunk_count = session.execute(
        text("SELECT count(*) FROM cdm_document_chunks WHERE cdm_document_id=:i"),
        {"i": doc_id},
    ).scalar_one()
    if chunk_count is None:
        raise RuntimeError(f"Chunk count query returned no value for {entry.filename}")

    return IngestResult(
        filename=entry.filename,
        ingest_status=str(row.ingest_status),
        word_count=_int_or_zero(row.word_count),
        chunk_count=int(chunk_count),
        extraction_backend=row.extraction_backend,
        ingest_error=row.ingest_error,
    )


def _check_task_result(task_result: Any, doc_id: UUID, filename: str) -> None:
    if not isinstance(task_result, dict):
        raise RuntimeError(f"Celery ingest for {filename} returned non-object result: {task_result!r}")
    result_doc_id = task_result.get("document_id")
    if result_doc_id != str(doc_id):
        raise RuntimeError(
            f"Celery ingest for {filename} returned document_id={result_doc_id!r}, expected {doc_id}"
        )
    result_status = task_result.get("status")
    if not isinstance(result_status, str) or not result_status:
        raise RuntimeError(f"Celery ingest for {filename} returned invalid status: {task_result!r}")


def _print_ingest_result(result: IngestResult) -> None:
    error = result.ingest_error or ""
    backend = result.extraction_backend or ""
    print(
        f"{result.filename[:64]:<64} {result.ingest_status:<10} "
        f"{result.word_count:>8} {result.chunk_count:>8} {backend:<16} {error}"
    )


def drop_fixture() -> int:
    session = open_session()
    try:
        org_id = resolve_org_id(session)
        rows = session.execute(
            text(
                "SELECT id FROM cdm_documents "
                "WHERE organization_id=:o AND original_filename LIKE :t "
                "ORDER BY original_filename"
            ),
            {"o": org_id, "t": TAG + "%"},
        ).all()
        ids = [row.id for row in rows]

        for document_id in ids:
            session.execute(
                text("DELETE FROM cdm_mappings WHERE cdm_document_id=:d"),
                {"d": document_id},
            )
            session.execute(
                text("DELETE FROM cdm_document_chunks WHERE cdm_document_id=:d"),
                {"d": document_id},
            )
            session.execute(
                text("DELETE FROM cdm_documents WHERE id=:d"),
                {"d": document_id},
            )
        session.commit()

        if ids:
            print(f"dropped {len(ids)} tagged fixture documents")
        else:
            print("dropped 0 tagged fixture documents; fixture was already absent")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def verify_fixture_cli() -> int:
    session = open_session()
    try:
        org_id = resolve_org_id(session)
        status = verify_fixture(session, org_id)
        _print_fixture_status(status)
        return 0 if status.ok else 1
    finally:
        session.close()


def _print_fixture_status(status: FixtureStatus) -> None:
    has_expected_count = status.n_documents == EXPECTED_DOCUMENT_COUNT
    has_no_status_failures = not any("ingest_status=" in failure for failure in status.failures)
    has_no_word_failures = not any("word_count=" in failure for failure in status.failures)
    has_no_chunk_failures = not any("chunks=" in failure for failure in status.failures)

    print(
        f"document count: {'OK' if has_expected_count else 'FAIL'} "
        f"({status.n_documents}/{EXPECTED_DOCUMENT_COUNT})"
    )
    print(f"parsed status: {'OK' if has_no_status_failures else 'FAIL'}")
    print(f"word counts: {'OK' if has_no_word_failures else 'FAIL'}")
    print(f"chunk counts: {'OK' if has_no_chunk_failures else 'FAIL'}")
    print(f"totals: documents={status.n_documents} chunks={status.n_chunks} words={status.total_words}")
    print(f"corpus fingerprint: {status.fingerprint}")

    if status.failures:
        print("failures:")
        for failure in status.failures:
            print(f"  - {failure}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage the CDM evaluation fixture lifecycle"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--stage", action="store_true", help="Fingerprint local fixture files")
    modes.add_argument("--ingest", action="store_true", help="Ingest fixture files through CDM")
    modes.add_argument("--drop", action="store_true", help="Remove tagged fixture rows")
    modes.add_argument("--verify", action="store_true", help="Verify tagged fixture readiness")
    args = parser.parse_args()

    try:
        if args.stage:
            return stage_manifest()
        if args.ingest:
            return ingest_fixture()
        if args.drop:
            return drop_fixture()
        if args.verify:
            return verify_fixture_cli()
        raise RuntimeError("No fixture mode selected")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
