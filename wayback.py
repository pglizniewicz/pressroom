#!/usr/bin/env python3
"""Generic Wayback Machine helpers, reusable by any dead-site scraper.

Everything that asks archive.org *what exists* goes through the public CDX
API (_cdx), and everything that asks for *content* goes through
fetch_snapshot, which caches it. The three CDX-backed queries are:

  - list_snapshots_by_prefix: every archived URL under a prefix.
  - list_all_captures: every HTTP-200 capture of one exact URL, for pages
    whose content grows over time so a single "latest" would miss revisions.
  - get_latest_working_snapshot: the newest capture of one URL that actually
    returned 200 - most captures of a dead site are 404s.

get_latest_working_snapshot used to drill the private __wb/sparkline and
__wb/calendarcaptures endpoints, which need a forged Referer to answer at all
and cost three requests per URL. CDX answers the same question in one, and
was verified to give identical timestamps on every stored row it was
compared against, including the awkward cases (newest captures are 404s;
only one capture exists; the newest servable capture is a revisit record).
"""

import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from fetch import HEADERS, SLEEP as CONTENT_SLEEP
import progress

CDX_URL = "https://web.archive.org/cdx/search/cdx"
SLEEP = 1.0


def snapshot_url(timestamp: str, original_url: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def _cdx(retries: int = 3, **params) -> list:
    """One CDX query -> a list of row dicts (the header row becomes the keys).

    archive.org's connection-level rate limiting is frequent enough that a
    bare cold-start easily hits it, so retry a few times before giving up.
    """
    params.setdefault("output", "json")
    for attempt in range(retries):
        try:
            r = requests.get(CDX_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            break
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(SLEEP * (attempt + 1) * 5)
    rows = r.json()
    if not rows:
        return []
    header, *data = rows
    return [dict(zip(header, row)) for row in data]


def list_snapshots_by_prefix(prefix_url: str, limit: int = 10000, retries: int = 3) -> list:
    """Return every archived URL under `prefix_url` as a list of dicts with
    keys original, mimetype, timestamp, endtimestamp, groupcount, uniqcount.
    """
    return _cdx(
        retries=retries,
        url=prefix_url,
        matchType="prefix",
        collapse="urlkey",
        fl="original,mimetype,timestamp,endtimestamp,groupcount,uniqcount",
        filter="!statuscode:[45]..",
        limit=limit,
    )


def list_snapshots_or_exit(prefix_url: str, **kwargs) -> list:
    """list_snapshots_by_prefix, but a failure after its retries ends the run
    with a one-line message instead of a urllib3 traceback.

    For the scrapers whose entire work list comes from this one call: there is
    nothing to degrade to, and exiting non-zero is the honest signal (returning
    an empty list would print "0 candidates" and exit 0, which reads as
    success to anything wrapping the script).
    """
    try:
        return list_snapshots_by_prefix(prefix_url, **kwargs)
    except Exception as e:
        raise SystemExit(
            f"Could not list archived pages under {prefix_url}: {e}\n"
            "archive.org is unreachable or rate-limiting - try again later."
        )


def list_all_captures(exact_url: str, retries: int = 3) -> list:
    """Return every historical HTTP-200 capture timestamp of one exact URL
    (no collapsing), for sites where the page's own content changes over
    time and a single "latest" snapshot would miss older revisions.
    """
    rows = _cdx(retries=retries, url=exact_url, filter="statuscode:200",
                fl="timestamp", limit=1000)
    return sorted({row["timestamp"] for row in rows})


def fetch_snapshot(conn: sqlite3.Connection, session: requests.Session, url: str, timeout: int = 20) -> bytes:
    """Fetch a Wayback snapshot URL's raw bytes (HTML or PDF), transparently
    caching them in the wayback_cache table on first fetch. A cache hit
    skips both the network call and the rate-limit sleep - only a real
    fetch needs to be polite to archive.org.

    On a real (non-cached) fetch, also records three encoding-diagnostic
    signals purely for later analysis - none of this affects the returned
    bytes or any parser's behavior:
      - id_content_type: the Content-Type header the `id_` response itself
        carried (only present when the original server declared a charset -
        often absent, see fw_guessed_charset below for a fallback signal).
      - fw_guessed_charset: the `x-archive-guessed-charset` header from the
        same capture's `fw_` variant (Wayback's own chardet-style guess -
        only fetched via a lightweight HEAD request, not a second full body).
      - bs4_encoding: what BeautifulSoup's own UnicodeDammit sniffing lands
        on with no override, for direct comparison against the two above.
    Best-effort only - any failure here is swallowed so it never affects the
    primary fetch.
    """
    row = conn.execute("SELECT content FROM wayback_cache WHERE url = ?", (url,)).fetchone()
    if row:
        return row[0]

    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    content = r.content

    id_content_type = r.headers.get("Content-Type")

    fw_guessed_charset = None
    try:
        if "id_/" in url:
            fw_url = url.replace("id_/", "fw_/", 1)
            r_fw = session.head(fw_url, headers=HEADERS, timeout=timeout)
            fw_guessed_charset = r_fw.headers.get("x-archive-guessed-charset")
    except Exception:
        pass

    bs4_encoding = None
    try:
        # Only sniff text. On a PDF or Word attachment the answer would be
        # meaningless anyway, and UnicodeDammit prints "Some characters could
        # not be decoded..." straight into the middle of the progress markers.
        if not content[:8].startswith((b"%PDF", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")):
            bs4_encoding = BeautifulSoup(content, "html.parser").original_encoding
    except Exception:
        pass

    conn.execute(
        "INSERT OR IGNORE INTO wayback_cache (url, content, id_content_type, fw_guessed_charset, bs4_encoding) "
        "VALUES (?, ?, ?, ?, ?)",
        (url, content, id_content_type, fw_guessed_charset, bs4_encoding),
    )
    conn.commit()
    time.sleep(CONTENT_SLEEP)
    return content


def sample_all_captures(conn: sqlite3.Connection, session: requests.Session, url: str, parse_fn, limit: int = None) -> list:
    """Sample every historical HTTP-200 capture of `url` (a listing/dump
    page whose content grows over time - pressdb.php-style sources), calling
    `parse_fn(content, url, timestamp)` on each and returning the flat
    concatenation of everything parse_fn yields.

    This only owns the listing -> per-capture fetch -> parse loop; the
    caller does its own accumulation/dedup on the returned list, since that
    genuinely differs per source (dedup key url vs (title,date), plain
    body-length compare vs a custom rank() preferring one URL shape over
    another).
    """
    try:
        timestamps = list_all_captures(url)
    except Exception as e:
        print(f"  ERROR listing captures: {e}")
        return []
    if limit:
        timestamps = timestamps[:limit]
    print(f"  {len(timestamps)} captures to sample", flush=True)

    entries = []
    for ts in timestamps:
        snap_url = snapshot_url(ts, url)
        try:
            content = fetch_snapshot(conn, session, snap_url, timeout=20)
            entries.extend(parse_fn(content, url, ts))
        except Exception as e:
            print(f"\n  ERROR fetching {snap_url}: {e}")
            continue
        print(progress.CAPTURE, end="", flush=True)
    return entries


def fetch_detail_snapshot(conn: sqlite3.Connection, session: requests.Session, url: str, parse_fn, timeout: int = 20):
    """Try to fetch+parse a per-item detail page. Returns (parsed, confirmed):
    `parsed` is {} if nothing was recovered; `confirmed` distinguishes a
    verified dead end (safe to permanently record a fallback/no-op) from a
    network hiccup (caller must not commit anything this run, so the item
    stays open to a full retry next time instead of getting stuck forever).

    A probe failure backs off SLEEP*2 before returning, matching the older
    per-scraper convention this consolidates (several earlier fetch_detail()
    copies had silently dropped this pause).
    """
    try:
        found = get_latest_working_snapshot(url)
    except Exception as e:
        print(f"\n  ERROR probing snapshots for {url}: {e}")
        time.sleep(CONTENT_SLEEP * 2)
        return {}, False
    if not found:
        return {}, True
    snap_url, ts = found
    try:
        content = fetch_snapshot(conn, session, snap_url, timeout=timeout)
        parsed = parse_fn(content)
    except Exception as e:
        print(f"\n  ERROR fetching {snap_url}: {e}")
        return {}, False
    if parsed.get("body"):
        parsed["detail_id"] = ts
        return parsed, True
    return {}, True


def get_latest_working_snapshot(original_url: str):
    """Find the most recent capture of `original_url` that returned HTTP 200.

    Returns (snapshot_url, timestamp) using the `id_` raw-content modifier, or
    None if the page never returned 200.

    `limit=-1` asks CDX for the last matching row, and it applies the filter
    before the limit - so a URL whose newest captures are 404s (common here:
    a page that later disappeared) still yields its newest *working* capture
    rather than nothing.
    """
    rows = _cdx(url=original_url, filter="statuscode:200", fl="timestamp", limit=-1)
    time.sleep(SLEEP)
    if not rows:
        return None
    timestamp = rows[0]["timestamp"]
    return snapshot_url(timestamp, original_url), timestamp
