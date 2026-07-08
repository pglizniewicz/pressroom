#!/usr/bin/env python3
"""Scraper for TerraTec's second dead press room, the PHP-Nuke "Presse @
TerraTec" portal (pressen.terratec.net) -> unified pressroom.db, sourced
entirely from Wayback Machine snapshots since the live site no longer exists.

This substantially overlaps in content with the older static
terratec.net/press/pressemit/ archive (source="terratec"), but uses an
unrelated URL scheme so the two can't share a dedup key. Kept as a distinct
source, same as creative/creative_gnw.

Usage:
  python scrape_terratec_pressen.py             # scrape every archived article
  python scrape_terratec_pressen.py --limit 5   # only process the first 5 (testing)
"""

import argparse
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from common import HEADERS, SLEEP, already_stored, init_db
import wayback

DB_PATH = Path(__file__).parent / "pressroom.db"
PREFIX = "http://pressen.terratec.net:80/"
SOURCE = "terratec_pressen"

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_TAG_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+?)\s*::\s*Press")
TITLE_TAG_MONTH_RE = re.compile(r"([A-Za-z]+\s+\d{4})\s*-\s*(.+?)\s*::\s*Press")


def list_articles() -> list:
    """Dedup by sid: mode/order/thold don't affect content, first-seen wins."""
    by_sid = {}
    for entry in wayback.list_snapshots_by_prefix(PREFIX):
        url = entry["original"]
        if "name=News" not in url or "file=article" not in url:
            continue
        m = SID_RE.search(url)
        if not m:
            continue
        sid = int(m.group(1))
        by_sid.setdefault(sid, url)
    return [by_sid[sid] for sid in sorted(by_sid)]


def parse_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # The heading anchor's CSS class isn't present in every capture (some
    # crawls render it as plain bold text instead) - the <title> tag is
    # present and consistently formatted in all captures, so use that.
    title_full = soup.title.get_text(strip=True) if soup.title else ""
    m = TITLE_TAG_RE.match(title_full)
    date_fmt = "%Y-%m-%d"
    if not m:
        # A handful of articles only carry month/year precision, e.g. "June 2007 - Title"
        m = TITLE_TAG_MONTH_RE.match(title_full)
        date_fmt = "%Y-%m"

    title = ""
    date = ""
    body = ""
    if m:
        date_str, title = m.group(1), m.group(2).strip()
        try:
            date = du.parse(date_str, dayfirst=True).strftime(date_fmt)
        except Exception:
            date = ""

        heading = f"{date_str} - {title}"
        text = soup.get_text(" ", strip=True)
        parts = text.split(heading)
        body = parts[-1].split("Related links")[0].strip() if len(parts) > 1 else text

    return {"title": title, "date": date, "body": body}


def scrape(limit: int = None) -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    session = requests.Session()

    print(f"[{SOURCE}] Listing archived articles under {PREFIX}", flush=True)
    urls = list_articles()
    if limit:
        urls = urls[:limit]
    print(f"[{SOURCE}] {len(urls)} candidate articles", flush=True)

    new_count = 0
    skip_count = 0
    dead_count = 0

    for url in urls:
        if already_stored(conn, url):
            skip_count += 1
            print(".", end="", flush=True)
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            continue
        if not found:
            dead_count += 1
            print(f"\n  no working snapshot for {url}")
            continue
        snapshot_url, timestamp = found

        try:
            r = session.get(snapshot_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            parsed = parse_snapshot(r.text)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            continue
        time.sleep(SLEEP)

        conn.execute(
            "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
            (SOURCE, timestamp, parsed["title"], parsed["date"], url, parsed["body"]),
        )
        conn.commit()
        new_count += 1
        print("+", end="", flush=True)

    total_in_db = conn.execute("SELECT count(*) FROM releases WHERE source = ?", (SOURCE,)).fetchone()[0]
    print(
        f"\nDone. Added {new_count} new, skipped {skip_count} existing, "
        f"{dead_count} never archived successfully. Total [{SOURCE}] in DB: {total_in_db}"
    )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TerraTec (pressen portal) press releases via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate articles")
    args = parser.parse_args()
    scrape(limit=args.limit)
