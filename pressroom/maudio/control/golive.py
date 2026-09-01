"""Scraper for Midiman/M-Audio's dead 2001-era press room, sourced entirely
from Wayback Machine snapshots (midiman.net, midiman.com and m-audio.com all
mirrored the same GoLive-generated static pages under slightly different paths
and brandings through the Midiman -> M-Audio transition).

Entry point: the known-good index-page snapshots (pressmn.htm), each a GoLive
"URLPopup" dropdown widget listing release pages as relative paths. The dropdown
data is duplicated in two inconsistent places on the page - a
`<csobj data='{...}'>` blob and the raw <option> tags, whose closing tags are
present in some captures and absent in others - and neither is complete on its
own, since one snapshot has an option added by hand that never reached the csobj
blob. So both are parsed and unioned by resolved url.

Beyond those index pages it also prefix-crawls each domain's press/ folders
through archive.list_snapshots_by_prefix: the index snapshots are a handful of
points in time, while the folders themselves were crawled independently and far
more densely.

Source tags are per-domain (midiman_net / midiman_com / maudio_com), not
per-brand, because GoLive's Midiman/M-Audio branding is used inconsistently
across the mirrors and only the domain is a reliable dedup boundary.

Two release-page templates:
  - GoLive (the majority): heading in <font size="5"><b>, and a dateline that is
    almost always literally "Arcadia, CA" with **no explicit date** - a genuine
    data gap, not a parse bug. Only prsupdac.htm embeds a parenthetical date.
  - Word/mso-export (prdighar.htm so far): AP-wire style, an ALL-CAPS city
    dateline with an explicit "Month Day, Year".

Known likely-unrecoverable items are attempted anyway rather than kept in a
skip-list, letting get_latest_working_snapshot return None: prcalfad.htm,
prport44.htm, possibly pr2044.htm/prmixm10.htm. The two .pdf releases of this
era (promni.pdf, MORE5.pdf) are filtered out by extension before any Wayback
lookup and are not counted `dead`, because they were never attempted - the
attachment converters exist, but these two have no capture anywhere to convert.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.database.control import connection
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail


INDEX_PAGES = [
    {
        "wayback_url": "https://web.archive.org/web/20010520165409id_/http://www.midiman.net:80/m-audio/html/pressmn.htm",
        "original": "http://www.midiman.net/m-audio/html/pressmn.htm",
        "source": "midiman_net",
    },
    {
        "wayback_url": "https://web.archive.org/web/20010212014811id_/http://www.midiman.net/midiman/html/pressmn.htm",
        "original": "http://www.midiman.net/midiman/html/pressmn.htm",
        "source": "midiman_net",
    },
    {
        "wayback_url": "https://web.archive.org/web/20010627015816id_/http://www.m-audio.com:80/m-audio/html/pressmn.htm",
        "original": "http://www.m-audio.com/m-audio/html/pressmn.htm",
        "source": "maudio_com",
    },
    {
        "wayback_url": "https://web.archive.org/web/20010701094006id_/http://www.m-audio.com:80/midiman/html/pressmn.htm",
        "original": "http://www.m-audio.com/midiman/html/pressmn.htm",
        "source": "maudio_com",
    },
    {
        "wayback_url": "https://web.archive.org/web/20010124074000id_/http://www.midiman.com/m-audio/html/pressmn.htm",
        "original": "http://www.midiman.com/m-audio/html/pressmn.htm",
        "source": "midiman_com",
    },
]

PREFIX_CRAWL_ROOTS = [
    ("http://www.midiman.com:80/m-audio/html/press/", "midiman_com"),
    ("http://www.midiman.com:80/midiman/html/press/", "midiman_com"),
    ("http://www.midiman.net:80/m-audio/html/press/", "midiman_net"),
    ("http://www.midiman.net:80/midiman/html/press/", "midiman_net"),
    ("http://www.m-audio.com:80/m-audio/html/press/", "maudio_com"),
    ("http://www.m-audio.com:80/midiman/html/press/", "maudio_com"),
]

CSOBJ_RE = re.compile(
    r"label\s*=\s*&quot;([^&]*)&quot;;\s*url\s*=\s*&quot;([^&]*)&quot;", re.IGNORECASE
)
OPTION_RE = re.compile(
    r'<option\s+value="([^"]+)"[^>]*>(.*?)(?=<option|</select|</OPTION>|$)',
    re.IGNORECASE | re.DOTALL,
)

ARCADIA_DATELINE_RE = re.compile(r"Arcadia,\s*Ca(?:lif)?\.?", re.IGNORECASE)
AP_DATELINE_RE = re.compile(
    r"\b[A-Z]{2,}(?:\s[A-Z]{2,})*,\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)"
)
DATE_PAREN_RE = re.compile(
    r"\((January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})\)",
    re.IGNORECASE,
)
DATE_BARE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s*(\d{4})",
    re.IGNORECASE,
)

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _iso_date(month_name: str, day: str, year: str) -> str:
    month = MONTHS.index(month_name.lower()) + 1
    return f"{year}-{month:02d}-{int(day):02d}"


def extract_dropdown_links(html: str, index_original_url: str) -> dict[str, str]:
    links = {}

    for label, rel_url in CSOBJ_RE.findall(html):
        if not rel_url:
            continue
        url = urljoin(index_original_url, rel_url)
        links.setdefault(url, label.strip())

    for rel_url, label_html in OPTION_RE.findall(html):
        label = BeautifulSoup(label_html, "html.parser").get_text(" ", strip=True)
        label = re.sub(r"</?option[^>]*>", "", label, flags=re.IGNORECASE).strip()
        url = urljoin(index_original_url, rel_url)
        links.setdefault(url, label)

    return links


def is_supported_page(url: str) -> bool:
    return url.lower().split("?", 1)[0].endswith((".htm", ".html"))


def parse_snapshot(content: bytes) -> Detail:
    # cp1252, stated: these are 2001-era GoLive pages, which CLAUDE.md names
    # as the case where decode_html is wrong - in prose full of accents two
    # adjacent high bytes can coincidentally form a valid UTF-8 sequence.
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    for tag in soup.find_all(["title", "script", "style"]):
        tag.decompose()

    # Flat text, kept ON PURPOSE and only for the two detections below. Unlike
    # every other parser here, this one derives the *title* by slicing the flat
    # text at the dateline, so it cannot simply be replaced by the DOM pass.
    text = soup.get_text(" ", strip=True)

    # The body does come from the DOM now. Calibration over every cached
    # capture of these two domains put the whole document's coverage of the
    # previously stored body at 1.00: a 2001 GoLive press page carries no nav
    # to exclude, which is why whole-page extraction was right all along and
    # only the *flattening* was wrong.
    body, body_html = richtext.extract(soup)

    date = ""
    m = DATE_PAREN_RE.search(text)
    if not m:
        m = DATE_BARE_RE.search(text)
    if m:
        try:
            date = _iso_date(m.group(1), m.group(2), m.group(3))
        except Exception:
            date = ""

    title = ""
    marker = ARCADIA_DATELINE_RE.search(text)
    if not marker:
        marker = AP_DATELINE_RE.search(text)
    if marker:
        candidate = text[: marker.start()].strip()
        half = len(candidate) // 2
        if half and candidate[:half].strip() == candidate[half:].strip():
            candidate = candidate[:half].strip()
        title = candidate

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def scrape(
    limit: int | None = None, prefix_crawl: bool = True, catch: dict | None = None
) -> None:
    conn = connection.connect()
    session = requests.Session()

    candidates = {}  # (source, url) -> title

    for page in [] if catch_up.no_crawl(catch) else INDEX_PAGES:
        print(f"[{page['source']}] Fetching index {page['wayback_url']}", flush=True)
        try:
            content = archive.fetch_snapshot(
                conn, session, page["wayback_url"], timeout=20
            )
            links = extract_dropdown_links(
                content.decode("cp1252", errors="replace"), page["original"]
            )
        except Exception as e:
            print(f"  ERROR fetching index page: {e}")
            continue
        print(f"  {len(links)} release links found", flush=True)
        for url, title in links.items():
            candidates.setdefault((page["source"], url), title)

    # Both halves of discovery need the guard, not just the first: this one is
    # a CDX prefix listing per root.
    if prefix_crawl and not catch_up.no_crawl(catch):
        for prefix, source in PREFIX_CRAWL_ROOTS:
            print(f"[{source}] Listing archived pages under {prefix}", flush=True)
            try:
                snapshots = archive.list_snapshots_by_prefix(prefix)
            except Exception as e:
                print(f"  ERROR listing prefix: {e}")
                continue
            for entry in snapshots:
                url = entry["original"]
                candidates.setdefault((source, url), "")
            print(f"  {len(snapshots)} archived pages found", flush=True)

    items = [
        {"source": source, "url": url, "title": title}
        for (source, url), title in candidates.items()
        if is_supported_page(url)
    ]
    if limit:
        items = items[:limit]
    print(f"{len(items)} total candidate release pages", flush=True)

    # Grouped by tag rather than walked in candidate order: a tag is what says
    # which rows a run owns, and it is what a Stats is labelled with - so one
    # pass per tag is what lets the library own the loop. The listing's title
    # rides along as the fallback for a capture whose own markup carries none.
    by_tag: dict[str, dict[str, str]] = {}
    for item in items:
        by_tag.setdefault(item["source"], {})[item["url"]] = item["title"]

    for tag, titles in by_tag.items():
        stats = Stats(tag, total=len(titles))
        discovery.from_candidates(
            conn,
            session,
            tag,
            list(titles),
            parse=parse_snapshot,
            stats=stats,
            titles=titles,
        )
        stats.summary(conn)
    # Phase 2 once per tag: three tags come out of this one CMS generation, and
    # the tag is what says which rows a run owns.
    for tag in sorted({p["source"] for p in INDEX_PAGES}):
        catch_up.run(conn, tag, catch, parser=parse_snapshot, session=session)
    conn.close()
