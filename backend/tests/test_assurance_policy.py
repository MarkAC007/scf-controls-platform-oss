"""Per-org assurance policy resolution (#787, ISC-69/70).

The absence of a policy row is the common case — every existing
organization has one — so "no row" must resolve to today's behaviour
rather than to an error or to a stricter default.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.assurance_policy import (  # noqa: E402
    DEFAULT_ASSURANCE_POLICY,
    AssurancePolicy,
    get_assurance_policy,
)


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.calls = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        return _Result(self.row)


@pytest.mark.asyncio
class TestPolicyResolution:
    async def test_missing_row_is_todays_behaviour(self):
        policy = await get_assurance_policy(_Session(None), uuid4())
        assert policy == DEFAULT_ASSURANCE_POLICY

    async def test_row_values_are_honoured(self):
        row = SimpleNamespace(
            require_evidence_attestation=True,
            require_reviewer_independence=True,
        )
        policy = await get_assurance_policy(_Session(row), uuid4())
        assert policy.require_evidence_attestation is True
        assert policy.require_reviewer_independence is True

    async def test_the_two_settings_are_independent(self):
        row = SimpleNamespace(
            require_evidence_attestation=True,
            require_reviewer_independence=False,
        )
        policy = await get_assurance_policy(_Session(row), uuid4())
        assert policy.require_evidence_attestation is True
        assert policy.require_reviewer_independence is False

    async def test_non_boolean_column_values_are_coerced(self):
        # A driver that hands back 0/1 must not make `if policy.x` read
        # correctly while `is True` reads wrong.
        row = SimpleNamespace(
            require_evidence_attestation=1,
            require_reviewer_independence=0,
        )
        policy = await get_assurance_policy(_Session(row), uuid4())
        assert policy.require_evidence_attestation is True
        assert policy.require_reviewer_independence is False

    async def test_resolution_is_a_single_query(self):
        session = _Session(None)
        await get_assurance_policy(session, uuid4())
        assert session.calls == 1


class TestPolicyValue:
    def test_equality_is_by_value(self):
        assert AssurancePolicy() == DEFAULT_ASSURANCE_POLICY

    def test_a_stricter_policy_is_not_the_default(self):
        assert AssurancePolicy(require_evidence_attestation=True) != DEFAULT_ASSURANCE_POLICY
