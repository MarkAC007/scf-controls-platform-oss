"""Regression for #1036: custom risk codes must advance past R-ORG-10.

The generator used a string max over ``risk_code``; once R-ORG-1..R-ORG-10
existed the max was "R-ORG-9", so every further create produced R-ORG-10 and
hit the unique constraint. The suffix must be compared numerically.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.custom_risks import _next_risk_code, _next_risk_number


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self.rows)


def test_next_number_empty_org_starts_at_one():
    assert _next_risk_number([]) == 1


def test_next_number_advances_past_ten():
    codes = [f"R-ORG-{n}" for n in range(1, 11)]
    assert _next_risk_number(codes) == 11


def test_next_number_is_numeric_not_lexicographic():
    # "R-ORG-9" sorts above "R-ORG-100" as a string; numerically 100 wins.
    assert _next_risk_number(["R-ORG-9", "R-ORG-100", "R-ORG-42"]) == 101


def test_next_number_ignores_gaps_and_malformed_codes():
    assert _next_risk_number(["R-ORG-3", "R-ORG-7", "R-ORG-", "junk"]) == 8


@pytest.mark.asyncio
async def test_next_risk_code_eleventh_is_r_org_11():
    db = _FakeSession([f"R-ORG-{n}" for n in range(1, 11)])
    assert await _next_risk_code(uuid4(), db) == "R-ORG-11"


@pytest.mark.asyncio
async def test_next_risk_code_first_is_r_org_1():
    assert await _next_risk_code(uuid4(), _FakeSession([])) == "R-ORG-1"
