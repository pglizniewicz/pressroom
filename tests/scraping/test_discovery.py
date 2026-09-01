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
from unittest import mock

from pressroom.capture.control import archive

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


class FakeArchive:
    """Stands in for the two archive.py calls `capture()` makes.

    Patched onto the module rather than reached through a session, because the
    point of these tests is the loop: no network, no page_cache, no CDX.
    """

    def __init__(
        self, *, snapshot=None, parsed=None, probe_error=None, fetch_error=None
    ):
        self.snapshot = snapshot
        self.parsed = parsed if parsed is not None else {}
        self.probe_error = probe_error
        self.fetch_error = fetch_error
        self.probed = []

    def get_latest_working_snapshot(self, url):
        self.probed.append(url)
        if self.probe_error:
            raise self.probe_error
        return self.snapshot

    def fetch_snapshot(self, conn, session, url, timeout=20):
        if self.fetch_error:
            raise self.fetch_error
        return b"<html>whatever the parser is fed</html>"

    def install(self, case):
        for name in ("get_latest_working_snapshot", "fetch_snapshot"):
            patcher = mock.patch.object(archive, name, getattr(self, name))
            patcher.start()
            case.addCleanup(patcher.stop)
        # capture() waits CONTENT_SLEEP*2 after a failed probe; a test does not.
        patcher = mock.patch("time.sleep")
        patcher.start()
        case.addCleanup(patcher.stop)
        return self


TS = "20040506070809"
SNAP = f"https://web.archive.org/web/{TS}id_/http://x/one"


def detail(body=ARTICLE, title="A release", date="2004-05-06"):
    return {"title": title, "date": date, "body": body, "body_html": f"<p>{body}</p>"}


class CaptureTest(support.DbCase):
    def one(self, fake):
        stats = Stats("src")
        found, _ = quiet(
            discovery.capture,
            self.conn,
            None,
            "http://x/one",
            lambda content: fake.parsed,
            stats=stats,
        )
        return found, stats.counts

    def test_a_capture_carries_its_own_address_back(self):
        """The whole reason this is not `fetch_detail_snapshot`: five loops
        rebuilt it by hand because they needed the snapshot url afterwards, and
        `body_origin` cannot be written without it."""
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail()).install(self)
        found, counts = self.one(fake)
        self.assertEqual((found.timestamp, found.origin_url), (TS, SNAP))
        self.assertEqual(found.parsed["body"], ARTICLE)
        self.assertEqual(sum(counts.values()), 0, "capture() counts nothing itself")

    def test_a_failed_probe_is_none_and_uncertain(self):
        fake = FakeArchive(probe_error=OSError("connection refused")).install(self)
        found, counts = self.one(fake)
        self.assertIsNone(found)
        self.assertEqual(counts["uncertain"], 1)
        self.assertEqual(counts["dead"], 0)

    def test_a_failed_fetch_is_none_and_uncertain(self):
        fake = FakeArchive(snapshot=(SNAP, TS), fetch_error=OSError("reset")).install(
            self
        )
        found, counts = self.one(fake)
        self.assertIsNone(found)
        self.assertEqual(counts["uncertain"], 1)

    def test_a_confirmed_absence_returns_a_verdict_rather_than_counting_it(self):
        """`dead`, a title-only stub, or a body read off a listing capture: three
        sources answer this differently, so the decision stays with them."""
        fake = FakeArchive(snapshot=None).install(self)
        found, counts = self.one(fake)
        self.assertIsNotNone(found)
        self.assertIsNone(found.timestamp)
        self.assertEqual(sum(counts.values()), 0)


class FromCandidatesTest(support.DbCase):
    def run_urls(self, fake, urls=("http://x/one",), titles=None):
        stats = Stats("src")
        quiet(
            discovery.from_candidates,
            self.conn,
            None,
            "src",
            list(urls),
            parse=lambda content: fake.parsed,
            stats=stats,
            titles=titles,
        )
        return stats.counts

    def row(self, url="http://x/one"):
        return self.conn.execute(
            "SELECT title, body, grade, detail_id FROM releases WHERE url = ?", (url,)
        ).fetchone()

    def origin(self, url="http://x/one"):
        got = self.conn.execute(
            "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
        ).fetchone()
        return got[0] if got else None

    def test_a_recovered_body_is_full_and_records_where_it_came_from(self):
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail()).install(self)
        counts = self.run_urls(fake)
        self.assertEqual(counts["added"], 1)
        title, body, grade, detail_id = self.row()
        self.assertEqual(
            (title, body, grade, detail_id), ("A release", ARTICLE, "full", TS)
        )
        self.assertEqual(self.origin(), SNAP)

    def test_a_bodyless_capture_is_a_stub_with_no_origin(self):
        """Stored, unlike in from_items, because the url is real and its capture
        is named - the row is what lets phase 2 come back for the text. But not
        `full`, and no origin: body_origin records the capture a body came from
        and there is no body."""
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail(body="")).install(self)
        counts = self.run_urls(fake)
        self.assertEqual((counts["stub"], counts["added"]), (1, 0))
        self.assertEqual(self.row()[2], "stub")
        self.assertIsNone(self.origin())

    def test_the_listing_s_title_is_the_fallback_when_the_markup_has_none(self):
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail(title="")).install(self)
        self.run_urls(fake, titles={"http://x/one": "From the listing"})
        self.assertEqual(self.row()[0], "From the listing")

    def test_a_confirmed_absence_is_dead_and_writes_nothing(self):
        counts = self.run_urls(FakeArchive(snapshot=None).install(self))
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )

    def test_a_candidate_already_stored_is_skipped_without_a_probe(self):
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail()).install(self)
        self.seed("src", url="http://x/one", body="already here")
        counts = self.run_urls(fake)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(fake.probed, [], "archive.org was asked about a stored row")
