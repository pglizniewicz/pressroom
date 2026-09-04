"""The one date parser, and the four spellings this corpus uses."""

import unittest

from pressroom.text.control.dating import iso_date


class IsoDateTest(unittest.TestCase):
    def test_the_four_spellings(self):
        for value, kw, want in (
            ("29.10.2002", {"dayfirst": True}, "2002-10-29"),
            ("October 8, 2014", {}, "2014-10-08"),
            ("11/14/2005", {}, "2005-11-14"),
            ("June 2007", {"fmt": "%Y-%m"}, "2007-06"),
        ):
            with self.subTest(value=value):
                self.assertEqual(iso_date(value, **kw), want)

    def test_dayfirst_is_not_decoration(self):
        """The failure the parameter exists for: without it dateutil reads the
        European sources' 06.05.2002 as June 5th."""
        self.assertEqual(iso_date("06.05.2002", dayfirst=True), "2002-05-06")
        self.assertEqual(iso_date("06.05.2002"), "2002-06-05")

    def test_fuzzy_skips_surrounding_prose(self):
        self.assertEqual(
            iso_date("Presseinformation vom 19.12.1997:", dayfirst=True, fuzzy=True),
            "1997-12-19",
        )

    def test_a_zone_name_is_accepted_and_the_printed_day_kept(self):
        """GlobeNewswire dates every listing entry "09:00 ET". dateutil does not
        know that name, warned on every run and says it will raise; `tzinfos`
        accepts it, and the date stays the day the publisher printed."""
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(
                iso_date("September 03, 2026 09:00 ET", fuzzy=True), "2026-09-03"
            )
            self.assertEqual(
                iso_date("March 6, 2013 23:30 PST", fuzzy=True), "2013-03-06"
            )

    def test_unparseable_returns_empty_rather_than_raising(self):
        """A release whose date cannot be read is still worth storing, which is
        why releases.date is a plain TEXT column with no format constraint."""
        for value in ("", None, "coming soon", "n/a"):
            self.assertEqual(iso_date(value), "")
