"""Scraper for sg.creative.com/corporate/pressroom → unified pressroom.db.

Unlike the Q4 IR platform (Intel/AMD), Creative's press room is paginated by
year (?year=YYYY, one page per year, no further pagination within a year) and
detail pages are ?id=NNNNN on the same path.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.decoding import decode_html
from pressroom.fetcher.control.politeness import HEADERS, SLEEP
from pressroom.text.control import richtext
from pressroom.database.control import connection
from pressroom.scraper.control import catch_up
from pressroom.scraper.control import discovery
from pressroom.reporting.entity.outcome import Stats
from pressroom.scraper.entity.parse import Detail, Entry

BASE_URL = "https://sg.creative.com"
LIST_URL = f"{BASE_URL}/corporate/pressroom"
SOURCE = "creative"
FIRST_YEAR = 1999


def parse_list_page(session: requests.Session, year: int) -> list[Entry]:
    r = session.get(LIST_URL, headers=HEADERS, params={"year": year}, timeout=15)
    r.raise_for_status()
    # decode_html(r.content), never r.text: this page claims UTF-8, but requests
    # falls back to ISO-8859-1 when the header omits the charset, which stored a
    # page's worth of bullet characters as C1 controls.
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
        items.append(
            {
                "url": url,
                "title": a.get_text(strip=True),
                "date": date,
                "detail_id": detail_id,
            }
        )
    return items


def parse_detail(content: bytes) -> Detail:
    """The release out of one live page, or `{}` if the content column is
    missing. Bytes in and no fetch: the library brings the page through
    `politeness.fetch_cached`, so a reparse costs no request."""
    soup = BeautifulSoup(decode_html(content), "html.parser")
    col = soup.select_one("div.corporate-content div.col-sm-8")
    if not col:
        return {}
    body, body_html = richtext.extract(col)
    return {"body": body, "body_html": body_html}


def scrape(
    from_year: int = FIRST_YEAR, to_year: int | None = None, catch: dict | None = None
) -> None:
    current_year = to_year or int(time.strftime("%Y"))

    conn = connection.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Scraping years {from_year}-{current_year}", flush=True)

    stats = Stats(SOURCE)

    # A computed range rather than a discovery result, so there was nothing for
    # the usual `[] if no_crawl` idiom to empty and this scraper kept fetching
    # every year's listing under `--offline`.
    years = [] if catch_up.no_crawl(catch) else range(from_year, current_year + 1)

    for year in years:
        print(f"  Year {year}", end="  ", flush=True)
        try:
            items = parse_list_page(session, year)
        except Exception as e:
            print(f"ERROR fetching list: {e}")
            time.sleep(SLEEP * 2)
            continue
        time.sleep(SLEEP)

        discovery.from_items(
            conn, session, SOURCE, items, parse=parse_detail, stats=stats
        )

        print()

    stats.summary(conn)
    # The live pass fetches through fetch_cached, so a page already in
    # page_cache costs nothing: this is both the offline reparse and the live
    # retry.
    catch_up.run(conn, SOURCE, catch, live_parser=parse_detail, session=session)
    conn.close()
