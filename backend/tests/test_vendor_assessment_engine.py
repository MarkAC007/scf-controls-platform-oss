"""
Unit tests for the native vendor AI assessment engine.

No real Anthropic API calls: live-path tests mock the anthropic SDK client;
mock-mode tests exercise the keyless deterministic path.
"""
import re
from types import SimpleNamespace

import pytest

from services.vendor_assessment_engine import (
    DEFAULT_MODEL,
    MAX_OUTPUT_TOKENS,
    VendorAssessmentError,
    build_mock_report,
    build_user_prompt,
    is_mock_mode,
    render_markdown,
    run_assessment,
    validate_report,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _valid_report(**overrides):
    report = build_mock_report("Acme Corp", "https://acme.example", "SSO", "Processor")
    report.update(overrides)
    return report


def _submission_block(report, block_id="toolu_submit_1"):
    return SimpleNamespace(type="tool_use", name="submit_assessment", id=block_id, input=report)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _search_result_block(urls):
    return SimpleNamespace(
        type="web_search_tool_result",
        tool_use_id="srvtoolu_1",
        content=[SimpleNamespace(type="web_search_result", url=u, title=u) for u in urls],
    )


def _response(content, stop_reason="tool_use"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class FakeAnthropicClient:
    """Minimal stand-in for anthropic.Anthropic with a scripted response queue."""

    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropicClient ran out of scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def live_env(monkeypatch):
    """Environment that selects the live (non-mock) path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.delenv("VENDOR_AI_MOCK", raising=False)
    monkeypatch.delenv("VENDOR_AI_MODEL", raising=False)


@pytest.fixture
def patch_client(monkeypatch):
    """Patch anthropic.Anthropic to return a scripted fake client."""
    import anthropic

    def _install(responses):
        client = FakeAnthropicClient(responses)
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **kw: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

class TestMockMode:
    def test_mock_flag_forces_mock_mode(self, monkeypatch):
        monkeypatch.setenv("VENDOR_AI_MOCK", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
        assert is_mock_mode() is True

    def test_missing_api_key_forces_mock_mode(self, monkeypatch):
        monkeypatch.delenv("VENDOR_AI_MOCK", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert is_mock_mode() is True

    def test_mock_mode_returns_valid_result_shape(self, monkeypatch):
        monkeypatch.setenv("VENDOR_AI_MOCK", "1")
        result = run_assessment(
            vendor_name="Acme Corp",
            vendor_description="Identity provider",
            vendor_website="https://acme.example",
            services_used="SSO and MFA",
            data_role="Processor",
            assessment_type="new",
            client_name="Demo Organization",
        )

        for key in (
            "report_json", "report_markdown", "rag_status", "recommendation",
            "risk_score", "risk_level", "executive_summary",
            "research_sources", "processing_time_ms",
        ):
            assert key in result, f"missing key: {key}"

        assert result["rag_status"] == "AMBER"
        assert result["recommendation"] == "CONDITIONAL_APPROVAL"
        assert isinstance(result["risk_score"], int)
        assert result["risk_score"] == result["report_json"]["residualRiskScore"]
        assert result["risk_level"] == "medium"  # residualRiskLevel lowercased
        assert validate_report(result["report_json"]) == []
        assert "Mock assessment — no AI call" in result["executive_summary"]
        assert "Acme Corp" in result["report_markdown"]
        assert isinstance(result["processing_time_ms"], int)
        assert result["research_sources"]  # populated from primarySources

    def test_mock_report_has_populated_controls_and_actions(self):
        report = build_mock_report("Acme Corp")
        assert report["confidentialityControls"]
        assert report["integrityControls"]
        assert report["availabilityControls"]
        assert report["mandatoryActions"]
        assert validate_report(report) == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_report_passes(self):
        assert validate_report(_valid_report()) == []

    @pytest.mark.parametrize("field,bad_value", [
        ("ragStatus", "ORANGE"),
        ("ragStatus", None),
        ("recommendation", "MAYBE"),
        ("inherentRiskLevel", "SEVERE"),
        ("residualRiskLevel", "medium"),  # lowercase is invalid at the LLM boundary
    ])
    def test_bad_enums_rejected(self, field, bad_value):
        problems = validate_report(_valid_report(**{field: bad_value}))
        assert problems, f"expected {field}={bad_value!r} to be rejected"
        assert any(field in p for p in problems)

    def test_missing_scores_rejected(self):
        report = _valid_report()
        report.pop("residualRiskScore")
        report.pop("inherentRiskScore")
        assert validate_report(report)

    def test_non_dict_rejected(self):
        assert validate_report("not a report") == ["report is not an object"]


# ---------------------------------------------------------------------------
# Live path: anthropic client wiring
# ---------------------------------------------------------------------------

class TestLivePath:
    def test_first_call_includes_web_search_tool(self, live_env, patch_client):
        client = patch_client([
            _response([_submission_block(_valid_report())]),
        ])
        result = run_assessment(
            vendor_name="Acme Corp",
            vendor_description="Identity provider",
            vendor_website="https://acme.example",
            services_used="SSO",
        )

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["model"] == DEFAULT_MODEL
        assert call["max_tokens"] == MAX_OUTPUT_TOKENS == 16384
        tool_types = {t.get("type") for t in call["tools"] if isinstance(t, dict)}
        assert "web_search_20250305" in tool_types
        web_search = next(t for t in call["tools"] if t.get("type") == "web_search_20250305")
        assert web_search["name"] == "web_search"
        assert web_search["max_uses"] == 8
        tool_names = {t.get("name") for t in call["tools"]}
        assert "submit_assessment" in tool_names
        # tool_choice must NOT be forced alongside the server-side web_search tool
        assert "tool_choice" not in call
        assert result["rag_status"] == "AMBER"

    def test_model_env_override(self, live_env, patch_client, monkeypatch):
        monkeypatch.setenv("VENDOR_AI_MODEL", "claude-test-model")
        client = patch_client([_response([_submission_block(_valid_report())])])
        run_assessment(
            vendor_name="Acme Corp",
            vendor_description="d",
            vendor_website="",
            services_used="s",
        )
        assert client.calls[0]["model"] == "claude-test-model"

    def test_web_search_citations_collected(self, live_env, patch_client):
        urls = ["https://trust.acme.example", "https://news.example/breach"]
        client = patch_client([
            _response(
                [_text_block("researching"), _search_result_block(urls)],
                stop_reason="end_turn",
            ),
            _response([_submission_block(_valid_report())]),
        ])
        result = run_assessment(
            vendor_name="Acme Corp",
            vendor_description="d",
            vendor_website="",
            services_used="s",
        )
        assert result["research_sources"] == urls
        # follow-up call nudges the model to submit; both tools stay available
        # (tool_choice cannot force a client tool alongside a server tool)
        second = client.calls[1]
        assert "tool_choice" not in second
        assert any(t.get("type") == "web_search_20250305" for t in second["tools"])
        nudge = second["messages"][-1]
        assert nudge["role"] == "user"
        assert "submit_assessment" in nudge["content"]

    def test_invalid_submission_retries_once_then_succeeds(self, live_env, patch_client):
        bad = _valid_report(ragStatus="ORANGE")
        good = _valid_report()
        client = patch_client([
            _response([_submission_block(bad, "toolu_bad")]),
            _response([_submission_block(good, "toolu_good")]),
        ])
        result = run_assessment(
            vendor_name="Acme Corp",
            vendor_description="d",
            vendor_website="",
            services_used="s",
        )
        assert len(client.calls) == 2
        # The retry request carries the is_error tool_result feedback
        retry_call = client.calls[1]
        last_user = retry_call["messages"][-1]
        assert last_user["role"] == "user"
        assert last_user["content"][0]["type"] == "tool_result"
        assert last_user["content"][0]["is_error"] is True
        assert "ragStatus" in last_user["content"][0]["content"]
        assert result["rag_status"] == "AMBER"

    def test_two_invalid_submissions_raise(self, live_env, patch_client):
        bad = _valid_report(ragStatus="ORANGE")
        patch_client([
            _response([_submission_block(bad, "toolu_1")]),
            _response([_submission_block(bad, "toolu_2")]),
        ])
        with pytest.raises(VendorAssessmentError):
            run_assessment(
                vendor_name="Acme Corp",
                vendor_description="d",
                vendor_website="",
                services_used="s",
            )

    def test_repeated_end_turn_without_submission_raises(self, live_env, patch_client):
        no_submit = _response([_text_block("done, here is my report as text")], stop_reason="end_turn")
        patch_client([no_submit, no_submit, no_submit])
        with pytest.raises(VendorAssessmentError):
            run_assessment(
                vendor_name="Acme Corp",
                vendor_description="d",
                vendor_website="",
                services_used="s",
            )

    def test_pause_turn_continues_conversation(self, live_env, patch_client):
        client = patch_client([
            _response([_text_block("searching...")], stop_reason="pause_turn"),
            _response([_submission_block(_valid_report())]),
        ])
        result = run_assessment(
            vendor_name="Acme Corp",
            vendor_description="d",
            vendor_website="",
            services_used="s",
        )
        assert len(client.calls) == 2
        # pause_turn continuation replays the assistant content without forcing
        second = client.calls[1]
        assert second["messages"][-1]["role"] == "assistant"
        assert "tool_choice" not in second
        assert result["recommendation"] == "CONDITIONAL_APPROVAL"


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

class TestMarkdownRenderer:
    SECTIONS = [
        "## 1. Executive Summary",
        "## 2. Vendor Overview",
        "## 3. Certification Status",
        "## 4. Breach History & Security Incidents",
        "## 5. CIA Triad Assessment",
        "## 6. Data Handling Assessment",
        "## 7. GDPR Compliance",
        "## 8. Supplier Evaluation Form Verification",
        "## 9. Risk Assessment",
        "## 10. Recommendation",
        "## 11. Sources",
        "## 12. Document Control",
    ]

    def test_renders_all_12_sections_in_order(self):
        md = render_markdown(_valid_report(), "Demo Organization", "new")
        positions = [md.find(section) for section in self.SECTIONS]
        assert all(p >= 0 for p in positions), (
            f"missing sections: {[s for s, p in zip(self.SECTIONS, positions) if p < 0]}"
        )
        assert positions == sorted(positions), "sections out of order"

    def test_header_and_footer_content(self):
        report = _valid_report()
        md = render_markdown(report, "Demo Organization", "annual-review")
        assert md.startswith("# Data Protection & Security Impact Assessment (DPSIA)")
        assert "## Vendor: Acme Corp" in md
        assert "Annual Review" in md
        assert "AMBER" in md
        assert "CONDITIONAL APPROVAL" in md
        assert "third-party risk management programme" in md

    def test_risk_and_action_details_rendered(self):
        report = _valid_report()
        md = render_markdown(report, "Client", "new")
        assert f"**Inherent Risk Score: {report['inherentRiskScore']} (MEDIUM)**" in md
        assert f"**{report['residualRiskScore']} (MEDIUM)**" in md
        assert "### Mandatory Actions" in md
        assert report["mandatoryActions"][0]["action"] in md

    def test_empty_breach_history_renders_placeholder(self):
        md = render_markdown(_valid_report(breachHistory=[]), "Client", "new")
        assert "No significant breach history identified." in md


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------

class TestUserPrompt:
    def test_prompt_includes_vendor_fields_and_research_context(self):
        prompt = build_user_prompt(
            vendor_name="Acme Corp",
            vendor_description="Identity provider",
            vendor_website="https://acme.example",
            services_used="SSO",
            data_role="Sub-processor",
            assessment_type="adhoc",
            client_name="Demo Organization",
            additional_context="Handles PII",
            research_context="HIBP: 2 breaches on record",
        )
        for expected in (
            "Acme Corp", "Identity provider", "https://acme.example", "SSO",
            "Sub-processor", "adhoc", "Demo Organization", "Handles PII",
            "HIBP: 2 breaches on record", "submit_assessment",
        ):
            assert expected in prompt
        assert re.search(r"\d{4}-\d{2}-\d{2}", prompt)

    def test_prompt_without_research_context_notes_absence(self):
        prompt = build_user_prompt(
            vendor_name="Acme Corp",
            vendor_description="d",
            vendor_website="",
            services_used="s",
            data_role="Processor",
            assessment_type="new",
            client_name="Client",
        )
        assert "No prior platform research available" in prompt
