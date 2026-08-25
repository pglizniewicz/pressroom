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
import time

import requests
from bs4 import BeautifulSoup

from encoding import decode_html
from fetch import HEADERS, SLEEP, fetch_cached
import richtext
from db import already_stored
import db
import reextract
from progress import Stats

BASE_URL = "https://sg.creative.com"
LIST_URL = f"{BASE_URL}/corporate/pressroom"
SOURCE = "creative"
FIRST_YEAR = 1999


def parse_list_page(session: requests.Session, year: int) -> list:
    r = session.get(LIST_URL, headers=HEADERS, params={"year": year}, timeout=15)
    r.raise_for_status()
    # decode_html(r.content), never r.text: this page claims UTF-8, but requests
    # falls back to ISO-8859-1 whenever the header omits the charset, which
    # stored 174 bullet characters (cp1252 0x95) as C1 controls across 23 rows.
    soup = BeautifulSoup(decode_html(r.content), "html.parser")

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


def fetch_body(conn, session: requests.Session, url: str) -> tuple:
    """(body, body_html) for one release, or ("", "") if the content column is
    missing. Goes through fetch_cached, so a reparse costs no request."""
    content = fetch_cached(conn, session, url)
    soup = BeautifulSoup(decode_html(content), "html.parser")
    col = soup.select_one("div.corporate-content div.col-sm-8")
    if not col:
        return "", ""
    return richtext.extract(col)


def scrape(from_year: int = FIRST_YEAR, to_year: int = None,
           catch: dict = None) -> None:
    current_year = to_year or int(time.strftime("%Y"))

    conn = db.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Scraping years {from_year}-{current_year}", flush=True)

    stats = Stats(SOURCE)

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
                stats.skipped()
                continue

            try:
                body, body_html = fetch_body(conn, session, item["url"])
            except Exception as e:
                # Write nothing. An inserted empty row is worse than no row:
                # already_stored() would skip it on every future run, so one
                # timeout would cost the release permanently. A network error is
                # not a verdict - report it and let the rerun pick it up.
                print(f"\n    ERROR fetching {item['url']}: {e}")
                stats.uncertain()
                continue

            if db.store_release(conn, SOURCE, item["url"], title=item["title"],
                                date=item["date"], body=body, body_html=body_html,
                                detail_id=item["detail_id"]):
                stats.added()

        print()

    stats.summary(conn)
    # fetch_body goes through fetch_cached, so a page already in page_cache
    # costs nothing: this is both the offline reparse and the live retry.
    reextract.run(conn, SOURCE, catch, fetch_body=fetch_body, session=session)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Creative Technology press releases")
    parser.add_argument("--from-year", type=int, default=FIRST_YEAR, help="Start year (default: 1999)")
    parser.add_argument("--to-year", type=int, default=None, help="End year (default: current year)")
    reextract.add_flags(parser)
    args = parser.parse_args()
    scrape(from_year=args.from_year, to_year=args.to_year,
           catch=reextract.options(args))
