"""Tests for the engagement-query status lifecycle.

Increment 4 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

open -> answered (on response) -> closed (auditor satisfied), with reopen allowed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.engagement_queries import (  # noqa: E402
    status_after_response,
    is_valid_query_transition,
)


def test_response_moves_open_to_answered():
    assert status_after_response("open") == "answered"


def test_response_keeps_answered_answered():
    assert status_after_response("answered") == "answered"


def test_response_does_not_reopen_a_closed_query():
    # A closed query must be explicitly reopened before it can be answered again.
    assert status_after_response("closed") == "closed"


def test_valid_manual_transitions():
    assert is_valid_query_transition("open", "closed") is True
    assert is_valid_query_transition("open", "answered") is True
    assert is_valid_query_transition("answered", "closed") is True
    assert is_valid_query_transition("answered", "open") is True
    assert is_valid_query_transition("closed", "open") is True  # reopen


def test_invalid_or_noop_transitions_rejected():
    assert is_valid_query_transition("closed", "answered") is False  # must reopen first
    assert is_valid_query_transition("open", "open") is False        # no-op
    assert is_valid_query_transition("open", "bogus") is False       # unknown target
