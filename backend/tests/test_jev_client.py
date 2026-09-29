"""Unit tests for services.jev_client — the TypeSafe System One HTTP client.

No network. The transport is a fake ``httpx`` module installed for the test,
so these cover exactly the two traps the module docstring names (answer
values are keyed by question TYPE; every asked question must be answered and
nothing else), the 429/529 backoff, and the no-key path.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import jev_client as jc  # noqa: E402


QUESTIONS = {
    "AO0001": {"type": "choice", "instructions": "x", "criteria": {"a": "A", "b": "B"}},
    "__relevance__": {"type": "score", "instructions": "y", "criteria": ["lo", "mid", "hi"]},
}


def _payload(**overrides):
    body = {
        "model": "jev-1.13.0",
        "answers": {
            "AO0001": {"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.9},
            "__relevance__": {"score": 1.6, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}, "confidence": 0.7},
        },
        "usage": {"input_tokens": 1200, "output_tokens": 0},
    }
    body.update(overrides)
    return body


class TestParseResponse:
    def test_lifts_the_type_named_value(self):
        parsed = jc.parse_response(_payload(), QUESTIONS, latency_ms=42)
        assert parsed.model == "jev-1.13.0"
        assert parsed.answers["AO0001"].value == "a"
        assert parsed.answers["AO0001"].type == "choice"
        assert parsed.answers["AO0001"].confidence == pytest.approx(0.9)
        assert parsed.answers["AO0001"].probabilities == {"a": 0.9, "b": 0.1}
        assert parsed.answers["__relevance__"].value == pytest.approx(1.6)
        assert parsed.input_tokens == 1200
        assert parsed.latency_ms == 42

    def test_missing_answer_is_refused(self):
        body = _payload()
        del body["answers"]["AO0001"]
        with pytest.raises(jc.JevResponseError, match="missing an answer for question 'AO0001'"):
            jc.parse_response(body, QUESTIONS, latency_ms=0)

    def test_extra_answer_is_refused(self):
        body = _payload()
        body["answers"]["AO9999"] = {"choice": "a"}
        with pytest.raises(jc.JevResponseError, match="not asked: AO9999"):
            jc.parse_response(body, QUESTIONS, latency_ms=0)

    def test_wrong_value_field_is_refused_not_read_as_none(self):
        # `.value` would silently be None; the client insists on the typed key.
        body = _payload()
        body["answers"]["AO0001"] = {"value": "a", "confidence": 0.9}
        with pytest.raises(jc.JevResponseError, match="has no 'choice' field"):
            jc.parse_response(body, QUESTIONS, latency_ms=0)

    def test_non_object_body_is_refused(self):
        with pytest.raises(jc.JevResponseError):
            jc.parse_response([], QUESTIONS, latency_ms=0)
        with pytest.raises(jc.JevResponseError, match="no 'answers'"):
            jc.parse_response({"model": "x"}, QUESTIONS, latency_ms=0)

    def test_unparseable_confidence_becomes_none(self):
        body = _payload()
        body["answers"]["AO0001"]["confidence"] = "high"
        parsed = jc.parse_response(body, QUESTIONS, latency_ms=0)
        assert parsed.answers["AO0001"].confidence is None


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _install_httpx(monkeypatch, responses):
    """A fake httpx whose post() pops the next scripted response."""
    calls = []
    fake = types.ModuleType("httpx")

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    fake.post = post
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return calls


class TestAsk:
    def test_no_key_is_unavailable_before_any_network_call(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: None)
        calls = _install_httpx(monkeypatch, [])
        with pytest.raises(jc.JevUnavailableError, match="TYPESAFE_API_KEY"):
            jc.ask("jev-1.13.0", {"x": 1}, QUESTIONS)
        assert calls == []

    @pytest.mark.parametrize("stand_in", ["changeme", "CHANGE_ME_typesafe", "   "])
    def test_placeholder_key_is_unavailable_not_sent(self, monkeypatch, stand_in):
        # The Settings card says "not configured" for these; the worker must
        # agree rather than send them and retry the resulting 401.
        monkeypatch.setattr(jc, "get_secret", lambda name: stand_in)
        calls = _install_httpx(monkeypatch, [])
        with pytest.raises(jc.JevUnavailableError, match="TYPESAFE_API_KEY"):
            jc.ask("jev-1.13.0", {"x": 1}, QUESTIONS)
        assert calls == []

    def test_posts_bearer_and_body_shape(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        calls = _install_httpx(monkeypatch, [_FakeResponse(200, _payload())])
        parsed = jc.ask("jev-1.13.0", {"doc": "text"}, QUESTIONS)
        assert parsed.answers["AO0001"].value == "a"
        (call,) = calls
        assert call["url"] == jc.DEFAULT_ENDPOINT
        assert call["headers"]["Authorization"] == "Bearer sk-test"
        assert call["json"] == {"model": "jev-1.13.0", "state": {"doc": "text"}, "questions": QUESTIONS}

    def test_endpoint_env_override(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        monkeypatch.setenv(jc.ENDPOINT_ENV, "https://proxy.example/v1/systemone")
        calls = _install_httpx(monkeypatch, [_FakeResponse(200, _payload())])
        jc.ask("jev-1.13.0", {}, QUESTIONS)
        assert calls[0]["url"] == "https://proxy.example/v1/systemone"

    @pytest.mark.parametrize("status", sorted(jc.RETRYABLE_STATUSES))
    def test_retries_with_backoff_on_rate_limit_and_overload(self, monkeypatch, status):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        calls = _install_httpx(monkeypatch, [
            _FakeResponse(status, text="slow down"),
            _FakeResponse(status, text="slow down"),
            _FakeResponse(200, _payload()),
        ])
        slept = []
        parsed = jc.ask("jev-1.13.0", {}, QUESTIONS, sleep=slept.append)
        assert parsed.answers["AO0001"].value == "a"
        assert len(calls) == jc.MAX_RETRIES + 1
        assert slept == [jc.BACKOFF_BASE_SECONDS, jc.BACKOFF_BASE_SECONDS * 2]

    def test_retry_budget_exhausted_raises_call_error(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        _install_httpx(monkeypatch, [_FakeResponse(429, text="x")] * (jc.MAX_RETRIES + 1))
        with pytest.raises(jc.JevCallError) as info:
            jc.ask("jev-1.13.0", {}, QUESTIONS, sleep=lambda s: None)
        assert info.value.status_code == 429

    def test_non_retryable_http_error_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        calls = _install_httpx(monkeypatch, [_FakeResponse(422, text='{"detail":"bad criteria"}')])
        with pytest.raises(jc.JevCallError) as info:
            jc.ask("jev-1.13.0", {}, QUESTIONS, sleep=lambda s: None)
        assert info.value.status_code == 422
        assert "bad criteria" in info.value.body
        assert len(calls) == 1

    def test_transport_failure_is_a_call_error(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        fake = types.ModuleType("httpx")

        def post(*a, **k):
            raise ConnectionError("dns")

        fake.post = post
        monkeypatch.setitem(sys.modules, "httpx", fake)
        with pytest.raises(jc.JevCallError, match="ConnectionError"):
            jc.ask("jev-1.13.0", {}, QUESTIONS)

    def test_non_json_200_is_a_response_error(self, monkeypatch):
        monkeypatch.setattr(jc, "get_secret", lambda name: "sk-test")
        _install_httpx(monkeypatch, [_FakeResponse(200, None, text="<html>")])
        with pytest.raises(jc.JevResponseError, match="non-JSON"):
            jc.ask("jev-1.13.0", {}, QUESTIONS)

    def test_empty_questions_is_a_programming_error(self):
        with pytest.raises(ValueError):
            jc.ask("jev-1.13.0", {}, {})
