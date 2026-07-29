#!/usr/bin/env python3
"""Scraper for GlobeNewswire's "Creative Labs, Inc." organization archive → unified pressroom.db.

Creative distributed some releases (2011-2021) via GlobeNewswire under the
org name "Creative Labs, Inc." — a different/overlapping set from the
sg.creative.com pressroom archive (some releases only exist here, e.g.
Sound Blaster Recon3D 2011, Z/Zx/ZxR 2012, Audigy Fx/Rx 2013; others are
duplicates of sg.creative.com content under a different URL).

Tagged with its own source ("creative_gnw") rather than merged into
"creative" since it's a distinct distribution channel with overlapping but
not identical coverage — use --source creative,creative_gnw in search.py to
query both together.

Usage:
  python scrape_globenewswire_creative.py                # all pages
  python scrape_globenewswire_creative.py --pages 2       # first 2 pages only
"""

import argparse
import re
import time

import requests
from bs4 import BeautifulSoup

from fetch import HEADERS, SLEEP
from db import already_stored
from dates import iso_date
import db
from progress import Stats

BASE_URL = "https://www.globenewswire.com"
LIST_URL = f"{BASE_URL}/en/search/organization/Creative%2520Labs%CE%B4%2520Inc%C2%A7"
SOURCE = "creative_gnw"


def parse_list_page(session: requests.Session, page: int) -> list:
    r = session.get(LIST_URL, headers=HEADERS, params={"page": page}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    for li in soup.select("li.row"):
        a = li.select_one("div.mainLink a")
        date_span = li.select_one("div.date-source span")
        if not a:
            continue
        href = a["href"]
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        date_text = date_span.get_text(strip=True) if date_span else ""
        date = iso_date(date_text, fuzzy=True)
        m = re.search(r"/news-release/\d{4}/\d{2}/\d{2}/(\d+)/", url)
        detail_id = m.group(1) if m else None
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
    body = soup.select_one("div.main-body-container.article-body")
    if not body:
        return ""
    return body.get_text(" ", strip=True)


def scrape(pages: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Scraping GlobeNewswire Creative Labs, Inc. archive", flush=True)

    stats = Stats(SOURCE)
    page = 1

    while True:
        if pages and page > pages:
            break
        print(f"  Page {page}", end="  ", flush=True)
        try:
            items = parse_list_page(session, page)
        except Exception as e:
            print(f"ERROR fetching list: {e}")
            time.sleep(SLEEP * 2)
            break
        time.sleep(SLEEP)

        if not items:
            print("(empty, stopping)")
            break

        for item in items:
            if already_stored(conn, item["url"]):
                stats.skipped()
                continue

            try:
                body = fetch_body(session, item["url"])
            except Exception as e:
                print(f"\n    ERROR fetching {item['url']}: {e}")
                body = ""
            time.sleep(SLEEP)

            if db.store_release(conn, SOURCE, item["url"], title=item["title"],
                                date=item["date"], body=body, detail_id=item["detail_id"]):
                stats.added()

        print()
        page += 1

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape GlobeNewswire Creative Labs, Inc. press releases")
    parser.add_argument("--pages", type=int, default=None, help="Number of pages to scrape (default: all)")
    args = parser.parse_args()
    scrape(pages=args.pages)
