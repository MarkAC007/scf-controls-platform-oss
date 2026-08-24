"""Runtime feature-flag endpoint (#787, ISC-80).

The webclient compiles ``VITE_ENABLE_PER_WINDOW_REVIEW`` in at build time;
the backend reads ``ENABLE_PER_WINDOW_REVIEW`` from its environment. Nothing
connected the two, so a deploy that set one and not the other left a UI and
an API disagreeing about which review workflow exists — silently, and in
the direction where the user is left with no way to review anything.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import features  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(features.router, prefix="/api")
    return TestClient(app)


class TestDefaults:
    def test_all_flags_default_off(self, client, monkeypatch):
        for name in (
            "ENABLE_PER_WINDOW_REVIEW",
            "ENABLE_WINDOW_ASSESSMENT_KSI",
            "ENABLE_COMPOSITE_KSI",
        ):
            monkeypatch.delenv(name, raising=False)
        assert client.get("/api/features").json() == {
            "per_window_review": False,
            "window_assessment_ksi": False,
            "composite_ksi": False,
        }


class TestReporting:
    @pytest.mark.parametrize(
        "env_name,key",
        [
            ("ENABLE_PER_WINDOW_REVIEW", "per_window_review"),
            ("ENABLE_WINDOW_ASSESSMENT_KSI", "window_assessment_ksi"),
            ("ENABLE_COMPOSITE_KSI", "composite_ksi"),
        ],
    )
    def test_each_flag_is_reported_independently(
        self, client, monkeypatch, env_name, key
    ):
        for name in (
            "ENABLE_PER_WINDOW_REVIEW",
            "ENABLE_WINDOW_ASSESSMENT_KSI",
            "ENABLE_COMPOSITE_KSI",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(env_name, "true")
        body = client.get("/api/features").json()
        assert body[key] is True
        assert all(v is False for k, v in body.items() if k != key)

    def test_value_is_case_insensitive(self, client, monkeypatch):
        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "TRUE")
        assert client.get("/api/features").json()["per_window_review"] is True

    def test_a_non_true_value_is_off_not_an_error(self, client, monkeypatch):
        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "yes")
        assert client.get("/api/features").json()["per_window_review"] is False

    def test_flags_are_read_per_request_not_cached_at_import(
        self, client, monkeypatch
    ):
        # A container restarted with a changed value must report the new
        # one. Caching at import would make the endpoint report the state
        # of the world at boot, which is precisely the staleness it exists
        # to detect.
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "false")
        assert client.get("/api/features").json()["composite_ksi"] is False
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        assert client.get("/api/features").json()["composite_ksi"] is True


class TestPayloadShape:
    def test_reports_only_deployment_flags(self, client):
        # No org scope, nothing tenant-specific: this endpoint must never
        # become a place where customer state leaks out unauthenticated.
        body = client.get("/api/features").json()
        assert set(body) == {
            "per_window_review",
            "window_assessment_ksi",
            "composite_ksi",
        }

    def test_values_are_booleans_not_strings(self, client, monkeypatch):
        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "true")
        assert all(isinstance(v, bool) for v in client.get("/api/features").json().values())
