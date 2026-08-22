#!/usr/bin/env python3
"""Backfill terratec_pressen/terratec_pressde with articles that only have a
working Wayback capture of their print.php?sid=N view, not the main
modules.php?...&file=article&sid=N page.

Same sid space as the existing sources (not a new channel), so recovered
rows are stored under the existing source tags.

Usage:
  python backfill_terratec_print.py             # both domains
  python backfill_terratec_print.py --limit 5   # cap per domain (testing)
"""

import argparse
import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from fetch import SLEEP
from dates import iso_date
import db
import richtext
from progress import Stats
import wayback


DOMAINS = [
    ("http://pressen.terratec.net:80/", "terratec_pressen"),
    ("http://pressde.terratec.net:80/", "terratec_pressde"),
]

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+)")


def stored_sids(conn: sqlite3.Connection, source: str) -> set:
    return {int(m.group(1)) for url in db.source_urls(conn, source)
            if (m := SID_RE.search(url))}


def missing_print_sids(prefix: str, have: set) -> list:
    sids = set()
    for entry in wayback.list_snapshots_or_exit(prefix):
        url = entry["original"]
        if "/print.php" not in url:
            continue
        m = SID_RE.search(url)
        if m:
            sids.add(int(m.group(1)))
    return sorted(sids - have)


def parse_print_snapshot(content: bytes) -> dict:
    # cp1252 stated, never sniffed: these pages predate UTF-8 and declare no
    # charset, so left to guess bs4 read them as ISO-8859-1 and stored the
    # cp1252 punctuation range as C1 control characters (see
    # repair_encoding.py, which had to undo exactly that).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    title_tag = soup.select_one("font.print-title")

    title = ""
    date = ""
    body = ""
    body_html = ""
    if title_tag:
        m = TITLE_RE.match(title_tag.get_text(strip=True))
        if m:
            date = iso_date(m.group(1), dayfirst=True)
            title = m.group(2).strip()

        body_tag = soup.select_one("font.print-normal")
        if body_tag:
            body, body_html = richtext.extract(body_tag)

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def backfill(prefix: str, source: str, limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    have = stored_sids(conn, source)
    sids = missing_print_sids(prefix, have)
    if limit:
        sids = sids[:limit]
    print(f"[{source}] {len(sids)} sids missing a full article but archived via print.php", flush=True)

    stats = Stats(source)

    for sid in sids:
        print_url = f"{prefix}print.php?sid={sid}"

        try:
            found = wayback.get_latest_working_snapshot(print_url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {print_url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_print_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if db.store_release(conn, source, print_url, title=parsed["title"],
                            date=parsed["date"], body=parsed["body"],
                            body_html=parsed["body_html"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Terratec articles via their print.php view")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N missing sids per domain")
    args = parser.parse_args()
    for prefix, source in DOMAINS:
        backfill(prefix, source, limit=args.limit)
