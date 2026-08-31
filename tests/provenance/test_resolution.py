"""Which bytes a row's body came out of, and whether they are its own page's.

`own_page()` is the function that picks the gate. Handing a listing capture to a
whole-page parser cost 64 rows, so "is this a capture of the row's own url" is
not an inline comparison anywhere.
"""

from pressroom.capture.control import address
from pressroom.provenance.control import resolution
from pressroom.provenance.entity import origin
from tests import support

TS = "20111011173713"
ROW_URL = "http://www.terratec.de/September_2011_DAB_151650.html"
OWN = address.snapshot_url(TS, ROW_URL)
LISTING = address.snapshot_url(TS, "http://www.terratec.de/presse.html")


class OriginKeyTest(support.DbCase):
    def test_falls_back_to_the_derivation_when_nothing_is_recorded(self):
        self.assertEqual(resolution.origin_key(self.conn, ROW_URL, TS), OWN)

    def test_the_recorded_entry_wins(self):
        """The derivation can only get the 158 listing-derived rows wrong: their
        detail_id is the *listing's* timestamp, honest and not enough."""
        origin.record(self.conn, ROW_URL, LISTING)
        self.assertEqual(resolution.origin_key(self.conn, ROW_URL, TS), LISTING)

    def test_empty_for_a_row_with_no_capture_at_all(self):
        self.assertEqual(
            resolution.origin_key(self.conn, "https://intc.com/x", "970"), ""
        )


class OwnPageTest(support.DbCase):
    def test_true_for_a_capture_of_the_row_s_own_url(self):
        self.assertTrue(resolution.own_page(self.conn, ROW_URL, TS))

    def test_false_once_a_listing_is_recorded(self):
        """#4414: the capture was never lost - `web/<ts>/…/presse.html` holds
        that release's full text - but the address built from the timestamp and
        the row's own url is a page archive.org has no capture of at all."""
        origin.record(self.conn, ROW_URL, LISTING)
        self.assertFalse(resolution.own_page(self.conn, ROW_URL, TS))

    def test_false_when_there_is_no_capture_to_speak_of(self):
        self.assertFalse(resolution.own_page(self.conn, "https://intc.com/x", "970"))


class CachedTest(support.DbCase):
    def test_returns_the_bytes_and_none_for_a_missing_key(self):
        self.cache(OWN, b"<html>the release</html>")
        self.assertEqual(resolution.cached(self.conn, OWN), b"<html>the release</html>")
        self.assertIsNone(resolution.cached(self.conn, LISTING))
        self.assertIsNone(resolution.cached(self.conn, ""))


class PageOfTest(support.DbCase):
    def test_splits_on_the_marker_every_key_carries(self):
        self.assertEqual(origin.page_of(LISTING), "http://www.terratec.de/presse.html")

    def test_the_report_stays_readable_when_there_is_no_marker(self):
        self.assertIsNone(origin.page_of("http://plain/url"))
        self.assertEqual(resolution.page_of("http://plain/url"), "http://plain/url")


class ClearTest(support.DbCase):
    def test_an_entry_is_dropped_the_moment_it_stops_being_true(self):
        """`twin.fill` is the caller: the text it writes came out of a *sibling
        row*, so whatever capture was recorded has stopped describing it."""
        origin.record(self.conn, ROW_URL, LISTING)
        origin.clear(self.conn, ROW_URL)
        self.assertEqual(resolution.origin_key(self.conn, ROW_URL, TS), OWN)

    def test_clearing_a_url_with_no_entry_is_a_no_op(self):
        origin.clear(self.conn, "http://never/seen")
