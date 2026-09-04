"""What a capture address looks like - the guard that stopped provenance from
minting dead links.

`is_capture_address` is what answers the fear that kept provenance in a separate
pass for a week: "passing a platform id by mistake would silently mint dead
links". Each rejection below is one of the values that used to be able to reach
that write.
"""

import unittest

from pressroom.fetcher.control import address

GOOD = (
    "https://web.archive.org/web/20111011173713id_/http://www.terratec.de/presse.html"
)


class IsCaptureAddressTest(unittest.TestCase):
    def test_accepts_the_only_shape_body_origin_may_hold(self):
        self.assertTrue(address.is_capture_address(GOOD))

    def test_rejects_every_value_that_used_to_reach_this_write(self):
        for value in (
            "970",  # a Q4 numeric id
            "node-4935591",  # a Drupal node id
            "http://www.terratec.de/presse.html",  # a bare row url
            # No `id_` marker: the viewer page, not the raw bytes - and not the
            # key page_cache stores anything under.
            "https://web.archive.org/web/20111011173713/http://www.terratec.de/x",
            "https://web.archive.org/web/2011101117371id_/http://x",  # 13 digits
            "",
            None,
            20111011173713,
        ):
            with self.subTest(value=value):
                self.assertFalse(address.is_capture_address(value))


class IsTimestampTest(unittest.TestCase):
    def test_exactly_fourteen_digits(self):
        self.assertTrue(address.is_timestamp("20111011173713"))
        for value in (
            "2011101117371",
            "201110111737133",
            "2011101117371a",
            "",
            None,
            "teaser",
        ):
            with self.subTest(value=value):
                self.assertFalse(address.is_timestamp(value))


class CaptureKeyTest(unittest.TestCase):
    def test_builds_the_page_cache_key(self):
        self.assertEqual(
            address.capture_key("20111011173713", "http://www.terratec.de/presse.html"),
            GOOD,
        )

    def test_empty_for_a_platform_id(self):
        """The live sources' detail_id is their own platform's id and never
        named a capture. An empty key is the answer, and what keeps a
        reader from building a link to a page that never existed."""
        self.assertEqual(address.capture_key("970", "https://www.intc.com/x"), "")
        self.assertEqual(address.capture_key(None, "https://www.intc.com/x"), "")

    def test_snapshot_url_keeps_the_id_marker(self):
        """`id_` asks archive.org for the original bytes with no toolbar
        injected; the browser strips it for a link meant for a human."""
        self.assertIn("id_/", address.snapshot_url("20030421210545", "http://x/y.pdf"))


ROW = "http://www.midiman.net/images/press/BX5_PR.pdf"
OWN = "https://web.archive.org/web/20030212170800id_/" + ROW
MIRROR = (
    "https://web.archive.org/web/20030421210545id_/"
    "http://www.m-audio.com/images/press/BX5_PR.pdf"
)


class TimestampOfTest(unittest.TestCase):
    def test_the_link_is_labelled_with_the_capture_the_link_opens(self):
        """#4984's detail_id is the listing's 20030212170800, its capture is
        20030421210545. Labelling the link with the row's detail_id names a
        capture the href does not go to."""
        self.assertEqual(address.timestamp_of(MIRROR), "20030421210545")
        self.assertIsNone(address.timestamp_of(None))
        self.assertIsNone(address.timestamp_of("https://example.test/no-capture"))


class ViewerUrlTest(unittest.TestCase):
    def test_a_row_with_no_capture_gets_no_link(self):
        """No link, the live sources included: their detail_id is their
        platform's own id and never named a capture."""
        self.assertIsNone(address.viewer_url(None))

    def test_the_link_drops_the_raw_bytes_marker(self):
        """`id_` is page_cache's variant; a human wants the ordinary viewer."""
        self.assertEqual(
            address.viewer_url(OWN), "https://web.archive.org/web/20030212170800/" + ROW
        )
