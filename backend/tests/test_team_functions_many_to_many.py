"""Contract tests for a team serving multiple business functions."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from models import Function, Team, team_functions
from schemas import TeamCreate, TeamUpdate


def test_team_model_exposes_the_join_table_relationship():
    assert team_functions.c.team_id.foreign_keys
    assert team_functions.c.function_id.foreign_keys
    assert Team.functions.property.secondary is team_functions
    assert Function.teams.property.secondary is team_functions


def test_create_accepts_multiple_functions_including_the_primary():
    primary, secondary = uuid4(), uuid4()
    payload = TeamCreate(
        name="Platform",
        description=None,
        function_id=primary,
        function_ids=[primary, secondary],
    )
    assert payload.function_ids == [primary, secondary]


def test_create_rejects_a_primary_outside_the_served_set():
    with pytest.raises(ValidationError, match="must include function_id"):
        TeamCreate(
            name="Platform",
            description=None,
            function_id=uuid4(),
            function_ids=[uuid4()],
        )


def test_update_rejects_an_empty_served_set():
    with pytest.raises(ValidationError):
        TeamUpdate(function_ids=[])
