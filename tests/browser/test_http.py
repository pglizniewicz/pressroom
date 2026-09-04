"""The browser boundary, R1–R5 of `pressroom/browser/__init__.py`: what the BC
answers to a request. The first word of every test's docstring is the id of the
statement it holds (the spec's D2); `test_spec_trace.py` keeps the two lists in
agreement. R6–R11, what the page renders, are `checks.md`'s.

The three strings next to a capture link are derived by their owners -
`address.timestamp_of`/`viewer_url` and `resolution.capture_page`/`capture_kind`,
tested there - and this file asserts they arrive in the JSON and `origin_url`
does not.
"""

import contextlib
import io
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from pressroom.browser.boundary import http
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.taxonomy.entity import company
from tests import support

ROW = "http://www.midiman.net/images/press/BX5_PR.pdf"
MIRROR = (
    "https://web.archive.org/web/20030421210545id_/"
    "http://www.m-audio.com/images/press/BX5_PR.pdf"
)
ARTICLE = "http://www.terratec.de/September_2011_-_DAB_151650.html"
LISTING = (
    "https://web.archive.org/web/20111011173713id_/http://www.terratec.de/presse.html"
)
OWN_PAGE = "http://www.terratec.de/January_2012_-_Aureon_151700.html"
OWN_CAPTURE = "https://web.archive.org/web/20120104073622id_/" + OWN_PAGE

QUALITY_COUNTERS = {
    "total",
    "teaser",
    "stub",
    "wayback",
    "platform_id",
    "short",
    "empty",
    "nodate",
    "mojibake",
    "plain",
}


class ServerCase(support.DbCase):
    """A real socket, so the handler's own dispatch is what runs. Everything
    that can be a function call is one, in the other classes."""

    def setUp(self):
        super().setUp()

        # A subclass rather than a patch: `db_path` is class state on the real
        # Handler and `log_message` prints every request, and a test that
        # mutated either would leak into the next one.
        class Quiet(http.Handler):
            db_path = self.db_path

            def log_message(self, fmt, *args):
                pass

        self.handler = Quiet
        self.server = self.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self, server_cls=ThreadingHTTPServer):
        server = server_cls(("127.0.0.1", 0), self.handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def get(self, path):
        """(status, body, headers) - an error answer is an answer, not a raise."""
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read(), e.headers
            finally:
                e.close()

    def json(self, path):
        status, body, _ = self.get(path)
        return status, json.loads(body)


# --- R1: serve ----------------------------------------------------------------


class ServeTest(ServerCase):
    def test_the_listener_is_bound_to_the_local_machine(self):
        """R1.1 - `listen()` binds 127.0.0.1 and nothing else, whatever the
        port; there is no remote caller to authenticate (D3)."""
        server = http.listen(self.db_path, 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertIs(http.Handler.db_path, self.db_path)

    def test_a_missing_database_is_an_error_on_stderr_and_a_non_zero_exit(self):
        """R1.2 - the console script passes `main()`'s return to sys.exit, so a
        return of 1 is the non-zero status; and `main()` returning at all is
        the proof that it never reached `serve_forever`."""
        missing = self.db_path.with_suffix(".absent.db")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = http.main(["--db", str(missing)])
        self.assertNotEqual(status, 0)
        self.assertIn(str(missing), err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_a_taken_port_is_named_on_stderr_and_a_non_zero_exit(self):
        """R1.3 - the port is held by a plain socket here, the way an instance
        left running from the previous session holds it."""
        taken = socket.socket()
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = http.main(["--db", str(self.db_path), "--port", str(port)])
        self.assertNotEqual(status, 0)
        self.assertIn(str(port), err.getvalue())
        self.assertIn("still running", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_every_request_opens_a_read_only_connection_and_closes_it(self):
        """R1.4 - one connection per api request, refusing a write, and closed
        by the time the request thread has ended; a static file opens none.

        The leak this test exists to keep out: the connection used to be kept
        on a threading.local and never closed, so a suite run printed one
        `ResourceWarning: unclosed database` per request that reached the
        database, and a long browsing session accumulated descriptors.

        `daemon_threads = False` is what makes the assertion deterministic
        rather than a poll: server_close() then joins the request threads, so
        by the time it returns every handler has left its `with`. The client
        otherwise has the response before the server has closed anything.
        """
        self.seed("intel", title="T", date="2007-01-01", body="Radium chipset")
        opened, refused = [], []
        real = connection.connect_ro

        def spy(db_path=None):
            conn = real(db_path)
            opened.append(conn)
            try:
                conn.execute("DELETE FROM releases WHERE id = -1")
            except sqlite3.OperationalError as e:
                refused.append(str(e))
            return conn

        class Joining(ThreadingHTTPServer):
            daemon_threads = False

        connection.connect_ro = spy
        self.addCleanup(setattr, connection, "connect_ro", real)

        server = Joining(("127.0.0.1", 0), self.handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for path in ("/api/quality", "/api/search?q=Radium", "/api/sources"):
                urllib.request.urlopen(base + path).close()
            urllib.request.urlopen(base + "/static/app.css").close()
        finally:
            server.shutdown()
            server.server_close()

        # Three api requests, three connections - and the static file none.
        self.assertEqual(len(opened), 3)
        self.assertEqual(len(refused), 3)
        for message in refused:
            self.assertIn("readonly", message)
        for conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("select 1")

    def test_an_unhandled_failure_answers_that_request_and_serving_goes_on(self):
        """R1.5 - a bug in one query is a 500 carrying its name, and the next
        request is served as if nothing happened."""
        real = http.query.quality_counts

        def boom(_conn):
            raise RuntimeError("probe")

        http.query.quality_counts = boom
        self.addCleanup(setattr, http.query, "quality_counts", real)

        status, payload = self.json("/api/quality")
        self.assertEqual(status, 500)
        self.assertIn("RuntimeError", payload["error"])
        self.assertEqual(self.json("/api/sources")[0], 200)


# --- R2: open the page ---------------------------------------------------------


class PageTest(ServerCase):
    def test_the_root_and_the_index_both_serve_the_page(self):
        """R2.1 - both paths answer the same bytes, the page file itself."""
        page = (http.STATIC_DIR / "index.html").read_bytes()
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, body, headers = self.get(path)
                self.assertEqual(status, 200)
                self.assertEqual(body, page)
                self.assertIn("text/html", headers["Content-Type"])

    def test_exactly_three_files_are_served_and_no_path_reaches_another(self):
        """R2.2 - the page, its script and its stylesheet by name (D5); a
        traversal, an encoded one, a sibling module and an unknown name are
        all "not found" with no file contents."""
        for path, ctype in (
            ("/static/index.html", "text/html"),
            ("/static/app.js", "javascript"),
            ("/static/app.css", "text/css"),
        ):
            with self.subTest(path=path):
                status, body, headers = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertIn(ctype, headers["Content-Type"])
        for path in (
            "/static/../pressroom.db",
            "/static/%2e%2e/http.py",
            "/static/__init__.py",
            "/static/nonsense.js",
        ):
            with self.subTest(path=path):
                status, body, _ = self.get(path)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body), {"error": "not found"})

    def test_any_other_path_is_a_structured_not_found(self):
        """R2.3 - a JSON error object, never a traceback, for an unknown page,
        an unknown api route and a release that does not exist."""
        for path in ("/nope", "/api/nope", "/api/release/999999"):
            with self.subTest(path=path):
                status, body, headers = self.get(path)
                self.assertEqual(status, 404)
                self.assertIn("application/json", headers["Content-Type"])
                self.assertIn("error", json.loads(body))

    def test_every_answer_is_marked_not_to_be_cached(self):
        """R2.4 - a file, an api answer, a not-found and a refusal alike, so a
        browser never shows yesterday's audit."""
        for path in (
            "/static/app.css",
            "/api/quality",
            "/nope",
            "/api/search?flags=teasr",
        ):
            with self.subTest(path=path):
                _, _, headers = self.get(path)
                self.assertEqual(headers["Cache-Control"], "no-store")


# --- R3: search releases -------------------------------------------------------


class SearchTest(support.DbCase):
    def search(self, **params):
        return http.handle_search(
            self.conn, {k: v if isinstance(v, list) else [v] for k, v in params.items()}
        )

    def titles(self, **params):
        return [r["title"] for r in self.search(**params)["results"]]

    def test_browsing_pages_newest_first_with_dateless_rows_last(self):
        """R3.1 - no text query skips the index and walks `releases` by date,
        which is the only way a row whose date is '' is ever reached."""
        self.seed("intel", title="old", date="2001-05-05", body="a")
        self.seed("intel", title="new", date="2009-05-05", body="b")
        self.seed("intel", title="undated", date="", body="c")
        page = self.search()
        self.assertEqual(self.titles(), ["new", "old", "undated"])
        self.assertIsNone(page["query_mode"])

    def test_a_text_query_matches_title_and_body_and_marks_the_excerpt(self):
        """R3.2 - relevance puts the row with three hits before the row with
        one, date order reverses them, and the body match is marked with the
        `>>>`/`<<<` pair the page turns into `<mark>`. A row matched on its
        title alone carries an unmarked body excerpt: there is no match in it
        to mark."""
        self.seed(
            "intel", title="dense", date="2001-01-01", body="Radium Radium Radium."
        )
        self.seed(
            "intel",
            title="sparse",
            date="2009-01-01",
            body="The Radium chipset ships in volume to partners this quarter.",
        )
        self.seed("intel", title="Radium", date="2005-01-01", body="Nothing here.")
        self.seed("intel", title="none", date="2005-01-01", body="Unrelated text.")

        ranked = self.search(q="Radium")
        self.assertEqual(ranked["query_mode"], "raw")
        by_title = {r["title"]: r for r in ranked["results"]}
        self.assertEqual(set(by_title), {"dense", "sparse", "Radium"})
        self.assertEqual(self.titles(q="Radium")[0], "dense")
        self.assertEqual(
            self.titles(q="Radium", order="date"), ["sparse", "Radium", "dense"]
        )
        for title in ("dense", "sparse"):
            self.assertIn(">>>Radium<<<", by_title[title]["excerpt"])

    def test_an_invalid_expression_is_searched_as_a_phrase(self):
        """R3.3 - `M-Audio`, the likeliest thing typed here, is an FTS5 syntax
        error; the answer is the phrase's hits, marked `literal` (D6)."""
        self.seed("midiman_com", title="T", date="2003-01-01", body="the M-Audio Delta")
        for q in ("M-Audio", "the M-Audio", 'Delta"'):
            with self.subTest(q=q):
                page = self.search(q=q)
                self.assertEqual(page["query_mode"], "literal")
                self.assertEqual(len(page["results"]), 1)

    def test_company_source_dates_and_flags_narrow_in_any_combination(self):
        """R3.4 - each filter alone, then together; a company and one of its
        own sources narrow to that source."""
        self.seed("intel", title="intel", date="2005-01-01", body="x" * 400)
        self.seed("amd", title="amd", date="2010-01-01", body="short", grade="teaser")
        self.seed("midiman_com", title="com", date="2003-01-01", body="y" * 400)
        self.seed("midiman_de", title="de", date="1999-01-01", body="z" * 400)
        cases = [
            (dict(company="intel"), ["intel"]),
            (dict(source="amd"), ["amd"]),
            (dict(**{"from": "2004-01-01", "to": "2006-12-31"}), ["intel"]),
            (dict(flags="teaser"), ["amd"]),
            (dict(flags="short"), ["amd"]),
            (dict(company="maudio", source="midiman_de"), ["de"]),
            (dict(company="intel,amd", **{"from": "2008-01-01"}), ["amd"]),
            (dict(company="maudio", flags="short"), []),
        ]
        for params, expected in cases:
            with self.subTest(**params):
                self.assertEqual(self.titles(**params), expected)

    def test_a_company_crossed_with_a_source_outside_it_is_an_empty_page(self):
        """R3.5 - company=amd&source=intel shares no row: zero results, not the
        whole corpus and not a refusal."""
        self.seed("intel", title="intel", date="2005-01-01", body="x")
        self.seed("amd", title="amd", date="2010-01-01", body="y")
        page = self.search(company="amd", source="intel")
        self.assertEqual(page["results"], [])
        self.assertIsNone(page["next"])

    def test_an_unknown_value_is_refused_and_the_accepted_ones_named(self):
        """R3.6 - a dropped typo would report the whole corpus as the filtered
        slice (D7). `inne` is refused too: an unmapped source is put there, and
        `tests/taxonomy/test_company.py` says why every source has an entry."""
        cases = [
            (dict(flags="teasr"), list(http.VALID_FLAGS)),
            (dict(company="nonsense"), list(company.COMPANIES)),
            (dict(company="inne"), list(company.COMPANIES)),
            (dict(order="sideways"), ["rank", "date"]),
            (dict(after="nonsense"), ["next"]),
            (dict(limit="many"), ["integer"]),
        ]
        for params, named in cases:
            with self.subTest(**params):
                with self.assertRaises(http.BadRequest) as caught:
                    self.search(**params)
                for word in named:
                    self.assertIn(word, str(caught.exception))

    def test_a_page_is_at_most_the_requested_size_with_a_cursor_or_none(self):
        """R3.7 - two of three, then the last one and no cursor; a size beyond
        the cap is cut to it; and relevance paging at its depth cap says
        `truncated` rather than ending quietly."""
        for i in range(1001):
            storage.store_release(
                self.conn,
                "intel",
                f"http://example.test/{i}",
                title=f"probe {i}",
                date=f"{1000 + i}-01-01",
                body="cap probe",
                commit=False,
            )
        self.conn.commit()

        first = self.search(limit="2", to="1002-12-31")
        self.assertEqual(len(first["results"]), 2)
        self.assertIsNotNone(first["next"])
        second = self.search(limit="2", to="1002-12-31", after=first["next"])
        self.assertEqual(len(second["results"]), 1)
        self.assertIsNone(second["next"])

        self.assertEqual(len(self.search(limit="999")["results"]), 200)

        deep = self.search(q="probe", limit="200", after="o:800")
        self.assertEqual(len(deep["results"]), 200)
        self.assertIsNone(deep["next"])
        self.assertTrue(deep["truncated"])
        self.assertFalse(first["truncated"])

    def test_each_result_carries_its_company_damage_and_capture_facts(self):
        """R3.8 - four rows, four cases: a mirror capture, a listing capture, a
        capture of the row's own page, and no capture at all. The page's kind
        and address appear only when the capture is of another page, and the
        raw origin address never."""
        storage.store_release(
            self.conn, "midiman_net_media_pr", ROW, title="mirror", body="t",
            date="2003-02-12", detail_id="20030212170800", origin_url=MIRROR,
        )  # fmt: skip
        storage.store_release(
            self.conn, "terratec_new_de", ARTICLE, title="listing", body="t",
            date="2011-09-01", origin_url=LISTING,
        )  # fmt: skip
        storage.store_release(
            self.conn, "terratec_new_de", OWN_PAGE, title="own", body="t",
            date="2012-01-01", origin_url=OWN_CAPTURE,
        )  # fmt: skip
        # The write repairs a plain body's mojibake, so the damage has to arrive
        # the way it does in the corpus: alongside markup, which is stored as is.
        self.seed(
            "intel",
            title="live",
            date="2007-01-01",
            body="Ã©",
            body_html="<p>Ã©</p>",
            detail_id="9",
        )

        rows = {r["title"]: r for r in http.handle_search(self.conn, {})["results"]}
        for row in rows.values():
            self.assertNotIn("origin_url", row)
            self.assertIn(row["capture_kind"], (None, "mirror", "other"))

        mirror = rows["mirror"]
        self.assertEqual(mirror["company"], "Midiman / M-Audio")
        self.assertFalse(mirror["damaged"])
        self.assertEqual(mirror["capture_kind"], "mirror")
        self.assertIn("www.m-audio.com", mirror["capture_page"])
        self.assertEqual(mirror["capture_ts"], "20030421210545")
        self.assertEqual(mirror["wayback_url"], MIRROR.replace("id_/", "/", 1))

        listing = rows["listing"]
        self.assertEqual(listing["capture_kind"], "other")
        self.assertTrue(listing["capture_page"].endswith("/presse.html"))
        self.assertEqual(listing["capture_ts"], "20111011173713")

        own = rows["own"]
        self.assertIsNone(own["capture_page"])
        self.assertIsNone(own["capture_kind"])
        self.assertEqual(own["capture_ts"], "20120104073622")
        self.assertTrue(own["wayback_url"].startswith("https://web.archive.org/web/"))

        live = rows["live"]
        self.assertEqual(live["company"], "Intel")
        self.assertTrue(live["damaged"])
        for key in ("wayback_url", "capture_page", "capture_kind", "capture_ts"):
            self.assertIsNone(live[key], key)


# --- R4: show a release --------------------------------------------------------


class ReleaseTest(ServerCase):
    def test_a_release_is_answered_in_full_with_its_neighbours(self):
        """R4.1 - text, markup, damage, company and tag, the capture facts, and
        the previous and next row of the same source - the amd row dated
        between them is not a neighbour."""
        first = self.seed("intel", title="first", date="2007-01-01", body="a")
        middle = self.seed(
            "intel", title="middle", date="2007-06-01", body="b", body_html="<p>b</p>"
        )
        last = self.seed("intel", title="last", date="2008-01-01", body="c")
        self.seed("amd", title="other", date="2007-03-01", body="d")

        row = http.handle_release(self.conn, self.row(middle)["id"])
        self.assertEqual(row["body"], "b")
        self.assertEqual(row["body_html"], "<p>b</p>")
        self.assertFalse(row["damaged"])
        self.assertEqual((row["company"], row["company_slug"]), ("Intel", "intel"))
        self.assertEqual(row["source"], "intel")
        for key in ("wayback_url", "capture_page", "capture_kind", "capture_ts"):
            self.assertIn(key, row)
        self.assertNotIn("origin_url", row)
        self.assertEqual(row["neighbours"]["prev"]["id"], self.row(first)["id"])
        self.assertEqual(row["neighbours"]["next"]["id"], self.row(last)["id"])

    def test_a_missing_id_is_not_found_and_a_non_number_is_refused(self):
        """R4.2 - two different answers, because they are two different
        mistakes: a link to a row that is gone, and a malformed request."""
        self.assertIsNone(http.handle_release(self.conn, 999999))
        status, payload = self.json("/api/release/999999")
        self.assertEqual((status, payload), (404, {"error": "no such release"}))
        status, payload = self.json("/api/release/abc")
        self.assertEqual(status, 400)
        self.assertIn("integer", payload["error"])


# --- R5: sources, companies, quality -------------------------------------------


class AuditTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.seed("midiman_com", title="a", date="2003-01-01", body="x" * 400)
        self.seed(
            "midiman_com", title="b", date="2004-01-01", body="short", grade="teaser"
        )
        self.seed("midiman_de", title="c", date="1999-01-01", body="y" * 400)
        self.seed("intel", title="d", date="", body="z" * 400, detail_id="9")

    def test_every_source_is_listed_with_its_size_span_and_gaps(self):
        """R5.1 - biggest first; a dateless source has an empty span; the gap
        counters are the audit table's columns."""
        status, payload = self.json("/api/sources")
        self.assertEqual(status, 200)
        rows = {r["source"]: r for r in payload["sources"]}
        self.assertEqual([r["source"] for r in payload["sources"]][0], "midiman_com")
        self.assertEqual(set(rows), {"midiman_com", "midiman_de", "intel"})
        com = rows["midiman_com"]
        self.assertEqual(
            (com["count"], com["first"], com["last"]), (2, "2003-01-01", "2004-01-01")
        )
        self.assertEqual((com["teaser"], com["short"], com["nodate"]), (1, 1, 0))
        self.assertEqual((rows["intel"]["first"], rows["intel"]["nodate"]), ("", 1))
        for key in (
            "count",
            "first",
            "last",
            "teaser",
            "short",
            "nodate",
            "mojibake",
            "plain",
        ):
            self.assertIn(key, com)

    def test_companies_are_the_sources_rolled_up_and_carry_them(self):
        """R5.2 - one row per company, biggest first, its span widened across
        its own sources, which it carries so the panel needs no second
        request."""
        status, payload = self.json("/api/companies")
        self.assertEqual(status, 200)
        top, second = payload["companies"]
        self.assertEqual(
            (top["company"], top["label"]), ("maudio", "Midiman / M-Audio")
        )
        self.assertEqual(
            (top["count"], top["first"], top["last"]), (3, "1999-01-01", "2004-01-01")
        )
        self.assertEqual(
            {s["source"] for s in top["sources"]}, {"midiman_com", "midiman_de"}
        )
        self.assertEqual(top["teaser"], 1)
        self.assertEqual(second["company"], "intel")

    def test_the_corpus_wide_counters_come_by_name_in_one_answer(self):
        """R5.3 - the ten names the audit view shows, nothing positional."""
        status, payload = self.json("/api/quality")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), QUALITY_COUNTERS)
        self.assertEqual(
            (payload["total"], payload["teaser"], payload["nodate"]), (4, 1, 1)
        )
        self.assertEqual((payload["short"], payload["platform_id"]), (1, 1))
