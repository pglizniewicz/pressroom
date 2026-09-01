"""Phase 1's strategies, and the three rules that hold inside every one.

Nineteen hand-written copies of this loop existed before the library did, and
what they drifted on is exactly these three rules - each one had a copy that
got it wrong while its siblings carried a comment saying why it must not be.
So this is the file that has to prove the library still holds them.

It is also the first test this layer has ever had.
`tests/test_offline_is_offline.py` runs all sixteen scrapers, but
`catch_up.no_crawl()` empties the candidate list, so the loop body never
executed under test until now.
"""

import contextlib
import io

from pressroom.release.control import storage
from pressroom.reporting.entity.outcome import Stats
from pressroom.scraping.control import discovery
from tests import support

ARTICLE = "The full press release text. " * 20


def quiet(fn, *a, **kw):
    """These strategies print one marker per outcome by design."""
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


def item(url="http://x/one", **kw):
    return {
        "url": url,
        "title": kw.get("title", "A release"),
        "date": kw.get("date", "2004-05-06"),
        "detail_id": kw.get("detail_id", "4711"),
    }


class FromItemsTest(support.DbCase):
    def run_items(self, items, fetch_body):
        stats = Stats("src")
        quiet(
            discovery.from_items,
            self.conn,
            None,
            "src",
            items,
            fetch_body=fetch_body,
            stats=stats,
        )
        return stats.counts

    def test_a_body_is_stored_with_the_listing_s_title_and_date(self):
        counts = self.run_items(
            [item()], lambda conn, session, url: (ARTICLE, f"<p>{ARTICLE}</p>")
        )
        self.assertEqual(counts["added"], 1)
        row = self.conn.execute(
            "SELECT source, title, date, body, body_html, detail_id, grade"
            " FROM releases WHERE url = ?",
            ("http://x/one",),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            (
                "src",
                "A release",
                "2004-05-06",
                ARTICLE,
                f"<p>{ARTICLE}</p>",
                "4711",
                "full",
            ),
        )

    def test_a_network_error_writes_nothing_and_is_uncertain(self):
        """Not `dead`, and not `skipped`: those rows are the ones a rerun exists
        to pick up. An inserted empty row would be worse than no row at all -
        already_stored() would skip it forever, so one timeout would cost the
        release permanently."""

        def boom(conn, session, url):
            raise OSError("connection reset by peer")

        counts = self.run_items([item()], boom)
        self.assertEqual(counts["uncertain"], 1)
        self.assertEqual(counts["dead"], 0)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )

    def test_an_empty_body_is_dead_and_never_a_stored_row(self):
        """A row with a full grade and no text cannot be told apart from a
        release that genuinely had none, and rule 1 makes it permanent."""
        counts = self.run_items([item()], lambda conn, session, url: ("", ""))
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(counts["added"], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )

    def test_an_item_already_stored_is_skipped_without_a_fetch(self):
        self.seed("src", url="http://x/one", body="already here")

        def refuse(conn, session, url):
            raise AssertionError("fetched an item that was already stored")

        counts = self.run_items([item()], refuse)
        self.assertEqual(counts["skipped"], 1)

    def test_a_write_that_inserted_nothing_is_skipped_not_added(self):
        """Invariant 5: store_release is INSERT OR IGNORE, so an unconditional
        counter reports phantom inserts on every rerun. Three copies of this
        loop did exactly that. The url is stored under another source here, so
        `already_stored` does not catch it first and the gate has to be the
        write's own return value."""
        self.seed("other", url="http://x/one", body="stored by a sibling tag")
        counts = self.run_items(
            [item()], lambda conn, session, url: (ARTICLE, f"<p>{ARTICLE}</p>")
        )
        self.assertEqual(counts["added"], 0)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(
            storage.stored_grade(self.conn, "http://x/one"),
            "full",
            "the sibling's row must be left exactly as it was",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT source FROM releases WHERE url = ?", ("http://x/one",)
            ).fetchone()[0],
            "other",
        )

    def test_one_item_s_failure_does_not_stop_the_ones_after_it(self):
        """These are hour-long crawls against a flaky archive: a rerun must pick
        up exactly what the last one could not get, which needs the loop to keep
        going past a single failure."""

        def fetch(conn, session, url):
            if url.endswith("two"):
                raise OSError("timed out")
            return ARTICLE, f"<p>{ARTICLE}</p>"

        counts = self.run_items(
            [item("http://x/one"), item("http://x/two"), item("http://x/three")], fetch
        )
        self.assertEqual((counts["added"], counts["uncertain"]), (2, 1))
        self.assertEqual(
            sorted(u for (u,) in self.conn.execute("SELECT url FROM releases")),
            ["http://x/one", "http://x/three"],
        )
