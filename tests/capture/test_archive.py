"""Which capture of a url the archive walk picks, and when it says so.

The regression this file exists for: `get_latest_working_snapshot` returned the
newest HTTP-200 capture, and for a dead press release on a rebuilt domain that
is the modern site's shell page. It parses to nothing, `discovery.fetch_detail_snapshot`
called that a confirmed verdict, and phase 2 recorded `dead` on a row whose
article was sitting in an older capture all along.

No CDX and no network: `list_all_captures` and `fetch_snapshot` are patched, so
what is under test is the ordering, the two probes and the `confirmed` rule.
"""

import io
import contextlib
import unittest
from unittest import mock

from pressroom.capture.control import archive
from pressroom.reporting.entity import selection

URL = "http://www.m-audio.com/news/en_us-1738.html"

# The measured pair this change exists for, kept where it can fail: the 2012
# capture is the article, the 2024 one is 40 KB of rebuilt m-audio.com that
# parses to nothing at all.
ARTICLE = b"x" * 915
SHELL = b"y" * 40772


def score_letters(content: bytes) -> int:
    """Points for `x` bytes only, so a bigger capture made of `y` scores zero -
    the shape of the trap, since the shell page is the larger file."""
    return content.count(b"x")


class WalkCase(unittest.TestCase):
    def setUp(self):
        selection.OVERRIDES.clear()
        self.addCleanup(selection.OVERRIDES.clear)
        patcher = mock.patch("time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, captures, *, listing_error=None, errors=()):
        """`captures` is {timestamp: bytes}; `errors` are timestamps whose fetch
        raises. Records every timestamp actually fetched."""
        self.fetched = []

        def list_all_captures(url, retries=3):
            if listing_error:
                raise listing_error
            return sorted(captures)

        def fetch_snapshot(conn, session, url, timeout=20):
            ts = url.split("/web/", 1)[1].split("id_/", 1)[0]
            self.fetched.append(ts)
            if ts in errors:
                raise OSError("connection reset")
            return captures[ts]

        for name, fn in (
            ("list_all_captures", list_all_captures),
            ("fetch_snapshot", fetch_snapshot),
        ):
            patcher = mock.patch.object(archive, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def walk(self, score=score_letters):
        with contextlib.redirect_stdout(io.StringIO()):
            return archive.fetch_best_matching_snapshot(None, None, URL, score)


class ChoiceTest(WalkCase):
    def test_the_earliest_capture_that_parses_wins_over_the_newest(self):
        """The regression. The newest capture answers HTTP 200 and is the bigger
        file; it is also not the article."""
        self.install({"20120607000000": ARTICLE, "20240320000000": SHELL})
        content, ts, confirmed = self.walk()
        self.assertEqual((content, ts, confirmed), (ARTICLE, "20120607000000", True))

    def test_the_earliest_wins_a_tie_and_says_nothing(self):
        """A probe that only matches the default does not move it, and a
        decision that was not made is not worth a line."""
        self.install({"20110101000000": ARTICLE, "20130101000000": ARTICLE})
        _, ts, _ = self.walk()
        self.assertEqual(ts, "20110101000000")
        self.assertEqual(selection.OVERRIDES, [])

    def test_every_capture_is_tried_before_a_dead_verdict(self):
        """Nine shells and an article behind them. This passed only when the
        six-attempt cap came off."""
        captures = {f"201{n}0101000000": SHELL for n in range(9)}
        captures["20200101000000"] = ARTICLE
        self.install(captures)
        _, ts, confirmed = self.walk()
        self.assertEqual((ts, confirmed), ("20200101000000", True))


class VerdictTest(WalkCase):
    def test_no_captures_at_all_is_a_confirmed_absence(self):
        self.install({})
        self.assertEqual(self.walk(), (None, None, True))

    def test_nothing_usable_anywhere_still_names_the_earliest_capture(self):
        """No content, but a timestamp: captures of this url exist and none
        scored. `from_candidates` turns that into a `stub`, where a url the
        archive never saw at all is `dead` and no row - so the two cases may not
        come back looking the same."""
        self.install({"20110101000000": SHELL, "20200101000000": SHELL})
        self.assertEqual(self.walk(), (None, "20110101000000", True))

    def test_a_cdx_failure_is_not_a_verdict(self):
        self.install({}, listing_error=OSError("connection refused"))
        self.assertEqual(self.walk(), (None, None, False))

    def test_a_fetch_error_makes_the_verdict_unconfirmed(self):
        """An untried capture might have been the article, so this may not be
        recorded as `dead`."""
        self.install(
            {"20110101000000": SHELL, "20200101000000": SHELL},
            errors={"20110101000000"},
        )
        content, _, confirmed = self.walk()
        self.assertEqual((content, confirmed), (None, False))

    def test_a_truncated_cdx_listing_is_not_a_confirmed_absence(self):
        """CDX_ROW_LIMIT rows back means the list was cut, so the walk has not
        seen everything and cannot call an absence confirmed."""
        captures = {f"2{n:013d}": SHELL for n in range(archive.CDX_ROW_LIMIT)}
        self.install(captures)
        content, _, confirmed = self.walk()
        self.assertEqual((content, confirmed), (None, False))


class YearProbeTest(WalkCase):
    def test_the_probe_replaces_a_capture_taken_too_early(self):
        """The earliest capture caught the page before it had settled."""
        self.install({"20110101000000": b"x" * 120, "20120401000000": b"x" * 3000})
        _, ts, _ = self.walk()
        self.assertEqual(ts, "20120401000000")
        (record,) = selection.OVERRIDES
        self.assertEqual(
            (record.taken, record.taken_score, record.passed, record.passed_score),
            ("20120401000000", 3000, "20110101000000", 120),
        )
        self.assertEqual(record.why, selection.LATER_IS_BETTER)

    def test_the_probe_is_the_first_capture_a_year_out_not_the_next_one(self):
        """What LATER_PROBE_YEARS means, which its name alone cannot say: the
        September capture is skipped because it is in the winner's own year."""
        self.install(
            {
                "20110101000000": ARTICLE,
                "20110901000000": ARTICLE,
                "20120401000000": ARTICLE,
                "20130101000000": ARTICLE,
            }
        )
        self.walk()
        self.assertIn("20120401000000", self.fetched)
        self.assertNotIn("20110901000000", self.fetched)

    def test_no_capture_a_year_out_means_no_year_probe(self):
        """Every capture is inside the winner's own year, so only the last-good
        probe has anywhere to go - and it ties, so nothing is reported."""
        self.install({"20110101000000": ARTICLE, "20110901000000": ARTICLE})
        _, ts, _ = self.walk()
        self.assertEqual(ts, "20110101000000")
        self.assertEqual(selection.OVERRIDES, [])

    def test_a_probe_that_cannot_be_fetched_is_reported_but_still_confirmed(self):
        """ "We could not check" is not "we checked", and it is the case a human
        most wants to see."""
        self.install(
            {"20110101000000": ARTICLE, "20130101000000": ARTICLE},
            errors={"20130101000000"},
        )
        _, ts, confirmed = self.walk()
        self.assertEqual((ts, confirmed), ("20110101000000", True))
        (record,) = selection.OVERRIDES
        self.assertEqual(record.why, selection.PROBE_UNREACHABLE)
        self.assertIsNone(record.passed_score)


class LastProbeTest(WalkCase):
    def test_the_last_good_capture_wins_when_the_release_was_corrected_later(self):
        """The hole one probe leaves: a correction published onto a site that
        then died has no capture a year out, but does have a last one."""
        self.install({"20110101000000": b"x" * 900, "20110901000000": b"x" * 2600})
        _, ts, _ = self.walk()
        self.assertEqual(ts, "20110901000000")
        (record,) = selection.OVERRIDES
        self.assertEqual(record.why, selection.NEWEST_IS_BETTER)

    def test_the_last_probe_walks_back_past_a_shell_page(self):
        """ "The last good one", not "the last one that answered" - the whole
        failure is that the shell page answers 200 and is not an error."""
        self.install(
            {
                "20110101000000": b"x" * 900,
                "20110601000000": b"x" * 2600,
                "20240101000000": SHELL,
            }
        )
        _, ts, _ = self.walk()
        self.assertEqual(ts, "20110601000000")

    def test_the_probes_never_fetch_the_same_capture_twice(self):
        """Probe 1 walks the head, probe 3 the tail past it, so the two together
        cover the list once even when nothing on the page ever validates."""
        self.install({"20110101000000": SHELL, "20200101000000": SHELL})
        self.walk()
        self.assertEqual(sorted(self.fetched), ["20110101000000", "20200101000000"])


if __name__ == "__main__":
    unittest.main()
