"""Local read-only HTTP browser over pressroom.db.

The second reader here, next to the search CLI, and like it a dependency-free
one: stdlib http.server, a JSON API, and one static page of vanilla JS. No
framework, no build step, nothing added to pyproject.toml.

What it deliberately does NOT do: write. It opens the database through
connect_ro(), never connect(), so it never calls init_db(), never creates a
table, never touches the FTS triggers, and an accidental write is an OperationalError
from SQLite rather than a quietly corrupted index. It binds 127.0.0.1 only -
there is no auth because there is no remote listener.

A connection lives exactly as long as the request that needs one, and closes
with it. ThreadingHTTPServer runs every request in a thread of its own and
joins none of them, so anything held past the end of do_GET is a file
descriptor nothing will ever close - see docs/adr/browser-panel.md.

Owns exactly one concern: HTTP. Every statement it runs lives in the release
component's query module; there is no SQL in this file.

Usage:
  pressroom-serve                                     # http://127.0.0.1:8765
  pressroom-serve --port 9000 --db /tmp/copy.db
"""

import argparse
import contextlib
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pressroom.taxonomy.entity import company
from pressroom.database.control import connection
from pressroom.provenance.entity import origin
from pressroom.release.control import query

STATIC_DIR = Path(__file__).parent / "static"

# A whitelist, not a path join: with only these three names reachable there is
# no way to express traversal ('/static/../http.py' simply isn't a key here).
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


VALID_FLAGS = ("teaser", "short", "nodate", "mojibake", "plain")


class BadRequest(Exception):
    """A malformed query parameter - answered as 400 JSON, not a traceback."""


def capture_ts(origin_url):
    """The timestamp of the capture a row's body came from, or None.

    Taken from the recorded capture, never from `releases.detail_id`: for a row
    whose bytes came off a sibling domain those two differ (#4984's detail_id is
    the listing's 20030212170800, its capture is 20030421210545), and labelling
    the link with the wrong one is the same misreading this whole table exists
    to end.
    """
    if not origin_url:
        return None
    m = re.search(r"/web/(\d{14})", origin_url)
    return m.group(1) if m else None


def capture_kind(page, row_url):
    """How the capture's page relates to the row's own url: 'mirror' | 'other'.

    A copy of the *same file* on a sibling domain is not a listing, and calling
    it one is what stopped 209 attachment rows from being written for a day: the
    browser's badge said "z listingu", which for `midiman.net/.../BX5_PR.pdf`
    read off `m-audio.com/.../BX5_PR.pdf` is simply false. Both addresses are
    start urls of one scraper (its own DOMAINS dict lists them side by side), so
    the honest label is "a copy from another domain".

    The file name decides, and only here: it is a *label*, never an identity
    test. Identity was established by the bytes (page_cache.content_sha256) and
    by the extracted text matching, path-with-domain-swapped - matching on the
    name alone once paired a row with a different release (#5343).
    """
    if not page or not row_url:
        return None
    return (
        "mirror"
        if page.rsplit("/", 1)[-1].lower() == row_url.rsplit("/", 1)[-1].lower()
        else "other"
    )


def capture_page(origin_url, row_url):
    """The page a `body_origin.origin_url` is a capture of - but only when
    that is *not* the row's own url, since that is the only case the reader has
    anything to say about.

    The split itself is origin.page_of - one implementation, shared with the
    provenance repair. What belongs here is the comparison: since body_origin
    covers every Wayback row, not just the discrepant ones, this is what keeps
    "(capture strony: …)" and the "z listingu" badge off the 1605 rows whose
    capture *is* of their own page."""
    page = origin.page_of(origin_url)
    return page if page and page != row_url else None


def wayback_url(origin_url):
    """The archive link for a row, or None if it has no capture.

    Reads `db.body_origin` and nothing else. It used to build the address from
    `detail_id` whenever that field looked like 14 digits, which was wrong twice
    over: the shape of an id is not a statement about what it refers to, and for
    a row whose text came off a listing the address it produced was a capture
    that never existed (#4414). A row with no recorded capture now gets no link,
    which is the only honest answer - including for the live sources, whose
    detail_id is their own platform's id and never named a capture at all.

    Stored minus the `id_` marker, so the reader lands on the ordinary viewer
    page rather than page_cache's raw-bytes variant.
    """
    return origin_url.replace("id_/", "/", 1) if origin_url else None


def _one(params, key, default=None):
    values = params.get(key) or []
    return values[0].strip() if values and values[0].strip() else default


def _many(params, key):
    """Repeated or comma-separated: ?source=a&source=b and ?source=a,b both."""
    out = []
    for raw in params.get(key) or []:
        out.extend(part.strip() for part in raw.split(",") if part.strip())
    return out


def _int(params, key, default):
    raw = _one(params, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise BadRequest(f"{key} must be an integer, got {raw!r}")


def _sources(params) -> list[str] | None:
    """The source filter, from `source=`, `company=`, or both.

    A company IS a list of sources (company.sources_for), so nothing here
    reaches for new SQL. Given both, the answer is their intersection - picking
    a company and then one of its mirrors narrows, it does not widen. Returns
    None when that intersection is empty (company=amd&source=intel): a filter
    nothing can match, which is a legitimate empty result, not a bad request,
    and must not fall through to "no source filter" = the whole corpus.
    """
    picked = _many(params, "source")
    slugs = _many(params, "company")
    unknown = company.unknown_slugs(slugs)
    if unknown:
        raise BadRequest(
            f"unknown company(ies): {', '.join(unknown)}; "
            f"valid: {', '.join(company.COMPANIES)}"
        )
    if not slugs:
        return picked
    from_companies = company.sources_for(slugs)
    if not picked:
        return from_companies
    return [s for s in picked if s in from_companies] or None


EMPTY_PAGE = {"results": [], "next": None, "truncated": False, "query_mode": None}


def handle_search(conn, params) -> dict[str, Any]:
    flags = _many(params, "flags")
    unknown = [f for f in flags if f not in VALID_FLAGS]
    if unknown:
        # Dropping a typo silently would report a full corpus as if it were the
        # filtered slice - the one lie an audit view must not tell.
        raise BadRequest(
            f"unknown flag(s): {', '.join(unknown)}; valid: {', '.join(VALID_FLAGS)}"
        )
    order = _one(params, "order", "rank")
    if order not in ("rank", "date"):
        raise BadRequest("order must be 'rank' or 'date'")
    after = _one(params, "after")
    if after and not re.match(r"^[od]:", after):
        raise BadRequest("malformed cursor")
    sources = _sources(params)
    if sources is None:
        return dict(EMPTY_PAGE)
    try:
        payload = query.search_releases(
            conn,
            _one(params, "q", "") or "",
            sources=sources,
            date_from=_one(params, "from"),
            date_to=_one(params, "to"),
            order=order,
            flags=flags,
            limit=_int(params, "limit", 50),
            after=after,
        )
    except (ValueError, sqlite3.OperationalError) as e:
        raise BadRequest(str(e))
    for row in payload["results"]:
        row["wayback_url"] = wayback_url(row.get("origin_url"))
        cap = row.pop("origin_url", None)
        page = capture_page(cap, row["url"])
        row["capture_page"] = page
        row["capture_kind"] = capture_kind(page, row["url"])
        row["capture_ts"] = capture_ts(cap)
        row["company"] = company.label(company.company_of(row["source"]))
    return payload


def handle_release(conn, rid: int):
    row = query.get_release(conn, rid)
    if row is None:
        return None
    row["wayback_url"] = wayback_url(row.get("origin_url"))
    cap = row.pop("origin_url", None)
    page = capture_page(cap, row["url"])
    row["capture_page"] = page
    row["capture_kind"] = capture_kind(page, row["url"])
    row["capture_ts"] = capture_ts(cap)
    row["body_len"] = len(row["body"])
    row["company_slug"] = company.company_of(row["source"])
    row["company"] = company.label(row["company_slug"])
    row["neighbours"] = query.neighbours(conn, rid)
    return row


class Handler(BaseHTTPRequestHandler):
    server_version = "pressroom"
    db_path = None

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        params = parse_qs(parts.query)

        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/") :])

            if not path.startswith("/api/"):
                return self._json({"error": "not found"}, status=404)

            # One connection per request, closed with it - `closing` covers the
            # error paths below too. Nothing is cached across requests because
            # there is no thread to cache it on: a request gets a thread of its
            # own and that thread exits with the response.
            with contextlib.closing(connection.connect_ro(self.db_path)) as conn:
                if path == "/api/search":
                    return self._json(handle_search(conn, params))
                if path == "/api/sources":
                    return self._json({"sources": query.list_sources(conn)})
                if path == "/api/companies":
                    # Company rows carry their own sources, so the panel gets
                    # both levels - the default company view and the source
                    # view it toggles to - from one request.
                    return self._json(
                        {"companies": company.roll_up(query.list_sources(conn))}
                    )
                if path == "/api/quality":
                    return self._json(query.quality_counts(conn))
                if path.startswith("/api/release/"):
                    try:
                        rid = int(path.rsplit("/", 1)[1])
                    except ValueError:
                        raise BadRequest("release id must be an integer")
                    row = handle_release(conn, rid)
                    if row is None:
                        return self._json({"error": "no such release"}, status=404)
                    return self._json(row)
                self._json({"error": "not found"}, status=404)
        except BadRequest as e:
            self._json({"error": str(e)}, status=400)
        except BrokenPipeError:
            pass  # browser navigated away mid-response
        except Exception as e:  # never take the server down over one request
            self._json({"error": f"{type(e).__name__}: {e}"}, status=500)

    def _static(self, name: str) -> None:
        ctype = STATIC_FILES.get(name)
        if ctype is None:
            return self._json({"error": "not found"}, status=404)
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            return self._json({"error": f"missing static file: {name}"}, status=500)
        self._send(body, ctype)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        print(
            f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=None, help="alternate database path")
    args = parser.parse_args()

    path = Path(args.db) if args.db else connection.DB_PATH
    if not path.exists():
        print(f"Database not found at {path}. Run a scraper first.")
        return

    Handler.db_path = path
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        # Almost always an instance left running from a previous session; a
        # traceback here says nothing a one-line answer doesn't.
        print(f"Cannot listen on 127.0.0.1:{args.port}: {e}")
        print(
            "Another pressroom browser is probably still running "
            "(pgrep -af '[p]ressroom-serve'), or pass --port."
        )
        return
    print(f"pressroom browser: http://127.0.0.1:{args.port}  ({path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()
