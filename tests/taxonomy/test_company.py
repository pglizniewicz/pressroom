"""The second axis over the 24 source tags.

An explicit table rather than a prefix rule, and the tests are about the two
ways that table can be wrong in a way nothing else notices: a tag filed twice,
and a tag filed nowhere.
"""

import unittest

from pressroom.taxonomy.entity import company


class TableTest(unittest.TestCase):
    def test_no_tag_belongs_to_two_companies(self):
        seen = {}
        for slug, (_label, sources) in company.COMPANIES.items():
            for src in sources:
                self.assertNotIn(
                    src, seen, f"{src} is under both {seen.get(src)} and {slug}"
                )
                seen[src] = slug

    def test_the_unknown_bucket_is_not_a_company(self):
        """`http._sources()` answers HTTP 400 for a slug that is not a key here,
        and the panel links to whatever company_of returns - so if UNKNOWN were
        ever made a key the 400 would turn into a silent whole-corpus search,
        and if a real source falls through to it the link 400s. Both halves of
        that are why soundonsound got an entry of its own."""
        self.assertNotIn(company.UNKNOWN, company.COMPANIES)
        self.assertEqual(company.label(company.UNKNOWN), company.UNKNOWN_LABEL)

    def test_company_of_reports_rather_than_guesses(self):
        """A prefix rule looks cheaper right up to the next scraper, where it
        files a new source under the wrong company and nothing fails."""
        self.assertEqual(company.company_of("terratec_pressde"), "terratec")
        self.assertEqual(company.company_of("terratec_brand_new"), company.UNKNOWN)

    def test_midiman_and_maudio_are_one_company(self):
        """midiman.com serves releases signed M-Audio and
        m-audio.com serves ones signed Midiman, so a split by domain would not
        be a split by brand."""
        self.assertEqual(
            company.company_of("midiman_com_pressdb"),
            company.company_of("maudio_com_media_news"),
        )

    def test_sources_for_dedups_and_ignores_unknown_slugs(self):
        got = company.sources_for(["intel", "intel", "nonsense"])
        self.assertEqual(got, ["intel"])
        self.assertEqual(company.unknown_slugs(["intel", "nonsense"]), ["nonsense"])


class RollUpTest(unittest.TestCase):
    def _row(self, source, count, first="", last="", **kw):
        row = {"source": source, "count": count, "first": first, "last": last}
        row.update(
            {
                k: kw.get(k, 0)
                for k in ("teaser", "short", "nodate", "mojibake", "plain")
            }
        )
        return row

    def test_totals_are_preserved(self):
        rows = [
            self._row("intel", 10, "2007-01-01", "2026-01-01", teaser=2),
            self._row("amd", 5, "2010-01-01", "2020-01-01", teaser=1),
        ]
        out = company.roll_up(rows)
        self.assertEqual(sum(c["count"] for c in out), 15)
        self.assertEqual(sum(c["teaser"] for c in out), 3)

    def test_an_empty_date_never_wins_a_min(self):
        """midiman_net's single row has no date at all; `""` would otherwise
        sort before 1996 and give its company a first year of nothing."""
        rows = [
            self._row("midiman_net", 1),
            self._row("midiman_com", 9, "1999-03-01", "1999-11-01"),
        ]
        out = company.roll_up(rows)
        self.assertEqual(out[0]["first"], "1999-03-01")
        self.assertEqual(out[0]["last"], "1999-11-01")

    def test_an_unmapped_source_lands_in_a_visible_bucket(self):
        """Rather than being left out of the counts, which would make every
        number on the audit screen wrong."""
        out = company.roll_up([self._row("brand_new_source", 3)])
        self.assertEqual(out[0]["company"], company.UNKNOWN)
        self.assertEqual(out[0]["count"], 3)
