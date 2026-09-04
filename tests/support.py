"""Shared machinery for the tests, so no test file re-invents it.

Three things every file here needs and exactly one of them is subtle:

  temp_db()   a real pressroom.db, empty, in a tempfile - built by the same
              init_db() the scrapers run, and seeded through the write path
              rather than by raw SQL, so what is under test is what runs.
  fixture()   an archived capture, as **bytes**. Never str: choosing the
              charset is the caller's decision everywhere in this repo, and a
              fixture that arrived decoded would test something else.
  no_network()  a context manager that makes any outbound request raise.

Two fake sessions sit next to the third, `Refusing` and `Serving`: what a test
hands to code that takes a session, to say what the network would have said.

The subtle one is the third. It raises a **BaseException**, not an Exception,
because several fetch sites in this tree wrap their call in `except Exception`
and degrade gracefully, so a probe raising an Exception is swallowed and the
source under test reports clean. The first version of this probe did exactly
that and reported five network-crawling sources as offline.
"""

import contextlib
import gzip
import json
import os
import pathlib
import socket
import tempfile
import unittest

import requests

from pressroom.database.control import connection

HERE = pathlib.Path(__file__).resolve().parent
CAPTURES = HERE / "fixtures" / "captures"
GOLDEN = HERE / "fixtures" / "golden"
MANIFEST = HERE / "fixtures" / "manifest.json"

# The real corpus, for the tests that can only run against it. Honours
# PRESSROOM_DB the same way every command does.
CORPUS_DB = pathlib.Path(os.environ.get("PRESSROOM_DB") or HERE.parent / "pressroom.db")
HAVE_CORPUS = CORPUS_DB.exists()
needs_corpus = unittest.skipUnless(HAVE_CORPUS, f"no corpus database at {CORPUS_DB}")


# --- fixtures ---------------------------------------------------------------


def manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def fixture(name: str) -> bytes:
    """The archived bytes of one committed capture.

    Via the manifest's `file`, because several fixtures share one capture: the
    three media_pr domains mirror the same listing, and so do the two pressdb
    ones. Sharing the blob keeps the same bytes from being committed three times.
    """
    stem = manifest().get(name, {}).get("file", name)
    return gzip.decompress((CAPTURES / f"{stem}.gz").read_bytes())


def golden(name: str):
    return json.loads((GOLDEN / f"{name}.json").read_text())


# --- a database -------------------------------------------------------------


class DbCase(unittest.TestCase):
    """A TestCase with `self.conn` on an empty pressroom.db.

    `connection.DB_PATH` is redirected for the duration, because the modules a
    scraper calls read it at call time; restored in tearDown so one test cannot
    leak a database into the next.
    """

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = pathlib.Path(path)
        self.addCleanup(self.db_path.unlink, missing_ok=True)

        self._saved_db_path = connection.DB_PATH
        connection.DB_PATH = self.db_path
        self.addCleanup(setattr, connection, "DB_PATH", self._saved_db_path)

        # connection.connect(), not a bare sqlite3.connect: it is the write
        # path's own opener, so a test runs against the same connection the
        # scrapers get - row_factory included. A fixture on a differently
        # configured connection tests a database this repo does not have.
        self.conn = connection.connect(self.db_path)
        self.addCleanup(self.conn.close)

    # Seeding goes through storage.store_release rather than an INSERT of its
    # own: the write path is under test in half these files, and a fixture that
    # bypassed it would be testing a table this repo does not have.
    def seed(self, source="src", url=None, **kw):
        from pressroom.release.control import storage

        url = url or f"http://example.test/{source}/{self.id()}/{len(self.urls)}"
        storage.store_release(self.conn, source, url, **kw)
        self.urls.append(url)
        return url

    @property
    def urls(self):
        if not hasattr(self, "_urls"):
            self._urls = []
        return self._urls

    def cache(self, key: str, content: bytes):
        """Put bytes in page_cache under a capture address, through the table's
        one writer - `page.store` - so a test seeds what the fetchers write,
        hash and time included. `replace=True` because a test re-seeding a key
        means it."""
        from pressroom.fetcher.entity import page

        page.store(self.conn, key, content, replace=True)

    def row(self, url: str) -> dict:
        cur = self.conn.execute(
            "SELECT id, source, detail_id, title, date, url, body, grade, body_html"
            "  FROM releases WHERE url = ?",
            (url,),
        )
        got = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], got)) if got else None


# --- the network, refused ---------------------------------------------------


class NetworkTouched(BaseException):
    """Deliberately not an Exception - see this module's docstring."""


@contextlib.contextmanager
def no_network():
    """Any outbound request inside this block raises NetworkTouched.

    Three layers, because one is not enough: requests' Session (what every
    scraper uses), the module-level shortcuts, and socket.connect underneath
    both - which also catches anything that uses urllib or http.client
    directly.
    """

    def refuse(*a, **kw):
        raise NetworkTouched(f"outbound request: {a[:2]}")

    saved = [
        (requests.Session, "request", requests.Session.request),
        (requests, "get", requests.get),
        (requests, "post", requests.post),
        (socket.socket, "connect", socket.socket.connect),
        (socket.socket, "connect_ex", socket.socket.connect_ex),
    ]
    for obj, name, _ in saved:
        setattr(obj, name, refuse)
    try:
        yield
    finally:
        for obj, name, original in saved:
            setattr(obj, name, original)


class Refusing:
    """A session whose every request fails, the way a dead network does.

    The hermetic contract's other half: a page is either in page_cache already
    or the session refuses, so seeding the cache and passing this proves the
    code under test never reached for the network. The default error is an
    Exception, because that is what the fetch sites catch; pass
    `NetworkTouched` to make a fetch fail the test instead of being handled.
    """

    def __init__(self, error=None):
        self.error = error or OSError("connection reset by peer")

    def get(self, *a, **kw):
        raise self.error


class Served:
    """What `Serving.get` hands back: the three things politeness reads off a
    response."""

    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"Content-Type": "text/html"}

    def raise_for_status(self):
        pass


class Serving:
    """A session that answers every request with the same bytes and remembers
    what it was asked for - the proof that a fetch happened, and of what."""

    def __init__(self, content: bytes):
        self.content = content
        self.urls = []

    def get(self, url, *a, **kw):
        self.urls.append(url)
        return Served(self.content)
