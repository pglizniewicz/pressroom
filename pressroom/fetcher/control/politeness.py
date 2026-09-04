#!/usr/bin/env python3
"""Shared HTTP client policy: how we identify ourselves, how fast we hammer
other people's servers, and which pages we keep.

Three ways to fetch, chosen by what the page is. An archive.org capture goes
through `archive.fetch_snapshot` and is cached forever: a snapshot never
changes. A live site's article goes through `fetch_cached` and is cached too,
with `refetch=True` to fetch it again when it did change. A live site's listing
goes through `fetch` and is never cached: it changes with every release the site
publishes, so a copy would say what the site said once.
"""

import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from pressroom.fetcher.entity import page

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}

# Pause between content fetches. archive.org in particular starts refusing
# connections without it, and a scrape of a dead site is never urgent.
SLEEP = 1.5


def _get(session: requests.Session, url: str, timeout: int):
    """The one GET: our headers, and an HTTP error is an exception."""
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def fetch(
    session: requests.Session,
    url: str,
    timeout: int = 20,
    sleep: float | None = None,
) -> bytes:
    """A live page's bytes, kept nowhere.

    For what a crawler reads off a live site - its listings. A listing changes
    every time the site publishes a release, and a rerun exists to see exactly
    that, so a cached copy would be a lie about the site: soundonsound's crawler
    cached its listings once and no rerun ever saw a newer article. No `conn`
    in the signature is the proof - this cannot write to page_cache.

    Every call is a fetch, so every call sleeps: `sleep` is the site's
    Crawl-delay where it publishes one, SLEEP otherwise.
    """
    content = _get(session, url, timeout).content
    time.sleep(SLEEP if sleep is None else sleep)
    return content


def fetch_cached(
    conn: sqlite3.Connection,
    session: requests.Session,
    url: str,
    timeout: int = 20,
    sleep: float | None = None,
    refetch: bool = False,
) -> bytes:
    """A live article's bytes, kept in page_cache from the first fetch on.

    The live-site counterpart to archive.fetch_snapshot, and it exists for the
    same reason: a parser fix must not cost a refetch. Most of this corpus was
    crawled twice because the live sources went straight through `session.get`.

    An article can change after publication, rarely, and `refetch=True` is how
    that is caught up with: the lookup is skipped, the page is fetched again and
    the row replaced (`page.store(replace=True)`, where the archive's `IGNORE`
    would fetch the new bytes and keep the old). A scraper
    exposes it as `--refetch`, on request; nothing here decides on its own that
    a cached article has gone stale.

    Deliberately *not* merged with archive.fetch_snapshot. That one logs every
    attempt to wayback_calls and probes the capture's `fw_` variant for
    archive.org's own charset guess; parameterising those away would leave a
    function whose signature is longer than either body.

    Returns bytes, never str: decoding is the caller's explicit choice
    (decoding.decode_html or an explicit from_encoding), never a guess.

    `sleep` overrides SLEEP for one source that asks for more patience than
    the rest: soundonsound.com publishes `Crawl-delay: 30` in its robots.txt,
    and a per-call override is the way to honour that without slowing every
    other scraper to a crawl. Only a real fetch sleeps - a cache hit stays
    free, which makes an interrupted run cheap to resume.
    """
    if not refetch:
        row = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?", (url,)
        ).fetchone()
        if row:
            return row[0]

    r = _get(session, url, timeout)
    content = r.content

    bs4_encoding = None
    try:
        if not content[:8].startswith(b"%PDF"):
            bs4_encoding = BeautifulSoup(content, "html.parser").original_encoding
    except Exception:
        pass

    page.store(
        conn,
        url,
        content,
        content_type=r.headers.get("Content-Type"),
        bs4_encoding=bs4_encoding,
        replace=refetch,
    )
    time.sleep(SLEEP if sleep is None else sleep)
    return content
