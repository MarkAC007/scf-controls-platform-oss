"""When evidence was collected, as distinct from when it was uploaded (#789).

``EvidenceTracking.last_collection_date`` drives two things that matter: the
maturity engine's freshness score, and the task generator's next-due date. It is
therefore a claim about *the evidence*, not about the platform — "this control
was last exercised on this date".

Two problems were sitting in it before this module existed.

**A parity gap.** The webhook inbox stamped it on ingest; the browser-upload path
did not. So the health dashboard and the maturity engine agreed about evidence
collected by a webhook and disagreed about evidence a human uploaded, with no
principle distinguishing the two.

**A proxy.** Stamping ``today`` on upload says an old document was collected
today. Upload a report covering last quarter and the programme reads as freshly
collected — which is precisely the substitution of "when it arrived" for "what it
covers" that the preparer-assertion columns (#786) exist to end.

So the rule here is: prefer what the preparer asserted the evidence covers
through, fall back to the upload date only when nothing was asserted, and never
move the date backwards.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def collection_date_from(
    effective_period_end: Optional[date],
    uploaded_at: Optional[datetime] = None,
) -> date:
    """The date this evidence should be treated as having been collected on.

    The end of the asserted effective period if there is one — a quarterly
    access review exported on 2 April was collected *for* the quarter that ended
    on 31 March, and dating it 2 April overstates the programme's freshness by
    the whole gap. Otherwise the upload date, which is the only signal left.
    """
    if effective_period_end is not None:
        return effective_period_end
    if uploaded_at is not None:
        return uploaded_at.date() if isinstance(uploaded_at, datetime) else uploaded_at
    return date.today()


def advance_last_collection_date(tracker, candidate: date) -> bool:
    """Move ``tracker.last_collection_date`` forward to ``candidate``, if it is forward.

    Returns True when the tracker was changed.

    Monotonic on purpose. Back-filling last year's evidence is a normal thing to
    do during an audit, and it must not make a live programme report as though it
    had not collected anything since. The column answers "when did this control
    last run", and uploading old paperwork does not un-run it.
    """
    if tracker is None:
        return False
    current = tracker.last_collection_date
    if current is not None and candidate <= current:
        return False
    tracker.last_collection_date = candidate
    return True
