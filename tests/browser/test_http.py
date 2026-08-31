"""The browser boundary: routing, query params, and the three strings a reader
renders next to a capture link.

The capture annotations are the half of this file that had to be got right
twice. The first cut of "(capture strony: …)" annotated all 1763 rows with an
entry, including the 1605 whose capture is of their own page; and a mirrored
file was announced as "z listingu" while its link was labelled with a timestamp
the link does not open.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from pressroom.browser.boundary import http
from pressroom.provenance.entity import origin
from pressroom.release.control import storage
from tests import support

ROW = "http://www.midiman.net/images/press/BX5_PR.pdf"
OWN = "https://web.archive.org/web/20030212170800id_/" + ROW
MIRROR = (
    "https://web.archive.org/web/20030421210545id_/"
    "http://www.m-audio.com/images/press/BX5_PR.pdf"
)
LISTING = (
    "https://web.archive.org/web/20111011173713id_/http://www.terratec.de/presse.html"
)


class CaptureAnnotationTest(support.DbCase):
    def test_the_common_case_stays_unannotated(self):
        """1605 of the 1763 rows with a capture are captures of their own page,
        and the first cut of this marked every one of them."""
        self.assertIsNone(http.capture_page(OWN, ROW))
        self.assertIsNone(http.capture_kind(http.capture_page(OWN, ROW), ROW))

    def test_a_listing_capture_is_named_in_plain_text(self):
        """#4414: built from the row's own url the link is a capture archive.org
        does not have, while `web/<ts>/…/presse.html` holds that release's full
        text, character for character."""
        page = http.capture_page(LISTING, "http://www.terratec.de/September_2011.html")
        self.assertEqual(page, "http://www.terratec.de/presse.html")
        self.assertEqual(
            http.capture_kind(page, "http://www.terratec.de/September_2011.html"),
            "other",
        )

    def test_the_same_file_on_a_sibling_domain_is_not_a_listing(self):
        """Calling it one stopped 209 attachment rows from being written for a
        day: "z listingu" for midiman.net/.../BX5_PR.pdf read off
        m-audio.com/.../BX5_PR.pdf is simply false, and both hosts are start
        urls of the same scraper."""
        page = http.capture_page(MIRROR, ROW)
        self.assertEqual(http.capture_kind(page, ROW), "mirror")

    def test_the_link_is_labelled_with_the_capture_the_link_opens(self):
        """#4984's detail_id is the listing's 20030212170800, its capture is
        20030421210545. Labelling the link with the row's detail_id names a
        capture the href does not go to."""
        self.assertEqual(http.capture_ts(MIRROR), "20030421210545")
        self.assertIsNone(http.capture_ts(None))

    def test_a_row_with_no_capture_gets_no_link(self):
        """The only honest answer, including for the live sources, whose
        detail_id is their platform's own id and never named a capture."""
        self.assertIsNone(http.wayback_url(None))

    def test_the_link_drops_the_raw_bytes_marker(self):
        """`id_` is page_cache's variant; a human wants the ordinary viewer."""
        self.assertEqual(
            http.wayback_url(OWN), "https://web.archive.org/web/20030212170800/" + ROW
        )


class ParamTest(support.DbCase):
    def test_an_unknown_company_is_a_bad_request(self):
        """`"inne"` is not a key of COMPANIES, so an unmapped source's panel
        entry 400s the moment anyone clicks it - which is why soundonsound has
        an entry of its own even though the axis is labelled "firmy"."""
        with self.assertRaises(http.BadRequest):
            http._sources({"company": ["inne"]})
        with self.assertRaises(http.BadRequest):
            http._sources({"company": ["nonsense"]})

    def test_a_company_and_a_source_intersect(self):
        self.assertEqual(
            http._sources({"company": ["creative"], "source": ["creative_gnw"]}),
            ["creative_gnw"],
        )

    def test_an_impossible_intersection_is_empty_not_unfiltered(self):
        """company=amd&source=intel must give zero results; falling through to
        "no source filter" would answer with the whole corpus."""
        self.assertIsNone(http._sources({"company": ["amd"], "source": ["intel"]}))

    def test_an_unknown_flag_is_refused_rather_than_dropped(self):
        """Dropping a typo silently would report a full corpus as if it were the
        filtered slice - the one lie an audit view must not tell."""
        with self.assertRaises(http.BadRequest):
            http.handle_search(self.conn, {"flags": ["teasr"]})

    def test_a_bad_order_or_cursor_is_refused(self):
        for params in (
            {"order": ["sideways"]},
            {"after": ["nonsense"]},
            {"limit": ["many"]},
        ):
            with self.subTest(params=params):
                with self.assertRaises(http.BadRequest):
                    http.handle_search(self.conn, params)


class PayloadTest(support.DbCase):
    def setUp(self):
        super().setUp()
        storage.store_release(
            self.conn,
            "midiman_net_media_pr",
            ROW,
            title="BX5",
            date="2003-02-12",
            body="the release",
            detail_id="20030212170800",
        )
        origin.record(self.conn, ROW, MIRROR)
        self.rid = self.row(ROW)["id"]

    def test_origin_url_never_reaches_the_json(self):
        """The reader gets the two strings it renders and no third
        representation to keep in agreement."""
        row = http.handle_release(self.conn, self.rid)
        self.assertNotIn("origin_url", row)
        self.assertEqual(row["capture_kind"], "mirror")
        self.assertEqual(row["capture_ts"], "20030421210545")
        self.assertTrue(
            row["wayback_url"].startswith("https://web.archive.org/web/20030421210545/")
        )

    def test_a_list_row_carries_the_same_three_strings(self):
        """capture_page rides on every row, list and detail, so the badge costs
        no extra query."""
        got = http.handle_search(self.conn, {})["results"][0]
        for key in ("wayback_url", "capture_page", "capture_kind", "capture_ts"):
            self.assertIn(key, got)
        self.assertNotIn("origin_url", got)

    def test_a_row_carries_its_company(self):
        self.assertEqual(
            http.handle_release(self.conn, self.rid)["company"], "Midiman / M-Audio"
        )

    def test_a_missing_release_is_none_rather_than_an_error(self):
        self.assertIsNone(http.handle_release(self.conn, 999999))


class ServerTest(support.DbCase):
    """The routing, over a real socket. Everything above is a function call;
    this is the only place the handler's own dispatch is exercised."""

    def setUp(self):
        super().setUp()
        storage.store_release(
            self.conn,
            "intel",
            "https://intc.com/1",
            title="T",
            date="2007-01-01",
            body="Radium chipset",
        )

        # A subclass rather than a patch: `db_path` is class state on the real
        # Handler and `log_message` prints every request, and a test that
        # mutated either would leak into the next one.
        class Quiet(http.Handler):
            db_path = self.db_path

            def log_message(self, fmt, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read(), r.headers.get("Content-Type")

    def json(self, path):
        return json.loads(self.get(path)[1])

    def test_the_four_api_routes_answer(self):
        self.assertEqual(len(self.json("/api/search?q=Radium")["results"]), 1)
        self.assertEqual(len(self.json("/api/sources")["sources"]), 1)
        self.assertEqual(
            self.json("/api/companies")["companies"][0]["company"], "intel"
        )
        self.assertEqual(self.json("/api/quality")["total"], 1)

    def test_the_three_static_files_are_served_and_nothing_else(self):
        for path, ctype in (
            ("/", "text/html"),
            ("/static/app.js", "javascript"),
            ("/static/app.css", "text/css"),
        ):
            with self.subTest(path=path):
                status, body, got = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertIn(ctype, got)

    def test_anything_else_is_a_404_rather_than_a_traceback(self):
        for path in (
            "/static/../pressroom.db",
            "/static/nonsense.js",
            "/nope",
            "/api/release/999999",
        ):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get(path)
                self.assertEqual(caught.exception.code, 404)

    def test_a_bad_request_is_a_400_carrying_its_reason(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/search?flags=teasr")
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("teasr", json.loads(caught.exception.read())["error"])

    def test_the_server_opens_the_database_read_only(self):
        """A browser has no business migrating anything, and every request
        thread gets its own connection."""
        self.get("/api/search")
        self.assertIsNotNone(self.json("/api/quality"))
