"""When evidence was collected, versus when it happened to be uploaded (#789).

Two defects sat in `EvidenceTracking.last_collection_date` before this:

  1. The webhook inbox stamped it on ingest and the browser-upload path did not,
     so the health dashboard and the maturity engine agreed about webhook
     evidence and disagreed about everything a person uploaded.
  2. Stamping `today` on upload claims an old document was collected today.

The rule these tests pin: prefer the asserted effective period end, fall back to
the upload date, and never move the column backwards.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.collection_date import (  # noqa: E402
    advance_last_collection_date,
    collection_date_from,
)


def tracker(last: date | None):
    t = MagicMock()
    t.last_collection_date = last
    return t


class TestWhichDateIsTheCollectionDate:

    def test_the_asserted_period_end_wins_over_the_upload_date(self):
        """The headline case, and the reason this function exists.

        A quarterly access review is exported and uploaded on 2 April. It was
        collected *for* the quarter that ended on 31 March. Dating it 2 April
        overstates the programme's freshness by the whole reporting gap — every
        quarter, forever, in the same direction.
        """
        assert collection_date_from(
            date(2026, 3, 31), uploaded_at=datetime(2026, 4, 2, 9, 0),
        ) == date(2026, 3, 31)

    def test_the_upload_date_is_used_when_nothing_was_asserted(self):
        """No change for the files already in every tenant."""
        assert collection_date_from(
            None, uploaded_at=datetime(2026, 4, 2, 9, 0),
        ) == date(2026, 4, 2)

    def test_a_plain_date_upload_stamp_is_accepted_as_well_as_a_datetime(self):
        assert collection_date_from(None, uploaded_at=date(2026, 4, 2)) == date(2026, 4, 2)

    def test_with_neither_signal_it_falls_back_to_today_rather_than_none(self):
        """The column is not nullable in practice — a caller needs a date."""
        assert collection_date_from(None, uploaded_at=None) == date.today()

    def test_an_asserted_period_in_the_future_is_still_honoured(self):
        """Not this function's judgement to make.

        A period ending in the future is odd, but it is what the preparer
        asserted, and silently substituting the upload date would be inventing a
        different claim. If future periods should be refused, that belongs at the
        point of assertion, not here.
        """
        assert collection_date_from(
            date(2027, 12, 31), uploaded_at=datetime(2026, 4, 2),
        ) == date(2027, 12, 31)


class TestTheColumnOnlyMovesForward:

    def test_a_later_date_advances_the_tracker(self):
        t = tracker(date(2026, 1, 31))
        assert advance_last_collection_date(t, date(2026, 3, 31)) is True
        assert t.last_collection_date == date(2026, 3, 31)

    def test_back_filling_old_evidence_does_not_un_run_the_control(self):
        """Uploading last year's paperwork during an audit is normal, and must
        not make a live programme report as though it had stopped collecting."""
        t = tracker(date(2026, 6, 30))
        assert advance_last_collection_date(t, date(2025, 3, 31)) is False
        assert t.last_collection_date == date(2026, 6, 30)

    def test_the_same_date_twice_is_not_a_change(self):
        t = tracker(date(2026, 6, 30))
        assert advance_last_collection_date(t, date(2026, 6, 30)) is False

    def test_a_first_collection_sets_the_column_from_null(self):
        t = tracker(None)
        assert advance_last_collection_date(t, date(2020, 1, 1)) is True
        assert t.last_collection_date == date(2020, 1, 1)

    def test_no_tracker_is_a_no_op_rather_than_an_error(self):
        """Evidence can be uploaded against a catalog item nobody is tracking."""
        assert advance_last_collection_date(None, date(2026, 6, 30)) is False
