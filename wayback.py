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

import time

import requests

from common import HEADERS

TIMEMAP_URL = "https://web.archive.org/web/timemap/json"
SPARKLINE_URL = "https://web.archive.org/__wb/sparkline"
CALENDAR_URL = "https://web.archive.org/__wb/calendarcaptures/2"
SLEEP = 1.0


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
