"""Regression tests for the v0.26.0 ThinkingBlock production incident.

Opus 5 runs adaptive thinking by default, so a Messages response leads with a
ThinkingBlock and `content[0].text` raises AttributeError. These tests pin the
failure shape with the real SDK block types and prove every consumer goes
through the shared extractor.
"""

import sys
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ThinkingBlock

from services.anthropic_response import NoTextBlockError, extract_text


def _thinking(text="reasoning..."):
    return ThinkingBlock(type="thinking", thinking=text, signature="sig")


def _text(text):
    return TextBlock(type="text", text=text)


class TestExtractText:
    def test_thinking_block_first_returns_the_text(self):
        """The exact production failure: thinking block ahead of the answer."""
        msg = SimpleNamespace(content=[_thinking(), _text('{"status": "ok"}')])
        assert extract_text(msg) == '{"status": "ok"}'

    def test_text_only_response_unchanged(self):
        """Sonnet-shaped responses (no thinking) keep working."""
        msg = SimpleNamespace(content=[_text("plain answer")])
        assert extract_text(msg) == "plain answer"

    def test_multiple_text_blocks_join_in_order(self):
        msg = SimpleNamespace(content=[_text("part one, "), _thinking(), _text("part two")])
        assert extract_text(msg) == "part one, part two"

    def test_no_text_block_raises_named_error_with_block_kinds(self):
        msg = SimpleNamespace(content=[_thinking()], stop_reason="max_tokens")
        with pytest.raises(NoTextBlockError) as exc:
            extract_text(msg)
        assert "thinking" in str(exc.value)
        assert "max_tokens" in str(exc.value)

    def test_old_first_block_read_still_fails_without_helper(self):
        """Documents WHY the helper exists; if the SDK ever grows .text on
        ThinkingBlock this stops guarding anything and can be retired."""
        msg = SimpleNamespace(content=[_thinking(), _text("x")])
        with pytest.raises(AttributeError):
            msg.content[0].text


class TestCallSitesUseTheHelper:
    """The class sweep, as a test: no direct content[0].text may come back."""

    CALL_SITE_FILES = (
        "tasks_assessment.py",
        "services/window_assessment_service.py",
        "services/artifact_type_extraction_service.py",
    )

    def test_no_first_block_reads_in_model_calling_code(self):
        from pathlib import Path

        backend_root = Path(__file__).resolve().parents[1]
        offenders = [
            rel
            for rel in self.CALL_SITE_FILES
            if "content[0].text" in (backend_root / rel).read_text()
        ]
        assert not offenders, (
            f"{offenders} read message.content[0].text directly — that broke "
            "production when Opus 5 (thinking on by default) shipped; use "
            "services.anthropic_response.extract_text"
        )


class TestTasksAssessmentWiring:
    """End-to-end through _call_llm with a thinking-first fake SDK."""

    def test_call_llm_survives_thinking_first_response(self, monkeypatch):
        import tasks_assessment as ta

        answer = '{"status": "sufficient"}'
        fake_message = SimpleNamespace(
            content=[_thinking(), _text(answer)],
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return fake_message

        class _Messages:
            def stream(self, **kwargs):
                # Streaming is required: large max_tokens ceilings make the
                # SDK refuse non-streaming requests, and Opus 5's thinking
                # tokens are spent inside max_tokens.
                assert kwargs["max_tokens"] >= 32000
                return _Stream()

        class _Client:
            def __init__(self, api_key):
                self.messages = _Messages()

        monkeypatch.setitem(
            sys.modules, "anthropic", SimpleNamespace(Anthropic=_Client)
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        result = ta._call_llm("system", "user")
        assert result["content"] == answer
        assert result["model"] == "claude-opus-5"
