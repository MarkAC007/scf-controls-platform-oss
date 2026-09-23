"""services/framework_registry.py — read it, heal it, and refuse to lie.

The production failure this module exists for: 2026.2 was applied by a build
that never wrote ``catalog_framework_registries``. The live catalogue therefore
carried no publisher focal-document identifiers, the DECLARED succession tiers
could never fire, and staging 2026.3 blocked on 73 "unexplained" framework
removals that were nothing of the kind. The only documented remedy was a CLI
command against the one workbook version it would accept.

The platform was never short of information: the applied run's own workbook is
still in object storage. ``ensure_live_framework_registry`` re-reads the
registry out of it at stage time and writes the row.

Two properties matter more than the happy path, and both are tested here:

* recovery NEVER raises — a blocked recovery must degrade into a sanity-check
  failure an operator can read, not a 500 that takes the upgrade with it;
* recovery NEVER writes a row it cannot vouch for — a registry read from a
  different release describes different rows, and the next diff would trust it.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import Select

from catalog_models import CatalogFrameworkRegistry
from models import CatalogImportRun
from services import framework_registry as fr

# CI runs pytest from the repo root, where backend/pytest.ini's asyncio_mode=auto
# is not read; mark explicitly like the sibling catalog suites.
pytestmark = pytest.mark.asyncio

# pytest.ini sets asyncio_mode = auto, so async tests need no marker.


V_LIVE = "2026.2"
V_NEXT = "2026.3"
T0 = datetime(2026, 1, 1)

REGISTRY_WITH_IDS = {
    "us_ca_ccpa_2025": {
        "name": "California CCPA (2025)",
        "focal_document_id": "usa-state-ca-ccpa-2025",
    },
    "general_iso_27002_2022": {
        "name": "ISO 27002:2022",
        "focal_document_id": "general-iso-27002-2022",
    },
}

REGISTRY_WITHOUT_IDS = {
    "us_ca_ccpa_2025": {"name": "California CCPA (2025)", "focal_document_id": None},
}


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


def _bound_status(stmt):
    """The literal bound to a ``status ==`` predicate, or None if there is none."""
    if stmt.whereclause is None:
        return None
    for key, value in stmt.compile().params.items():
        if key == "status" or key.startswith("status_"):
            return value
    return None


class FakeSession:
    """Table-keyed selects, real add/commit bookkeeping.

    Entity SELECTs return the whole table; the module under test re-applies
    every predicate in Python precisely so a fake like this cannot hand it a row
    for the wrong version and have it believed.
    """

    def __init__(self, *, registries=(), runs=(), live_version=V_LIVE):
        self.tables = {
            CatalogFrameworkRegistry: list(registries),
            CatalogImportRun: list(runs),
        }
        self.live_version = live_version
        self.commits = 0
        self.added = []

    def add(self, obj):
        self.added.append(obj)
        for model, rows in self.tables.items():
            if isinstance(obj, model):
                rows.append(obj)
                return
        raise AssertionError(f"add() of unknown row type: {type(obj)}")

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt, params=None):
        assert isinstance(stmt, Select), stmt
        entity = stmt.column_descriptions[0]["entity"]
        if entity is CatalogImportRun:
            # get_current_catalog_version (services.catalog_apply) filters
            # status == 'applied' and takes the latest by completed_at IN SQL,
            # not in Python. Emulated here so this fake cannot make a staging
            # run for the next release look like the live catalogue — the fake
            # equivalent of the bug under test.
            rows = [
                r for r in self.tables[entity]
                if _bound_status(stmt) in (None, getattr(r, "status", None))
            ]
            rows.sort(
                key=lambda r: (
                    getattr(r, "completed_at", None) is not None,
                    getattr(r, "completed_at", None) or T0,
                ),
                reverse=True,
            )
            return _FakeResult(rows)
        if entity in self.tables:
            return _FakeResult(self.tables[entity])
        # max(scf_catalog_controls.catalog_version) — the live-version bootstrap.
        return _FakeResult([self.live_version] if self.live_version else [])


def _registry_row(version=V_LIVE, registry=None, source="apply"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        catalog_version=version,
        registry=registry if registry is not None else REGISTRY_WITH_IDS,
        source=source,
        created_at=T0,
        updated_at=T0,
    )


def _applied_run(
    version=V_LIVE,
    key="catalog/runs/abc/scf.xlsx",
    status="applied",
    completed_at=T0,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        to_version=version,
        workbook_object_key=key,
        created_at=T0,
        completed_at=completed_at,
    )


class _StubExtractor:
    def __init__(self, version, registry):
        self.version = version
        self.registry = registry
        self.calls = []

    def extract_framework_registry_only(self, path):
        self.calls.append(path)
        return self.version, self.registry


@pytest.fixture
def stub_recovery(monkeypatch):
    """Point the module's workbook download and extractor at fakes.

    Returns a setter so each test states the workbook it is recovering from.
    """
    state = {}

    def _download(object_key):
        state["downloaded"] = object_key
        if state.get("download_error"):
            raise state["download_error"]
        return state["path"]

    monkeypatch.setattr(fr, "_download_workbook_to_temp", _download)

    def configure(*, version=V_LIVE, registry=None, path="/tmp/recovered.xlsx",
                  download_error=None, extract_error=None):
        state["path"] = path
        state["download_error"] = download_error
        extractor = _StubExtractor(
            version, REGISTRY_WITH_IDS if registry is None else registry
        )
        if extract_error is not None:
            def _boom(_path):
                raise extract_error
            extractor.extract_framework_registry_only = _boom
        monkeypatch.setattr(fr, "_load_extractor", lambda: extractor)
        state["extractor"] = extractor
        return state

    configure()
    return configure


# ---------------------------------------------------------------------------
# ensure_live_framework_registry
# ---------------------------------------------------------------------------


async def test_a_stored_row_is_returned_without_touching_object_storage(stub_recovery):
    """The overwhelmingly common case must cost nothing.

    Staging calls this on every upgrade. An install whose last apply wrote the
    row must not download a workbook to learn what it already knows.
    """
    session = FakeSession(registries=[_registry_row()])
    stub_recovery()

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is True
    assert status.catalog_version == V_LIVE
    assert status.source == "apply"
    assert status.entries == 2 and status.with_focal_document_id == 2
    assert status.recovered_from_run_id is None
    assert status.reason is None
    assert session.commits == 0, "a read must not commit"


async def test_a_row_for_a_different_version_is_not_mistaken_for_this_one():
    """The 2026.1|seed row production had, against a 2026.2 catalogue.

    Returning it would report a usable registry describing rows that are no
    longer in the database.
    """
    session = FakeSession(registries=[_registry_row(version="2026.1")], runs=[])

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert status.catalog_version == V_LIVE
    assert "no applied upgrade run for 2026.2" in status.reason


async def test_a_stored_row_with_no_identifiers_is_reported_unusable(stub_recovery):
    """The exact shape of the seed row that caused the outage.

    A registry with no focal-document identifiers is no more useful than no
    registry, so the two must read the same to the gate. It is still RETURNED
    rather than recovered over: the row exists, and silently replacing an
    existing record from a stored workbook is a bigger action than reporting it.
    """
    session = FakeSession(
        registries=[_registry_row(registry=REGISTRY_WITHOUT_IDS, source="seed")]
    )
    stub_recovery()

    status = await fr.ensure_live_framework_registry(session)

    assert status.registry == REGISTRY_WITHOUT_IDS
    assert status.usable is False
    assert status.with_focal_document_id == 0
    assert "carries no focal-document identifiers" in status.reason
    assert "seed" in status.reason
    assert session.commits == 0


async def test_recovery_writes_the_registry_from_the_applied_runs_workbook(
    stub_recovery,
):
    """The fix, in one test.

    No registry row, but the applied run that produced the live catalogue still
    has its workbook. The row is written, stamped 'recovered', and committed —
    the correction outlives a run that is subsequently blocked or cancelled.
    """
    run = _applied_run()
    session = FakeSession(registries=[], runs=[run])
    state = stub_recovery(version=V_LIVE, registry=REGISTRY_WITH_IDS)

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is True
    assert status.source == fr.SOURCE_RECOVERED
    assert status.recovered_from_run_id == str(run.id)
    assert status.entries == 2 and status.with_focal_document_id == 2
    assert status.rows_written == 1
    assert state["downloaded"] == run.workbook_object_key
    assert session.commits == 1, "the correction must be committed, not left pending"

    written = session.tables[CatalogFrameworkRegistry]
    assert len(written) == 1
    assert written[0].catalog_version == V_LIVE
    assert written[0].source == fr.SOURCE_RECOVERED
    assert written[0].registry == REGISTRY_WITH_IDS


async def test_recovery_refuses_a_workbook_for_a_different_version(stub_recovery):
    """The one thing recovery must never do.

    The row is stamped with the LIVE version. Writing identifiers read from a
    different release would claim they describe rows they do not describe, and
    the next upgrade's diff would treat that claim as the publisher's own.
    Better to block with a reason than to record a plausible lie.
    """
    session = FakeSession(registries=[], runs=[_applied_run()])
    stub_recovery(version=V_NEXT, registry=REGISTRY_WITH_IDS)

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "2026.3" in status.reason and "2026.2" in status.reason
    assert session.tables[CatalogFrameworkRegistry] == []
    assert session.commits == 0


async def test_no_applied_run_with_a_workbook_reports_why(stub_recovery):
    """A cleaned-up workbook_object_key is not a recoverable state.

    The cleanup beat nulls the key. Acting on the row anyway would download
    nothing and report a recovery that never happened.
    """
    session = FakeSession(registries=[], runs=[_applied_run(key=None)])
    stub_recovery()

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "still holds its workbook in object storage" in status.reason
    assert session.commits == 0


async def test_an_older_releases_surviving_workbook_is_not_a_recovery_source(
    stub_recovery,
):
    """The trap: the live release's workbook is gone, an older one's is not.

    The cleanup beat nulls workbook_object_key oldest-last in no particular
    order, so an install can easily hold a 2025.4 workbook and not a 2026.2 one.
    Recovering from it would stamp 2025.4's identifiers as 2026.2's. The version
    is matched on the RUN, before anything is downloaded.
    """
    live_run = _applied_run(version=V_LIVE, key=None, completed_at=datetime(2026, 6, 1))
    older = _applied_run(version="2025.4", completed_at=datetime(2025, 6, 1))
    session = FakeSession(registries=[], runs=[live_run, older])
    state = stub_recovery()

    status = await fr.ensure_live_framework_registry(session)

    assert status.catalog_version == V_LIVE, "the live version is the latest apply"
    assert status.usable is False
    assert "no applied upgrade run for 2026.2" in status.reason
    assert "downloaded" not in state, "nothing should have been fetched"


async def test_a_non_applied_run_is_not_a_recovery_source(stub_recovery):
    """A staging run's workbook has not been applied to anything."""
    session = FakeSession(registries=[], runs=[_applied_run(status="staged")])
    stub_recovery()

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "no applied upgrade run for 2026.2" in status.reason


async def test_an_unreadable_object_degrades_to_a_reason_not_an_exception(
    stub_recovery,
):
    """Staging must survive object storage being unavailable.

    Recovery is an opportunistic correction. If it cannot run, the sanity check
    turns the reason into operator-actionable text; it does not take the upgrade
    down with a 500.
    """
    session = FakeSession(registries=[], runs=[_applied_run()])
    stub_recovery(download_error=FileNotFoundError("object not found in storage"))

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "could not be read from object storage" in status.reason
    assert session.commits == 0


async def test_an_unreadable_workbook_degrades_to_a_reason_not_an_exception(
    stub_recovery,
):
    session = FakeSession(registries=[], runs=[_applied_run()])
    stub_recovery(extract_error=ValueError("no catalog version in workbook"))

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "could not be read as an SCF workbook" in status.reason
    assert session.commits == 0


async def test_a_pre_2026_1_stored_workbook_carries_no_registry_to_recover(
    stub_recovery,
):
    session = FakeSession(registries=[], runs=[_applied_run()])
    stub_recovery(version=V_LIVE, registry={})

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert "carries no framework registry" in status.reason
    assert session.tables[CatalogFrameworkRegistry] == []


async def test_an_unseeded_catalog_reports_that_rather_than_recovering():
    session = FakeSession(registries=[], runs=[], live_version=None)

    status = await fr.ensure_live_framework_registry(session)

    assert status.usable is False
    assert status.catalog_version is None
    assert "no live catalog version" in status.reason


# ---------------------------------------------------------------------------
# read_live_framework_registry — the GET, which must never write
# ---------------------------------------------------------------------------


async def test_read_never_recovers_even_when_recovery_would_succeed(stub_recovery):
    """A GET that writes a row is a GET that can be replayed into a change."""
    session = FakeSession(registries=[], runs=[_applied_run()])
    stub_recovery()

    status = await fr.read_live_framework_registry(session)

    assert status.registry is None
    assert status.usable is False
    assert "no framework registry is stored for the live catalog" in status.reason
    assert session.commits == 0
    assert session.tables[CatalogFrameworkRegistry] == []


async def test_read_returns_the_stored_row_with_its_provenance():
    session = FakeSession(registries=[_registry_row(source=fr.SOURCE_RECOVERED)])

    status = await fr.read_live_framework_registry(session)

    assert status.usable is True
    assert status.source == fr.SOURCE_RECOVERED
    assert status.entries == 2 and status.with_focal_document_id == 2


# ---------------------------------------------------------------------------
# register_framework_registry_from_workbook — the operator-driven path
# ---------------------------------------------------------------------------


async def test_register_writes_the_row_stamped_with_the_live_version():
    session = FakeSession(registries=[])
    extractor = _StubExtractor(V_LIVE, REGISTRY_WITH_IDS)

    status = await fr.register_framework_registry_from_workbook(
        session, "/tmp/operator.xlsx", extractor=extractor
    )

    assert status.catalog_version == V_LIVE
    assert status.workbook_version == V_LIVE
    assert status.source == fr.SOURCE_BACKFILL
    assert status.rows_written == 1
    assert session.commits == 1
    assert session.tables[CatalogFrameworkRegistry][0].source == fr.SOURCE_BACKFILL


async def test_register_replaces_an_existing_row_in_place():
    """The seed row is corrected, not duplicated.

    Two rows for one catalogue version would make "the registry for 2026.2"
    ambiguous, and whichever the next SELECT happened to return would decide
    the upgrade.
    """
    existing = _registry_row(registry=REGISTRY_WITHOUT_IDS, source="seed")
    session = FakeSession(registries=[existing])
    extractor = _StubExtractor(V_LIVE, REGISTRY_WITH_IDS)

    status = await fr.register_framework_registry_from_workbook(
        session, "/tmp/operator.xlsx", extractor=extractor
    )

    assert len(session.tables[CatalogFrameworkRegistry]) == 1
    assert existing.registry == REGISTRY_WITH_IDS
    assert existing.source == fr.SOURCE_BACKFILL
    assert status.with_focal_document_id == 2


async def test_register_raises_on_a_version_mismatch_and_writes_nothing():
    session = FakeSession(registries=[])
    extractor = _StubExtractor(V_NEXT, REGISTRY_WITH_IDS)

    with pytest.raises(fr.RegistryVersionMismatch) as exc:
        await fr.register_framework_registry_from_workbook(
            session, "/tmp/wrong.xlsx", extractor=extractor
        )

    assert exc.value.workbook_version == V_NEXT
    assert exc.value.live_version == V_LIVE
    assert session.tables[CatalogFrameworkRegistry] == []
    assert session.commits == 0


async def test_register_allows_a_mismatch_only_when_explicitly_told_to():
    """The CLI escape hatch. It echoes BOTH versions back at the operator.

    Not offered over HTTP: a console button that quietly accepts the wrong
    release is how the wrong identifiers get recorded in the first place.
    """
    session = FakeSession(registries=[])
    extractor = _StubExtractor(V_NEXT, REGISTRY_WITH_IDS)

    status = await fr.register_framework_registry_from_workbook(
        session,
        "/tmp/next.xlsx",
        extractor=extractor,
        allow_version_mismatch=True,
    )

    assert status.catalog_version == V_LIVE, "the row is stamped LIVE, not workbook"
    assert status.workbook_version == V_NEXT
    assert session.tables[CatalogFrameworkRegistry][0].catalog_version == V_LIVE


async def test_register_refuses_a_workbook_with_no_registry():
    """An empty row asserts "this version has no identifiers", which is worse
    than having no row: the gate would report a usable-looking registry."""
    session = FakeSession(registries=[])
    extractor = _StubExtractor(V_LIVE, {})

    with pytest.raises(ValueError, match="no framework registry"):
        await fr.register_framework_registry_from_workbook(
            session, "/tmp/old.xlsx", extractor=extractor
        )
    assert session.tables[CatalogFrameworkRegistry] == []


async def test_register_refuses_when_the_catalog_is_not_seeded():
    session = FakeSession(registries=[], live_version=None)
    extractor = _StubExtractor(V_LIVE, REGISTRY_WITH_IDS)

    with pytest.raises(ValueError, match="no live catalog version"):
        await fr.register_framework_registry_from_workbook(
            session, "/tmp/any.xlsx", extractor=extractor
        )


async def test_register_accepts_a_caller_supplied_source():
    session = FakeSession(registries=[])
    extractor = _StubExtractor(V_LIVE, REGISTRY_WITH_IDS)

    status = await fr.register_framework_registry_from_workbook(
        session, "/tmp/any.xlsx", source=fr.SOURCE_RECOVERED, extractor=extractor
    )
    assert status.source == fr.SOURCE_RECOVERED


# ---------------------------------------------------------------------------
# LiveRegistryStatus.usable
# ---------------------------------------------------------------------------


def test_usable_requires_identifiers_not_merely_a_row():
    assert fr.LiveRegistryStatus().usable is False
    assert fr.LiveRegistryStatus(registry={}, entries=0).usable is False
    assert (
        fr.LiveRegistryStatus(registry=REGISTRY_WITHOUT_IDS, entries=1).usable is False
    )
    assert (
        fr.LiveRegistryStatus(
            registry=REGISTRY_WITH_IDS, entries=2, with_focal_document_id=2
        ).usable
        is True
    )
