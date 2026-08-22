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
import richtext
from progress import Stats
import wayback

PREFIX = "http://www.terratec.net:80/press/pressemit/"
SOURCE = "terratec"

DATE_RE = re.compile(r"Press Release,\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE)


def is_html_page(original_url: str) -> bool:
    return original_url.lower().split("?", 1)[0].endswith((".htm", ".html"))


def parse_snapshot(content: bytes) -> dict:
    # from_encoding, not decode_html: these 2002 pages declare no charset at
    # all and are wholly pre-UTF-8, so the bytes are cp1252 - and in prose full
    # of German accents two adjacent high bytes can coincidentally form a valid
    # UTF-8 sequence that decode_html would honour. Left to sniff, bs4 read
    # them as ISO-8859-1 and stored the cp1252 punctuation range as C1 control
    # characters (20 rows, undone by repair_encoding.py).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    text = soup.get_text(" ", strip=True)
    # Body from the DOM, date from the flat text. These pages are one big
    # layout table, and the article's table is the one carrying the most text -
    # see richtext.densest for why that beats a width= selector here. The flat
    # text stays for DATE_RE, which scans the whole page including the
    # header where the date actually sits.
    body, body_html = richtext.extract(richtext.densest(soup, "table", border="0"))
    if not body:
        # No layout table, or one with nothing in it: a handful of these
        # captures are 290-byte "page moved" stubs. Fall back to the flat text
        # rather than to nothing - an empty body_html means "not converted",
        # an empty body would mean the row was wiped.
        body, body_html = text, None

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

    return {"title": title, "date": date, "body": body, "body_html": body_html}


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
                            date=parsed["date"], body=parsed["body"],
                            body_html=parsed["body_html"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TerraTec press releases via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate pages")
    args = parser.parse_args()
    scrape(limit=args.limit)
