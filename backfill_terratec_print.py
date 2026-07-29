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
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from common import SLEEP, init_db
import wayback

DB_PATH = Path(__file__).parent / "pressroom.db"

DOMAINS = [
    ("http://pressen.terratec.net:80/", "terratec_pressen"),
    ("http://pressde.terratec.net:80/", "terratec_pressde"),
]

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+)")


def stored_sids(conn: sqlite3.Connection, source: str) -> set:
    sids = set()
    for (url,) in conn.execute("SELECT url FROM releases WHERE source = ?", (source,)):
        m = SID_RE.search(url)
        if m:
            sids.add(int(m.group(1)))
    return sids


def missing_print_sids(prefix: str, have: set) -> list:
    sids = set()
    for entry in wayback.list_snapshots_by_prefix(prefix):
        url = entry["original"]
        if "/print.php" not in url:
            continue
        m = SID_RE.search(url)
        if m:
            sids.add(int(m.group(1)))
    return sorted(sids - have)


def parse_print_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.select_one("font.print-title")

    title = ""
    date = ""
    body = ""
    if title_tag:
        m = TITLE_RE.match(title_tag.get_text(strip=True))
        if m:
            try:
                date = du.parse(m.group(1), dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                date = ""
            title = m.group(2).strip()

        body_tag = soup.select_one("font.print-normal")
        if body_tag:
            body = body_tag.get_text(" ", strip=True)

    return {"title": title, "date": date, "body": body}


def backfill(prefix: str, source: str, limit: int = None) -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    session = requests.Session()

    have = stored_sids(conn, source)
    sids = missing_print_sids(prefix, have)
    if limit:
        sids = sids[:limit]
    print(f"[{source}] {len(sids)} sids missing a full article but archived via print.php", flush=True)

    new_count = 0
    dead_count = 0

    for sid in sids:
        print_url = f"{prefix}print.php?sid={sid}"

        try:
            found = wayback.get_latest_working_snapshot(print_url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {print_url}: {e}")
            time.sleep(SLEEP * 2)
            continue
        if not found:
            dead_count += 1
            print(f"\n  no working print.php snapshot for sid={sid}")
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_print_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            continue

        conn.execute(
            "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
            (source, timestamp, parsed["title"], parsed["date"], print_url, parsed["body"]),
        )
        conn.commit()
        new_count += 1
        print("+", end="", flush=True)

    total_in_db = conn.execute("SELECT count(*) FROM releases WHERE source = ?", (source,)).fetchone()[0]
    print(
        f"\n[{source}] Backfilled {new_count} via print.php, {dead_count} never archived successfully. "
        f"Total in DB: {total_in_db}"
    )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Terratec articles via their print.php view")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N missing sids per domain")
    args = parser.parse_args()
    for prefix, source in DOMAINS:
        backfill(prefix, source, limit=args.limit)
