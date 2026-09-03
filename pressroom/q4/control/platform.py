"""Press rooms running the Q4 Inc. investor-relations platform.

Q4 hosts IR sites for many public companies on one shared template, so one
parser covers all of them - currently intc.com and ir.amd.com, which is why
those two components are barely more than a url and a call to scrape() here.
The listing container and the title link are the only things that differ, so
they are parameters rather than two copies of this file.

Nothing else in this repo uses this: every other source is a one-of-a-kind dead
site with its own bespoke parser.

Encoding: these are the only *live* sites here, and that is exactly where
`r.text` is tempting and wrong. Q4's pages are UTF-8 but the response header does
not always say so, and requests then falls back to ISO-8859-1, which stored a
page's worth of trademark signs as raw C1 control characters. Every parse below
goes through decode_html(r.content).
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.reporting.entity.outcome import Stats
from pressroom.text.control.decoding import decode_html
from pressroom.capture.control.politeness import HEADERS, SLEEP
from pressroom.text.control import richtext
from pressroom.scraping.entity.parse import Detail, Entry


def get_total_pages(session: requests.Session, list_url: str) -> int:
    r = session.get(list_url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    # decode_html(r.content), never r.text - see the module docstring.
    soup = BeautifulSoup(decode_html(r.content), "html.parser")
    last = 1
    for a in soup.select("ul.pagination li a"):
        m = re.search(r"(\d+)$", a.get_text(strip=True))
        if m:
            last = max(last, int(m.group(1)))
    return last


def _parse_date(time_el) -> str:
    """Extract ISO date from <time> element; fall back to dateutil for freeform text."""
    if time_el is None:
        return ""
    dt_attr = time_el.get("datetime", "")
    if dt_attr:
        return dt_attr[:10]
    text = time_el.get_text(strip=True)
    # Falls back to the raw text, not "" - a Q4 page always has *something*
    # here and keeping it beats discarding it.
    return iso_date(text) or text


def parse_list_page(
    session: requests.Session,
    list_url: str,
    page: int,
    base_url: str,
    container_sel: str = "article.media-container",
    title_link_sel: str = "div.media-title a",
) -> list[Entry]:
    url = f"{list_url}?page={page}"
    r = session.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(decode_html(r.content), "html.parser")

    items = []
    for container in soup.select(container_sel):
        a = container.select_one(title_link_sel)
        time_el = container.select_one("div.date time")
        if not a:
            continue
        href = a["href"]
        if not href.startswith("http"):
            href = base_url + href
        m = re.search(r"/detail/(\d+)/", href)
        detail_id = m.group(1) if m else None
        items.append(
            {
                "url": href,
                "title": a.get_text(strip=True),
                "date": _parse_date(time_el),
                "detail_id": detail_id,
            }
        )
    return items


def parse_detail(content: bytes) -> Detail:
    """The release out of one live page, or `{}` if the article container is
    missing. Bytes in and no fetch: the library brings the page through
    `politeness.fetch_cached`, so a reparse costs no request."""
    soup = BeautifulSoup(decode_html(content), "html.parser")
    article = soup.select_one("article.full-news-article")
    if not article:
        return {}
    for el in article.select("div.related-documents-line, h1.article-heading"):
        el.decompose()
    body, body_html = richtext.extract(article)
    return {"body": body, "body_html": body_html}


def scrape(
    source: str,
    list_url: str,
    pages: int | None = None,
    start: int = 1,
    container_sel: str = "article.media-container",
    title_link_sel: str = "div.media-title a",
    catch: dict | None = None,
) -> None:
    base_url = re.match(r"(https?://[^/]+)", list_url).group(1)

    conn = connection.connect()
    session = requests.Session()

    total = 0
    if not catch_up.no_crawl(catch):
        print("Detecting total page count...", flush=True)
        total = get_total_pages(session, list_url)
        time.sleep(SLEEP)

    end_page = start + pages - 1 if pages else total
    end_page = min(end_page, total)

    print(f"[{source}] Scraping pages {start}–{end_page} of {total} total", flush=True)

    stats = Stats(source)

    for page in range(start, end_page + 1):
        print(f"  Page {page}/{end_page}", end="  ", flush=True)
        try:
            items = parse_list_page(
                session, list_url, page, base_url, container_sel, title_link_sel
            )
        except Exception as e:
            print(f"ERROR fetching list: {e}")
            time.sleep(SLEEP * 2)
            continue
        time.sleep(SLEEP)

        discovery.from_items(
            conn, session, source, items, parse=parse_detail, stats=stats
        )

        print()

    stats.summary(conn)
    # Phase 2 for the tag this run owns.
    catch_up.run(conn, source, catch, live_parser=parse_detail, session=session)
    conn.close()
