"""Scraper for soundonsound.com's audio-interface coverage -> pressroom.db.

The first source here that is neither a manufacturer's press room nor a dead
site. Sound on Sound is a live magazine, its articles are *about* many makers
rather than issued by one, and the corpus runs unbroken from March 2000 to
today - so it is also the first source whose oldest rows are as complete as
its newest.

Scope is two faceted-search subjects about the same hardware category - 6986
"Audio Interfaces" and 7099 "Soundcards". They overlap, and that is fine:
`releases.url` is the dedup key, so the second list only adds what the first
missed.

MARKUP (Drupal 7, views + facetapi)

  listing   `div.views-row` per result, but only those containing
            `article[about]`: three rows per page are promo blocks with no
            article in them, so a naive count overstates the page size by
            three.
  url       the `about` attribute ("/reviews/swissonic-usb-studio-d"), not the
            anchor href - the row contains several links (image, title, topic
            tags) and `about` is the one that names the node.
  detail_id `<article id="node-4935591">` -> "4935591". Drupal's own node id,
            stable across url changes, and deliberately NOT a timestamp: a
            14-digit detail_id means "Wayback capture" everywhere in this repo,
            so faking one here would put a dead archive.org link on every row.
  date      TWO formats, both in the listing, and picking the wrong parser
            silently invents a day:
              news items      <span>Published 19/8/26</span>   d/m/yy
              magazine items  div.field--issue-date ->
                              "Published March 2000"           month only
            The second needs iso_date(..., fmt="%Y-%m"); without it dateutil
            fills the day in from *today's* date and March 2000 becomes
            2000-03-21. Same trap as terratec/control/portal.py.
  body      `div.node__content`, stable on both the 2000 and the 2026
            template. No paywall on this material: the oldest review returns
            its full text.

ROBOTS.TXT

  Crawl-delay: 30, honoured through fetch_cached(sleep=CRAWL_DELAY), which makes
  a full run several hours. It is resumable and everything lands in page_cache,
  so the cost is paid once.

  The *listing* urls match `Disallow: /*?*f[0]=`; the articles do not. That rule
  guards against faceted-search crawl traps - the combinatorial explosion of
  filter permutations - and fetching a few dozen named pages at one every thirty
  seconds is not the behaviour it defends against. Recorded here rather than
  assumed, because it was a deliberate call.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.capture.control.politeness import fetch_cached
from pressroom.database.control import connection
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.scraping.entity.parse import Detail, Entry

BASE_URL = "https://www.soundonsound.com"
SOURCE = "soundonsound"

# robots.txt: "Crawl-delay: 30". Every fetch in this module passes it.
CRAWL_DELAY = 30

FACETS = [
    {"subject": "6986", "label": "Audio Interfaces"},
    {"subject": "7099", "label": "Soundcards"},
]

NODE_ID_RE = re.compile(r"^node-(\d+)$")
DMY_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")


def listing_url(subject: str, page: int) -> str:
    """The faceted-search URL, built the way the site writes it. The subject
    value is double-encoded in the site's own links (`node%253Afield_subject`)
    and copying that verbatim is what makes the facet resolve."""
    base = f"{BASE_URL}/search?f%5B0%5D=node%253Afield_subject%3A{subject}"
    return base if page == 0 else f"{base}&page={page}"


def _entry_date(row) -> str:
    """The listing's date, whichever of the two shapes this row uses."""
    issue = row.select_one(".field--issue-date")
    if issue:
        # "Published March 2000" - month precision, so fmt must say so.
        return iso_date(issue.get_text(" ", strip=True), fuzzy=True, fmt="%Y-%m")
    m = DMY_RE.search(row.get_text(" ", strip=True))
    return iso_date(m.group(1), dayfirst=True) if m else ""


def parse_listing(content: bytes) -> list[Entry]:
    """One search page -> the articles on it. Empty list means the last page."""
    soup = BeautifulSoup(decode_html(content), "html.parser")

    items = []
    for row in soup.select("div.views-row"):
        art = row.find("article", about=True)
        if not art:
            continue  # promo block, not a result
        m = NODE_ID_RE.match(art.get("id") or "")
        title_el = row.select_one(".node__title a") or row.find("h2") or row.find("h3")
        items.append(
            {
                "url": urljoin(BASE_URL, art["about"]),
                "detail_id": m.group(1) if m else None,
                "title": title_el.get_text(" ", strip=True) if title_el else "",
                "date": _entry_date(row),
                "section": art["about"].strip("/").split("/")[0],
            }
        )
    return items


def parse_detail(content: bytes) -> Detail:
    """The article out of one live page, or `{}` if the container is missing.
    Bytes in and no fetch: the library brings the page through
    `politeness.fetch_cached`, with this site's Crawl-delay as `sleep`.

    decode_html(bytes), never r.text - the page declares UTF-8 but requests
    falls back to ISO-8859-1 whenever a header omits the charset.
    """
    soup = BeautifulSoup(decode_html(content), "html.parser")
    node = soup.select_one("div.node__content")
    if not node:
        return {}
    body, body_html = richtext.extract(node)
    return {"body": body, "body_html": body_html}


def collect(conn, session, pages: int | None = None) -> dict[str, Entry]:
    """Walk both facets -> {url: item}. Deduped across facets by url, which is
    also the DB's key, so the two lists cannot produce two rows."""
    found = {}
    for facet in FACETS:
        print(f"[{SOURCE}] facet {facet['subject']} ({facet['label']})", flush=True)
        page = 0
        while True:
            if pages and page >= pages:
                break
            try:
                content = fetch_cached(
                    conn,
                    session,
                    listing_url(facet["subject"], page),
                    sleep=CRAWL_DELAY,
                )
            except Exception as e:
                print(f"\n  ERROR listing page {page}: {e}")
                break
            items = parse_listing(content)
            if not items:
                break
            for item in items:
                found.setdefault(item["url"], item)
            print(
                f"  strona {page}: {len(items)} pozycji, razem unikalnych {len(found)}",
                flush=True,
            )
            page += 1
    return found


def scrape(
    limit: int | None = None,
    pages: int | None = None,
    list_only: bool = False,
    catch: dict | None = None,
) -> None:
    conn = connection.connect()
    session = requests.Session()

    # A dict, not a list: collect() returns {url: item} and the next line asks
    # for .values() - an empty *list* here is an AttributeError.
    found = {} if catch_up.no_crawl(catch) else collect(conn, session, pages)
    print(f"\n[{SOURCE}] {len(found)} unikalnych artykułów w obu listach", flush=True)
    if list_only:
        conn.close()
        return

    items = list(found.values())[:limit] if limit else list(found.values())
    stats = Stats(SOURCE, total=len(items))

    discovery.from_items(
        conn,
        session,
        SOURCE,
        items,
        parse=parse_detail,
        stats=stats,
        sleep=CRAWL_DELAY,
    )

    stats.summary(conn)
    catch_up.run(
        conn,
        SOURCE,
        catch,
        live_parser=parse_detail,
        sleep=CRAWL_DELAY,
        session=session,
    )
    conn.close()
