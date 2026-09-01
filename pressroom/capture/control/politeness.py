#!/usr/bin/env python3
"""Shared HTTP client policy: how we identify ourselves, how fast we hammer
other people's servers, and how we avoid asking twice.
"""

import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from pressroom.capture.entity import page

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}

# Pause between content fetches. archive.org in particular starts refusing
# connections without it, and a scrape of a dead site is never urgent.
SLEEP = 1.5


def fetch_cached(
    conn: sqlite3.Connection,
    session: requests.Session,
    url: str,
    timeout: int = 20,
    sleep: float | None = None,
) -> bytes:
    """Fetch a live page's raw bytes, caching them in page_cache on first hit.

    The live-site counterpart to archive.fetch_snapshot, and it exists for the
    same reason: a parser fix must not cost a refetch. Most of this corpus was
    crawled twice because the live sources went straight through `session.get`.

    Deliberately *not* merged with archive.fetch_snapshot. That one logs every
    attempt to wayback_calls and probes the capture's `fw_` variant for
    archive.org's own charset guess; parameterising those away would leave a
    function whose signature is longer than either body.

    Returns bytes, never str: decoding is the caller's explicit choice
    (encoding.decode_html or an explicit from_encoding), never a guess.

    `sleep` overrides SLEEP for one source that asks for more patience than
    the rest: soundonsound.com publishes `Crawl-delay: 30` in its robots.txt,
    and a per-call override is the way to honour that without slowing every
    other scraper to a crawl. Only a real fetch sleeps - a cache hit stays
    free, which is what makes an interrupted run cheap to resume.
    """
    row = conn.execute(
        "SELECT content FROM page_cache WHERE url = ?", (url,)
    ).fetchone()
    if row:
        return row[0]

    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    content = r.content

    bs4_encoding = None
    try:
        if not content[:8].startswith(b"%PDF"):
            bs4_encoding = BeautifulSoup(content, "html.parser").original_encoding
    except Exception:
        pass

    conn.execute(
        "INSERT OR IGNORE INTO page_cache "
        "(url, content, id_content_type, bs4_encoding, content_sha256, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            url,
            content,
            r.headers.get("Content-Type"),
            bs4_encoding,
            page.content_hash(content),
            time.time(),
        ),
    )
    conn.commit()
    time.sleep(SLEEP if sleep is None else sleep)
    return content
