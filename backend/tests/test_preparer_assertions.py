"""
Preparer assertions on evidence files (#786, #802).

Twelve columns that record what a *person* claims the artefact is evidence of:
the window it covers, the population it was drawn from, how it was sampled, and
how the data inside it was produced. Nothing measures these and nothing can —
they are the facts only the preparer knows.

The single property every test here defends is that **"not asserted" stays
distinct from "asserted"**. A default, a back-fill or a coerced empty string
would each manufacture a claim nobody made, in the one place on the record
where the point is that someone took responsibility for it.
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.preparer_assertions import PREPARER_ASSERTION_FIELDS, has_any_assertion  # noqa: E402
from evidence_mocks import unasserted  # noqa: E402



# ---------------------------------------------------------------------------
# Fixtures
#
# Deliberately local rather than promoted to conftest.py. Moving the evidence
# API's fixtures into a shared conftest would put them in scope for every test
# in the suite, and a `membership` fixture that silently applies to unrelated
# modules is how a test starts passing for the wrong reason.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def signing_secret(monkeypatch):
    """Upload tickets refuse to sign without one — see download_token."""
    import services.download_token as dt

    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "test-signing-secret")
    monkeypatch.setattr(dt, "_SECRET", None)
    yield
    monkeypatch.setattr(dt, "_SECRET", None)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id
    m.user.db_id = str(user_id)
    m.user.email = "preparer@example.com"
    m.user.display_name = "Preparer"
    m.role = "editor"
    return m


@pytest.fixture
def mock_db():
    from unittest.mock import AsyncMock

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    # Default every lookup to "found nothing". A MagicMock result would hand
    # back a MagicMock row, and callers that compare it to a date — the
    # collection-date stamp, for one — blow up on a comparison rather than
    # taking the empty branch the test intended.
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    return db


# ---------------------------------------------------------------------------
# The field set itself
# ---------------------------------------------------------------------------

class TestFieldSetIsSingleSourced:
    """Five places have to agree on which twelve columns these are."""

    def test_every_declared_field_exists_on_the_model(self):
        """A typo here would silently drop a field from the API and the audit log."""
        import catalog_models  # noqa: F401 — completes the mapper registry
        from models import EvidenceFile

        columns = {c.key for c in EvidenceFile.__table__.columns}
        missing = [f for f in PREPARER_ASSERTION_FIELDS if f not in columns]
        assert missing == [], f"declared but not on the model: {missing}"

    def test_the_model_has_no_assertion_column_the_field_set_forgot(self):
        """The failure this catches is a *thirteenth* column added to models.py alone.

        It would then be persisted by nothing, returned by nothing and audited
        by nothing — present in the schema, absent from the product.
        """
        import catalog_models  # noqa: F401
        from models import EvidenceFile

        prefixes = ("effective_period_", "population_", "sample_", "ipe_")
        on_model = {
            c.key for c in EvidenceFile.__table__.columns
            if c.key.startswith(prefixes)
        }
        assert on_model == set(PREPARER_ASSERTION_FIELDS)

    def test_every_field_is_nullable(self):
        """Nullability is the feature. A NOT NULL here would force a claim."""
        import catalog_models  # noqa: F401
        from models import EvidenceFile

        for field in PREPARER_ASSERTION_FIELDS:
            column = EvidenceFile.__table__.columns[field]
            assert column.nullable is True, f"{field} must stay nullable"

    def test_no_field_carries_a_server_default(self):
        """A server default is a back-fill that runs forever, one row at a time."""
        import catalog_models  # noqa: F401
        from models import EvidenceFile

        for field in PREPARER_ASSERTION_FIELDS:
            column = EvidenceFile.__table__.columns[field]
            assert column.server_default is None, f"{field} must not default"
            assert column.default is None, f"{field} must not default"

    def test_every_field_is_audited(self):
        """"Did someone widen the period when the sample came up short?" is
        exactly the question an audit trail exists to answer."""
        from services.audit_service import EVIDENCE_FILE_TRACKED_FIELDS

        missing = [f for f in PREPARER_ASSERTION_FIELDS if f not in EVIDENCE_FILE_TRACKED_FIELDS]
        assert missing == [], f"assertions not tracked in the audit trail: {missing}"


class TestHasAnyAssertion:
    """Partially asserted is a third state, and it is not the same as neither."""

    def test_a_file_with_nothing_asserted_reports_nothing(self):
        f = unasserted(MagicMock())
        assert has_any_assertion(f) is False

    def test_one_asserted_field_is_enough(self):
        f = unasserted(MagicMock())
        f.population_size = 400
        assert has_any_assertion(f) is True

    def test_a_zero_population_is_an_assertion_not_an_absence(self):
        """`population_size = 0` says "there was nothing to sample" — a real,
        testable claim. Treating it as unasserted would erase it."""
        f = unasserted(MagicMock())
        f.population_size = 0
        assert has_any_assertion(f) is True


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class TestConfirmRequestValidatesAssertions:
    """Rules that stop an incoherent assertion reaching the database."""

    def _request(self, **kwargs):
        from schemas import EvidenceFileConfirmRequest

        return EvidenceFileConfirmRequest(s3_key="evidence/x/y.pdf", **kwargs)

    def test_a_whole_ordered_period_is_accepted(self):
        r = self._request(
            effective_period_start=date(2026, 1, 1),
            effective_period_end=date(2026, 3, 31),
        )
        assert r.effective_period_start == date(2026, 1, 1)
        assert r.effective_period_end == date(2026, 3, 31)

    def test_a_single_day_period_is_accepted(self):
        """A point-in-time artefact — a configuration screenshot — covers one day."""
        r = self._request(
            effective_period_start=date(2026, 2, 2),
            effective_period_end=date(2026, 2, 2),
        )
        assert r.effective_period_start == r.effective_period_end

    def test_an_end_before_its_start_is_refused(self):
        with pytest.raises(Exception) as exc:
            self._request(
                effective_period_start=date(2026, 3, 1),
                effective_period_end=date(2026, 1, 1),
            )
        assert "cannot be before" in str(exc.value)

    def test_a_start_with_no_end_is_refused(self):
        """The case a field-level validator would miss entirely, because a
        validator does not run for a field the caller simply omitted."""
        with pytest.raises(Exception) as exc:
            self._request(effective_period_start=date(2026, 1, 1))
        assert "effective_period_end is required" in str(exc.value)

    def test_an_end_with_no_start_is_refused(self):
        with pytest.raises(Exception) as exc:
            self._request(effective_period_end=date(2026, 1, 1))
        assert "effective_period_start is required" in str(exc.value)

    def test_asserting_no_period_at_all_is_fine(self):
        """Optional means optional. Most uploads will assert nothing."""
        r = self._request()
        assert r.effective_period_start is None
        assert r.effective_period_end is None

    def test_a_sample_larger_than_its_population_is_refused(self):
        with pytest.raises(Exception) as exc:
            self._request(population_size=2, sample_size=5)
        assert "cannot exceed" in str(exc.value)

    def test_a_full_population_sample_is_accepted(self):
        """Testing every item is the strongest sample there is, not an error."""
        r = self._request(population_size=12, sample_size=12, sample_method="full population")
        assert r.sample_size == 12

    def test_a_sample_with_no_declared_population_is_accepted(self):
        """Incomplete, not incoherent. The UI nudges; the API does not refuse —
        a preparer who knows the sample and not yet the population should be
        able to record what they know."""
        r = self._request(sample_size=25)
        assert r.sample_size == 25
        assert r.population_size is None

    def test_a_negative_population_is_refused(self):
        with pytest.raises(Exception):
            self._request(population_size=-1)


class TestResponseCarriesAssertionsBack:
    """A field the API accepts but never returns is a field nobody can check."""

    def test_to_response_actually_reads_the_assertions_off_the_row(self):
        """The schema declaring a field proves nothing about the handler filling it.

        Every other test in this class builds an `EvidenceFileResponse` by hand,
        which exercises pydantic and not `_to_response`. Deleting the assertion
        spread from the handler leaves all of them green while the API silently
        returns twelve nulls for a fully asserted file — so this one goes through
        the converter itself.
        """
        from api.evidence_files import _to_response

        extractor = uuid4()
        f = MagicMock()
        f.id = uuid4()
        f.organization_id = uuid4()
        f.evidence_id = "ERL-001"
        f.filename = "joiners-q1.csv"
        f.s3_key = "evidence/o/k.csv"
        f.content_type = "text/csv"
        f.file_size_bytes = 2048
        f.sha256_hash = None
        f.classification = "internal"
        f.scan_status = "clean"
        f.scan_details = None
        f.computed_sha256 = None
        f.hash_verification_status = "verified"
        f.hash_verified_at = None
        f.hash_verification_details = None
        f.uploaded_by_user_id = None
        f.uploaded_at = datetime(2026, 4, 2, 9, 0, 0)
        f.expires_at = None
        f.is_deleted = False
        f.uploaded_by = None
        f.review_status = "not_reviewed"
        f.reviewed_by_user_id = None
        f.reviewed_at = None
        f.review_notes = None
        f.reviewed_by = None

        asserted = {
            "effective_period_start": date(2026, 1, 1),
            "effective_period_end": date(2026, 3, 31),
            "population_size": 412,
            "population_source": "All joiners in Workday, 1 Jan – 31 Mar 2026",
            "sample_size": 25,
            "sample_method": "random",
            "sample_basis": "AICPA sample size guidance for a quarterly control",
            "ipe_source_system": "Workday",
            "ipe_query_or_filter": "Report: New Hires, hire_date in Q1 2026",
            "ipe_extracted_by_user_id": extractor,
            "ipe_extracted_at": datetime(2026, 4, 1, 17, 30, 0),
            "ipe_completeness_check": "Row count reconciled to the Workday headcount report",
        }
        # Every field, from the single source — a thirteenth column is covered
        # here the moment it is added to the tuple.
        assert set(asserted) == set(PREPARER_ASSERTION_FIELDS)
        for field, value in asserted.items():
            setattr(f, field, value)

        response = _to_response(f, download_url=None)

        for field, value in asserted.items():
            assert getattr(response, field) == value, f"{field} was dropped on the way out"

    def test_to_response_reports_an_unasserted_file_as_none(self):
        """Not "" and not a default — the absence has to survive the converter too."""
        from api.evidence_files import _to_response

        f = unasserted(MagicMock())
        f.id = uuid4()
        f.organization_id = uuid4()
        f.evidence_id = "ERL-001"
        f.filename = "screenshot.png"
        f.s3_key = "evidence/o/s.png"
        f.content_type = "image/png"
        f.file_size_bytes = 12
        f.sha256_hash = None
        f.classification = "internal"
        f.scan_status = "clean"
        f.scan_details = None
        f.computed_sha256 = None
        f.hash_verification_status = "verified"
        f.hash_verified_at = None
        f.hash_verification_details = None
        f.uploaded_by_user_id = None
        f.uploaded_at = datetime(2026, 4, 2, 9, 0, 0)
        f.expires_at = None
        f.is_deleted = False
        f.uploaded_by = None
        f.review_status = "not_reviewed"
        f.reviewed_by_user_id = None
        f.reviewed_at = None
        f.review_notes = None
        f.reviewed_by = None

        response = _to_response(f, download_url=None)

        for field in PREPARER_ASSERTION_FIELDS:
            assert getattr(response, field) is None

    def test_response_declares_every_assertion_field(self):
        from schemas import EvidenceFileResponse

        missing = [f for f in PREPARER_ASSERTION_FIELDS if f not in EvidenceFileResponse.model_fields]
        assert missing == [], f"accepted on confirm but never returned: {missing}"

    def test_request_declares_every_assertion_field(self):
        from schemas import EvidenceFileConfirmRequest

        missing = [
            f for f in PREPARER_ASSERTION_FIELDS
            if f not in EvidenceFileConfirmRequest.model_fields
        ]
        assert missing == [], f"on the model but not capturable: {missing}"

    def test_every_assertion_field_defaults_to_none_on_the_response(self):
        """Historical files must come back as "not asserted", never as a value."""
        from schemas import EvidenceFileResponse

        for field in PREPARER_ASSERTION_FIELDS:
            assert EvidenceFileResponse.model_fields[field].default is None

    def test_a_fully_asserted_file_round_trips(self):
        from schemas import EvidenceFileResponse

        extractor = uuid4()
        response = EvidenceFileResponse(
            id=uuid4(),
            organization_id=uuid4(),
            evidence_id="ERL-001",
            filename="joiners-q1.csv",
            s3_key="evidence/o/k.csv",
            content_type="text/csv",
            file_size_bytes=2048,
            classification="internal",
            uploaded_at=datetime(2026, 4, 2, 9, 0, 0),
            is_deleted=False,
            effective_period_start=date(2026, 1, 1),
            effective_period_end=date(2026, 3, 31),
            population_size=412,
            population_source="All joiners in Workday, 1 Jan – 31 Mar 2026",
            sample_size=25,
            sample_method="random",
            sample_basis="AICPA sample size guidance for a quarterly control",
            ipe_source_system="Workday",
            ipe_query_or_filter="Report: New Hires, hire_date between 2026-01-01 and 2026-03-31",
            ipe_extracted_by_user_id=extractor,
            ipe_extracted_at=datetime(2026, 4, 1, 17, 30, 0),
            ipe_completeness_check="Row count reconciled to the Workday headcount report",
        )
        assert response.effective_period_start == date(2026, 1, 1)
        assert response.population_size == 412
        assert response.sample_method == "random"
        assert response.ipe_extracted_by_user_id == extractor
        assert response.ipe_completeness_check.startswith("Row count reconciled")

    def test_an_unasserted_file_round_trips_as_none_not_as_blank(self):
        """The distinction the whole feature rests on: absent, not empty."""
        from schemas import EvidenceFileResponse

        response = EvidenceFileResponse(
            id=uuid4(),
            organization_id=uuid4(),
            evidence_id="ERL-001",
            filename="old.pdf",
            s3_key="evidence/o/old.pdf",
            content_type="application/pdf",
            file_size_bytes=10,
            classification="internal",
            uploaded_at=datetime(2024, 1, 1, 0, 0, 0),
            is_deleted=False,
        )
        for field in PREPARER_ASSERTION_FIELDS:
            value = getattr(response, field)
            assert value is None, f"{field} came back as {value!r}, not None"


# ---------------------------------------------------------------------------
# The confirm endpoint persists them
# ---------------------------------------------------------------------------

class TestConfirmPersistsAssertions:

    @pytest.mark.asyncio
    async def test_confirm_writes_every_asserted_field_onto_the_record(
        self, membership, mock_db, org_id
    ):
        from unittest.mock import patch as _patch

        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest
        from tests.test_evidence_files_api import make_ticket

        s3_key = f"evidence/{org_id}/2026/04/abc123456789_joiners.csv"
        request = EvidenceFileConfirmRequest(
            s3_key=s3_key,
            upload_ticket=make_ticket(org_id, "ERL-001", membership, s3_key),
            effective_period_start=date(2026, 1, 1),
            effective_period_end=date(2026, 3, 31),
            population_size=412,
            population_source="All joiners in Workday",
            sample_size=25,
            sample_method="random",
            sample_basis="Quarterly control, AICPA guidance",
            ipe_source_system="Workday",
            ipe_query_or_filter="Report: New Hires",
            ipe_extracted_at=datetime(2026, 4, 1, 17, 30, 0),
            ipe_completeness_check="Reconciled to headcount",
        )

        mock_db.flush = _mk_async()
        mock_db.commit = _mk_async()
        mock_db.refresh = _mk_async()

        with _patch("api.evidence_files.tag_evidence_object"), \
             _patch("api.evidence_files.run_validation", new=_mk_async()), \
             _patch("api.evidence_files.log_entity_changes", new=_mk_async()), \
             _patch("api.evidence_files.enqueue_integrity_verification"), \
             _patch("api.evidence_files._safe_download_url", return_value=None), \
             _patch("api.evidence_files._to_response") as to_response:
            to_response.return_value = MagicMock()
            await confirm_upload(
                org_id=org_id,
                evidence_id="ERL-001",
                request=request,
                http_request=MagicMock(),
                membership=membership,
                db=mock_db,
            )

        created = mock_db.add.call_args[0][0]
        assert created.effective_period_start == date(2026, 1, 1)
        assert created.effective_period_end == date(2026, 3, 31)
        assert created.population_size == 412
        assert created.population_source == "All joiners in Workday"
        assert created.sample_size == 25
        assert created.sample_method == "random"
        assert created.sample_basis == "Quarterly control, AICPA guidance"
        assert created.ipe_source_system == "Workday"
        assert created.ipe_query_or_filter == "Report: New Hires"
        assert created.ipe_completeness_check == "Reconciled to headcount"

    @pytest.mark.asyncio
    async def test_confirm_leaves_unasserted_fields_null(self, membership, mock_db, org_id):
        """The common case. Nothing may be invented from uploaded_at."""
        from unittest.mock import patch as _patch

        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest
        from tests.test_evidence_files_api import make_ticket

        s3_key = f"evidence/{org_id}/2026/04/abc123456789_screenshot.png"
        request = EvidenceFileConfirmRequest(
            s3_key=s3_key,
            upload_ticket=make_ticket(org_id, "ERL-001", membership, s3_key),
        )

        mock_db.flush = _mk_async()
        mock_db.commit = _mk_async()
        mock_db.refresh = _mk_async()

        with _patch("api.evidence_files.tag_evidence_object"), \
             _patch("api.evidence_files.run_validation", new=_mk_async()), \
             _patch("api.evidence_files.log_entity_changes", new=_mk_async()), \
             _patch("api.evidence_files.enqueue_integrity_verification"), \
             _patch("api.evidence_files._safe_download_url", return_value=None), \
             _patch("api.evidence_files._to_response") as to_response:
            to_response.return_value = MagicMock()
            await confirm_upload(
                org_id=org_id,
                evidence_id="ERL-001",
                request=request,
                http_request=MagicMock(),
                membership=membership,
                db=mock_db,
            )

        created = mock_db.add.call_args[0][0]
        for field in PREPARER_ASSERTION_FIELDS:
            assert getattr(created, field) is None, f"{field} was invented"
        # And specifically not from the upload timestamp, which is the one
        # back-fill anybody would reach for.
        assert created.effective_period_start is None


def _mk_async():
    from unittest.mock import AsyncMock

    return AsyncMock()


class TestConfirmStampsTheCollectionDate:
    """The browser-upload path now does what the webhook inbox always did (#789).

    These go through `confirm_upload` itself. The rule lives in
    `services/collection_date.py` and is unit-tested in `test_collection_date.py`;
    what is at stake here is whether the endpoint calls it at all, and with the
    right inputs — a lookup that never happens is invisible to a unit test of the
    thing it was supposed to call.
    """

    @staticmethod
    def _tracker(last):
        t = MagicMock()
        t.last_collection_date = last
        return t

    async def _confirm(self, mock_db, membership, org_id, tracker, **request_kwargs):
        from unittest.mock import patch as _patch

        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest
        from tests.test_evidence_files_api import make_ticket

        s3_key = f"evidence/{org_id}/2026/04/abc123456789_joiners.csv"
        request = EvidenceFileConfirmRequest(
            s3_key=s3_key,
            upload_ticket=make_ticket(org_id, "ERL-001", membership, s3_key),
            **request_kwargs,
        )
        mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=tracker)
        mock_db.flush = _mk_async()
        mock_db.commit = _mk_async()
        mock_db.refresh = _mk_async()

        with _patch("api.evidence_files.tag_evidence_object"), \
             _patch("api.evidence_files.run_validation", new=_mk_async()), \
             _patch("api.evidence_files.log_entity_changes", new=_mk_async()), \
             _patch("api.evidence_files.enqueue_integrity_verification"), \
             _patch("api.evidence_files._safe_download_url", return_value=None), \
             _patch("api.evidence_files._to_response") as to_response:
            to_response.return_value = MagicMock()
            await confirm_upload(
                org_id=org_id,
                evidence_id="ERL-001",
                request=request,
                http_request=MagicMock(),
                membership=membership,
                db=mock_db,
            )

    @pytest.mark.asyncio
    async def test_the_asserted_period_end_becomes_the_collection_date(
        self, membership, mock_db, org_id
    ):
        """Uploaded 2 April, covers Q1 — the programme was collected on 31 March."""
        tracker = self._tracker(date(2026, 1, 1))
        await self._confirm(
            mock_db, membership, org_id, tracker,
            effective_period_start=date(2026, 1, 1),
            effective_period_end=date(2026, 3, 31),
        )
        assert tracker.last_collection_date == date(2026, 3, 31)

    @pytest.mark.asyncio
    async def test_an_unasserted_upload_stamps_today(self, membership, mock_db, org_id):
        """The parity fix itself: this path used to stamp nothing at all."""
        tracker = self._tracker(date(2020, 1, 1))
        await self._confirm(mock_db, membership, org_id, tracker)
        assert tracker.last_collection_date == datetime.utcnow().date()

    @pytest.mark.asyncio
    async def test_back_filling_old_evidence_does_not_regress_the_tracker(
        self, membership, mock_db, org_id
    ):
        tracker = self._tracker(date(2026, 6, 30))
        await self._confirm(
            mock_db, membership, org_id, tracker,
            effective_period_start=date(2025, 1, 1),
            effective_period_end=date(2025, 12, 31),
        )
        assert tracker.last_collection_date == date(2026, 6, 30)

    @pytest.mark.asyncio
    async def test_no_tracker_is_survivable(self, membership, mock_db, org_id):
        """Evidence can be uploaded against a catalog item nobody is tracking.

        If this raises, every upload to an untracked evidence item 500s — which
        is a far worse outcome than the freshness column staying unset.
        """
        await self._confirm(mock_db, membership, org_id, None)
        assert mock_db.add.called
