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

from encoding import decode_html
from fetch import HEADERS, SLEEP, fetch_cached
import richtext
from db import already_stored
from dates import iso_date
import db
import reextract
from progress import Stats

def make_session():
    """A session GlobeNewswire will actually answer.

    Akamai Bot Manager started dropping plain requests/urllib3 in 2026: DNS
    and the TLS handshake both succeed, then an HTTP/1.1 request gets no
    response at all (read timeout) and HTTP/2 gets an immediate RST_STREAM.
    Extra browser headers change nothing - the block is on the TLS/HTTP2
    fingerprint, not the User-Agent - and all 53 rows of this source failed
    that way on 2026-08-21.

    curl_cffi is libcurl with browser fingerprints (the maintained
    lexiforest/curl-impersonate fork), and its Session is API-compatible with
    requests.Session for everything fetch_cached and this module use. Imported
    here rather than in fetch.py so the other scrapers keep working on a bare
    system Python with no such dependency.

    The impersonation profile is a moving target: when this starts timing out
    again, bump curl_cffi and try a newer profile before blaming the parser.
    """
    from curl_cffi import requests as impersonating
    return impersonating.Session(impersonate=IMPERSONATE)


BASE_URL = "https://www.globenewswire.com"
IMPERSONATE = "chrome"
LIST_URL = f"{BASE_URL}/en/search/organization/Creative%2520Labs%CE%B4%2520Inc%C2%A7"
SOURCE = "creative_gnw"


def parse_list_page(session: requests.Session, page: int) -> list:
    r = session.get(LIST_URL, headers=HEADERS, params={"page": page}, timeout=15)
    r.raise_for_status()
    # decode_html(r.content), never r.text - the convention holds for live
    # sites too: requests guesses ISO-8859-1 when the header omits a charset.
    soup = BeautifulSoup(decode_html(r.content), "html.parser")

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


def fetch_body(conn, session: requests.Session, url: str) -> tuple:
    """(body, body_html) for one release, or ("", "") if the article container
    is missing. Goes through fetch_cached, so a reparse costs no request.

    decode_html(bytes), never r.text - the convention holds for live sites too:
    requests guesses ISO-8859-1 when the header omits a charset.
    """
    content = fetch_cached(conn, session, url)
    soup = BeautifulSoup(decode_html(content), "html.parser")
    body = soup.select_one("div.main-body-container.article-body")
    if not body:
        return "", ""
    return richtext.extract(body)


def scrape(pages: int = None, catch: dict = None) -> None:
    conn = db.connect()
    session = make_session()

    print(f"[{SOURCE}] Scraping GlobeNewswire Creative Labs, Inc. archive", flush=True)

    stats = Stats(SOURCE)
    page = 1
    stop = reextract.no_crawl(catch)

    while not stop:
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
        page += 1

    stats.summary(conn)
    reextract.run(conn, SOURCE, catch, fetch_body=fetch_body, session=session)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape GlobeNewswire Creative Labs, Inc. press releases")
    parser.add_argument("--pages", type=int, default=None, help="Number of pages to scrape (default: all)")
    reextract.add_flags(parser)
    args = parser.parse_args()
    scrape(pages=args.pages, catch=reextract.options(args))
