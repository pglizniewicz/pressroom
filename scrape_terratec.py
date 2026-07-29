#!/usr/bin/env python3
"""Scraper for TerraTec's dead press room (terratec.net/press/pressemit/) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots since
the live site no longer exists.

Usage:
  python scrape_terratec.py             # scrape every archived press page
  python scrape_terratec.py --limit 5   # only process the first 5 pages (testing)
"""

import argparse
import re
import time

import requests
from bs4 import BeautifulSoup

from fetch import SLEEP
from db import already_stored
from dates import iso_date
import db
from progress import Stats
import wayback

PREFIX = "http://www.terratec.net:80/press/pressemit/"
SOURCE = "terratec"

DATE_RE = re.compile(r"Press Release,\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE)


def is_html_page(original_url: str) -> bool:
    return original_url.lower().split("?", 1)[0].endswith((".htm", ".html"))


def parse_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    date = ""
    m = DATE_RE.search(text)
    if m:
        date = iso_date(m.group(1), dayfirst=True)

    title = ""
    bold_tags = soup.find_all(["b", "strong"])
    for i, tag in enumerate(bold_tags):
        if "press release" in tag.get_text(strip=True).lower():
            if i + 1 < len(bold_tags):
                title = bold_tags[i + 1].get_text(strip=True)
            break

    return {"title": title, "date": date, "body": text}


def scrape(limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Listing archived pages under {PREFIX}", flush=True)
    snapshots = [s for s in wayback.list_snapshots_or_exit(PREFIX) if is_html_page(s["original"])]
    if limit:
        snapshots = snapshots[:limit]
    print(f"[{SOURCE}] {len(snapshots)} candidate press pages", flush=True)

    stats = Stats(SOURCE)

    for entry in snapshots:
        url = entry["original"]
        if already_stored(conn, url):
            stats.skipped()
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if db.store_release(conn, SOURCE, url, title=parsed["title"],
                            date=parsed["date"], body=parsed["body"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TerraTec press releases via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate pages")
    args = parser.parse_args()
    scrape(limit=args.limit)
