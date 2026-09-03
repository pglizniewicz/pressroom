"""Scraper for M-Audio's m-audio.com "/news" blog -> unified pressroom.db,
sourced entirely from Wayback Machine snapshots.

The fourth CMS generation for this company: inMusic Brands' Foundation-CSS blog,
built after the brand was acquired. **The corpus is frozen** - CDX shows two
dozen articles published between 2014 and 2019 and nothing since, with later
captures being the identical backlog re-skinned with newer chrome. So one
`get_latest_working_snapshot()` per pagination url recovers the final set: no
historical-capture sampling as in media_pr.py, no prefix-crawl bonus discovery
as in media_news.py. The listing is five items a page over five pages, and
/news/P25 has never been captured.

No structured date field anywhere on the site: dates are prose in the body
("City, ST, USA - Month D, YYYY." or "City, ST, USA (Month D, YYYY)-", the dash
varying by era) and recovered through DATE_RE, which is dash-agnostic.

Encoding: these captures replay through the `id_` modifier with a bare
"Content-Type: text/html" and no charset, so `r.text` guesses Latin-1 and turns
the site's real UTF-8 into "M-AUDIOÂ®". Parsed from bytes through
`decode_html()` - never `r.text`, and never left to BeautifulSoup to sniff.
"""

import re
import sqlite3
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.database.control import connection
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.fetcher.control import archive
from pressroom.scraping.entity.parse import Detail, Entry

SOURCE = "maudio_com_news"

LISTING_PAGES = [
    "http://m-audio.com/news",
    "http://m-audio.com/news/P5",
    "http://m-audio.com/news/P10",
    "http://m-audio.com/news/P15",
    "http://m-audio.com/news/P20",
]
DETAIL_URL_TMPL = "http://m-audio.com/news/articles/{}"
SLUG_RE = re.compile(r"/news/articles/([\w-]+)")

MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
)
DATE_RE = re.compile(rf"{MONTHS} \d{{1,2}},\s*\d{{4}}")


def _extract_date(text: str) -> str:
    m = DATE_RE.search(text)
    return iso_date(m.group(0)) if m else ""


def parse_listing_page(content: bytes, base_url: str) -> list[Entry]:
    # These captures are valid utf-8, so decode_html is currently a no-op here
    # - it's used anyway so a single stray cp1252 byte can't silently hand the
    # whole page to chardet. See decoding.py.
    soup = BeautifulSoup(decode_html(content), "html.parser")
    entries = []
    for li in soup.select("ul.large-block-grid-1 li"):
        a = li.select_one("p strong a.link")
        if not a or not a.get("href"):
            continue
        m = SLUG_RE.search(urljoin(base_url, a["href"]))
        if not m:
            continue
        slug = m.group(1)
        title = a.get_text(" ", strip=True)

        p = a.find_parent("p")
        teaser = teaser_html = ""
        if p:
            strong = p.find("strong")
            if strong:
                strong.decompose()
            teaser, teaser_html = richtext.extract(p)

        entries.append(
            {
                "slug": slug,
                "url": DETAIL_URL_TMPL.format(slug),
                "title": title,
                "teaser": teaser,
                "teaser_html": teaser_html,
                "date": _extract_date(teaser),
            }
        )
    return entries


def parse_detail(content: bytes) -> Detail:
    soup = BeautifulSoup(decode_html(content), "html.parser")
    container = soup.select_one("div.theme-page")
    if not container:
        return {}
    h5 = container.find("h5")
    title = h5.get_text(" ", strip=True) if h5 else ""
    if h5:
        h5.decompose()
    body, body_html = richtext.extract(container)
    if not body:
        return {}
    return {
        "title": title,
        "body": body,
        "body_html": body_html,
        "date": _extract_date(body),
    }


def discover_listing(conn: sqlite3.Connection) -> list[Entry]:
    session = requests.Session()
    by_slug = {}
    for page_url in LISTING_PAGES:
        print(f"[{SOURCE}] Fetching {page_url}", flush=True)
        try:
            found = archive.get_latest_working_snapshot(page_url)
            if not found:
                print("  no working snapshot found, skipping this page")
                continue
            snapshot_url, _ts = found
            content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            entries = parse_listing_page(content, page_url)
        except Exception as e:
            print(f"  ERROR: {e} (skipping this page for this run)")
            continue
        for e in entries:
            by_slug[e["slug"]] = e
        print(f"  {len(entries)} entries", flush=True)
    return list(by_slug.values())


def scrape(limit: int | None = None, catch: dict | None = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    entries = [] if catch_up.no_crawl(catch) else discover_listing(conn)
    print(
        f"[{SOURCE}] {len(entries)} distinct articles found across all listing pages",
        flush=True,
    )
    if limit:
        entries = entries[:limit]

    stats = Stats(SOURCE, total=len(entries))

    # prefer_parsed: this blog's article page states its own headline and date
    # better than the listing does, and the upgrade carries both.
    discovery.from_teasers(
        conn,
        session,
        SOURCE,
        entries,
        parse=parse_detail,
        stats=stats,
        prefer_parsed=True,
    )

    stats.summary(conn)
    catch_up.run(
        conn, SOURCE, catch, parser=parse_detail, session=session, twins_too=True
    )
    conn.close()
