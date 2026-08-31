"""Phase 2's strategies, and the three rules that hold inside every one.

Each rule cost data before it was a rule, and all three now live in the library
in one copy precisely so a scraper cannot get them wrong - which makes this the
file that has to prove they are still there.
"""

import contextlib
import io

from pressroom.capture.control import address
from pressroom.provenance.entity import origin
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.release.entity.grade import Grade
from tests import support

TS = "20111011173713"
ARTICLE = "<p>" + "The full press release text. " * 30 + "</p>"


def quiet(fn, *a, **kw):
    """These strategies report to stdout by design; a test wants the DB."""
    with contextlib.redirect_stdout(io.StringIO()) as out:
        fn(*a, **kw)
    return out.getvalue()


class CursorTest(support.DbCase):
    def setUp(self):
        super().setUp()
        self.done = self.seed(
            "src", url="http://x/done", body="done", body_html="<p>done</p>"
        )
        self.todo = self.seed("src", url="http://x/todo", body="todo")

    def test_the_cursor_is_body_html_is_null(self):
        """Without one, a listing pass walked all 159 terratec_new rows instead
        of the 24 pending ones."""
        self.assertEqual(
            [u for u, _ in catch_up.pending(self.conn, "src")], [self.todo]
        )

    def test_force_widens_it_to_every_row_of_the_source(self):
        self.assertEqual(
            {u for u, _ in catch_up.pending(self.conn, "src", force=True)},
            {self.done, self.todo},
        )

    def test_another_source_is_never_in_the_cursor(self):
        self.seed("other", url="http://x/other", body="other")
        self.assertNotIn(
            "http://x/other",
            {u for u, _ in catch_up.pending(self.conn, "src", force=True)},
        )


class GateUnderForceTest(support.DbCase):
    def test_a_listing_teaser_cannot_replace_the_article_even_under_force(self):
        """The 2026-08-22 incident: 122 full articles overwritten by their
        listing teasers in one run, #4445 going 4287 -> 359 characters. This
        CMS embeds the full text on the listing for recent releases and
        truncates older entries, so a listing entry is not automatically the
        better copy - and `safe_to_write` provably cannot catch it, because a
        lost tail is `edges_only`, the same signature as correctly dropped nav.
        """
        url = self.seed(
            "src",
            url="http://x/1",
            body="A" * 4287,
            body_html="<p>" + "A" * 4287 + "</p>",
        )

        def collect(_conn):
            return {url: {"body": "A" * 359, "body_html": "<p>short</p>"}}

        out = quiet(catch_up.from_listings, self.conn, "src", collect, force=True)
        self.assertIn("WSTRZYMANE", out)
        self.assertEqual(len(self.row(url)["body"]), 4287)


class GateByAddressTest(support.DbCase):
    """The gate is chosen by the address, not by a flag."""

    def setUp(self):
        super().setUp()
        self.url = "http://www.midiman.net/news/en_us-596.html"
        self.listing = address.snapshot_url(
            TS, "http://www.midiman.net/news/pressdb.php"
        )
        self.own = address.snapshot_url(TS, self.url)
        storage.store_release(
            self.conn,
            "src",
            self.url,
            body="a 413-char teaser",
            detail_id=TS,
            grade=Grade.TEASER,
        )

    def _parser_returning(self, body):
        return lambda _content: {"body": body, "body_html": f"<p>{body}</p>"}

    def test_a_listing_capture_goes_through_the_strict_gate(self):
        """#5012, caught the moment the address rule landed: `--from-cache`
        would have handed this row the *listing* capture its teaser came from,
        and a whole-page parse returns the whole listing - 34k characters of
        other releases' text, which `safe_to_write` reads as a teaser recovering
        its article and allows."""
        origin.record(self.conn, self.url, self.listing)
        self.cache(self.listing, b"<html>the whole listing</html>")
        out = quiet(
            catch_up.from_cache,
            self.conn,
            "src",
            self._parser_returning("somebody else's release, " * 200),
        )
        self.assertIn("WSTRZYMANE", out)
        self.assertEqual(self.row(self.url)["body"], "a 413-char teaser")

    def test_the_same_bytes_would_pass_the_other_gate(self):
        """Asserted so the test above cannot go green for the wrong reason."""
        from pressroom.release.control import gate

        long_text = "somebody else's release, " * 200
        self.assertTrue(gate.safe_to_write("a 413-char teaser", long_text)[0])
        self.assertFalse(gate.strict_same_text("a 413-char teaser", long_text)[0])

    def test_a_capture_of_the_row_s_own_page_goes_through_safe_to_write(self):
        origin.record(self.conn, self.url, self.own)
        self.cache(self.own, b"<html>the release</html>")
        quiet(
            catch_up.from_cache,
            self.conn,
            "src",
            self._parser_returning("The real article, at last. " * 40),
        )
        self.assertIn("The real article", self.row(self.url)["body"])

    def test_a_collector_takes_the_row_instead_of_the_whole_page_parser(self):
        """If the caller has a url-keyed collector, such a row is skipped here
        and handled by from_listings - which is what the old registry's
        membership test became."""
        origin.record(self.conn, self.url, self.listing)
        self.cache(self.listing, b"<html>the whole listing</html>")
        quiet(
            catch_up.from_cache,
            self.conn,
            "src",
            self._parser_returning("anything at all " * 100),
            has_collector=True,
        )
        self.assertEqual(self.row(self.url)["body"], "a 413-char teaser")


class WriteTest(support.DbCase):
    def test_an_upgrade_says_the_verdict_changed(self):
        url = self.seed(
            "src", url="http://x/1", body="blurb", detail_id=TS, grade=Grade.TEASER
        )
        own = address.snapshot_url(TS, url)
        origin.record(self.conn, url, own)
        self.cache(own, b"<html>x</html>")
        quiet(
            catch_up.from_cache,
            self.conn,
            "src",
            lambda _c: {
                "body": "the real article " * 20,
                "body_html": "<p>the real article</p>",
            },
        )
        self.assertEqual(self.row(url)["grade"], "full")

    def test_the_address_is_recorded_beside_the_text(self):
        """Not a clear: the key this strategy reads *is* the recorded entry, so
        clearing it deletes a true statement and takes the row's archive link
        with it - 38 rows lost their link that way."""
        url = self.seed("src", url="http://x/1", body="blurb", detail_id=TS)
        own = address.snapshot_url(TS, url)
        self.cache(own, b"<html>x</html>")
        quiet(
            catch_up.from_cache,
            self.conn,
            "src",
            lambda _c: {
                "body": "the real article " * 20,
                "body_html": "<p>the real article</p>",
            },
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
            ).fetchone()[0],
            own,
        )

    def test_an_attachment_row_never_reaches_an_html_parser(self):
        """BeautifulSoup does not refuse binary - it returns a document whose
        get_text() is the PDF stream decoded as characters, with no exception to
        catch. 23 rows held `%PDF-1.3 %...` over correctly extracted text."""
        url = self.seed(
            "src", url="http://x/spec.pdf", body="the extracted text", detail_id=TS
        )
        self.cache(address.snapshot_url(TS, url), b"%PDF-1.3\n%\xe2\xe3\xcf\xd3")

        def explode(_content):
            raise AssertionError("a .pdf row reached the HTML parser")

        quiet(catch_up.from_cache, self.conn, "src", explode)
        self.assertEqual(self.row(url)["body"], "the extracted text")

    def test_binary_bytes_under_an_html_url_are_dead_not_parsed(self):
        url = self.seed("src", url="http://x/1.html", body="stored", detail_id=TS)
        self.cache(address.snapshot_url(TS, url), b"%PDF-1.3\n%\xe2\xe3\xcf\xd3")

        def explode(_content):
            raise AssertionError("binary bytes reached the HTML parser")

        quiet(catch_up.from_cache, self.conn, "src", explode)
        self.assertEqual(self.row(url)["body"], "stored")


class TitleTest(support.DbCase):
    def test_a_title_is_written_only_over_an_empty_one(self):
        """Tried the other way round, the same rule filled 8 rows and *changed*
        30 - twelve of them from a correct title to an empty one."""
        for name, stored, want in (
            ("empty", "", "Recovered headline"),
            ("kept", "The stored title", "The stored title"),
        ):
            with self.subTest(case=name):
                url = f"http://x/{name}"
                self.seed("src", url=url, title=stored, body="b", detail_id=TS)
                own = address.snapshot_url(TS, url)
                self.cache(own, b"<html>x</html>")
                quiet(
                    catch_up.from_cache,
                    self.conn,
                    "src",
                    lambda _c: {
                        "title": "Recovered headline",
                        "body": "the real article " * 20,
                        "body_html": "<p>the real article</p>",
                    },
                )
                self.assertEqual(self.row(url)["title"], want)


class ListingsTest(support.DbCase):
    def test_an_entry_matching_no_stored_url_is_dropped_never_inserted(self):
        """So a re-extraction cannot mint rows under urls nobody has seen."""
        self.seed("src", url="http://x/known", body="short")

        def collect(_conn):
            return {
                "http://x/known": {
                    "body": "the real article " * 20,
                    "body_html": "<p>a</p>",
                },
                "http://x/invented": {"body": "text " * 50, "body_html": "<p>b</p>"},
            }

        quiet(catch_up.from_listings, self.conn, "src", collect)
        self.assertIsNone(self.row("http://x/invented"))
        self.assertIn("the real article", self.row("http://x/known")["body"])


class RetextTest(support.DbCase):
    def test_body_is_re_derived_from_the_stored_markup(self):
        url = self.seed(
            "src",
            url="http://x/1",
            body="stale flat text",
            body_html="<p>alpha</p><p>beta</p>",
        )
        quiet(catch_up.retext, self.conn, "src")
        self.assertEqual(self.row(url)["body"], "alpha\n\nbeta")

    def test_it_records_no_origin_because_no_capture_was_involved(self):
        url = self.seed("src", url="http://x/1", body="stale", body_html="<p>alpha</p>")
        quiet(catch_up.retext, self.conn, "src")
        self.assertIsNone(
            self.conn.execute(
                "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
            ).fetchone()
        )

    def test_a_row_with_no_markup_is_left_alone(self):
        url = self.seed("src", url="http://x/1", body="flat, no html")
        quiet(catch_up.retext, self.conn, "src")
        self.assertEqual(self.row(url)["body"], "flat, no html")


class FlagTest(support.DbCase):
    def test_no_crawl_is_exactly_the_three_flags_that_mean_no_network(self):
        for opts, want in (
            ({"offline": True}, True),
            ({"only_retext": True}, True),
            ({"seed": True}, True),
            ({"force": True}, False),
            ({}, False),
            (None, False),
        ):
            with self.subTest(opts=opts):
                self.assertEqual(catch_up.no_crawl(opts), want)

    def test_confirm_force_refuses_when_there_is_no_terminal(self):
        """--force is the flag whose earlier equivalent overwrote 122 full
        articles, so a bulk rewrite is stated out loud first - and a pipe with
        no tty answers no rather than blocking a cron."""
        self.seed("src", url="http://x/1", body="b", body_html="<p>b</p>")
        self.assertFalse(self._confirm(yes=False))
        self.assertTrue(self._confirm(yes=True))

    def _confirm(self, *, yes):
        with contextlib.redirect_stdout(io.StringIO()):
            return catch_up.confirm_force(self.conn, "src", yes=yes)

    def test_options_maps_the_flags_to_the_keywords_catch_up_takes(self):
        class Args:
            no_catch_up = False
            force = True
            yes = False
            retext = False
            offline = True
            seed_cache = False
            attachments = False

        got = catch_up.options(Args())
        self.assertEqual(got["force"], True)
        self.assertEqual(got["offline"], True)
        self.assertEqual(got["only_retext"], False)
        self.assertEqual(got["catch_up"], True)
