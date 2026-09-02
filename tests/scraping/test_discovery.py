"""Phase 1's strategies, and the three rules that hold inside every one.

Nineteen hand-written copies of this loop existed before the library did, and
what they drifted on is exactly these three rules - each one had a copy that
got it wrong while its siblings carried a comment saying why it must not be.
So this is the file that has to prove the library still holds them.

It is also the first test this layer has ever had.
`tests/test_offline_is_offline.py` runs all fifteen scrapers, but
`catch_up.no_crawl()` empties the candidate list, so the loop body never
executed under test until now.
"""

import contextlib
import io
from unittest import mock

from pressroom.capture.control import archive

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
        counter reports phantom inserts on every rerun - three copies of this
        loop did exactly that.

        `already_stored` is url-keyed, so it normally catches this first and the
        write's return value is the second gate on the same question. Reaching
        that gate needs the row to appear *after* the pre-check, which is what
        the fetch does here: it is the shape of two runs of the same source
        overlapping, and the reason the gate is not redundant.
        """

        def fetch_and_race(conn, session, url):
            self.seed("other", url=url, body="stored while we were fetching")
            return ARTICLE, f"<p>{ARTICLE}</p>"

        counts = self.run_items([item()], fetch_and_race)
        self.assertEqual(counts["added"], 0)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT source, body FROM releases WHERE url = ?", ("http://x/one",)
            ).fetchone()[1],
            "stored while we were fetching",
            "the row that won the race must be left exactly as it was",
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
    """Stands in for the two archive.py calls the capture walk makes.

    Patched onto the module rather than reached through a session, because the
    point of these tests is the loop: no network, no page_cache, no CDX. The
    walk itself is left real - `capture()` is now one call into it, and stubbing
    that call out would test nothing but the mock.

    One capture in the list, so there is no year-later probe and no tail to walk
    back over: what the walk chooses out of several is
    `tests/capture/test_archive.py`'s subject, not this file's.
    """

    def __init__(
        self, *, snapshot=None, parsed=None, probe_error=None, fetch_error=None
    ):
        self.snapshot = snapshot
        self.parsed = parsed if parsed is not None else {}
        self.probe_error = probe_error
        self.fetch_error = fetch_error
        self.probed = []

    def list_all_captures(self, url, retries=3):
        self.probed.append(url)
        if self.probe_error:
            raise self.probe_error
        return [TS] if self.snapshot else []

    def fetch_snapshot(self, conn, session, url, timeout=20):
        if self.fetch_error:
            raise self.fetch_error
        return b"<html>whatever the parser is fed</html>"

    def install(self, case):
        for name in ("list_all_captures", "fetch_snapshot"):
            patcher = mock.patch.object(archive, name, getattr(self, name))
            patcher.start()
            case.addCleanup(patcher.stop)
        # The walk waits CONTENT_SLEEP*2 after a failed listing; a test does not.
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
    def run_urls(
        self,
        fake,
        urls=("http://x/one",),
        titles=None,
        dates=None,
        stub_if_absent=False,
    ):
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
            dates=dates,
            stub_if_absent=stub_if_absent,
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
        and there is no body.

        This is also what `_detail_score`'s middle rung is for. The walk only
        returns a capture that scores above zero, so a scorer counting body
        length alone would reject this one, `capture()` would report a confirmed
        absence, and every title-only row would become `dead` instead."""
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

    def test_a_capture_that_parses_to_nothing_is_still_a_stub_not_a_dead_end(self):
        """The archive has this url; not one of its captures carries anything a
        parser can use. That is not the same as never having been archived, and
        the difference is a row: `dead` would drop a release the listing knew
        the title of."""
        fake = FakeArchive(snapshot=(SNAP, TS), parsed={}).install(self)
        counts = self.run_urls(fake, titles={"http://x/one": "From the listing"})
        self.assertEqual((counts["stub"], counts["dead"]), (1, 0))
        self.assertEqual(self.row()[0], "From the listing")

    def test_a_candidate_already_stored_is_skipped_without_a_probe(self):
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail()).install(self)
        self.seed("src", url="http://x/one", body="already here")
        counts = self.run_urls(fake)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(fake.probed, [], "archive.org was asked about a stored row")

    def date(self, url="http://x/one"):
        got = self.conn.execute(
            "SELECT date FROM releases WHERE url = ?", (url,)
        ).fetchone()
        return got[0] if got else None

    def test_the_listing_s_date_is_the_fallback_when_the_markup_has_none(self):
        """The pair of the title fallback above, and needed by the same sources:
        these listings state the date in their own column, and half the
        templates behind them put none on the article page at all."""
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail(date="")).install(self)
        self.run_urls(fake, dates={"http://x/one": "1999-12-31"})
        self.assertEqual(self.date(), "1999-12-31")

    def test_a_parsed_date_still_beats_the_listing_s(self):
        fake = FakeArchive(snapshot=(SNAP, TS), parsed=detail()).install(self)
        self.run_urls(fake, dates={"http://x/one": "1999-12-31"})
        self.assertEqual(self.date(), "2004-05-06")

    def test_a_url_only_a_listing_named_survives_an_absence_as_a_stub(self):
        """The archive never saw this url - which for a url read off a folder
        listing would be a contradiction, and for one read off an index page is
        the ordinary case: the release existed, its page was never captured.
        The listing's title and date are real, so the row is worth keeping."""
        fake = FakeArchive(snapshot=None).install(self)
        counts = self.run_urls(
            fake,
            titles={"http://x/one": "Named by the listing"},
            dates={"http://x/one": "1999-12-31"},
            stub_if_absent=True,
        )
        self.assertEqual((counts["stub"], counts["dead"]), (1, 0))
        title, body, grade, detail_id = self.row()
        self.assertEqual((title, body, grade), ("Named by the listing", "", "stub"))
        self.assertIsNone(detail_id, "a stub with no capture cannot name one")
        self.assertIsNone(self.origin(), "no body, so no origin")

    def test_metadata_is_what_earns_that_row_not_the_flag(self):
        """Both channels of a merged pool go through one call, so the flag is on
        for candidates that carry no metadata too. Nothing to keep means the
        default verdict stands."""
        counts = self.run_urls(
            FakeArchive(snapshot=None).install(self), stub_if_absent=True
        )
        self.assertEqual((counts["dead"], counts["stub"]), (1, 0))
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )

    def test_without_the_flag_a_listing_s_metadata_does_not_make_a_row(self):
        """Off by default: three sources pass `titles` without meaning this, and
        an absence there is still a `dead`."""
        counts = self.run_urls(
            FakeArchive(snapshot=None).install(self),
            titles={"http://x/one": "Named by the listing"},
        )
        self.assertEqual((counts["dead"], counts["stub"]), (1, 0))
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )


def teaser_entry(url="http://x/one", **kw):
    return {
        "url": url,
        "title": kw.get("title", "Listing headline"),
        "date": kw.get("date", "2004-05-06"),
        "teaser": kw.get("teaser", "The first two sentences only."),
        "teaser_html": kw.get("teaser_html", "<p>The first two sentences only.</p>"),
    }


class FromTeasersTest(support.DbCase):
    def run_entries(self, entries, detail=None, confirmed=True, prefer_parsed=False):
        """`detail` is what the fake detail fetch hands back; None means it
        recovered nothing, and `confirmed` then says whether that is a verdict
        or a network failure."""
        stats = Stats("src")
        calls = []

        def fetch_detail(conn, session, url, parse):
            calls.append(url)
            if detail is None:
                return {}, confirmed
            return dict(detail, detail_id=TS, origin_url=SNAP), True

        quiet(
            discovery.from_teasers,
            self.conn,
            None,
            "src",
            entries,
            parse=lambda content: {},
            stats=stats,
            fetch_detail=fetch_detail,
            prefer_parsed=prefer_parsed,
        )
        return stats.counts, calls

    def row(self, url="http://x/one"):
        return tuple(
            self.conn.execute(
                "SELECT title, date, body, grade FROM releases WHERE url = ?", (url,)
            ).fetchone()
        )

    def origin(self, url="http://x/one"):
        got = self.conn.execute(
            "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
        ).fetchone()
        return got[0] if got else None

    def test_a_failed_probe_on_a_stored_teaser_is_uncertain_not_skipped(self):
        """Rule 3, and the one the copies disagreed on: three of the four asked
        `confirmed` before asking about the teaser and said in a comment why,
        while `maudio/presse_de.py` had the two the other way round - so a
        network error there was filed under "already as good as it gets", which
        is exactly where a rerun stops looking."""
        self.seed("src", url="http://x/one", body="teaser", grade="teaser")
        counts, _ = self.run_entries([teaser_entry()], detail=None, confirmed=False)
        self.assertEqual(counts["uncertain"], 1)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(self.row()[2], "teaser", "nothing may be written")

    def test_a_confirmed_absence_leaves_a_stored_teaser_alone_and_skips_it(self):
        self.seed("src", url="http://x/one", body="teaser", grade="teaser")
        counts, _ = self.run_entries([teaser_entry()], detail=None, confirmed=True)
        self.assertEqual((counts["skipped"], counts["uncertain"]), (1, 0))

    def test_a_stored_teaser_gains_the_article_and_its_provenance(self):
        self.seed("src", url="http://x/one", body="teaser", grade="teaser")
        counts, _ = self.run_entries([teaser_entry()], detail=detail())
        self.assertEqual(counts["upgraded"], 1)
        self.assertEqual(self.row(), ("", "", ARTICLE, "full"))
        self.assertEqual(self.origin(), SNAP)

    def test_without_prefer_parsed_the_upgrade_writes_the_body_alone(self):
        """Two of these CMSes state the headline and the date better on the
        listing than on the article page, so the upgrade must not touch them."""
        self.seed(
            "src",
            url="http://x/one",
            title="Listing headline",
            date="2004-05-06",
            body="teaser",
            grade="teaser",
        )
        self.run_entries([teaser_entry()], detail=detail(title="PRINT VERSION"))
        title, date, body, _ = self.row()
        self.assertEqual((title, date), ("Listing headline", "2004-05-06"))
        self.assertEqual(body, ARTICLE)

    def test_with_prefer_parsed_the_upgrade_carries_the_page_s_title_and_date(self):
        self.seed(
            "src",
            url="http://x/one",
            title="Listing headline",
            date="2004-05-06",
            body="teaser",
            grade="teaser",
        )
        self.run_entries(
            [teaser_entry()],
            detail=detail(title="The real headline", date="2004-05-07"),
            prefer_parsed=True,
        )
        self.assertEqual(self.row()[:2], ("The real headline", "2004-05-07"))

    def test_an_unseen_entry_with_an_article_is_added_with_its_origin(self):
        counts, _ = self.run_entries([teaser_entry()], detail=detail())
        self.assertEqual(counts["added"], 1)
        self.assertEqual(
            self.row(), ("Listing headline", "2004-05-06", ARTICLE, "full")
        )
        self.assertEqual(self.origin(), SNAP)

    def test_an_unseen_entry_with_no_article_keeps_the_teaser(self):
        counts, _ = self.run_entries([teaser_entry()], detail=None, confirmed=True)
        self.assertEqual(counts["teaser"], 1)
        self.assertEqual(self.row()[3], "teaser")
        self.assertIsNone(self.origin(), "the teaser did not come from that capture")

    def test_an_unseen_entry_with_neither_is_dead(self):
        counts, _ = self.run_entries(
            [teaser_entry(teaser="", teaser_html="")], detail=None, confirmed=True
        )
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM releases").fetchone()[0], 0
        )

    def test_a_row_already_as_good_as_it_gets_is_skipped_without_a_fetch(self):
        self.seed("src", url="http://x/one", body=ARTICLE)
        counts, calls = self.run_entries([teaser_entry()], detail=detail())
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(calls, [], "the detail page was fetched for nothing")
