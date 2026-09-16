"""Window assessment v2 — parity with the per-file #881 contract.

Covers, without a database or a model:
- prompt assembly: objectives (with SCF rigor), collection block, per-file
  truncation disclosure, membership reason, dedupe/omission disclosure, and
  the artifact-type sections being absent when the catalog publishes none
- the strict window parser: refuses cut-off, malformed and mis-attributed
  answers instead of repairing them
- membership rule: asserted effective period first, upload date as fallback
- text budget: identical payloads collapse, the window total is bounded, and
  what was not shown is disclosed
- assess_window end to end against a stub session: a verdict lands with
  ao_findings / counts / membership / schema_version, and a cut-off answer
  lands as status=error with no fabricated verdict
"""
import inspect
import json
import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import window_assessment_service as svc  # noqa: E402
from services.assessment_prompts import (  # noqa: E402
    ASSESSMENT_RIGOR_SCALE,
    ControlContext,
    PROMPT_VERSION,
    WINDOW_ASSESSMENT_OUTPUT_SCHEMA,
    WINDOW_PROMPT_VERSION,
    WINDOW_SCHEMA_VERSION,
    _assessment_objectives_block,
    build_window_assessment_prompt,
)
from services.assessment_verdict import (  # noqa: E402
    AssessmentParseError,
    ParsedWindowAssessment,
    parse_window_assessment_v2,
)
from services.window_assessment_service import (  # noqa: E402
    MEMBERSHIP_ASSERTED_PERIOD,
    MEMBERSHIP_UPLOADED_AT,
    PER_FILE_TEXT_CAP,
    WINDOW_TEXT_BUDGET,
    _FileInWindow,
    _apply_text_budget,
    _compute_window_hash,
    _derive_window_status,
    _membership_rule,
    _membership_snapshot,
    _plan_prompt_content,
    _text_budget_finding,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WINDOW_START = datetime(2026, 8, 12, 12, 0, 0)
WINDOW_END = datetime(2026, 9, 16, 12, 0, 0)


def _context(objectives=None, rigor=None) -> ControlContext:
    objectives = objectives if objectives is not None else [
        {"ao_id": "BCD-11_A01", "scf_id": "BCD-11", "objective_text": "Backups are performed.", "expected_results": "Backup logs"},
        {"ao_id": "BCD-11_A02", "scf_id": "BCD-11", "objective_text": "Backups are tested.", "expected_results": ""},
    ]
    return ControlContext(
        evidence_id="E-BCD-01",
        artifact_title="Backup reports",
        artifact_description="Nightly backup job output",
        area_of_focus="Resilience",
        controls=[{"scf_id": "BCD-11", "control_name": "Data Backups", "control_description": "Create recurring backups."}],
        context_hash="c" * 64,
        framework_version="2026.1",
        objectives=objectives,
        objectives_capped=False,
        objective_rigor=rigor or {"BCD-11_A01": 1, "BCD-11_A02": 3},
    )


def _prompt_file(file_id="f1", **overrides) -> dict:
    base = {
        "file_id": file_id,
        "filename": "backup.json",
        "content_type": "application/json",
        "source": "AzureBackup",
        "uploaded_at": "2026-09-10T02:00:00",
        "text": '{"job": "ok"}',
        "truncated": False,
        "effective_period_start": None,
        "effective_period_end": None,
        "membership_rule": "uploaded_at",
        "represents": [],
    }
    base.update(overrides)
    return base


def _build(files=None, expected=None, collection=None, omitted=None, ctx=None):
    files = files if files is not None else [_prompt_file()]
    return build_window_assessment_prompt(
        control_context=ctx or _context(),
        window_start=WINDOW_START.isoformat(),
        window_end=WINDOW_END.isoformat(),
        frequency_used="monthly",
        files=files,
        expected_artifact_types=expected or [],
        source_coverage={"AzureBackup": len(files)},
        artifact_type_coverage={e["type"]: {"present": False, "file_count": 0} for e in (expected or [])},
        assessment_date="2026-09-16",
        collection=collection,
        omitted_files=omitted,
    )


def _win_file(uploaded_at=None, sha="deadbeef", text="{}", **kw) -> _FileInWindow:
    return _FileInWindow(
        id=uuid4(),
        filename="webhook_AzureBackup_x.json",
        s3_key="evidence/x.json",
        content_type="application/json",
        uploaded_at=uploaded_at or datetime(2026, 9, 10, 2, 0, 0),
        source_label="AzureBackup",
        extracted_text=text,
        sha256_hash=sha,
        content_hash=sha,
        **kw,
    )


def _answer(file_ids, designations=("appears_satisfied", "gap_identified"), **extra) -> str:
    body = {
        "relevance_score": 80,
        "status": "partial",
        "summary": "One objective shown, one not.",
        "ao_findings": [
            {"ao_id": "BCD-11_A01", "suggested_designation": designations[0], "rationale": "Job output shows nightly runs.", "suggestion": "", "evidence_file_ids": list(file_ids)},
            {"ao_id": "BCD-11_A02", "suggested_designation": designations[1], "rationale": "No restore test in the window.", "suggestion": "Add a restore-test collector.", "evidence_file_ids": []},
        ],
        "file_effective_dates": [
            {"file_id": file_ids[0], "evidence_effective_date": "2026-09-09", "effective_date_source": "job timestamp"}
        ] if file_ids else [],
        "findings": [],
    }
    body.update(extra)
    return json.dumps(body)


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

class TestVersioning:
    def test_window_prompt_has_its_own_version(self):
        assert WINDOW_PROMPT_VERSION != PROMPT_VERSION

    def test_window_schema_version_is_two(self):
        assert WINDOW_SCHEMA_VERSION == 2

    def test_window_hash_is_salted_with_the_window_version_not_the_per_file_one(self):
        source = inspect.getsource(svc._compute_window_hash)
        assert "WINDOW_PROMPT_VERSION" in source
        assert "PROMPT_VERSION]" not in source.replace("WINDOW_PROMPT_VERSION", "")

    def test_service_does_not_import_the_per_file_version(self):
        assert not hasattr(svc, "PROMPT_VERSION")

    def test_membership_rule_participates_in_the_hash(self):
        f = _win_file()
        a = _compute_window_hash("E", WINDOW_START, WINDOW_END, [f])
        f.membership_rule = MEMBERSHIP_ASSERTED_PERIOD
        b = _compute_window_hash("E", WINDOW_START, WINDOW_END, [f])
        assert a != b


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

class TestPromptAssembly:
    def test_objectives_and_ao_ids_are_rendered(self):
        _system, user = _build()
        assert "## Assessment Objectives" in user
        assert "BCD-11_A01" in user and "BCD-11_A02" in user
        assert "There are 2 to answer" in user

    def test_rigor_is_tagged_per_objective_and_the_scale_is_explained(self):
        _system, user = _build()
        assert "**BCD-11_A01** _(AR 1)_" in user
        assert "**BCD-11_A02** _(AR 3)_" in user
        assert "## Assessment Rigor Scale" in user
        for level, (name, _meaning) in ASSESSMENT_RIGOR_SCALE.items():
            assert f"AR {level} — {name}" in user

    def test_per_file_prompt_block_is_untouched_by_rigor_when_not_passed(self):
        block = _assessment_objectives_block(_context().objectives, False)
        assert "(AR" not in block

    def test_collection_block_renders_method_and_system(self):
        _system, user = _build(collection={"method_of_collection": "Azure Backup webhook", "collecting_system": "Azure Recovery Services"})
        assert "## How This Evidence Is Collected" in user
        assert "**Method of collection:** Azure Backup webhook" in user
        assert "**Collecting system:** Azure Recovery Services" in user

    def test_collection_block_says_so_when_nothing_is_recorded(self):
        _system, user = _build(collection={"method_of_collection": None, "collecting_system": ""})
        assert "Not recorded for this evidence item" in user

    def test_truncation_is_disclosed_per_file(self):
        files = [_prompt_file("f1", truncated=True), _prompt_file("f2", truncated=False)]
        _system, user = _build(files=files)
        assert "file f1 is shown only in part" in user
        assert "file f2 is shown only in part" not in user

    def test_membership_reason_and_asserted_period_are_shown(self):
        f = _prompt_file("f1", membership_rule="asserted_period", effective_period_start="2026-09-01", effective_period_end="2026-09-30", uploaded_at="2026-10-20T00:00:00")
        _system, user = _build(files=[f])
        assert "Preparer-asserted effective period:** 2026-09-01 → 2026-09-30" in user
        assert "asserted effective period overlaps the window" in user
        assert "not a date extracted from the document" in user

    def test_representative_names_the_files_it_stands_for(self):
        f = _prompt_file("f1", represents=["f2", "f3"])
        _system, user = _build(files=[f])
        assert "Identical content was also uploaded as:** f2, f3" in user

    def test_file_ids_are_offered_for_attribution(self):
        _system, user = _build(files=[_prompt_file("f1"), _prompt_file("f2")])
        assert "Use only these ids: f1, f2" in user

    def test_omitted_files_are_listed_and_counted(self):
        _system, user = _build(files=[_prompt_file("f1")], omitted=[{"file_id": "f9", "filename": "old.json", "source": "AzureBackup", "uploaded_at": "2026-08-13T00:00:00"}])
        assert "## Files Counted but Not Shown" in user
        assert "id=f9" in user
        assert "**Files in window:** 2 (1 shown below)" in user

    def test_no_artifact_type_sections_when_catalog_publishes_none(self):
        _system, user = _build(expected=[])
        assert "## Expected Artifact Types" not in user
        assert "## Artifact Type Coverage" not in user
        assert "Not extracted" not in user
        assert "do not invent an artifact-type checklist" in user

    def test_artifact_type_sections_present_when_catalog_publishes_them(self):
        _system, user = _build(expected=[{"type": "restore_test_result", "mandatory": True, "weight": "high", "description": "Proof of a restore"}])
        assert "## Expected Artifact Types" in user
        assert "restore_test_result: MISSING" in user

    def test_system_prompt_uses_advisory_vocabulary_only(self):
        system, _user = _build()
        assert "appears_satisfied" in system and "cannot_assess" in system
        assert "You are ADVISORY" in system

    def test_effective_date_instruction_forbids_copying_the_assertion(self):
        _system, user = _build()
        assert "do not copy it as the extracted date" in user

    def test_schema_requires_attributed_objective_answers_and_file_dates(self):
        assert "ao_findings" in WINDOW_ASSESSMENT_OUTPUT_SCHEMA["required"]
        assert "file_effective_dates" in WINDOW_ASSESSMENT_OUTPUT_SCHEMA["required"]
        ao_item = WINDOW_ASSESSMENT_OUTPUT_SCHEMA["properties"]["ao_findings"]["items"]
        assert "evidence_file_ids" in ao_item["required"]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestWindowParser:
    AO = ["BCD-11_A01", "BCD-11_A02"]
    FILES = ["f1", "f2"]

    def test_valid_answer_parses(self):
        parsed = parse_window_assessment_v2(_answer(["f1"]), "end_turn", self.AO, self.FILES)
        assert isinstance(parsed, ParsedWindowAssessment)
        assert parsed.ao_findings[0]["evidence_file_ids"] == ["f1"]
        assert parsed.ao_findings[1]["evidence_file_ids"] == []
        assert parsed.file_effective_dates == [
            {"file_id": "f1", "evidence_effective_date": "2026-09-09", "effective_date_source": "job timestamp"}
        ]
        assert parsed.designations == ["appears_satisfied", "gap_identified"]

    def test_cut_off_answer_is_refused_and_not_retryable(self):
        with pytest.raises(AssessmentParseError) as exc:
            parse_window_assessment_v2(_answer(["f1"])[:40], "max_tokens", self.AO, self.FILES)
        assert exc.value.retryable is False

    def test_invalid_json_is_refused(self):
        with pytest.raises(AssessmentParseError):
            parse_window_assessment_v2("not json {[", "end_turn", self.AO, self.FILES)

    def test_unknown_objective_is_refused(self):
        with pytest.raises(AssessmentParseError, match="not among"):
            parse_window_assessment_v2(_answer(["f1"]), "end_turn", ["BCD-11_A01", "BCD-11_A09"], self.FILES)

    def test_missing_objective_is_refused(self):
        with pytest.raises(AssessmentParseError, match="missing"):
            parse_window_assessment_v2(_answer(["f1"]), "end_turn", self.AO + ["BCD-12_A01"], self.FILES)

    def test_attribution_to_a_file_outside_the_window_is_refused(self):
        with pytest.raises(AssessmentParseError, match="not in the window"):
            parse_window_assessment_v2(_answer(["f7"]), "end_turn", self.AO, self.FILES)

    def test_missing_attribution_array_is_refused(self):
        body = json.loads(_answer(["f1"]))
        del body["ao_findings"][0]["evidence_file_ids"]
        with pytest.raises(AssessmentParseError, match="no evidence_file_ids"):
            parse_window_assessment_v2(json.dumps(body), "end_turn", self.AO, self.FILES)

    def test_effective_date_for_a_file_outside_the_window_is_refused(self):
        body = json.loads(_answer(["f1"]))
        body["file_effective_dates"] = [{"file_id": "f7", "evidence_effective_date": "2026-01-01"}]
        with pytest.raises(AssessmentParseError, match="not in the window"):
            parse_window_assessment_v2(json.dumps(body), "end_turn", self.AO, self.FILES)

    def test_malformed_date_degrades_to_null_with_a_recorded_complaint(self):
        body = json.loads(_answer(["f1"]))
        body["file_effective_dates"] = [{"file_id": "f1", "evidence_effective_date": "Sept 9"}]
        parsed = parse_window_assessment_v2(json.dumps(body), "end_turn", self.AO, self.FILES)
        assert parsed.file_effective_dates[0]["evidence_effective_date"] is None
        assert any("File f1" in f["message"] for f in parsed.findings)

    def test_off_contract_status_is_dropped_not_fatal(self):
        parsed = parse_window_assessment_v2(_answer(["f1"], status="excellent"), "end_turn", self.AO, self.FILES)
        assert parsed.model_status is None


# ---------------------------------------------------------------------------
# Membership rule
# ---------------------------------------------------------------------------

class TestMembershipRule:
    def _rule(self, uploaded_at=None, start=None, end=None):
        return _membership_rule(
            uploaded_at=uploaded_at, effective_period_start=start, effective_period_end=end,
            window_start=WINDOW_START, window_end=WINDOW_END,
        )

    def test_asserted_period_overlapping_window_is_selected(self):
        assert self._rule(uploaded_at=WINDOW_START + timedelta(days=3), start=date(2026, 9, 1), end=date(2026, 9, 30)) == MEMBERSHIP_ASSERTED_PERIOD

    def test_unasserted_file_uploaded_in_window_falls_back_to_upload_date(self):
        assert self._rule(uploaded_at=WINDOW_START + timedelta(days=3)) == MEMBERSHIP_UPLOADED_AT

    def test_asserted_in_window_but_uploaded_outside_is_included(self):
        assert self._rule(uploaded_at=WINDOW_END + timedelta(days=30), start=date(2026, 8, 20), end=date(2026, 8, 31)) == MEMBERSHIP_ASSERTED_PERIOD

    def test_uploaded_in_window_but_asserted_wholly_outside_is_excluded(self):
        assert self._rule(uploaded_at=WINDOW_START + timedelta(days=3), start=date(2025, 1, 1), end=date(2025, 12, 31)) is None

    def test_unasserted_file_uploaded_outside_is_excluded(self):
        assert self._rule(uploaded_at=WINDOW_START - timedelta(days=1)) is None

    def test_open_ended_start_only_assertion_extends_forward(self):
        assert self._rule(uploaded_at=WINDOW_START - timedelta(days=400), start=date(2024, 1, 1)) == MEMBERSHIP_ASSERTED_PERIOD

    def test_open_ended_end_only_assertion_extends_backward(self):
        assert self._rule(uploaded_at=WINDOW_END + timedelta(days=10), end=date(2026, 8, 15)) == MEMBERSHIP_ASSERTED_PERIOD
        assert self._rule(uploaded_at=WINDOW_END + timedelta(days=10), end=date(2026, 8, 1)) is None


# ---------------------------------------------------------------------------
# Text budget
# ---------------------------------------------------------------------------

class TestTextBudget:
    def test_twenty_two_identical_payloads_collapse_to_one_representative(self):
        files = [_win_file(uploaded_at=datetime(2026, 9, 15) - timedelta(days=i), sha="same") for i in range(22)]
        _plan_prompt_content(files)
        shown = [f for f in files if f.represented_by is None]
        assert len(shown) == 1
        assert shown[0] is files[0]  # newest is the representative
        assert all(f.represented_by == files[0].id for f in files[1:])

    def test_files_without_a_hash_are_never_collapsed(self):
        files = [_win_file(sha=None), _win_file(sha=None)]
        for f in files:
            f.content_hash = None
        _plan_prompt_content(files)
        assert all(f.represented_by is None for f in files)

    def test_per_file_cap_and_window_budget_both_bound_the_text(self):
        f = _win_file()
        cut, left = _apply_text_budget(f, "x" * (PER_FILE_TEXT_CAP + 5), WINDOW_TEXT_BUDGET)
        assert len(cut) == PER_FILE_TEXT_CAP and f.truncated is True
        assert left == WINDOW_TEXT_BUDGET - PER_FILE_TEXT_CAP
        g = _win_file()
        cut, left = _apply_text_budget(g, "y" * 500, 100)
        assert len(cut) == 100 and g.truncated is True and left == 0

    def test_untruncated_text_is_not_marked(self):
        f = _win_file()
        cut, _left = _apply_text_budget(f, "short", WINDOW_TEXT_BUDGET)
        assert cut == "short" and f.truncated is False

    def test_disclosure_names_duplicates_and_omissions(self):
        rep = _win_file()
        dup = _win_file(represented_by=rep.id)
        omitted = _win_file(omitted_reason="text_budget")
        finding = _text_budget_finding([rep, dup, omitted])
        assert finding["category"] == "coverage"
        assert str(dup.id) in finding["message"] and str(omitted.id) in finding["message"]
        assert finding["duplicate_file_ids"] == [str(dup.id)]
        assert finding["omitted_file_ids"] == [str(omitted.id)]
        assert f"{WINDOW_TEXT_BUDGET:,}" in finding["message"]

    def test_no_disclosure_when_everything_was_shown(self):
        assert _text_budget_finding([_win_file(), _win_file(sha="other")]) is None

    def test_membership_snapshot_records_rule_and_usage(self):
        rep = _win_file(membership_rule=MEMBERSHIP_ASSERTED_PERIOD, effective_period_start=date(2026, 9, 1), effective_period_end=None, truncated=True)
        dup = _win_file(represented_by=rep.id)
        snap = _membership_snapshot([rep, dup])
        assert snap[str(rep.id)]["rule"] == MEMBERSHIP_ASSERTED_PERIOD
        assert snap[str(rep.id)]["effective_period_start"] == "2026-09-01"
        assert snap[str(rep.id)]["in_prompt"] is True and snap[str(rep.id)]["truncated"] is True
        assert snap[str(dup.id)]["in_prompt"] is False
        assert snap[str(dup.id)]["represented_by"] == str(rep.id)

    def test_max_output_tokens_is_adaptive_thinking_safe(self):
        assert svc.MAX_OUTPUT_TOKENS == 32000


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------

def _parsed(designations, model_status="partial"):
    return ParsedWindowAssessment(
        summary="s", relevance_score=50.0, model_status=model_status,
        ao_findings=[{"ao_id": f"A{i}", "suggested_designation": d, "rationale": "", "suggestion": "", "evidence_file_ids": []} for i, d in enumerate(designations)],
        findings=[], file_effective_dates=[],
    )


class TestDeriveWindowStatus:
    def test_insufficient_sample_wins(self):
        status, reason, note = _derive_window_status(_parsed(["appears_satisfied"]), True)
        assert status == "insufficient_sample" and reason is None and note is None

    def test_status_is_derived_from_designations(self):
        status, reason, note = _derive_window_status(_parsed(["appears_satisfied", "appears_satisfied"], model_status="partial"), False)
        assert status == "sufficient" and reason is None
        assert note is not None and "derived from its own per-objective designations" in note["message"]

    def test_all_cannot_assess_is_unassessable_with_reason(self):
        status, reason, _note = _derive_window_status(_parsed(["cannot_assess", "cannot_assess"]), False)
        assert status == "unassessable" and reason

    def test_no_objectives_falls_back_to_the_model_status(self):
        status, _reason, note = _derive_window_status(_parsed([], model_status="sufficient"), False)
        assert status == "sufficient" and note is None

    def test_no_objectives_and_no_usable_status_records_partial_with_a_finding(self):
        status, _reason, note = _derive_window_status(_parsed([], model_status=None), False)
        assert status == "partial" and "pending human review" in note["message"]


# ---------------------------------------------------------------------------
# assess_window end to end against a stub session
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, **kw):
        self.id = kw.pop("id", uuid4())
        self.filename = kw.pop("filename", "webhook_AzureBackup_x.json")
        self.s3_key = kw.pop("s3_key", "evidence/x.json")
        self.content_type = kw.pop("content_type", "application/json")
        self.uploaded_at = kw.pop("uploaded_at", datetime.utcnow() - timedelta(days=2))
        self.sha256_hash = kw.pop("sha256_hash", "abc")
        self.computed_sha256 = kw.pop("computed_sha256", None)
        self.effective_period_start = kw.pop("effective_period_start", None)
        self.effective_period_end = kw.pop("effective_period_end", None)
        self.storage_config_id = None
        self.is_deleted = False
        assert not kw


class _Session:
    """Answers the three SELECTs assess_window issues, records writes.

    Calls four onward are the terminal-verdict writer (PR2): the version
    INSERT ... RETURNING answers ``first()`` with a version number, and the
    pointer UPDATE needs nothing back.
    """

    def __init__(self, tracking, file_rows):
        self.tracking = tracking
        self.file_rows = file_rows
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.expired = []
        self.calls = 0
        self.statements = []

    def execute(self, stmt, params=None):
        self.calls += 1
        self.statements.append((stmt, params))
        result = MagicMock()
        if self.calls == 1:
            result.scalar_one_or_none.return_value = self.tracking
        elif self.calls == 2:
            result.scalars.return_value.all.return_value = self.file_rows
        elif self.calls == 3:
            result.scalar_one_or_none.return_value = None
        else:
            result.first.return_value = (1,)
        return result

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def expire(self, obj, attribute_names=None):
        self.expired.append((obj, tuple(attribute_names or ())))


class _Extracted:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def stubbed(monkeypatch):
    downloads = []

    def fake_download(s3_key, org_id=None, storage_config_id=None):
        downloads.append(s3_key)
        return b"{}"

    monkeypatch.setattr(svc, "download_evidence_bytes", fake_download)
    monkeypatch.setattr(svc, "extract_text_from_bytes", lambda data, content_type, filename: _Extracted('{"job": "ok"}'))
    monkeypatch.setattr(svc, "_build_expected_artifact_types", lambda session, evidence_id: [])
    monkeypatch.setattr(svc, "_fetch_webhook_sources_for_files", lambda session, ids: {})
    monkeypatch.setattr(svc, "_prior_review_reference", lambda session, org, ev: None)
    monkeypatch.setattr(svc, "assemble_control_context_sync", lambda session, evidence_id: _context())
    monkeypatch.setattr(svc, "resolve_model", lambda role: "claude-test")
    monkeypatch.setattr(svc, "model_cost_cents", lambda model, i, o: 0.1)
    return downloads


def _tracking():
    t = MagicMock()
    t.frequency = "monthly"
    t.method_of_collection = "Azure Backup webhook"
    t.collecting_system = "Azure Recovery Services"
    return t


class TestAssessWindowEndToEnd:
    def test_verdict_lands_with_objectives_counts_membership_and_schema(self, stubbed):
        rows = [_Row(), _Row(sha256_hash="def", uploaded_at=datetime.utcnow() - timedelta(days=5))]
        captured = {}

        def fake_llm(system_prompt, user_prompt):
            captured["user"] = user_prompt
            return {"content": _answer([str(rows[0].id)]), "model": "claude-test", "input_tokens": 10, "output_tokens": 5, "stop_reason": "end_turn"}

        with patch.object(svc, "_call_llm", fake_llm):
            session = _Session(_tracking(), rows)
            result = svc.assess_window(session, organization_id=uuid4(), evidence_id="E-BCD-01", assessment_source="ingest")

        assert result.status == "partial"
        assert result.schema_version == WINDOW_SCHEMA_VERSION
        assert result.prompt_version == WINDOW_PROMPT_VERSION
        assert [a["ao_id"] for a in result.ao_findings] == ["BCD-11_A01", "BCD-11_A02"]
        assert result.ao_findings[0]["evidence_file_ids"] == [str(rows[0].id)]
        assert result.gap_count == 1 and result.cannot_assess_count == 0
        assert result.file_effective_dates[0]["evidence_effective_date"] == "2026-09-09"
        assert set(result.file_membership) == {str(r.id) for r in rows}
        assert result.file_membership[str(rows[0].id)]["rule"] == MEMBERSHIP_UPLOADED_AT
        assert result.assessment_source == "ingest"
        assert "Azure Backup webhook" in captured["user"]
        assert "BCD-11_A02" in captured["user"]

    def test_cut_off_answer_is_recorded_as_error_with_no_verdict(self, stubbed):
        rows = [_Row()]
        fake_llm = lambda s, u: {"content": '{"relevance_score": 8', "model": "claude-test", "input_tokens": 10, "output_tokens": 32000, "stop_reason": "max_tokens"}
        with patch.object(svc, "_call_llm", fake_llm):
            result = svc.assess_window(_Session(_tracking(), rows), organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status == "error"
        assert result.ao_findings == [] and result.gap_count == 0
        assert result.relevance_score is None
        assert "cut off at the token ceiling" in result.summary
        assert result.model_id == "claude-test"
        assert result.prompt_version == WINDOW_PROMPT_VERSION
        assert result.schema_version == WINDOW_SCHEMA_VERSION

    def test_invalid_answer_is_recorded_as_error_not_coerced(self, stubbed):
        rows = [_Row()]
        fake_llm = lambda s, u: {"content": '{"status": "sufficient", "findings": []}', "model": "claude-test", "input_tokens": 1, "output_tokens": 1, "stop_reason": "end_turn"}
        with patch.object(svc, "_call_llm", fake_llm):
            result = svc.assess_window(_Session(_tracking(), rows), organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status == "error"
        assert "ao_findings must be an array" in result.summary

    def test_twenty_two_identical_payloads_are_downloaded_once_and_disclosed(self, stubbed):
        rows = [_Row(sha256_hash="same", s3_key=f"evidence/{i}.json", uploaded_at=datetime.utcnow() - timedelta(hours=i)) for i in range(22)]
        captured = {}

        def fake_llm(system_prompt, user_prompt):
            captured["user"] = user_prompt
            return {"content": _answer([str(rows[0].id)]), "model": "claude-test", "input_tokens": 10, "output_tokens": 5, "stop_reason": "end_turn"}

        with patch.object(svc, "_call_llm", fake_llm):
            result = svc.assess_window(_Session(_tracking(), rows), organization_id=uuid4(), evidence_id="E-BCD-01")

        assert stubbed == ["evidence/0.json"]
        assert len(result.file_ids) == 22
        in_prompt = [fid for fid, m in result.file_membership.items() if m["in_prompt"]]
        assert in_prompt == [str(rows[0].id)]
        disclosure = [f for f in result.findings if f.get("duplicate_file_ids")]
        assert len(disclosure) == 1 and len(disclosure[0]["duplicate_file_ids"]) == 21
        assert captured["user"].count("### File ") == 1
        assert "21 more file(s)" in captured["user"]

    def test_window_text_budget_omits_files_and_discloses_them(self, stubbed, monkeypatch):
        monkeypatch.setattr(svc, "WINDOW_TEXT_BUDGET", 20)
        rows = [_Row(sha256_hash=f"h{i}", s3_key=f"evidence/{i}.json", uploaded_at=datetime.utcnow() - timedelta(hours=i)) for i in range(3)]
        with patch.object(svc, "_call_llm", lambda s, u: {"content": _answer([str(rows[0].id)]), "model": "m", "input_tokens": 1, "output_tokens": 1, "stop_reason": "end_turn"}):
            result = svc.assess_window(_Session(_tracking(), rows), organization_id=uuid4(), evidence_id="E-BCD-01")
        # First file consumes the 20-char budget (its 13 chars), second is cut
        # to the 7 that remain, third is omitted entirely.
        m = result.file_membership
        assert m[str(rows[0].id)]["in_prompt"] and not m[str(rows[0].id)]["truncated"]
        assert m[str(rows[1].id)]["in_prompt"] and m[str(rows[1].id)]["truncated"]
        assert m[str(rows[2].id)]["omitted_reason"] == "text_budget"
        assert stubbed == ["evidence/0.json", "evidence/1.json"]
        disclosure = [f for f in result.findings if f.get("omitted_file_ids")]
        assert disclosure[0]["omitted_file_ids"] == [str(rows[2].id)]

    def test_asserted_period_file_uploaded_outside_the_window_is_included_with_its_reason(self, stubbed):
        rows = [_Row(uploaded_at=datetime.utcnow() + timedelta(days=60), effective_period_start=date.today() - timedelta(days=10), effective_period_end=date.today() - timedelta(days=3))]
        with patch.object(svc, "_call_llm", lambda s, u: {"content": _answer([str(rows[0].id)]), "model": "m", "input_tokens": 1, "output_tokens": 1, "stop_reason": "end_turn"}):
            result = svc.assess_window(_Session(_tracking(), rows), organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.file_ids == [str(rows[0].id)]
        assert result.file_membership[str(rows[0].id)]["rule"] == MEMBERSHIP_ASSERTED_PERIOD

    def test_no_files_path_still_stamps_the_window_version_and_schema(self, stubbed):
        result = svc.assess_window(_Session(_tracking(), []), organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status == "insufficient_sample"
        assert result.schema_version == WINDOW_SCHEMA_VERSION
        assert result.prompt_version == WINDOW_PROMPT_VERSION
        assert result.file_membership == {}
