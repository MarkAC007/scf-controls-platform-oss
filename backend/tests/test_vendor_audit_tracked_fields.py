# SPDX-License-Identifier: AGPL-3.0-only
"""
Vendor audit coverage (#1276).

``VENDOR_TRACKED_FIELDS`` listed ``risk_tier``, a name the ``Vendor`` model
does not have, and omitted ``criticality``. The vendor endpoints build their
audit diff with ``hasattr(vendor, f)`` over that set, so the stale name was
dropped silently and a criticality change wrote no audit entry at all.

These tests need no database.
"""
import uuid
from unittest.mock import MagicMock

import pytest

import catalog_models  # noqa: F401  (System.catalog_template resolves against it)
from models import Vendor
from services.audit_service import VENDOR_TRACKED_FIELDS, log_entity_changes


def _tracked(vendor: Vendor) -> dict:
    """The diff input exactly as ``api/vendors.py`` builds it."""
    return {f: getattr(vendor, f) for f in VENDOR_TRACKED_FIELDS if hasattr(vendor, f)}


def test_criticality_is_tracked():
    assert "criticality" in VENDOR_TRACKED_FIELDS


def test_every_tracked_vendor_field_is_a_vendor_column():
    """A name the model lacks is skipped by ``hasattr`` and never audited."""
    columns = set(Vendor.__table__.columns.keys())
    assert sorted(set(VENDOR_TRACKED_FIELDS) - columns) == []


@pytest.mark.asyncio
async def test_criticality_change_writes_an_update_entry():
    vendor = Vendor(name="Acme", status="prospect", criticality="low")
    old_values = _tracked(vendor)
    vendor.criticality = "critical"
    new_values = _tracked(vendor)

    entries = await log_entity_changes(
        db=MagicMock(), organization_id=uuid.uuid4(), entity_type="vendor",
        entity_id=uuid.uuid4(), action="update", changed_by_user_id=uuid.uuid4(),
        old_values=old_values, new_values=new_values,
        tracked_fields=VENDOR_TRACKED_FIELDS,
    )

    assert [(e.field_name, e.old_value, e.new_value) for e in entries] == [
        ("criticality", '"low"', '"critical"'),
    ]


@pytest.mark.asyncio
async def test_criticality_is_recorded_at_creation():
    vendor = Vendor(name="Acme", status="prospect", criticality="high")

    entries = await log_entity_changes(
        db=MagicMock(), organization_id=uuid.uuid4(), entity_type="vendor",
        entity_id=uuid.uuid4(), action="create", changed_by_user_id=uuid.uuid4(),
        old_values={}, new_values=_tracked(vendor),
        tracked_fields=VENDOR_TRACKED_FIELDS,
    )

    assert {e.field_name: e.new_value for e in entries}["criticality"] == '"high"'
