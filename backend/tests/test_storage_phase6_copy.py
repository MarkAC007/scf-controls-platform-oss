"""Phase 6 of bring-your-own evidence storage: the copy between stores.

ISA 20260912-0930, criteria 46 to 51.

No network, no database, no Redis. Two stores are two dictionaries behind a
fake facade, which is enough to prove everything these criteria are about —
what gets read, what gets written, what gets verified, what gets committed, and
above all what never gets called.

Four choices about how these are built, because each is the difference between
a test that proves something and one that passes.

**The anti-delete criterion is asserted on the store, not on the source.** A
grep for ``delete`` in the task would pass the day somebody deletes through a
helper with another name. The fake stores raise if anything asks them to
delete, and the assertion is made after a whole run over multiple files
including a failing one — so it covers the error paths, which is where a
"clean up what we half-wrote" delete would actually get added.

**Resumability is proved by running the task twice over the same rows**, with
the store instrumented to count reads. The second run must read nothing,
because every row already points at the target. A test that only checked the
final state would pass against an implementation that copied everything twice.

**A verification failure is proved to leave the row on the source**, not merely
to be reported. The point of per-row commit is that a failed file is still
readable from where its bytes are; a task that reported the failure and moved
the pointer anyway would satisfy a weaker test and lose the file.

**Per-row commit is counted.** One commit per copied row, not one at the end:
that is the whole mechanism behind resumability, and it is invisible in the
final state.
"""
from __future__ import annotations

import re
import hashlib
import uuid
from typing import Any, Dict, List, Optional

import pytest

# Imported for its side effect: `models.System` relates to
# `SystemCatalogTemplate`, which lives here, and SQLAlchemy configures no
# mapper until both are in the registry.
import catalog_models  # noqa: F401
import tasks_evidence_storage_copy as copy_task
from services import storage_config

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
SOURCE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
TARGET_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class DeleteAttempted(AssertionError):
    """Raised if anything asks a store to delete during a copy (ISC 49)."""


class FakeStore:
    """One object store: a dict of key -> bytes, plus counters.

    ``delete_object`` exists and raises. A store that simply had no delete
    method would make the anti-criterion pass by accident on any facade that
    stopped exposing one; this way the assertion is about the copy's behaviour.
    """

    def __init__(self, objects: Optional[Dict[str, bytes]] = None):
        self.objects: Dict[str, bytes] = dict(objects or {})
        self.reads = 0
        self.writes = 0
        self.heads = 0
        self.deletes = 0

    def delete_object(self, key: str) -> None:
        self.deletes += 1
        raise DeleteAttempted(f"the copy asked {self!r} to delete {key}")


class FakeConfig:
    """Stands in for a ResolvedStorageConfig. Carries an id and a store."""

    def __init__(self, config_id: uuid.UUID, store: FakeStore, bucket: str = "b"):
        self.config_id = str(config_id)
        self.store = store
        self.bucket = bucket


class FakeFacade:
    """The three copy primitives, over FakeConfig objects.

    Deliberately the same names and signatures ``services.storage_service``
    exposes, so a rename on the real facade breaks these tests rather than
    leaving them passing against a function that no longer exists.
    """

    def __init__(self, corrupt_on_write: Optional[Dict[str, bytes]] = None):
        #: key -> the bytes to store INSTEAD of what was handed over, to
        #: simulate a store that silently mangles a write.
        self.corrupt_on_write = corrupt_on_write or {}

    def head_object(self, config: FakeConfig, key: str) -> Optional[dict]:
        config.store.heads += 1
        data = config.store.objects.get(key)
        if data is None:
            return None
        return {"size": len(data), "etag": "x", "content_type": "application/pdf"}

    def download_object_to_fileobj(self, config: FakeConfig, key: str, fileobj) -> None:
        config.store.reads += 1
        data = config.store.objects.get(key)
        if data is None:
            raise KeyError(key)
        fileobj.write(data)

    def upload_object_from_fileobj(
        self, config: FakeConfig, key: str, fileobj, content_type: str, org_id: str
    ) -> None:
        config.store.writes += 1
        payload = fileobj.read()
        config.store.objects[key] = self.corrupt_on_write.get(key, payload)

    def download_blob_stream_for_config(self, config: FakeConfig, key: str):
        config.store.reads += 1
        data = config.store.objects.get(key)
        if data is None:
            return None
        return iter([data])


class FakeRow:
    """An EvidenceFile row, with only what the copy touches."""

    def __init__(self, s3_key: str, storage_config_id=None, content_type="application/pdf"):
        self.s3_key = s3_key
        self.storage_config_id = storage_config_id
        self.content_type = content_type
        self.organization_id = ORG_A


class FakeSession:
    """Counts commits and refuses inserts.

    ``add`` raising is ISC 51 in its strongest available form: the copy must
    update the row it found, and a second ``EvidenceFile`` for the copy would
    breach the unique constraint on ``s3_key`` and double every count an
    auditor reads.
    """

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.added: List[Any] = []

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def add(self, obj) -> None:  # pragma: no cover — the assertion is that it is never reached
        self.added.append(obj)
        raise AssertionError("the copy inserted a row instead of updating one")

    def close(self) -> None:
        pass


@pytest.fixture()
def stores():
    payloads = {
        "evidence/a/one.pdf": b"the first evidence file",
        "evidence/a/two.pdf": b"the second evidence file, longer than the first",
    }
    return FakeStore(payloads), FakeStore()


@pytest.fixture()
def wired(monkeypatch, stores):
    """Wire the task onto the fakes and hand back everything a test needs."""
    source_store, target_store = stores
    source = FakeConfig(SOURCE_ID, source_store)
    target = FakeConfig(TARGET_ID, target_store)
    facade = FakeFacade()
    session = FakeSession()

    rows = [
        FakeRow("evidence/a/one.pdf", SOURCE_ID),
        FakeRow("evidence/a/two.pdf", SOURCE_ID),
    ]

    records: List[dict] = []
    monkeypatch.setattr(copy_task, "write_run", lambda record: records.append(dict(record)))
    monkeypatch.setattr(copy_task, "read_run", lambda run_id: None)
    monkeypatch.setattr(copy_task, "clear_active", lambda org_id: None)
    monkeypatch.setattr(copy_task, "_get_sync_session", lambda: session)
    monkeypatch.setattr(copy_task, "_rows_to_copy", lambda s, o, c: rows)

    def fake_resolve_for_file(org_id, config_id):
        return source if str(config_id) == str(SOURCE_ID) else target

    monkeypatch.setattr(storage_config, "resolve_for_file", fake_resolve_for_file)

    import services.storage_service as real_facade

    for name in (
        "head_object",
        "download_object_to_fileobj",
        "upload_object_from_fileobj",
        "download_blob_stream_for_config",
    ):
        monkeypatch.setattr(real_facade, name, getattr(facade, name))

    # Retirement needs a database; it has its own tests below.
    monkeypatch.setattr(copy_task, "_retire_source_if_empty", None)

    return {
        "source": source,
        "target": target,
        "source_store": source_store,
        "target_store": target_store,
        "facade": facade,
        "session": session,
        "rows": rows,
        "records": records,
    }


def run_copy(monkeypatch, retire_result: bool = False) -> dict:
    """Call the task body with retirement stubbed out."""
    import asyncio

    async def _no_retire(*args, **kwargs):
        return retire_result, "" if retire_result else "stubbed"

    monkeypatch.setattr(copy_task, "_retire_source_if_empty", _no_retire)
    return copy_task.copy_evidence_store.run(
        str(ORG_A), str(SOURCE_ID), str(TARGET_ID), "run-1"
    )


# ---------------------------------------------------------------------------
# ISC 47 — every copied object is verified by size and checksum
# ---------------------------------------------------------------------------


def test_a_clean_copy_verifies_size_and_checksum_and_reports_it():
    facade = FakeFacade()
    source = FakeConfig(SOURCE_ID, FakeStore({"k": b"payload bytes"}))
    target = FakeConfig(TARGET_ID, FakeStore())

    outcome = copy_task.copy_one_object(
        facade, source, target, "k", "application/pdf", str(ORG_A)
    )

    assert outcome["ok"] is True
    assert outcome["size"] == len(b"payload bytes")
    assert outcome["sha256"] == hashlib.sha256(b"payload bytes").hexdigest()
    assert target.store.objects["k"] == b"payload bytes"


def test_a_store_that_mangles_the_write_is_caught_by_the_checksum():
    """The size is right and the bytes are wrong — only the digest catches it.

    The corrupted payload is the same length as the original on purpose. An
    implementation that verified size alone, or that compared ETags, would
    report this copy as a success.
    """
    original = b"payload bytes"
    mangled = b"PAYLOAD BYTES"
    assert len(original) == len(mangled)

    facade = FakeFacade(corrupt_on_write={"k": mangled})
    source = FakeConfig(SOURCE_ID, FakeStore({"k": original}))
    target = FakeConfig(TARGET_ID, FakeStore())

    outcome = copy_task.copy_one_object(
        facade, source, target, "k", "application/pdf", str(ORG_A)
    )

    assert outcome["ok"] is False
    assert "checksum" in outcome["reason"]


def test_a_short_write_is_caught_by_the_size_check():
    facade = FakeFacade(corrupt_on_write={"k": b"short"})
    source = FakeConfig(SOURCE_ID, FakeStore({"k": b"payload bytes"}))
    target = FakeConfig(TARGET_ID, FakeStore())

    outcome = copy_task.copy_one_object(
        facade, source, target, "k", "application/pdf", str(ORG_A)
    )

    assert outcome["ok"] is False
    assert "size mismatch" in outcome["reason"]


def test_an_object_missing_from_the_source_is_a_failure_not_a_silent_skip():
    """The row says there are bytes and the store disagrees.

    Reporting success here would bury an integrity finding under a green
    progress bar, and would let the source be retired with a file still
    pointing at it.
    """
    facade = FakeFacade()
    source = FakeConfig(SOURCE_ID, FakeStore())
    target = FakeConfig(TARGET_ID, FakeStore())

    outcome = copy_task.copy_one_object(
        facade, source, target, "gone", "application/pdf", str(ORG_A)
    )

    assert outcome["ok"] is False
    assert "not found in the source" in outcome["reason"]
    assert target.store.objects == {}


def test_a_failure_reason_never_carries_a_bucket_endpoint_or_credential():
    """Reasons name a class of failure, never what the store said back.

    Same rule as the connection probe: a message echoed from the far end turns
    an administrator's screen into a read oracle for the platform's network.
    """

    class Exploding(FakeFacade):
        def head_object(self, config, key):
            raise RuntimeError(
                "https://secret-endpoint.internal/bucket-name?X-Amz-Credential=AKIAEXAMPLE"
            )

    outcome = copy_task.copy_one_object(
        Exploding(),
        FakeConfig(SOURCE_ID, FakeStore({"k": b"x"})),
        FakeConfig(TARGET_ID, FakeStore()),
        "k",
        "application/pdf",
        str(ORG_A),
    )

    assert outcome["ok"] is False
    assert outcome["reason"] == "source unreadable (RuntimeError)"
    for leaked in ("secret-endpoint", "bucket-name", "AKIA"):
        assert leaked not in outcome["reason"]


# ---------------------------------------------------------------------------
# ISC 46 — resumable, and committed per row
# ---------------------------------------------------------------------------


def test_each_copied_row_is_committed_on_its_own(monkeypatch, wired):
    result = run_copy(monkeypatch)

    assert result["copied"] == 2
    assert wired["session"].commits == 2, "one commit per row, not one at the end"
    assert [row.storage_config_id for row in wired["rows"]] == [TARGET_ID, TARGET_ID]


def test_a_re_run_skips_rows_already_in_the_target_and_reads_nothing(monkeypatch, wired):
    first = run_copy(monkeypatch)
    assert first["copied"] == 2

    reads_after_first = wired["source_store"].reads
    writes_after_first = wired["target_store"].writes
    commits_after_first = wired["session"].commits

    second = run_copy(monkeypatch)

    assert second["copied"] == 0
    assert second["skipped"] == 2
    assert wired["source_store"].reads == reads_after_first, "re-read a finished row"
    assert wired["target_store"].writes == writes_after_first, "re-wrote a finished row"
    assert wired["session"].commits == commits_after_first, "committed nothing new"


def test_a_run_interrupted_after_one_row_resumes_from_the_second(monkeypatch, wired):
    """The half-done state is the point: one row moved, one not, both readable.

    Simulated by failing the second file, which is what an interruption leaves
    behind as far as the database is concerned — and then letting the re-run
    find the first row already done.
    """
    facade = wired["facade"]
    original_head = facade.head_object

    def head_but_second_file_is_missing(config, key):
        if key == "evidence/a/two.pdf" and config is wired["source"]:
            return None
        return original_head(config, key)

    monkeypatch.setattr(
        __import__("services.storage_service", fromlist=["x"]),
        "head_object",
        head_but_second_file_is_missing,
    )

    first = run_copy(monkeypatch)
    assert first["copied"] == 1
    assert first["failed"] == 1
    assert wired["rows"][0].storage_config_id == TARGET_ID
    assert wired["rows"][1].storage_config_id == SOURCE_ID

    # The second file comes back; the re-run finishes the job without redoing
    # the first.
    monkeypatch.setattr(
        __import__("services.storage_service", fromlist=["x"]),
        "head_object",
        original_head,
    )
    writes_after_first = wired["target_store"].writes

    second = run_copy(monkeypatch)
    assert second["copied"] == 1
    assert second["skipped"] == 1
    assert second["failed"] == 0
    assert wired["target_store"].writes == writes_after_first + 1


# ---------------------------------------------------------------------------
# ISC 49 — no source object is ever deleted
# ---------------------------------------------------------------------------


def test_no_delete_is_attempted_on_either_store_across_a_whole_run(monkeypatch, wired):
    run_copy(monkeypatch)

    assert wired["source_store"].deletes == 0
    assert wired["target_store"].deletes == 0
    assert set(wired["source_store"].objects) == {
        "evidence/a/one.pdf",
        "evidence/a/two.pdf",
    }, "the source objects must survive the copy untouched"


def test_no_delete_is_attempted_even_when_a_row_fails(monkeypatch, wired):
    """The error path is where a "tidy up the half-written object" delete goes.

    A partially written target object is left where it is deliberately: the row
    still points at the source, so the stray object is harmless, and deleting
    on an error path is one refactor away from deleting on a false alarm.
    """

    def head_but_target_never_has_it(config, key):
        if config is wired["target"]:
            return None
        data = config.store.objects.get(key)
        return None if data is None else {"size": len(data), "etag": "x", "content_type": "x"}

    monkeypatch.setattr(
        __import__("services.storage_service", fromlist=["x"]),
        "head_object",
        head_but_target_never_has_it,
    )

    result = run_copy(monkeypatch)

    assert result["failed"] == 2
    assert result["copied"] == 0
    assert wired["source_store"].deletes == 0
    assert wired["target_store"].deletes == 0


def test_the_task_source_contains_no_delete_call():
    """A source-level backstop for the criterion above.

    The behavioural tests cover every path a run takes today. This one catches
    a delete added to a path they do not reach — a cleanup branch, an
    exception handler — without needing a test for each.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(copy_task))
    # Strip every docstring first: this module's own prose says the words, and
    # a test that a comment can break is a test nobody trusts.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("delete_object", "delete_objects", "remove_object", "delete_blob"):
        assert forbidden not in called, f"the copy task calls {forbidden}"


# ---------------------------------------------------------------------------
# ISC 51 — never a second row
# ---------------------------------------------------------------------------


def test_the_copy_updates_rows_and_never_inserts_one(monkeypatch, wired):
    run_copy(monkeypatch)

    assert wired["session"].added == []
    assert len(wired["rows"]) == 2


def test_a_failed_row_is_left_pointing_at_the_source(monkeypatch, wired):
    """Partial-but-consistent: the file is still readable from where it is."""

    def head_but_target_never_has_it(config, key):
        if config is wired["target"]:
            return None
        data = config.store.objects.get(key)
        return None if data is None else {"size": len(data), "etag": "x", "content_type": "x"}

    monkeypatch.setattr(
        __import__("services.storage_service", fromlist=["x"]),
        "head_object",
        head_but_target_never_has_it,
    )

    run_copy(monkeypatch)

    assert [row.storage_config_id for row in wired["rows"]] == [SOURCE_ID, SOURCE_ID]
    assert wired["session"].commits == 0
    assert wired["session"].rollbacks == 2


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def test_null_rows_are_enumerated_only_while_the_source_is_still_effective(monkeypatch):
    """NULL means "resolve me by organisation", so it points at the source
    exactly while the source is what the organisation resolves to."""
    captured = {}

    class FakeQuery:
        def where(self, *args):
            captured["predicates"] = args
            return self

        def order_by(self, *args):
            return self

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        def execute(self, statement):
            return FakeResult()

    import services.storage_config as sc

    class Resolved:
        def __init__(self, config_id):
            self.config_id = config_id

    # Source IS the effective configuration → NULL rows qualify.
    monkeypatch.setattr(sc, "resolve", lambda org: Resolved(str(SOURCE_ID)))
    from sqlalchemy import or_ as _or

    calls = []
    monkeypatch.setattr(
        copy_task, "or_", lambda *preds: calls.append(preds) or _or(*preds)
    )
    copy_task._rows_to_copy(Session(), ORG_A, str(SOURCE_ID))
    assert len(calls[-1]) == 2, "the NULL clause should be present"

    # Source is NOT the effective configuration → NULL rows do not.
    monkeypatch.setattr(sc, "resolve", lambda org: Resolved(str(TARGET_ID)))
    copy_task._rows_to_copy(Session(), ORG_A, str(SOURCE_ID))
    assert len(calls[-1]) == 1, "the NULL clause should be absent"


# ---------------------------------------------------------------------------
# ISC 48 — reads resolve per row
# ---------------------------------------------------------------------------


def _row(config_id, org_id=None, bucket="from-row"):
    return storage_config.StoredConfigRow(
        config_id=str(config_id),
        organization_id=str(org_id) if org_id else None,
        provider=storage_config.PROVIDER_S3_COMPATIBLE,
        bucket=bucket,
        region="eu-west-1",
        endpoint_url="https://store.example.com",
        public_endpoint="",
        path_style=True,
        sse_mode="none",
        access_key_id="k",
        secret_access_key="s",
        key_version="1",
    )


def test_a_stamped_row_reads_from_its_own_store_not_the_organisations():
    """The file's configuration wins over the organisation's current one.

    This is what keeps a file readable mid-copy: the organisation has already
    switched to the target, and this file's bytes are still in the source.
    """
    resolver = storage_config.StorageConfigResolver(
        loader=lambda: [_row(TARGET_ID, ORG_A, bucket="the-new-store")]
    )
    resolver._by_id_loader = lambda cid: _row(SOURCE_ID, ORG_A, bucket="the-old-store")

    by_org = resolver.resolve(str(ORG_A))
    by_file = resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID))

    assert by_org.bucket == "the-new-store"
    assert by_file.bucket == "the-old-store"
    assert by_file.config_id == str(SOURCE_ID)


def test_a_moved_row_reads_from_the_target():
    resolver = storage_config.StorageConfigResolver(
        loader=lambda: [_row(TARGET_ID, ORG_A, bucket="the-new-store")]
    )
    resolver._by_id_loader = lambda cid: _row(TARGET_ID, ORG_A, bucket="the-new-store")

    moved = resolver.resolve_for_file(str(ORG_A), str(TARGET_ID))
    assert moved.bucket == "the-new-store"


def test_a_retired_configuration_still_resolves_for_a_file_that_names_it():
    """`load_active_rows` filters to active; `load_row_by_id` must not.

    Retire is not delete. Evidence written under a retired configuration stays
    readable, which is the whole reason retiring and deleting are separate
    operations.
    """
    resolver = storage_config.StorageConfigResolver(loader=lambda: [])
    resolver._by_id_loader = lambda cid: _row(SOURCE_ID, ORG_A, bucket="retired-store")

    resolved = resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID))
    assert resolved.bucket == "retired-store"


def test_an_unstamped_row_resolves_by_organisation_exactly_as_before():
    resolver = storage_config.StorageConfigResolver(
        loader=lambda: [_row(TARGET_ID, ORG_A, bucket="org-store")]
    )
    resolver._by_id_loader = lambda cid: pytest.fail("NULL must not hit the by-id load")

    assert resolver.resolve_for_file(str(ORG_A), None).bucket == "org-store"


def test_a_dangling_configuration_id_falls_back_rather_than_refusing_the_read():
    resolver = storage_config.StorageConfigResolver(
        loader=lambda: [_row(TARGET_ID, ORG_A, bucket="org-store")]
    )
    resolver._by_id_loader = lambda cid: None

    assert resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID)).bucket == "org-store"


def test_the_by_id_cache_is_dropped_on_invalidate():
    """A rotation must reach a file's own configuration, not only the org's."""
    calls = []

    def loader(cid):
        calls.append(cid)
        return _row(SOURCE_ID, ORG_A)

    resolver = storage_config.StorageConfigResolver(loader=lambda: [])
    resolver._by_id_loader = loader

    resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID))
    resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID))
    assert len(calls) == 1, "the second read should have been cached"

    resolver.invalidate()
    resolver.resolve_for_file(str(ORG_A), str(SOURCE_ID))
    assert len(calls) == 2, "invalidate must drop the by-id cache too"


def test_the_download_paths_pass_the_files_own_configuration():
    """The read paths must thread the row's store through, not the org's.

    Source-level, and deliberately so: these call sites are the difference
    between ISC 48 holding and a file becoming unreadable the moment its
    organisation switches store, and neither has a return value a unit test
    could inspect without a live store.
    """
    import inspect

    from api import evidence_files

    download = inspect.getsource(evidence_files.download_evidence_file)
    assert "storage_config_id=" in download
    assert "evidence_file.storage_config_id" in download


# ---------------------------------------------------------------------------
# ISC 50 — a referenced configuration cannot be deleted
# ---------------------------------------------------------------------------


def test_delete_is_refused_with_a_count_while_files_reference_the_config():
    import asyncio

    from services import evidence_storage_admin

    class Row:
        id = SOURCE_ID
        status = storage_config.STATUS_RETIRED

    async def exercise():
        async def fake_count(session, config_id):
            return 7

        original = evidence_storage_admin.files_referencing
        evidence_storage_admin.files_referencing = fake_count
        try:
            with pytest.raises(evidence_storage_admin.StorageConfigInUse) as excinfo:
                await evidence_storage_admin.delete_config(None, Row())
            return excinfo.value
        finally:
            evidence_storage_admin.files_referencing = original

    error = asyncio.run(exercise())
    assert error.file_count == 7
    assert "7 evidence file(s)" in str(error)


def test_the_reference_count_follows_the_rows_not_the_organisation():
    """After a copy the count on the source drops and the target's rises.

    `files_referencing` counts by `storage_config_id`, and the copy moves that
    column, so this holds by construction — this test pins the construction.
    """
    import inspect

    from services import evidence_storage_admin

    source = inspect.getsource(evidence_storage_admin.files_referencing)
    assert "EvidenceFile.storage_config_id == config_id" in source


# ---------------------------------------------------------------------------
# The route contract
# ---------------------------------------------------------------------------


def test_the_copy_run_response_names_no_credential_field():
    """The response model is the boundary, as it is for the config response."""
    from api.evidence_storage import EvidenceStorageCopyRunResponse

    fields = set(EvidenceStorageCopyRunResponse.model_fields)
    assert fields == {
        "run_id",
        "organization_id",
        "source_config_id",
        "target_config_id",
        "status",
        "total",
        "copied",
        "failed",
        "skipped",
        "remaining",
        "failures",
        "source_retired",
        "source_retired_reason",
        "message",
        "started_at",
        "finished_at",
        "updated_at",
    }
    for forbidden in ("secret", "access_key", "password", "credential", "token"):
        assert not any(forbidden in name for name in fields)


def test_copy_run_routes_are_declared_before_the_config_id_routes():
    """FastAPI matches in declaration order.

    Declared after `/{config_id}`, `GET .../copy-runs` would be captured by it
    and answered with a 422 for a config id that is not a UUID — which is
    exactly what happened to `/effective` in Phase 3 before it was moved.
    """
    from api.evidence_storage import router

    paths = [getattr(route, "path", "") for route in router.routes]
    copy_runs = min(i for i, p in enumerate(paths) if p.endswith("/copy-runs"))
    by_id = min(
        i for i, p in enumerate(paths) if p.endswith("/evidence-storage/{config_id}")
    )
    assert copy_runs < by_id


def test_every_copy_route_requires_an_organisation_admin():
    """Same gate as every other route on this surface (D14).

    Read off the ROUTER, not off a hand-written list: a route added to this
    surface without the gate is exactly the thing worth catching, and a list
    of names cannot catch what nobody remembered to add to it. `copy-sources`
    (D51) was missing from the old list (D53).
    """
    from api import evidence_storage
    from auth import require_org_role

    admin_gate = require_org_role("admin")
    gate_name = getattr(admin_gate, "__name__", "")
    assert gate_name, "the role gate must be a named callable to be recognisable"

    copy_paths = [
        route
        for route in evidence_storage.router.routes
        if "/copy" in getattr(route, "path", "")
    ]
    # The four the surface has today: the trigger, the run list, one run, and
    # the source list. A fifth arriving without a gate fails below, not here.
    assert len(copy_paths) >= 4, [r.path for r in copy_paths]

    for route in copy_paths:
        gates = [
            dep.call
            for dep in route.dependant.dependencies
            if getattr(dep.call, "__name__", "") == gate_name
        ]
        assert gates, f"{route.path} has no organisation-role gate"
        # And it is the ADMIN gate, not "editor" or "viewer". The closure's
        # cell holds the role it was built for.
        roles = {
            cell.cell_contents
            for gate in gates
            for cell in (gate.__closure__ or ())
            if isinstance(cell.cell_contents, str)
        }
        assert "admin" in roles, (route.path, roles)


def test_the_trigger_is_rate_limited_and_the_reads_are():
    import inspect

    from api import evidence_storage

    trigger = inspect.getsource(evidence_storage).split(
        "async def copy_evidence_storage"
    )[0]
    assert "@rate_limit_write" in trigger.rsplit("@router.post", 1)[-1]


def test_a_run_belonging_to_another_organisation_is_not_readable(monkeypatch):
    """404, with the same body as a run id that does not exist.

    The scope check is on the record's own organisation id rather than on the
    path, because the run id is a UUID this server minted and trusting the URL
    to scope it would let any tenant's admin read any run.
    """
    import asyncio

    from fastapi import HTTPException

    from api import evidence_storage

    record = copy_task.new_run_record("r1", str(ORG_B), str(SOURCE_ID), str(TARGET_ID))
    monkeypatch.setattr(copy_task, "read_run", lambda rid: record)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            evidence_storage.get_evidence_storage_copy_run.__wrapped__(
                request=None, response=None, org_id=ORG_A, run_id="r1", membership=None
            )
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Copy run not found"


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


def test_the_run_reports_source_retired_only_when_the_task_says_so(monkeypatch, wired):
    not_retired = run_copy(monkeypatch, retire_result=False)
    assert not_retired["source_retired"] is False

    for row in wired["rows"]:
        row.storage_config_id = SOURCE_ID
    retired = run_copy(monkeypatch, retire_result=True)
    assert retired["source_retired"] is True


def test_retirement_is_not_attempted_when_any_row_failed(monkeypatch, wired):
    """A source still holding somebody's bytes stays active.

    Retiring it would be honest about the configuration and dishonest about the
    files: the row would leave the resolver's active set while files still
    resolve to it by id.
    """
    attempts = []

    async def record_attempt(*args, **kwargs):
        attempts.append(args)
        return True, ""

    monkeypatch.setattr(copy_task, "_retire_source_if_empty", record_attempt)

    def head_but_target_never_has_it(config, key):
        if config is wired["target"]:
            return None
        data = config.store.objects.get(key)
        return None if data is None else {"size": len(data), "etag": "x", "content_type": "x"}

    monkeypatch.setattr(
        __import__("services.storage_service", fromlist=["x"]),
        "head_object",
        head_but_target_never_has_it,
    )

    result = copy_task.copy_evidence_store.run(
        str(ORG_A), str(SOURCE_ID), str(TARGET_ID), "run-x"
    )
    assert result["failed"] == 2
    assert attempts == [], "retirement must not be attempted while a row failed"


def test_retirement_goes_through_the_admin_service_not_a_raw_update():
    """So the audit row, the version bump and the invalidation all happen."""
    import inspect

    source = inspect.getsource(copy_task._retire_source_if_empty)
    assert "evidence_storage_admin.retire_config" in source
    # No hand-rolled status write: an UPDATE here would skip the audit row, the
    # version bump and the cache invalidation that retire_config performs.
    assert "update(" not in source
    # An assignment to a status, not a comparison against one: `status ==` is
    # how the helper decides whether a retirement is needed at all, and a
    # substring check for "status =" would flag it.
    assert re.search(r"status\s*=(?!=)", source) is None


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_counts_reach_the_run_record_as_the_run_proceeds(monkeypatch, wired):
    run_copy(monkeypatch)

    records = wired["records"]
    assert records[0]["status"] == copy_task.STATE_RUNNING
    assert records[-1]["status"] == copy_task.STATE_COMPLETED
    assert records[-1]["copied"] == 2
    assert records[-1]["remaining"] == 0
    # Progress is written per row, not once at the end: the record passes
    # through every intermediate count rather than jumping from 0 to 2.
    counts = [r["copied"] for r in records]
    assert counts == sorted(counts), "progress went backwards"
    assert 1 in counts, "the record never showed the run part-way through"
    assert counts.count(1) >= 1 and counts[0] == 0 and counts[-1] == 2


def test_a_failed_run_records_the_exception_type_and_not_its_message(monkeypatch, wired):
    def explode(org, cid):
        raise RuntimeError("https://internal.example/bucket?X-Amz-Credential=AKIA123")

    monkeypatch.setattr(storage_config, "resolve_for_file", explode)

    with pytest.raises(RuntimeError):
        run_copy(monkeypatch)

    final = wired["records"][-1]
    assert final["status"] == copy_task.STATE_FAILED
    assert final["message"] == "The copy stopped: RuntimeError."
    assert "AKIA" not in final["message"]
    assert "internal.example" not in final["message"]


# ---------------------------------------------------------------------------
# ISC 50 — "the source is finished with" is a reference count, not a status
# ---------------------------------------------------------------------------


class _RetireRow:
    def __init__(self, status, organization_id=ORG_A, is_bundled=False):
        self.id = SOURCE_ID
        self.status = status
        self.organization_id = organization_id
        self.is_bundled = is_bundled


def _wire_retirement(monkeypatch, row, referencing: int, org_referencing: int = 0):
    """Stand the async retirement helper up over fakes.

    The helper imports its dependencies inside the function, so the patches
    have to land on the modules it will import, not on the task module.
    """

    import types

    retired = []

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return row

        def scalar_one(self):
            # The per-organisation reference count the platform-source branch
            # asks for. Distinct from `files_referencing`, which counts every
            # tenant's rows.
            return org_referencing

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    admin = types.SimpleNamespace(
        files_referencing=_async_return(referencing),
        retire_config=_record_retire(retired),
        Actor=lambda label=None: label,
    )

    import services.evidence_storage_admin as real_admin

    monkeypatch.setattr(real_admin, "files_referencing", admin.files_referencing)
    monkeypatch.setattr(real_admin, "retire_config", admin.retire_config)

    import database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session())

    async def _dispose():
        return None

    monkeypatch.setattr(
        database, "engine", types.SimpleNamespace(dispose=_dispose), raising=False
    )
    return retired


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def _record_retire(sink):
    async def _inner(session, row, actor=None):
        sink.append(row)
        row.status = storage_config.STATUS_RETIRED
        return row

    return _inner


def test_a_source_already_retired_with_no_files_left_reports_finished(monkeypatch):
    """The ordinary flow: activation retired the source before the copy ran.

    Activating a new store retires the outgoing one in the same transaction,
    so by the time a copy runs the source is normally already retired. If the
    answer here were "did this run retire it", every operator in the ordinary
    flow would be told the store they had just emptied was still in service —
    and the screen's one destructive-adjacent claim would be wrong in the
    common case rather than the rare one.
    """
    import asyncio

    row = _RetireRow(storage_config.STATUS_RETIRED)
    retired = _wire_retirement(monkeypatch, row, referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is True
    assert reason == ""
    # Nothing to do: it was already out of service.
    assert retired == []


def test_an_active_source_with_no_files_left_is_retired_and_reports_finished(monkeypatch):
    import asyncio

    row = _RetireRow(storage_config.STATUS_ACTIVE)
    retired = _wire_retirement(monkeypatch, row, referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is True
    assert reason == ""
    assert retired == [row], "an active source with nothing in it must be retired"


@pytest.mark.parametrize(
    "status", [storage_config.STATUS_ACTIVE, storage_config.STATUS_RETIRED]
)
def test_a_source_with_files_left_is_never_reported_finished(monkeypatch, status):
    """Whatever its status. A retired store with a file still in it is not
    done with, and saying it is invites an operator to empty it."""
    import asyncio

    row = _RetireRow(status)
    retired = _wire_retirement(monkeypatch, row, referencing=1)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is False
    assert "still reference it" in reason
    assert retired == []


# ---------------------------------------------------------------------------
# Repair, finding 3 — the bundled-to-own-store migration must be triggerable
#
# The sequence the whole feature exists for: install bundled, write evidence to
# the platform store, then bring your own store. Activation stamps those files
# with the PLATFORM row, because that is genuinely where the bytes are. If the
# copy route only accepts org-scoped rows, that stamp is a one-way door and the
# evidence is stranded. These tests hold the door open — and hold it open
# narrowly, because the same row is every other tenant's store too.
# ---------------------------------------------------------------------------

PLATFORM_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


class _ConfigRow:
    """Just enough of `EvidenceStorageConfig` for the resolver under test."""

    def __init__(self, config_id, organization_id, status=None, is_bundled=False):
        self.id = config_id
        self.organization_id = organization_id
        self.status = status or storage_config.STATUS_ACTIVE
        self.is_bundled = is_bundled
        self.provider = storage_config.PROVIDER_S3_COMPATIBLE
        self.bucket = "evidence"
        self.endpoint_url = "https://store.example.com"


def _source_row_db(row, org_file_count: int = 0):
    """A db seam for `_source_row`: one row, and one count."""

    class Result:
        def scalar_one_or_none(self):
            return row

        def scalar_one(self):
            return org_file_count

    class Db:
        async def execute(self, *_a, **_k):
            return Result()

    return Db()


def _call_source_row(org_id, config_id, db):
    import asyncio

    from api import evidence_storage as api_mod

    return asyncio.run(api_mod._source_row(org_id, config_id, db))


def test_the_copy_accepts_the_platform_row_as_a_source_once_files_are_stamped_to_it():
    """The state activation leaves behind, and the whole point of the repair.

    Install bundled, write evidence, bring your own store: activation stamps
    those files with the PLATFORM row, because that is genuinely where the
    bytes are. The stamp IS the organisation's claim on reading out of that
    row, and without this the files are stranded — the route answered 404 for
    the one source that mattered.
    """
    row = _ConfigRow(PLATFORM_ID, None)
    got = _call_source_row(ORG_A, PLATFORM_ID, _source_row_db(row, org_file_count=7))
    assert got is row


def test_the_platform_row_is_not_a_source_for_an_organisation_with_nothing_in_it():
    """404, not 403, and not a free read of a shared row.

    The stamp is the only claim. "This organisation resolves to the platform
    row" is not a second one: every organisation without a configuration of its
    own resolves there, so that test admits every tenant and scopes nothing.
    404 rather than 403 for the anti-enumeration reason in `_not_found` (D34).
    """
    from fastapi import HTTPException

    row = _ConfigRow(PLATFORM_ID, None)
    with pytest.raises(HTTPException) as exc:
        _call_source_row(ORG_A, PLATFORM_ID, _source_row_db(row, org_file_count=0))
    assert exc.value.status_code == 404


def test_the_source_resolver_does_not_admit_every_tenant_through_resolution(monkeypatch):
    """The mutation this guards against, asserted by BEHAVIOUR (D53).

    This was an `inspect.getsource` substring match asserting that
    `storage_config.resolve` does not appear in `_source_row`. It catches a
    deletion and misses a rewrite: `import services.storage_config as sc`
    followed by `sc.resolve(...)` re-admits every tenant on this installation
    to the shared platform store with all 54 tests still green.

    So the resolver itself is replaced, on the MODULE, which is the one object
    every spelling of the import shares. It is replaced with something that
    returns exactly what a resolve-based clause would want — this organisation
    resolving to the platform row — so a reintroduced clause finds its answer
    and hands out the row, and the assertion below fails.
    """
    from fastapi import HTTPException

    from services import storage_config as sc

    calls = []

    def _resolves_to_the_platform_row(org_id=None):
        calls.append(org_id)
        return sc.ResolvedStorageConfig(
            config_id=str(PLATFORM_ID),
            source=sc.SOURCE_PLATFORM,
            provider=sc.PROVIDER_MINIO,
            bucket="evidence",
        )

    monkeypatch.setattr(sc, "resolve", _resolves_to_the_platform_row)
    monkeypatch.setattr(
        sc, "resolve_platform", lambda: _resolves_to_the_platform_row(None)
    )

    row = _ConfigRow(PLATFORM_ID, None)
    with pytest.raises(HTTPException) as exc:
        _call_source_row(ORG_A, PLATFORM_ID, _source_row_db(row, org_file_count=0))
    assert exc.value.status_code == 404
    # Belt and braces: it did not merely ignore the answer, it never asked.
    assert calls == []


def test_another_tenants_row_is_never_a_source():
    from fastapi import HTTPException

    row = _ConfigRow(SOURCE_ID, ORG_B)
    with pytest.raises(HTTPException) as exc:
        _call_source_row(ORG_A, SOURCE_ID, _source_row_db(row))
    assert exc.value.status_code == 404


def test_the_target_still_goes_through_the_strictly_org_scoped_resolver():
    """Source-only, and the route says so in one line of source.

    A platform row accepted as a TARGET would let one organisation's copy write
    into every other tenant's store. `_writable_row` refuses it (404), and the
    route must keep using `_writable_row` for the target — this asserts the
    wiring, because the refusal is only reachable through it.
    """
    import inspect

    from api import evidence_storage as api_mod

    source = inspect.getsource(api_mod.copy_evidence_storage)
    assert "source = await _source_row(org_id, config_id, db)" in source
    assert "target = await _writable_row(org_id, target_config_id, db)" in source
    # And the strict resolver really does refuse a platform row.
    assert "row.organization_id != org_id" in inspect.getsource(api_mod._writable_row)


def test_the_enumeration_is_scoped_to_this_organisation():
    """Two tenants stamped to the same platform row; only one moves.

    Without the organisation predicate, a copy out of the shared platform store
    would re-point every other tenant's evidence at this organisation's bucket
    — and the objects would not be there.
    """
    import inspect

    source = inspect.getsource(copy_task._rows_to_copy)
    assert "EvidenceFile.organization_id == org_id" in source

    captured = {}

    class FakeQuery:
        pass

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        def execute(self, statement):
            captured["sql"] = str(statement)
            return FakeResult()

    import services.storage_config as sc

    sc_resolve = sc.resolve
    sc.resolve = lambda org: type("R", (), {"config_id": str(PLATFORM_ID)})()
    try:
        copy_task._rows_to_copy(Session(), ORG_A, str(PLATFORM_ID))
    finally:
        sc.resolve = sc_resolve

    sql = captured["sql"]
    assert "organization_id" in sql, "the query must be scoped to one organisation"


def test_the_retirement_guard_never_retires_the_platform_store(monkeypatch):
    """The destructive one.

    The platform row is the bundled store for every tenant on the
    installation. Retiring it at the end of one organisation's copy would take
    evidence storage out of service for all of them — and `files_referencing`
    counts across tenants, so a zero is not even reachable here in practice.
    The guard runs before the count, and the run says why.
    """
    import asyncio

    row = _RetireRow(storage_config.STATUS_ACTIVE, organization_id=None)
    retired = _wire_retirement(monkeypatch, row, referencing=0, org_referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is False
    assert reason == copy_task.PLATFORM_SOURCE_REASON
    assert retired == [], "the platform store must never be retired by a copy"


def test_a_platform_source_reports_finished_on_this_organisations_rows_only(monkeypatch):
    """"Finished with" is asked of this organisation's files, not every tenant's.

    Another tenant's files keeping the reason in "still in use" would tell this
    operator their copy had not finished, for a reason they cannot act on.
    """
    import asyncio

    row = _RetireRow(storage_config.STATUS_ACTIVE, organization_id=None)
    _wire_retirement(monkeypatch, row, referencing=9999, org_referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is False
    # Not the cross-tenant count: this organisation is done.
    assert reason == copy_task.PLATFORM_SOURCE_REASON

    row = _RetireRow(storage_config.STATUS_ACTIVE, organization_id=None)
    _wire_retirement(monkeypatch, row, referencing=0, org_referencing=3)
    _, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert "3" in reason and "left in service" in reason


def test_the_retirement_guard_never_retires_another_organisations_row(monkeypatch):
    import asyncio

    row = _RetireRow(storage_config.STATUS_ACTIVE, organization_id=ORG_B)
    retired = _wire_retirement(monkeypatch, row, referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is False
    assert "another organisation" in reason
    assert retired == []


def test_the_retirement_guard_never_retires_a_bundled_row(monkeypatch):
    """A row the installer marked bundled is the installation's, not the
    organisation's, whatever scope it carries."""
    import asyncio

    row = _RetireRow(storage_config.STATUS_ACTIVE, organization_id=ORG_A, is_bundled=True)
    retired = _wire_retirement(monkeypatch, row, referencing=0)

    retired_flag, reason = asyncio.run(
        copy_task._retire_source_if_empty(str(SOURCE_ID), "run-1", str(ORG_A))
    )
    assert retired_flag is False
    assert reason == copy_task.BUNDLED_SOURCE_REASON
    assert retired == []


def test_the_copy_sources_route_is_declared_before_the_config_id_route():
    from api.evidence_storage import router

    paths = [getattr(route, "path", "") for route in router.routes]
    sources = min(i for i, p in enumerate(paths) if p.endswith("/copy-sources"))
    by_id = min(
        i for i, p in enumerate(paths) if p.endswith("/evidence-storage/{config_id}")
    )
    assert sources < by_id


def test_the_copy_source_response_names_no_credential_field():
    from api.evidence_storage import EvidenceStorageCopySourceResponse

    fields = set(EvidenceStorageCopySourceResponse.model_fields)
    assert fields == {
        "config_id",
        "scope",
        "provider",
        "provider_label",
        "bucket",
        "endpoint_url",
        "status",
        "file_count",
    }
    for forbidden in ("secret", "access_key", "password", "credential", "token"):
        assert not any(forbidden in name for name in fields)


# ---------------------------------------------------------------------------
# Repair, findings 1 and 2 — the last two reads that did not resolve per row
# ---------------------------------------------------------------------------


def _facade_capture(monkeypatch, name):
    """Record the config the facade hands the S3 driver for one call."""
    from services import storage_service as facade

    seen = {}

    def _fake(*args, **kwargs):
        seen["config"] = kwargs.get("config")
        return True if name == "check_object_exists" else "quarantine/k"

    import services.s3_service as s3

    monkeypatch.setattr(s3, name, _fake)
    monkeypatch.setattr(facade, "_detect_backend", lambda: facade.BACKEND_S3)
    return seen


def test_evidence_validation_checks_the_store_the_file_is_actually_in(monkeypatch):
    """Finding 1. Resolving to the platform store unconditionally reported
    every file of every organisation on its own store as missing — from its
    first upload, not merely after a switch."""
    from services import storage_service as facade

    seen = _facade_capture(monkeypatch, "check_object_exists")
    monkeypatch.setattr(
        facade,
        "resolve_config_for_file",
        lambda org, cfg: f"per-row:{org}:{cfg}",
    )
    monkeypatch.setattr(
        storage_config, "resolve_platform", lambda: "PLATFORM", raising=False
    )

    facade.check_object_exists("k", str(ORG_A), str(SOURCE_ID))
    assert seen["config"] == f"per-row:{ORG_A}:{SOURCE_ID}"

    # And a platform-scope caller (the catalogue workbook) keeps its old answer.
    facade.check_object_exists("k")
    assert seen["config"] == "PLATFORM"


class _StampedFile:
    """The four attributes the per-row read callers touch.

    A real `EvidenceFile` would need a mapper, a session and a row; these tests
    are about which arguments reach the facade, and a namespace is enough to
    make that visible.
    """

    def __init__(self, s3_key, organization_id, storage_config_id, scan_status="clean"):
        self.id = uuid.uuid4()
        self.s3_key = s3_key
        self.organization_id = organization_id
        self.storage_config_id = storage_config_id
        self.scan_status = scan_status


def test_the_validation_rule_passes_the_files_organisation_and_configuration(monkeypatch):
    """Finding 2's caller, asserted by BEHAVIOUR (D53).

    This was a substring match on the module source. It catches a deletion and
    misses a rewrite: wrapping the per-row argument in `if False and ...` keeps
    every matched string in place and regresses the finding with 155 tests
    green. What matters is what arrives at the facade, so that is what is read.
    """
    import asyncio

    from services import storage_service as facade, validation_service

    seen = {}

    def _check(key, org_id=None, storage_config_id=None):
        seen["args"] = (key, org_id, storage_config_id)
        return True

    monkeypatch.setattr(facade, "check_object_exists", _check)
    monkeypatch.setattr(facade, "is_configured", lambda: True)

    evidence_file = _StampedFile(
        s3_key="org/EV-1/f.pdf",
        organization_id=ORG_A,
        storage_config_id=SOURCE_ID,
    )
    result = asyncio.run(validation_service._rule_s3_object_exists(evidence_file))

    assert result["level"] == "valid"
    assert seen["args"] == ("org/EV-1/f.pdf", str(ORG_A), str(SOURCE_ID))


def test_the_validation_rule_sends_no_configuration_for_an_unstamped_file(monkeypatch):
    """A row written before Phase 6 has no stamp, and must not acquire one by
    accident: `None` means "resolve for this organisation", which is the right
    answer for a file whose bytes really are in that organisation's store."""
    import asyncio

    from services import storage_service as facade, validation_service

    seen = {}
    monkeypatch.setattr(
        facade,
        "check_object_exists",
        lambda key, org_id=None, storage_config_id=None: (
            seen.update(args=(key, org_id, storage_config_id)) or True
        ),
    )
    monkeypatch.setattr(facade, "is_configured", lambda: True)

    asyncio.run(
        validation_service._rule_s3_object_exists(
            _StampedFile(s3_key="k", organization_id=ORG_A, storage_config_id=None)
        )
    )
    assert seen["args"] == ("k", str(ORG_A), None)


def test_quarantine_moves_the_object_in_the_store_it_is_actually_in(monkeypatch):
    """Finding 2. An infected object whose bytes are in a store the
    organisation has stopped writing to has to be quarantined THERE. Resolving
    by organisation moved nothing, and the failure showed up only as an audit
    row with the key unchanged."""
    from services import storage_service as facade

    seen = _facade_capture(monkeypatch, "move_to_quarantine")
    monkeypatch.setattr(
        facade, "resolve_config_for_file", lambda org, cfg: f"per-row:{org}:{cfg}"
    )
    monkeypatch.setattr(facade, "resolve_config", lambda org: f"by-org:{org}")

    facade.move_to_quarantine("k", str(ORG_A), str(SOURCE_ID))
    assert seen["config"] == f"per-row:{ORG_A}:{SOURCE_ID}"


def test_the_quarantine_caller_passes_the_files_configuration(monkeypatch):
    """Same rewrite-blind guard, same fix (D53).

    An infected object whose bytes are in a store the organisation has stopped
    writing to has to be quarantined THERE. Resolving by organisation moved
    nothing, and the failure showed up only as an audit row with the key
    unchanged — which is why a source-text assertion was never enough.
    """
    from services import evidence_quarantine

    seen = {}

    def _move(key, org_id, storage_config_id=None):
        seen["args"] = (key, org_id, storage_config_id)
        return "quarantine/" + key

    monkeypatch.setattr(
        "services.storage_service.move_to_quarantine", _move
    )
    monkeypatch.setattr(
        evidence_quarantine, "write_system_audit_row", lambda *a, **k: None
    )

    evidence_file = _StampedFile(
        s3_key="org/EV-1/f.pdf",
        organization_id=ORG_A,
        storage_config_id=SOURCE_ID,
    )
    evidence_quarantine.quarantine_evidence_file(object(), evidence_file)

    assert seen["args"] == ("org/EV-1/f.pdf", str(ORG_A), str(SOURCE_ID))
    assert evidence_file.s3_key == "quarantine/org/EV-1/f.pdf"


def test_the_azure_branches_refuse_a_per_row_argument_rather_than_ignoring_it():
    """Non-blocking carry-forward, made loud.

    `_detect_backend` checks one environment variable first, so these branches
    are reachable — and the Azure driver takes no configuration, so every
    per-row call silently read the one account in the environment. A wrong
    answer with no error is worse than a refusal.
    """
    import inspect

    from services import storage_service as facade

    for fn in (
        facade.generate_download_url,
        facade.download_blob_stream,
        facade.check_object_exists,
        facade.move_to_quarantine,
    ):
        source = inspect.getsource(fn)
        assert "_refuse_azure_per_row()" in source, fn.__name__
