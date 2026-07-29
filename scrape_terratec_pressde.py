#!/usr/bin/env python3
"""Scraper for TerraTec's German-language press portal (pressde.terratec.net,
same PHP-Nuke "Presse @ TerraTec" software as pressen.terratec.net) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots since
the live site no longer exists.

Likely overlaps in content with the English pressen.terratec.net portal and
the older static terratec.net/press/pressemit/ archive, but uses an unrelated
URL scheme so the two can't share a dedup key. Kept as a distinct source,
same as creative/creative_gnw.

Usage:
  python scrape_terratec_pressde.py             # scrape every archived article
  python scrape_terratec_pressde.py --limit 5   # only process the first 5 (testing)
"""

import argparse
import re
import time

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from fetch import SLEEP
from db import already_stored
import db
from progress import Stats
import wayback

PREFIX = "http://pressde.terratec.net:80/"
SOURCE = "terratec_pressde"

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_TAG_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+?)\s*::\s*Press")
TITLE_TAG_MONTH_RE = re.compile(r"([A-Za-z]+\s+\d{4})\s*-\s*(.+?)\s*::\s*Press")

# The sidebar box after the article body isn't labeled consistently across
# captures ("Links!" in German templates, "Related links" seen on the
# English portal's markup bleeding through some snapshots) - cut at whichever
# comes first.
END_MARKERS = ["Links!", "Related links"]


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


def _cut_at_first_marker(text: str) -> str:
    cut = len(text)
    for marker in END_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()


def parse_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # The heading anchor's CSS class isn't present in every capture (some
    # crawls render it as plain bold text instead) - the <title> tag is
    # present and consistently formatted in all captures, so use that.
    title_full = soup.title.get_text(strip=True) if soup.title else ""
    m = TITLE_TAG_RE.match(title_full)
    date_fmt = "%Y-%m-%d"
    if not m:
        # A handful of articles only carry month/year precision
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
        body = _cut_at_first_marker(parts[-1]) if len(parts) > 1 else text

    return {"title": title, "date": date, "body": body}


def scrape(limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Listing archived articles under {PREFIX}", flush=True)
    urls = list_articles()
    if limit:
        urls = urls[:limit]
    print(f"[{SOURCE}] {len(urls)} candidate articles", flush=True)

    stats = Stats(SOURCE)

    for url in urls:
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
    parser = argparse.ArgumentParser(description="Scrape TerraTec (German pressde portal) press releases via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate articles")
    args = parser.parse_args()
    scrape(limit=args.limit)
