"""Scraper for GlobeNewswire's "Creative Labs, Inc." organization archive → unified pressroom.db.

Creative distributed some releases (2011-2021) via GlobeNewswire under the
org name "Creative Labs, Inc." — a different/overlapping set from the
sg.creative.com pressroom archive (some releases only exist here, e.g.
Sound Blaster Recon3D 2011, Z/Zx/ZxR 2012, Audigy Fx/Rx 2013; others are
duplicates of sg.creative.com content under a different URL).

Tagged with its own source ("creative_gnw") rather than merged into
"creative" since it's a distinct distribution channel with overlapping but
not identical coverage — use `pressroom-search --source creative,creative_gnw`
to query both together.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.decoding import decode_html
from pressroom.fetcher.control.politeness import HEADERS, SLEEP
from pressroom.text.control import richtext
from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.reporting.entity.outcome import Stats
from pressroom.scraping.entity.parse import Detail, Entry


def make_session():
    """A session GlobeNewswire will actually answer.

    Akamai Bot Manager started dropping plain requests/urllib3 in 2026: DNS
    and the TLS handshake both succeed, then an HTTP/1.1 request gets no
    response at all (read timeout) and HTTP/2 gets an immediate RST_STREAM.
    Extra browser headers change nothing - the block is on the TLS/HTTP2
    fingerprint, not the User-Agent, so every row of this source fails that way.

    curl_cffi is libcurl with browser fingerprints (the maintained
    lexiforest/curl-impersonate fork), and its Session is API-compatible with
    requests.Session for everything fetch_cached and this module use. Imported
    here rather than at module level so a missing curl_cffi breaks exactly this
    one scraper with a plain ImportError, instead of every module in the tree.

    The impersonation profile is a moving target: when this starts timing out
    again, bump curl_cffi and try a newer profile before blaming the parser.
    """
    from curl_cffi import requests as impersonating

    return impersonating.Session(impersonate=IMPERSONATE)


BASE_URL = "https://www.globenewswire.com"
IMPERSONATE = "chrome"
LIST_URL = f"{BASE_URL}/en/search/organization/Creative%2520Labs%CE%B4%2520Inc%C2%A7"
SOURCE = "creative_gnw"


def parse_list_page(session: requests.Session, page: int) -> list[Entry]:
    r = session.get(LIST_URL, headers=HEADERS, params={"page": page}, timeout=15)
    r.raise_for_status()
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
    """The release out of one live page, or `{}` if the article container is
    missing. Bytes in and no fetch: the library brings the page through
    `politeness.fetch_cached`, so a reparse costs no request.

    decode_html(bytes), never r.text - the convention holds for live sites too:
    requests guesses ISO-8859-1 whenever the header omits a charset.
    """
    soup = BeautifulSoup(decode_html(content), "html.parser")
    node = soup.select_one("div.main-body-container.article-body")
    if not node:
        return {}
    body, body_html = richtext.extract(node)
    return {"body": body, "body_html": body_html}


def scrape(pages: int | None = None, catch: dict | None = None) -> None:
    conn = connection.connect()
    session = make_session()

    print(f"[{SOURCE}] Scraping GlobeNewswire Creative Labs, Inc. archive", flush=True)

    stats = Stats(SOURCE)
    page = 1
    stop = catch_up.no_crawl(catch)

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

        discovery.from_items(
            conn, session, SOURCE, items, parse=parse_detail, stats=stats
        )

        print()
        page += 1

    stats.summary(conn)
    catch_up.run(conn, SOURCE, catch, live_parser=parse_detail, session=session)
    conn.close()
