"""``field_name`` must fit its column, whatever a caller composes.

``AuditLog.field_name`` is a bounded ``varchar``, and several callers build the
value out of data with no length bound. The document section routes are the
sharp case: the name is ``section:<section_id>:<action>`` and a section id is
derived from heading text, so a long heading in a Statement of Applicability
produces a name the column cannot hold. Postgres does not truncate -- it raises
``StringDataRightTruncationError``, which aborts the surrounding transaction.
The user's edit is lost because the *audit* of it did not fit.
"""
import pytest

from services.audit_service import (
    MAX_FIELD_NAME_LENGTH,
    clamp_field_name,
)


class TestClampFieldName:
    def test_a_short_name_is_returned_untouched(self):
        assert clamp_field_name("status") == "status"

    def test_none_stays_none(self):
        # Most audit entries name no field at all.
        assert clamp_field_name(None) is None

    def test_a_name_exactly_at_the_limit_is_untouched(self):
        name = "a" * MAX_FIELD_NAME_LENGTH
        assert clamp_field_name(name) == name

    def test_an_over_long_name_fits_the_column(self):
        clamped = clamp_field_name("b" * (MAX_FIELD_NAME_LENGTH * 3))
        assert len(clamped) <= MAX_FIELD_NAME_LENGTH

    def test_two_different_long_names_stay_distinguishable(self):
        # A plain truncation would collapse these into one another, and the
        # audit trail would claim two different fields were the same field.
        prefix = "section:" + "x" * MAX_FIELD_NAME_LENGTH
        first = clamp_field_name(prefix + ":resolve")
        second = clamp_field_name(prefix + ":edit")
        assert first != second
        assert len(first) <= MAX_FIELD_NAME_LENGTH
        assert len(second) <= MAX_FIELD_NAME_LENGTH

    def test_the_readable_head_survives(self):
        # The point of clamping rather than dropping the name: someone reading
        # the trail can still tell what kind of thing was changed.
        clamped = clamp_field_name("section:" + "y" * MAX_FIELD_NAME_LENGTH)
        assert clamped.startswith("section:")

    def test_clamping_is_deterministic(self):
        name = "section:" + "z" * MAX_FIELD_NAME_LENGTH
        assert clamp_field_name(name) == clamp_field_name(name)

    def test_a_clamped_name_is_visibly_marked(self):
        clamped = clamp_field_name("q" * (MAX_FIELD_NAME_LENGTH + 1))
        assert "~" in clamped

    def test_the_limit_is_read_from_the_model_not_hardcoded(self):
        from models import AuditLog

        assert MAX_FIELD_NAME_LENGTH == AuditLog.field_name.type.length


@pytest.mark.parametrize("length", [1, 50, MAX_FIELD_NAME_LENGTH - 1,
                                    MAX_FIELD_NAME_LENGTH,
                                    MAX_FIELD_NAME_LENGTH + 1,
                                    MAX_FIELD_NAME_LENGTH * 10])
def test_no_input_length_ever_exceeds_the_column(length):
    assert len(clamp_field_name("n" * length)) <= MAX_FIELD_NAME_LENGTH
