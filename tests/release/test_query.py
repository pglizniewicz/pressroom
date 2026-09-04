"""The read shapes both readers share - and the audit flags, which are the
numbers the browser puts on screen.

A wrong flag does not fail; it makes the audit view report less outstanding work
than there is, which is exactly what the `plain` flag did for 358 rows and the
`mojibake` one did for 23.
"""

from pressroom.release.control import query, storage
from pressroom.release.entity.grade import Grade
from tests import support


class FlagTest(support.DbCase):
    def setUp(self):
        super().setUp()
        self.rows = {}
        for name, kw in {
            "clean": dict(
                title="T", date="2003-01-01", body="x" * 900, body_html="<p>x</p>"
            ),
            "teaser": dict(
                title="T",
                date="2003-01-01",
                body="x" * 900,
                body_html="<p>x</p>",
                grade=Grade.TEASER,
            ),
            "stub": dict(
                title="T",
                date="2003-01-01",
                body="x" * 900,
                body_html="<p>x</p>",
                grade=Grade.STUB,
            ),
            "short": dict(
                title="T", date="2003-01-01", body="tiny", body_html="<p>tiny</p>"
            ),
            "nodate": dict(title="T", date="", body="x" * 900, body_html="<p>x</p>"),
            "plain": dict(title="T", date="2003-01-01", body="x" * 900),
        }.items():
            url = f"http://x/{name}.html"
            storage.store_release(self.conn, "src", url, **kw)
            self.rows[name] = url
        # An attachment row: no HTML behind it by construction, so it must not
        # count as outstanding formatting work.
        storage.store_release(
            self.conn,
            "src",
            "http://x/spec.pdf",
            title="T",
            date="2003-01-01",
            body="x" * 900,
        )
        # Encoding damage the SQL has to see without a regex.
        storage.store_release(
            self.conn,
            "src",
            "http://x/damaged.html",
            title="T",
            date="2003-01-01",
            body="GeForce\x99 x" * 90,
            body_html="<p>x</p>",
        )

    def _flagged(self, flag):
        return {
            r["url"]
            for r in query.search_releases(self.conn, flags=[flag], limit=200)[
                "results"
            ]
        }

    def test_each_flag_selects_exactly_its_own_rows(self):
        for flag, want in (
            ("teaser", {self.rows["teaser"], self.rows["stub"]}),
            ("short", {self.rows["short"]}),
            ("nodate", {self.rows["nodate"]}),
            ("mojibake", {"http://x/damaged.html"}),
        ):
            with self.subTest(flag=flag):
                self.assertEqual(self._flagged(flag), want)

    def test_plain_excludes_attachment_rows(self):
        """A .pdf/.doc release was extracted with pdftotext/antiword and has no
        HTML behind it. Counting them made the audit overstate the remaining
        work by 358 rows of 1706, and the whole point of that view is that its
        numbers are correct."""
        self.assertEqual(self._flagged("plain"), {self.rows["plain"]})

    def test_an_unknown_flag_is_ignored_rather_than_narrowing_the_search(self):
        everything = query.search_releases(self.conn, limit=200)["results"]
        self.assertEqual(
            len(
                query.search_releases(self.conn, flags=["nonsense"], limit=200)[
                    "results"
                ]
            ),
            len(everything),
        )

    def test_quality_counts_agree_with_the_flags(self):
        counts = query.quality_counts(self.conn)
        for flag in ("short", "nodate", "mojibake", "plain"):
            with self.subTest(flag=flag):
                self.assertEqual(counts[flag], len(self._flagged(flag)))

    def test_the_audit_cards_split_what_the_flag_combines(self):
        """`quality_counts` reports teaser and stub as two numbers because the
        audit view has a card for each; `FLAG_SQL["teaser"]` combines them
        because a reader filtering for teasers wants both. The two are only
        consistent as a sum, which is the relationship worth pinning - the
        per-source table's `teaser` column uses the combined rule."""
        counts = query.quality_counts(self.conn)
        self.assertEqual(
            counts["teaser"] + counts["stub"], len(self._flagged("teaser"))
        )
        by_source = {r["source"]: r for r in query.list_sources(self.conn)}
        self.assertEqual(by_source["src"]["teaser"], len(self._flagged("teaser")))


class FilterTest(support.DbCase):
    def setUp(self):
        super().setUp()
        for src, date, body in (
            ("intel", "2007-05-01", "Radium chipset"),
            ("amd", "2010-05-01", "Radium processor"),
            ("amd", "", "Radium undated"),
        ):
            storage.store_release(
                self.conn, src, f"http://x/{src}{date}", title="T", date=date, body=body
            )

    def test_filters_intersect_rather_than_being_ignored(self):
        """`#company=amd&source=intel` must give zero results: the filter is
        impossible, not meaningless."""
        got = query.search_releases(
            self.conn, sources=["intel"], date_from="2009-01-01"
        )["results"]
        self.assertEqual(got, [])

    def test_browsing_reaches_the_undated_rows(self):
        """An empty query is not a degenerate search but the browsing case, and
        it is the only way to reach the rows whose date is ''."""
        got = query.search_releases(self.conn, order="date", limit=50)["results"]
        self.assertEqual(len(got), 3)
        self.assertIn("", {r["date"] for r in got})

    def test_a_hyphenated_query_falls_back_to_a_literal_phrase(self):
        """FTS5 barewords are alphanumeric, so 'M-Audio' - the single most
        likely thing to type into this search - is a syntax error."""
        got = query.search_releases(self.conn, "M-Audio")
        self.assertEqual(got["query_mode"], "literal")

    def test_a_real_fts_query_stays_raw(self):
        got = query.search_releases(self.conn, "Radium")
        self.assertEqual(got["query_mode"], "raw")
        self.assertEqual(len(got["results"]), 3)


class NeighbourTest(support.DbCase):
    def setUp(self):
        super().setUp()
        self.ids = {}
        for src, date in (
            ("a", "2003-01-01"),
            ("a", "2003-06-01"),
            ("a", "2003-12-01"),
            ("b", "2003-06-15"),
        ):
            url = f"http://x/{src}{date}"
            storage.store_release(self.conn, src, url, title=date, date=date, body="b")
            self.ids[(src, date)] = self.row(url)["id"]

    def test_neighbours_stay_inside_one_source(self):
        got = query.neighbours(self.conn, self.ids[("a", "2003-06-01")])
        self.assertEqual(got["prev"]["date"], "2003-01-01")
        self.assertEqual(got["next"]["date"], "2003-12-01")

    def test_the_ends_of_a_source_have_none(self):
        first = query.neighbours(self.conn, self.ids[("a", "2003-01-01")])
        last = query.neighbours(self.conn, self.ids[("a", "2003-12-01")])
        self.assertIsNone(first["prev"])
        self.assertIsNone(last["next"])

    def test_a_missing_row_answers_rather_than_raising(self):
        self.assertEqual(
            query.neighbours(self.conn, 999999), {"prev": None, "next": None}
        )


class SourceListTest(support.DbCase):
    def test_a_source_with_no_dated_row_carries_an_empty_span(self):
        """`""` would otherwise sort before 1996 in the panel's chronological sort, so
        midiman_net - whose single row has no date - has to sort last rather
        than first."""
        storage.store_release(
            self.conn, "midiman_net", "http://x/1", title="T", date="", body="b"
        )
        storage.store_release(
            self.conn,
            "midiman_com",
            "http://x/2",
            title="T",
            date="1999-03-01",
            body="b",
        )
        rows = {r["source"]: r for r in query.list_sources(self.conn)}
        self.assertEqual(rows["midiman_net"]["first"], "")
        self.assertEqual(rows["midiman_com"]["first"], "1999-03-01")

    def test_get_release_returns_the_body_and_none_for_a_missing_id(self):
        storage.store_release(
            self.conn,
            "src",
            "http://x/1",
            title="T",
            body="the article",
            body_html="<p>the article</p>",
        )
        rid = self.row("http://x/1")["id"]
        self.assertEqual(query.get_release(self.conn, rid)["body"], "the article")
        self.assertIsNone(query.get_release(self.conn, 999999))
