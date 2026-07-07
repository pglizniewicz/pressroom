#!/usr/bin/env python3
"""Scraper for sg.creative.com/corporate/pressroom → unified pressroom.db.

Unlike the Q4 IR platform (Intel/AMD), Creative's press room is paginated by
year (?year=YYYY, one page per year, no further pagination within a year) and
detail pages are ?id=NNNNN on the same path.

Usage:
  python scrape_creative.py                      # all years, 1999-current
  python scrape_creative.py --from-year 2020      # 2020 onward
  python scrape_creative.py --from-year 2020 --to-year 2022
"""

import argparse
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from common import HEADERS, SLEEP, already_stored, init_db

DB_PATH = Path(__file__).parent / "pressroom.db"
BASE_URL = "https://sg.creative.com"
LIST_URL = f"{BASE_URL}/corporate/pressroom"
SOURCE = "creative"
FIRST_YEAR = 1999


def parse_list_page(session: requests.Session, year: int) -> list:
    r = session.get(LIST_URL, headers=HEADERS, params={"year": year}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    for li in soup.select("ul.prListing li"):
        a = li.select_one("div.pr-title a")
        date_el = li.select_one("div.pr-date")
        if not a:
            continue
        href = a["href"]
        url = f"{LIST_URL}{href}" if href.startswith("?") else href
        m = re.search(r"id=(\d+)", href)
        detail_id = m.group(1) if m else None
        date_text = date_el.get_text(strip=True) if date_el else ""
        date = date_text.replace("/", "-") if date_text else ""
        items.append({
            "url": url,
            "title": a.get_text(strip=True),
            "date": date,
            "detail_id": detail_id,
        })
    return items


def fetch_body(session: requests.Session, url: str) -> str:
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    col = soup.select_one("div.corporate-content div.col-sm-8")
    if not col:
        return ""
    return col.get_text(" ", strip=True)


def scrape(from_year: int = FIRST_YEAR, to_year: int = None) -> None:
    import datetime
    current_year = to_year or int(time.strftime("%Y"))

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    session = requests.Session()

    print(f"[{SOURCE}] Scraping years {from_year}-{current_year}", flush=True)

    new_count = 0
    skip_count = 0

    for year in range(from_year, current_year + 1):
        print(f"  Year {year}", end="  ", flush=True)
        try:
            items = parse_list_page(session, year)
        except Exception as e:
            print(f"ERROR fetching list: {e}")
            time.sleep(SLEEP * 2)
            continue
        time.sleep(SLEEP)

        for item in items:
            if already_stored(conn, item["url"]):
                skip_count += 1
                print(".", end="", flush=True)
                continue

            try:
                body = fetch_body(session, item["url"])
            except Exception as e:
                print(f"\n    ERROR fetching {item['url']}: {e}")
                body = ""
            time.sleep(SLEEP)

            conn.execute(
                "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
                (SOURCE, item["detail_id"], item["title"], item["date"], item["url"], body),
            )
            conn.commit()
            new_count += 1
            print("+", end="", flush=True)

        print()

    total_in_db = conn.execute("SELECT count(*) FROM releases WHERE source = ?", (SOURCE,)).fetchone()[0]
    print(f"\nDone. Added {new_count} new, skipped {skip_count} existing. Total [{SOURCE}] in DB: {total_in_db}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Creative Technology press releases")
    parser.add_argument("--from-year", type=int, default=FIRST_YEAR, help="Start year (default: 1999)")
    parser.add_argument("--to-year", type=int, default=None, help="End year (default: current year)")
    args = parser.parse_args()
    scrape(from_year=args.from_year, to_year=args.to_year)
