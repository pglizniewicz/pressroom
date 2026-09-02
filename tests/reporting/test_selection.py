"""The trailer that says which capture the archive walk did not take.

Not an eighth outcome and not a marker - the seven are fixed. This is the
`decoding.REPAIRS` mechanism a second time: a collector the fetch appends to,
drained by `Stats.summary()` at the end of a run, silent when the default
answer held.
"""

import io
import contextlib
import unittest

from pressroom.reporting.entity import selection
from pressroom.reporting.entity.outcome import Stats


def summary(source="src"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        Stats(source).summary()
    return out.getvalue()


def note(url="http://x/1", taken="20130411000000", passed="20120607000000", **kw):
    selection.note(
        url,
        taken=taken,
        taken_score=kw.get("taken_score", 1204),
        passed=passed,
        passed_score=kw.get("passed_score", 915),
        why=kw.get("why", selection.LATER_IS_BETTER),
    )


class TrailerTest(unittest.TestCase):
    def setUp(self):
        selection.OVERRIDES.clear()
        self.addCleanup(selection.OVERRIDES.clear)

    def test_it_is_silent_when_nothing_was_overridden(self):
        """The common case by construction, so it has to cost no output at
        all - the same contract as the encoding-repair line above it."""
        self.assertNotIn("inna kopia", summary())

    def test_it_names_the_row_both_captures_and_both_scores(self):
        """The four things the report exists to answer: which row, what we
        took, what we passed over, and why."""
        note()
        text = summary()
        for wanted in (
            "http://x/1",
            "20130411000000",
            "1204",
            "20120607000000",
            "915",
            selection.LATER_IS_BETTER,
        ):
            self.assertIn(wanted, text)

    def test_an_unreachable_probe_reads_differently_from_a_losing_one(self):
        """A probe we could not fetch has no score, and printing `0` for it
        would say we looked and found nothing - which is a different claim."""
        note(passed_score=None, why=selection.PROBE_UNREACHABLE)
        text = summary()
        self.assertIn("blad", text)
        self.assertIn(selection.PROBE_UNREACHABLE, text)

    def test_the_list_is_capped_but_the_header_counts_them_all(self):
        for n in range(selection.CAP + 5):
            note(url=f"http://x/{n}")
        text = summary()
        self.assertIn(f"inna kopia niz domyslna: {selection.CAP + 5}", text)
        self.assertIn("... i 5 wiecej", text)
        self.assertNotIn(f"http://x/{selection.CAP}", text)

    def test_a_second_summary_says_nothing_because_the_first_drained_it(self):
        """`catch_up()` builds a fresh Stats per strategy and summarises several
        times per source; without the drain the third would reprint the
        first's rows."""
        note()
        self.assertIn("inna kopia", summary())
        self.assertNotIn("inna kopia", summary())


if __name__ == "__main__":
    unittest.main()
