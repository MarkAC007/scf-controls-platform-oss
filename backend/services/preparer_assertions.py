"""The preparer assertion field set, named once (#786, #802).

Twelve columns arrived together on `evidence_files`, and five places have to
agree on exactly which twelve they are: the model, the confirm request schema,
the response schema, the record built at confirm, and the audit trail's tracked
fields. Spelling them out five times is how a thirteenth column gets added to
four of them.

This is a tuple, not a set, and the order matches the declaration order in
`models.py`. Anything iterating it to build a form or a diff reads the fields
in the order a preparer thinks about them — period, then population, then
sample, then IPE — rather than in whatever order a hash happened to produce.

A guard test asserts this tuple against the ORM columns, so a column added to
the model without being added here fails the suite rather than going quietly
missing from the API and the audit log.
"""
from typing import Tuple

#: Order matters — see the module note.
PREPARER_ASSERTION_FIELDS: Tuple[str, ...] = (
    "effective_period_start",
    "effective_period_end",
    "population_size",
    "population_source",
    "sample_size",
    "sample_method",
    "sample_basis",
    "ipe_source_system",
    "ipe_query_or_filter",
    "ipe_extracted_by_user_id",
    "ipe_extracted_at",
    "ipe_completeness_check",
)


def has_any_assertion(evidence_file) -> bool:
    """True when the preparer asserted at least one thing about this artefact.

    Used to tell "nothing was asserted" from "something was asserted and the
    rest was left blank" — two states that must not render the same way. A file
    with a declared population and no sample is a partially-supported claim; a
    file with nothing at all is an unsupported one, and the difference is the
    whole reason these columns are nullable.
    """
    return any(getattr(evidence_file, field, None) is not None for field in PREPARER_ASSERTION_FIELDS)
