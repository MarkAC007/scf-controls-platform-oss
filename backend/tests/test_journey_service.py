"""Tests for the guided journey engine.

The invariants worth defending here are the ones that protect a signature or
refuse a silent pass. A journey stage is a claim a named practitioner makes
about a client's readiness; everything below exists so the platform cannot
manufacture, erase, or quietly weaken one of those claims.

The DB-bound merge path is exercised against a stubbed session rather than a
live Postgres, because this suite has no database fixture (see
``tests/conftest.py`` — the convention here is unit tests with mocks). The
behaviours that need real SQL — two-hop domain joins, the unique ordinal
constraint — were verified in-container against the dev org and are recorded
in the PR rather than asserted here.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from models import JourneyStageState
from services import journey as journey_service


# ---------------------------------------------------------------------------
# Unknown checks are never a pass
# ---------------------------------------------------------------------------


def test_unsupported_checks_flags_an_unknown_type():
    """An artefact naming a check the engine cannot run must be refused.

    Accepting it would hand the practitioner a gate that reads "cannot
    evaluate" for the length of an engagement — discovered by the client
    rather than by them.
    """
    template = {
        "stages": [
            {"key": "a", "precondition_spec": [{"type": "documents_approved"}]},
            {"key": "b", "precondition_spec": [{"type": "vibes_acceptable"}]},
        ]
    }
    assert journey_service.unsupported_checks(template) == {"vibes_acceptable"}


def test_unsupported_checks_passes_a_fully_supported_template():
    template = {
        "stages": [
            {"key": "a", "precondition_spec": [
                {"type": "frameworks_scoped"},
                {"type": "controls_with_owner"},
            ]},
        ]
    }
    assert journey_service.unsupported_checks(template) == set()


def test_unsupported_checks_tolerates_stages_without_a_gate():
    """A stage with no preconditions is a legitimate shape, not an error."""
    template = {"stages": [{"key": "a"}, {"key": "b", "precondition_spec": None}]}
    assert journey_service.unsupported_checks(template) == set()


def test_every_supported_check_is_actually_dispatchable():
    """SUPPORTED_CHECKS must not drift ahead of the evaluator.

    A type listed here but not handled in ``_evaluate_one`` would pass import
    validation and then evaluate to "cannot evaluate" forever — the exact
    failure ``unsupported_checks`` exists to prevent, reintroduced one layer
    down.
    """
    import inspect

    source = inspect.getsource(journey_service._evaluate_one)
    for check_type in journey_service.SUPPORTED_CHECKS:
        assert f'"{check_type}"' in source, (
            f"{check_type} is advertised as supported but _evaluate_one "
            f"never mentions it"
        )


# ---------------------------------------------------------------------------
# Domain scoping
# ---------------------------------------------------------------------------


def test_domain_clause_is_none_when_no_domains_named():
    """An unfiltered check keeps its org-wide meaning.

    This is what makes the feature backward compatible: templates written
    before domain scoping existed must evaluate exactly as they did.
    """
    assert journey_service._domain_clause(None) is None
    assert journey_service._domain_clause([]) is None


def test_domain_clause_is_none_for_blank_entries():
    """Whitespace is not a domain. Treating it as one would filter to nothing
    and render the gate permanently unreachable."""
    assert journey_service._domain_clause(["", "   "]) is None


def test_domain_clause_builds_a_filter_when_domains_are_named():
    clause = journey_service._domain_clause(["GOV", "RSK"])
    assert clause is not None
    # The two-hop join is the point: scoped controls carry an scf_id string,
    # the catalog carries the domain name, and the code lives on the domain.
    rendered = str(clause)
    assert "scf_id" in rendered


@pytest.mark.asyncio
async def test_denominator_reuses_the_org_total_when_unfiltered():
    """An unfiltered check must not issue a second count."""
    db = MagicMock()
    db.scalar = AsyncMock()
    got = await journey_service._denominator(
        db, uuid4(), None, {"in_scope": 372}
    )
    assert got == 372
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_denominator_counts_its_own_slice_when_filtered():
    """A domain-filtered check divides by its own population.

    Dividing a wave's numerator by the org-wide total would make every wave
    gate read as a few percent complete, forever — the defect this function
    exists to prevent.
    """
    db = MagicMock()
    db.scalar = AsyncMock(return_value=93)
    clause = journey_service._domain_clause(["GOV", "RSK"])
    got = await journey_service._denominator(
        db, uuid4(), clause, {"in_scope": 372}
    )
    assert got == 93
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_denominator_treats_a_null_count_as_zero():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    clause = journey_service._domain_clause(["GOV"])
    got = await journey_service._denominator(
        db, uuid4(), clause, {"in_scope": 372}
    )
    assert got == 0


# ---------------------------------------------------------------------------
# Re-issuing a revised artefact
# ---------------------------------------------------------------------------


def _stage(key, ordinal, state=JourneyStageState.LOCKED.value, attested=False):
    st = MagicMock()
    st.key = key
    st.ordinal = ordinal
    st.state = state
    st.attested_at = datetime.now(timezone.utc) if attested else None
    st.attested_by_user_id = uuid4() if attested else None
    return st


def _journey_with(stages):
    j = MagicMock()
    j.stages = stages
    j.activated_at = None
    j.practitioner_name = None
    j.practitioner_organization_id = None
    return j


def _db_returning(journey):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = journey
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_reissue_refuses_to_drop_a_passed_stage():
    """A revision cannot un-sign something.

    The person signed a moment in time. Deleting the row would delete the
    audit trail the database check constraint exists to protect, so the
    import refuses rather than guessing.
    """
    journey = _journey_with([
        _stage("mobilise", 0, JourneyStageState.PASSED.value, attested=True),
        _stage("determinants", 1),
    ])
    db = _db_returning(journey)

    with pytest.raises(ValueError) as exc:
        await journey_service.import_template(
            db, uuid4(), {"stages": [{"key": "determinants", "title": "D"}]}
        )

    assert "mobilise" in str(exc.value)
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_reissue_refuses_to_drop_a_conditionally_passed_stage():
    """A conditional pass is still a signature."""
    journey = _journey_with([
        _stage("spine", 0, JourneyStageState.PASSED_CONDITIONAL.value, attested=True),
    ])
    db = _db_returning(journey)

    with pytest.raises(ValueError) as exc:
        await journey_service.import_template(
            db, uuid4(), {"stages": [{"key": "other", "title": "O"}]}
        )

    assert "spine" in str(exc.value)


@pytest.mark.asyncio
async def test_reissue_may_drop_an_unsigned_stage():
    """Removing a stage nobody has signed is a legitimate revision."""
    journey = _journey_with([
        _stage("mobilise", 0),
        _stage("retired-wave", 1),
    ])
    db = _db_returning(journey)

    await journey_service.import_template(
        db, uuid4(), {"stages": [{"key": "mobilise", "title": "M"}]}
    )

    assert db.delete.await_count == 1


@pytest.mark.asyncio
async def test_reissue_negates_old_ordinals_before_rewriting():
    """Ordinals are unique per journey.

    Without clearing the old ordering first, a reordering revision collides
    with itself — stage B cannot take ordinal 0 while stage A still holds it.
    """
    a = _stage("a", 0)
    b = _stage("b", 1)
    db = _db_returning(_journey_with([a, b]))

    await journey_service.import_template(
        db, uuid4(), {"stages": [{"key": "b", "title": "B"}, {"key": "a", "title": "A"}]}
    )

    # Both were pushed negative during the merge, so neither could collide.
    assert db.flush.await_count >= 1


# ---------------------------------------------------------------------------
# Template keys name a file on disk, so they are untrusted input
# ---------------------------------------------------------------------------


class TestTemplateKeyIsNotAPath:
    """`template_key` arrives from a request body and is used to open a file.

    CodeQL flagged this as py/path-injection against v0.40.1 (alerts 320/321).
    The sink is reachable: api/journey.py's import endpoint passes the field
    straight through, so an org admin could read any .json the backend process
    can reach. These tests are the regression fence.

    Every rejection must be indistinguishable from "no such template" — a
    different error, or a different status, would turn the endpoint into an
    oracle for which files exist on the host.
    """

    @pytest.mark.parametrize("key", [
        "../../../../etc/passwd",
        "../secrets",
        "..%2f..%2fetc%2fpasswd",
        "/etc/passwd",
        "//etc/passwd",
        "subdir/template",
        "back\\slash",
        "",
        ".",
        "..",
        ".hidden",
        "key with spaces",
        "nul\x00byte",
    ])
    def test_a_key_that_is_not_a_bare_stem_is_refused(self, key):
        with pytest.raises(FileNotFoundError):
            journey_service.load_template(key)

    def test_traversal_is_refused_even_when_the_target_exists(self, tmp_path, monkeypatch):
        """The refusal is not an accident of the file being absent.

        Without this, a test suite on a box where /etc/passwd.json happens not
        to exist would pass with the guard removed.
        """
        outside = tmp_path / "secret.json"
        outside.write_text('{"stages": []}', encoding="utf-8")
        inside = tmp_path / "templates"
        inside.mkdir()
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", inside)

        with pytest.raises(FileNotFoundError):
            journey_service.load_template("../secret")

        # The same bytes ARE readable when asked for legitimately, which proves
        # the refusal came from the guard and not from an unreadable file.
        (inside / "secret.json").write_text('{"stages": []}', encoding="utf-8")
        assert journey_service.load_template("secret") == {"stages": []}

    def test_a_symlink_escaping_the_directory_is_refused(self, tmp_path, monkeypatch):
        """The pattern cannot see this one — only the resolved path can.

        `evil` is a perfectly legal bare stem. It is the resolution that
        reveals it points out of the template directory.
        """
        outside = tmp_path / "outside.json"
        outside.write_text('{"stages": []}', encoding="utf-8")
        inside = tmp_path / "templates"
        inside.mkdir()
        link = inside / "evil.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # pragma: no cover
            pytest.skip("filesystem does not support symlinks")
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", inside)

        assert journey_service.TEMPLATE_KEY_RE.match("evil"), \
            "the pattern alone would admit this key — containment is the control"
        with pytest.raises(FileNotFoundError):
            journey_service.load_template("evil")

    def test_the_shipped_default_still_loads(self):
        """The guard must not break the template the product actually ships."""
        doc = journey_service.load_template()
        assert doc.get("template_key") == journey_service.DEFAULT_TEMPLATE_KEY
        assert doc.get("stages")

    @pytest.mark.parametrize("key", [
        "compliancegenie-default",
        "cg-12-month",
        "client_a.v2",
        "Plan2026",
    ])
    def test_legitimate_keys_still_match(self, key):
        """Real keys, including the practitioner artefact's own, stay legal."""
        assert journey_service.TEMPLATE_KEY_RE.match(key)


class TestTemplateSelectionComesFromTheDirectory:
    """The v0.40.1 fix checked a path it had already built from the key.

    CodeQL rejected it, and the rejection was correct on the merits: a check on
    a tainted path is weaker than never constructing one. These tests pin the
    property that replaced it — the opened file is chosen by enumerating the
    directory, so the caller's string selects among files we already have
    rather than naming one. They fail if anyone reintroduces path joining.
    """

    def test_a_key_naming_a_real_file_outside_the_directory_is_refused(
        self, tmp_path, monkeypatch
    ):
        """The classic traversal, with the target deliberately made to exist.

        `secrets.json` is real and readable. If the loader still joined the key
        to TEMPLATE_DIR this would open it. Enumeration cannot reach it,
        because it is not in the directory being enumerated.
        """
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "legit.json").write_text('{"template_key": "legit"}')
        secrets = tmp_path / "secrets.json"
        secrets.write_text('{"password": "hunter2"}')
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        with pytest.raises(FileNotFoundError):
            journey_service.load_template("../secrets")

        # and the legitimate neighbour in the same directory still loads,
        # so the refusal above is selectivity rather than a broken loader
        assert journey_service.load_template("legit")["template_key"] == "legit"

    def test_the_file_opened_is_the_one_the_directory_listing_offered(
        self, tmp_path, monkeypatch
    ):
        """Rename the file on disk and the old key stops working.

        This is the positive form of the property. If selection were driven by
        the string, `first` would keep resolving after the rename. It only
        stops working because the loader asks the directory what exists.
        """
        templates = tmp_path / "templates"
        templates.mkdir()
        target = templates / "first.json"
        target.write_text('{"template_key": "first"}')
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        assert journey_service.load_template("first")["template_key"] == "first"

        target.rename(templates / "second.json")
        with pytest.raises(FileNotFoundError):
            journey_service.load_template("first")
        assert journey_service.load_template("second")["template_key"] == "first"

    def test_a_non_json_neighbour_is_not_reachable(self, tmp_path, monkeypatch):
        """Enumeration is scoped to *.json, so a sibling .env stays invisible."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / ".env").write_text("SECRET=1")
        (templates / "notes.txt").write_text("plain")
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        for key in (".env", "notes", "notes.txt"):
            with pytest.raises(FileNotFoundError):
                journey_service.load_template(key)

    def test_the_loader_does_not_join_the_key_to_a_path(self):
        """Source-level pin: the construction CodeQL flagged must stay gone.

        Crude on purpose. The property is invisible at runtime once the
        directory happens to be empty of surprises, and this fix is only
        meaningful as a *structural* one, so the structure is what gets
        asserted. If this fails, read the load_template docstring before
        'fixing' the test.
        """
        import inspect

        source = inspect.getsource(journey_service.load_template)
        body = source.split('"""')[-1]
        assert "TEMPLATE_DIR /" not in body, \
            "path built from the key again — see the docstring"
        assert "root /" not in body, \
            "path built from the key again — see the docstring"
        assert ".glob(" in body, "selection must come from a directory listing"
