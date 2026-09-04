"""Local read-only HTTP browser over pressroom.db.

The second reader here, next to the search CLI, and like it a dependency-free
one: stdlib http.server, a JSON API, and one static page of vanilla JS. No
framework, no build step, nothing added to pyproject.toml.

What it does NOT do: write. `connect_ro()`, never `connect()`, so
an accidental write is an OperationalError rather than a quietly corrupted
index; and 127.0.0.1 only, so there is no auth because there is no remote
listener.

A connection lasts exactly as long as the request that needs one and closes with
it - ThreadingHTTPServer joins none of its threads, so anything held past the
end of do_GET is a descriptor nothing will close.

Owns one concern: HTTP. There is no SQL in this file.

→ docs/adr/browser-panel.md

Usage:
  pressroom-serve                                     # http://127.0.0.1:8765
  pressroom-serve --port 9000 --db /tmp/copy.db
"""

import argparse
import contextlib
import json
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pressroom.taxonomy.entity import company
from pressroom.database.control import connection
from pressroom.provenance.control import resolution
from pressroom.fetcher.control import address
from pressroom.release.control import query

# The only address the server ever binds: there is no remote listener (R1.1).
HOST = "127.0.0.1"

STATIC_DIR = Path(__file__).parent / "static"

# An allowlist, not a path join: with only these three names reachable there is
# no way to express traversal.
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


VALID_FLAGS = ("teaser", "short", "nodate", "mojibake", "plain")


class BadRequest(Exception):
    """A malformed query parameter - answered as 400 JSON, not a traceback."""


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
    needs new SQL. Given both, the answer is their intersection - picking
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
        # filtered slice - the one wrong answer an audit view must not give.
        raise BadRequest(
            f"unknown flag(s): {', '.join(unknown)}; valid: {', '.join(VALID_FLAGS)}"
        )
    order = _one(params, "order", "rank")
    if order not in ("rank", "date"):
        raise BadRequest("order must be 'rank' or 'date'")
    after = _one(params, "after")
    if after and not re.match(r"^[od]:", after):
        raise BadRequest(
            f"malformed cursor {after!r}: after must be the 'next' value of an "
            "earlier answer"
        )
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
        _annotate(row)
    return payload


def _annotate(row) -> None:
    """The capture facts and the company a list row and the detail share
    (R3.8, R4.1): the viewer link and timestamp off the recorded origin, the
    capture's page and its kind only when that page is not the row's own, and
    the raw origin address dropped - the reader gets the strings it renders and
    no third representation to keep in agreement."""
    cap = row.pop("origin_url", None)
    row["wayback_url"] = address.viewer_url(cap)
    page = resolution.capture_page(cap, row["url"])
    row["capture_page"] = page
    row["capture_kind"] = resolution.capture_kind(page, row["url"])
    row["capture_ts"] = address.timestamp_of(cap)
    row["company_slug"] = company.company_of(row["source"])
    row["company"] = company.label(row["company_slug"])


def handle_release(conn, rid: int):
    row = query.get_release(conn, rid)
    if row is None:
        return None
    _annotate(row)
    row["body_len"] = len(row["body"])
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

            # `closing` covers the error paths below too.
            with contextlib.closing(connection.connect_ro(self.db_path)) as conn:
                if path == "/api/search":
                    return self._json(handle_search(conn, params))
                if path == "/api/sources":
                    return self._json({"sources": query.list_sources(conn)})
                if path == "/api/companies":
                    # Company rows carry their own sources, so both levels of
                    # the panel come from one request.
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


def listen(db_path: Path, port: int) -> ThreadingHTTPServer:
    """Bind on the local machine only, over one database file, and serve
    nothing yet: the caller runs the loop. Raises OSError when the port is
    taken."""
    Handler.db_path = db_path
    return ThreadingHTTPServer((HOST, port), Handler)


def main(argv=None) -> int:
    """The exit status is returned, not raised: the console script wrapper
    passes it to sys.exit, and a test can call this and read it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=None, help="alternate database path")
    args = parser.parse_args(argv)

    path = Path(args.db) if args.db else connection.DB_PATH
    if not path.exists():
        print(f"Database not found at {path}. Run a scraper first.", file=sys.stderr)
        return 1

    try:
        server = listen(path, args.port)
    except OSError as e:
        # Almost always an instance left running from a previous session; a
        # traceback here says nothing a one-line answer doesn't.
        print(f"Cannot listen on {HOST}:{args.port}: {e}", file=sys.stderr)
        print(
            "Another pressroom browser is probably still running "
            "(pgrep -af '[p]ressroom-serve'), or pass --port.",
            file=sys.stderr,
        )
        return 1
    print(f"pressroom browser: http://{HOST}:{args.port}  ({path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0
