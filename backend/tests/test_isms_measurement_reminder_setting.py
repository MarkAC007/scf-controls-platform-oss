# SPDX-License-Identifier: AGPL-3.0-only
"""The measurement reminder's timing is an organisation setting.

``isms_measurement_reminder_days``: the reminder for a logged metric goes out
once at most that many days of the period remain, today counted, and never
before the period starts. Absent or not a whole number from 1 to 366 reads as
14. The rule is pure; the sweep's use of it is covered against a database in
``test_isms_objective_reminders.py``.
"""
import inspect
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import catalog_models  # noqa: E402,F401 - registers mappers referenced by models
from schemas import OrganizationSettingsResponse, OrganizationSettingsUpdate  # noqa: E402
from services.isms_metrics import status as status_rules  # noqa: E402
from services.isms_metrics.reminders import (  # noqa: E402
    DEFAULT_MEASUREMENT_REMINDER_DAYS,
    MEASUREMENT_REMINDER_DAYS_KEY as KEY,
    measurement_window_open,
    resolve_measurement_reminder_days,
)


@pytest.mark.parametrize("frequency,day,expected", [
    ("monthly", date(2026, 11, 18), date(2026, 11, 30)),
    ("monthly", date(2026, 12, 1), date(2026, 12, 31)),
    ("monthly", date(2028, 2, 29), date(2028, 2, 29)),
    ("monthly", date(2027, 2, 1), date(2027, 2, 28)),
    ("quarterly", date(2026, 11, 18), date(2026, 12, 31)),
    ("quarterly", date(2026, 1, 1), date(2026, 3, 31)),
    ("quarterly", date(2026, 6, 30), date(2026, 6, 30)),
    ("annually", date(2026, 2, 2), date(2026, 12, 31)),
    ("nightly", date(2026, 2, 2), None),
    (None, date(2026, 2, 2), None),
])
def test_period_end_is_the_last_day_of_the_calendar_period(frequency, day, expected):
    assert status_rules.period_end(frequency, day) == expected


@pytest.mark.parametrize("frequency,day,lead,expected", [
    # Quarter ending 31 December, 14 days: opens on 18 December.
    ("quarterly", date(2026, 12, 17), 14, False),
    ("quarterly", date(2026, 12, 18), 14, True),
    ("quarterly", date(2026, 12, 31), 14, True),
    ("quarterly", date(2026, 11, 18), 14, False),
    # One day: the last day of the period only.
    ("monthly", date(2026, 11, 29), 1, False),
    ("monthly", date(2026, 11, 30), 1, True),
    # A lead as long as the period or longer is the first day of the period, never earlier.
    ("monthly", date(2026, 11, 1), 30, True),
    ("monthly", date(2026, 12, 1), 30, False),
    ("monthly", date(2026, 12, 1), 366, True),
    ("quarterly", date(2026, 10, 1), 366, True),
    ("annually", date(2028, 1, 1), 366, True),
    ("annually", date(2026, 1, 1), 365, True),
    ("annually", date(2028, 1, 1), 365, False),  # a leap year has 366 days
    # No calendar period: nothing to wait for.
    (None, date(2026, 11, 18), 1, True),
    ("nightly", date(2026, 11, 18), 14, True),
])
def test_the_window_opens_once_at_most_that_many_days_of_the_period_remain(frequency, day, lead, expected):
    assert measurement_window_open(frequency, day, lead) is expected


@pytest.mark.parametrize("settings", [None, {}, {"industry": "Fintech"}, "not a dict", []])
def test_the_setting_defaults_to_fourteen_days_when_absent(settings):
    assert DEFAULT_MEASUREMENT_REMINDER_DAYS == 14
    assert resolve_measurement_reminder_days(settings) == 14


@pytest.mark.parametrize("stored", [0, -3, 367, 14.0, "14", "7", None, True, False, [14], {"days": 14}])
def test_a_stored_value_that_is_not_a_whole_number_from_1_to_366_reads_as_the_default(stored):
    assert resolve_measurement_reminder_days({KEY: stored}) == 14


@pytest.mark.parametrize("stored", [1, 7, 14, 30, 366])
def test_a_valid_stored_value_is_used(stored):
    assert resolve_measurement_reminder_days({KEY: stored}) == stored


class TestSettingsEndpoint:
    def _db(self, org):
        db = AsyncMock()
        db.add = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=org)
        db.execute = AsyncMock(return_value=result)
        return db

    def _org(self, settings):
        return SimpleNamespace(id=uuid4(), name="Acme Widgets", settings=settings)

    async def _patch(self, org, **changes):
        from api.organizations import update_organization_settings

        membership = MagicMock()
        membership.user.db_id = str(uuid4())
        with patch("api.organizations.log_entity_changes", new=AsyncMock()) as audit:
            out = await update_organization_settings(
                org_id=org.id, settings_data=OrganizationSettingsUpdate(**changes), request=MagicMock(),
                membership=membership, db=self._db(org),
            )
        return out, audit

    def test_the_response_defaults_to_fourteen_and_the_update_is_optional(self):
        assert OrganizationSettingsResponse().isms_measurement_reminder_days == 14
        assert KEY not in OrganizationSettingsUpdate(industry="x").model_dump(exclude_unset=True)

    @pytest.mark.asyncio
    async def test_get_returns_the_stored_days_and_the_default_when_none_is_stored(self):
        from api.organizations import get_organization_settings

        for settings, expected in (({KEY: 30}, 30), ({}, 14), ({KEY: "soon"}, 14)):
            org = self._org(settings)
            out = await get_organization_settings(org_id=org.id, membership=MagicMock(), db=self._db(org))
            assert out.isms_measurement_reminder_days == expected

    @pytest.mark.asyncio
    async def test_patch_stores_the_days_writes_the_audit_row_and_leaves_other_settings_alone(self):
        org = self._org({"industry": "Fintech", "owner_teams": ["GRC"]})
        out, audit = await self._patch(org, isms_measurement_reminder_days=366)
        assert out.isms_measurement_reminder_days == 366
        assert org.settings == {"industry": "Fintech", "owner_teams": ["GRC"], KEY: 366}
        assert audit.await_args.kwargs["new_values"]["settings"][KEY] == 366
        assert KEY not in audit.await_args.kwargs["old_values"]["settings"]

    @pytest.mark.asyncio
    async def test_a_patch_that_does_not_send_it_keeps_the_stored_days(self):
        org = self._org({KEY: 7})
        out, _ = await self._patch(org, industry="Health")
        assert (out.isms_measurement_reminder_days, org.settings[KEY]) == (7, 7)

    @pytest.mark.parametrize("value", [0, -1, 367, 1000, 14.5, "14", True])
    def test_a_value_that_is_not_a_whole_number_from_1_to_366_is_refused(self, value):
        with pytest.raises(ValidationError):
            OrganizationSettingsUpdate(isms_measurement_reminder_days=value)

    @pytest.mark.parametrize("value", [1, 14, 366])
    def test_the_ends_of_the_range_are_accepted(self, value):
        assert OrganizationSettingsUpdate(isms_measurement_reminder_days=value).isms_measurement_reminder_days == value

    def test_only_an_admin_may_change_it_and_any_member_may_read_it(self):
        """The routes' own role gates: PATCH admin, GET viewer."""
        from api import organizations

        def role_of(handler):
            dependency = inspect.signature(handler).parameters["membership"].default.dependency
            return [c.cell_contents for c in dependency.__closure__ if isinstance(c.cell_contents, str)]

        assert role_of(organizations.update_organization_settings) == ["admin"]
        assert role_of(organizations.get_organization_settings) == ["viewer"]
