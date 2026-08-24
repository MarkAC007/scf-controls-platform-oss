"""Shared helpers for tests that build mock evidence files.

Three separate modules construct a ``MagicMock`` evidence file and hand it to
``api.evidence_files._to_response``. MagicMock invents an attribute for
anything asked of it, and pydantic will not accept a ``Mock`` where an
``Optional[date]`` is declared — so every column added to ``EvidenceFile``
breaks all three at once, for a reason that has nothing to do with what any of
them is testing.

Keeping the fix in one place means a thirteenth preparer-assertion column is a
one-line edit to ``PREPARER_ASSERTION_FIELDS`` and nothing else.

A plain module rather than a conftest fixture on purpose: a fixture of this
name would be in scope for every unrelated test module in the suite, and this
is a helper, not a piece of test state.
"""
from services.preparer_assertions import PREPARER_ASSERTION_FIELDS


def unasserted(mock_file):
    """Put a mock evidence file in the state most evidence is actually in: nothing asserted.

    Setting the columns to None explicitly keeps the default honest — these
    fixtures describe files nobody asserted anything about, which is exactly
    what "not asserted" means.
    """
    for field in PREPARER_ASSERTION_FIELDS:
        setattr(mock_file, field, None)
    return mock_file
