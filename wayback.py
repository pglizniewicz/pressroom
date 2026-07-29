#!/usr/bin/env python3
"""Generic Wayback Machine helpers, reusable by any dead-site scraper.

Two building blocks:
  - list_snapshots_by_prefix: enumerate every archived URL under a prefix
    (CDX timemap API).
  - get_latest_working_snapshot: for a single URL, find the most recent
    capture that actually returned HTTP 200 (many captures are dead/error
    responses), by drilling sparkline -> year calendar -> day calendar.

The internal __wb/sparkline and __wb/calendarcaptures endpoints return a
disguised HTML 404 unless a Referer pointing at a web.archive.org/web/...
URL is sent - a normal User-Agent alone is not enough. Verified empirically.
"""

import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from fetch import HEADERS, SLEEP as CONTENT_SLEEP
import progress

TIMEMAP_URL = "https://web.archive.org/web/timemap/json"
SPARKLINE_URL = "https://web.archive.org/__wb/sparkline"
CALENDAR_URL = "https://web.archive.org/__wb/calendarcaptures/2"
SLEEP = 1.0


def snapshot_url(timestamp: str, original_url: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def list_snapshots_by_prefix(prefix_url: str, limit: int = 10000, retries: int = 3) -> list:
    """Return every archived URL under `prefix_url` as a list of dicts with
    keys original, mimetype, timestamp, endtimestamp, groupcount, uniqcount.

    This single request is the entry point every scraper run starts with, and
    archive.org's connection-level rate limiting is frequent enough that a
    bare cold-start easily hits it - retry a few times before giving up.
    """
    for attempt in range(retries):
        try:
            r = requests.get(
                TIMEMAP_URL,
                params={
                    "url": prefix_url,
                    "matchType": "prefix",
                    "collapse": "urlkey",
                    "output": "json",
                    "fl": "original,mimetype,timestamp,endtimestamp,groupcount,uniqcount",
                    "filter": "!statuscode:[45]..",
                    "limit": limit,
                },
                headers=HEADERS,
                timeout=30,
            )
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


def list_all_captures(exact_url: str, retries: int = 3) -> list:
    """Return every historical HTTP-200 capture timestamp of one exact URL
    (no collapsing), for sites where the page's own content changes over
    time and a single "latest" snapshot would miss older revisions.
    """
    for attempt in range(retries):
        try:
            r = requests.get(
                TIMEMAP_URL,
                params={"url": exact_url, "output": "json", "fl": "timestamp,statuscode", "limit": 1000},
                headers=HEADERS,
                timeout=30,
            )
            r.raise_for_status()
            break
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(SLEEP * (attempt + 1) * 5)
    rows = r.json()
    if not rows:
        return []
    _, *data = rows
    seen = set()
    timestamps = []
    for ts, status in data:
        if status == "200" and ts not in seen:
            seen.add(ts)
            timestamps.append(ts)
    return sorted(timestamps)


def _get_wb_json(session: requests.Session, url: str, params: dict, referer: str) -> dict:
    headers = {**HEADERS, "Referer": referer}
    r = session.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


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

    Walks candidate years newest-first (from the sparkline), and within the
    first year that has any 200 capture, picks the latest day and then the
    latest time on that day. Returns (snapshot_url, timestamp) using the
    `id_` raw-content modifier, or None if the page never returned 200.
    """
    session = requests.Session()
    referer = f"https://web.archive.org/web/2020/{original_url}"

    sparkline = _get_wb_json(
        session, SPARKLINE_URL, {"output": "json", "url": original_url, "collection": "web"}, referer
    )
    time.sleep(SLEEP)

    for year in sorted(sparkline.get("years", {}), reverse=True):
        year_data = _get_wb_json(
            session, CALENDAR_URL, {"url": original_url, "date": year, "groupby": "day"}, referer
        )
        time.sleep(SLEEP)

        day_hits = [item for item in year_data.get("items", []) if item[1] == 200]
        if not day_hits:
            continue
        month, day = max(
            (int(str(item[0]).zfill(4)[:-2]), int(str(item[0]).zfill(4)[-2:])) for item in day_hits
        )
        date_str = f"{year}{month:02d}{day:02d}"

        day_data = _get_wb_json(session, CALENDAR_URL, {"url": original_url, "date": date_str}, referer)
        time.sleep(SLEEP)

        time_hits = [item for item in day_data.get("items", []) if item[1] == 200]
        if not time_hits:
            continue
        hms = max(str(item[0]).zfill(6) for item in time_hits)
        timestamp = f"{date_str}{hms}"
        return f"https://web.archive.org/web/{timestamp}id_/{original_url}", timestamp

    return None
