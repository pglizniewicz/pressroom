"""Scraper for TerraTec's 2007-2013 CMS-era press site
(terratec.net/en/company/press/ and /de/unternehmen/presse/).

Two complementary techniques, since neither alone is complete:
  1. Prefix crawl of individually-archived article pages (mostly 2007-2010).
  2. Time-series sampling of the "New Releases" (full text embedded) and
     "Press archive" (title only) listing pages across every historical
     capture, since these pages are a rolling window that drops old
     entries over time - a single "latest" snapshot misses most of it.

No automated English/German dedup - stored separately, one source per
language, and the preference between them is decided by hand.
"""

import re

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from pressroom.release.control.storage import already_stored
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraper.control import catch_up
from pressroom.scraper.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.control.outcome import Stats
from pressroom.fetcher.control import archive
from pressroom.text.control.decoding import decode_html
from pressroom.scraper.entity.parse import Detail, Entry


LANGS = {
    "en": {
        "source": "terratec_new_en",
        "prefix": "http://www.terratec.net/en/company/press/",
        "listing_urls": [
            "http://www.terratec.net/en/company/press/press.html",
            "http://www.terratec.net/en/company/press/archive.html",
        ],
    },
    "de": {
        "source": "terratec_new_de",
        "prefix": "http://www.terratec.net/de/unternehmen/presse/",
        "listing_urls": [
            "http://www.terratec.net/de/unternehmen/presse/presse.html",
            "http://www.terratec.net/de/unternehmen/presse/archiv.html",
        ],
    },
}

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

ARTICLE_FILE_RE = re.compile(r"_\d+\.html?(?:$|\?)", re.IGNORECASE)
# "Month YYYY - Title" or "EventName YYYY - Title" (e.g. "CeBIT 2008 - ...")
TITLE_DATE_RE = re.compile(r"^([A-Za-zäöüÄÖÜ]+)\s+(\d{4})\s*-\s*(.+)$")

# div#Content > div.column.span-8 - the column that holds the release on both
# page shapes this CMS has (a listing of div.block entries, or one article).
# Measured over all 164 cached captures: present in every one of them, and the
# only h2s outside it are chrome ("Unternehmen" in the menu, "Presse-Kontakt"
# in the right column). That is what makes the date-less headline below safe to
# accept - the container identifies a heading as a release, so TITLE_DATE_RE no
# longer has to.
CONTENT_ID = "Content"
ARTICLE_COLUMN = "span-8"


def article_area(soup):
    """The column a release is in, or None on a capture that predates this
    CMS (the 2003-era terratec.net/press/ pages, which other scrapers own)."""
    content = soup.find("div", id=CONTENT_ID)
    return content.find("div", class_=ARTICLE_COLUMN) if content else None


def normalize_url(url: str) -> str:
    return url.replace(":80/", "/") if url else url


def parse_month_year(title: str):
    m = TITLE_DATE_RE.match(title.strip())
    if not m:
        return "", title.strip()
    month_word, year, rest = m.groups()
    month_num = GERMAN_MONTHS.get(month_word.lower())
    if not month_num:
        try:
            month_num = du.parse(f"{month_word} 1 {year}").month
        except Exception:
            month_num = None
    if month_num:
        return f"{year}-{month_num:02d}", rest.strip()
    # Event-name prefix (CeBIT, IFA, ...) rather than a month - year-only precision
    return year, rest.strip()


def parse_detail(content: bytes) -> Detail:
    """One article page -> {title, date, body, body_html}, for
    the parser this scraper hands to catch_up.

    extract_entries already handles an individual article page (one <h2>, no
    div.block wrapper) as well as a listing; this just unwraps the single
    entry, taking the longest if a capture happens to carry several. Returns
    an empty body when there is no heading at all, which the caller reads as
    "this parser does not cover this capture".
    """
    entries = [e for e in extract_entries(content) if e.get("body_html")]
    if not entries:
        return {"title": "", "date": "", "body": "", "body_html": None}
    best = max(entries, key=lambda e: len(e["body"]))
    return {
        "title": best["title"],
        "date": best["date"],
        "body": best["body"],
        "body_html": best["body_html"],
    }


def parse_first_entry(content: bytes) -> Detail:
    """The article as its own page states it: on this CMS an article page is the
    same template as a listing, so the article is the first entry
    extract_entries yields. `{}` when it yields none, which is what every other
    whole-page parser returns for "no article in this capture".

    Not parse_detail(): that one is phase 2's and takes the *longest* entry
    carrying markup, which is the right choice when reparsing a capture whose
    shape is unknown and a different question from "the first thing on this
    page".
    """
    entries = extract_entries(content)
    if not entries:
        return {}
    first = entries[0]
    return {
        "title": first["title"],
        "date": first["date"],
        "body": first["body"],
        "body_html": first["body_html"],
    }


def extract_entries(
    content: bytes, base_url: str | None = None, timestamp: str | None = None
) -> list[Entry]:
    """Every <h2>Month YYYY - Title</h2> heading on the page, each paired
    with its containing block's link and body text. Works for both listing
    pages (many headings, each in its own div.block) and individual article
    pages (one heading, no div.block wrapper). `base_url` is unused (this
    page's own links are already absolute) - the 3-arg shape matches
    discovery.sample_all_captures' parse_fn contract.

    Takes bytes and decodes them here through `decode_html`: these pages declare
    charset=utf-8 and are valid UTF-8, and handing the bytes to BeautifulSoup
    instead lets chardet guess - it picks cp1252 on some captures and cp1258 on
    others, which is how 'FĂ¼r' gets stored."""
    soup = BeautifulSoup(decode_html(content), "html.parser")
    area = article_area(soup)
    headings = area.find_all("h2") if area else soup.find_all("h2")
    entries = []

    for h2 in headings:
        heading = h2.get_text(" ", strip=True)
        if TITLE_DATE_RE.match(heading):
            date, title = parse_month_year(heading)
        elif area is not None and (
            h2.find_parent("div", class_="block") or len(headings) == 1
        ):
            # A fallback, never a replacement. Later captures dropped the
            # "Month YYYY - " prefix from the headline, and the regex is the
            # only thing that recognised a release, so those pages parsed to
            # nothing at all: 9 of the 144 cached article captures and 8
            # listing entries. Inside the article column a heading in a
            # div.block, or the column's only heading, *is* the release
            # headline; the date is then simply not on the page, and the row
            # keeps the one its listing gave it.
            date, title = "", heading
        else:
            continue

        container = h2.find_parent("div", class_="block") or h2.parent

        url = None
        for a in container.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and ARTICLE_FILE_RE.search(href):
                url = normalize_url(href)
                break

        work = BeautifulSoup(str(container), "html.parser")
        work_h2 = work.find("h2")
        if work_h2:
            work_h2.decompose()
        for p in work.find_all("p"):
            if p.find("a", class_="arrow"):
                p.decompose()
        body, body_html = richtext.extract(work)

        entries.append(
            {
                "url": url,
                "title": title,
                "date": date,
                "body": body,
                "body_html": body_html,
                "detail_id": timestamp,
            }
        )

    return entries


def scrape_lang(lang: str, limit: int | None = None, catch: dict | None = None) -> None:
    cfg = LANGS[lang]
    source = cfg["source"]
    conn = connection.connect()
    session = requests.Session()

    best = {}  # url -> {title, date, body, body_html, detail_id}

    def consider(url, title, date, body, body_html, detail_id):
        if not url:
            return
        cur = best.get(url)
        if cur is None or len(body) > len(cur["body"]):
            best[url] = {
                "title": title,
                "date": date,
                "body": body,
                "body_html": body_html,
                "detail_id": detail_id,
            }

    # 1. Time-series sample every historical capture of the listing pages.
    # Small and fast (a couple dozen fetches total) - always completes in one
    # go, so do this first and keep the results in memory for step 2 to draw on.
    print(f"[{source}] Sampling listing-page history", flush=True)
    for listing_url in cfg["listing_urls"]:
        print(f"  {listing_url}", flush=True)
        entries = (
            []
            if catch_up.no_crawl(catch)
            else discovery.sample_all_captures(
                conn, session, listing_url, extract_entries
            )
        )
        for e in entries:
            consider(
                e["url"],
                e["title"],
                e["date"],
                e["body"],
                e["body_html"],
                e["detail_id"],
            )

    # 2. Prefix crawl of individually-archived article pages - the slow part,
    # prone to Wayback's transient rate-limiting, so write incrementally
    # (already_stored dedup) so a rerun resumes instead of redoing everything.
    print(f"\n[{source}] Listing archived articles under {cfg['prefix']}", flush=True)
    # Not fatal, unlike the single-phase scrapers: step 1 already collected
    # entries that step 3 stores, so losing the prefix crawl must not discard
    # them - carry on without the prefix crawl.
    #
    # Guarded like step 1. The bare except below is why a miss here goes
    # unnoticed - it swallows anything a probe raises, which is why the check
    # for it raises a BaseException instead.
    if catch_up.no_crawl(catch):
        snapshots = []
    else:
        try:
            snapshots = archive.list_snapshots_by_prefix(cfg["prefix"])
        except Exception as e:
            print(
                f"  ERROR listing articles: {e}\n  continuing with listing-page results only"
            )
            snapshots = []
    prefix_urls = [
        normalize_url(e["original"])
        for e in snapshots
        if ARTICLE_FILE_RE.search(e["original"].split("?", 1)[0])
    ]
    if limit:
        prefix_urls = prefix_urls[:limit]
    print(
        f"[{source}] {len(prefix_urls)} individually-archived article candidates",
        flush=True,
    )

    stats = Stats(source, total=len(prefix_urls))

    for url in prefix_urls:
        if already_stored(conn, url):
            stats.skipped()
            continue

        found = discovery.capture(conn, session, url, parse_first_entry, stats=stats)
        if found is None:
            continue

        title, date, body, body_html, detail_id = "", "", "", "", "stub"
        if found.parsed:
            title, date = found.parsed["title"], found.parsed["date"]
            body, body_html, detail_id = (
                found.parsed["body"],
                found.parsed["body_html"],
                found.timestamp,
            )

        # A listing-page capture may have a fuller body than the article's own page.
        listed = best.pop(url, None)
        if listed and len(listed["body"]) > len(body):
            title, date = listed["title"], listed["date"]
            body, body_html, detail_id = (
                listed["body"],
                listed["body_html"],
                listed["detail_id"],
            )

        if not title and not body:
            stats.dead()
            continue

        storage.store_release(
            conn,
            source,
            url,
            title=title,
            date=date,
            body=body,
            body_html=body_html or None,
            detail_id=detail_id if body else None,
            grade="full" if body else "stub",
        )
        if body:
            stats.added()
        else:
            stats.stub()

    # 3. Any listing-page discoveries never covered by the prefix crawl at all.
    for url, e in best.items():
        if already_stored(conn, url):
            continue
        storage.store_release(
            conn,
            source,
            url,
            title=e["title"],
            date=e["date"],
            body=e["body"],
            body_html=e["body_html"] or None,
            detail_id=e["detail_id"] if e["body"] else None,
            grade="full" if e["body"] else "stub",
            commit=False,
        )
        if e["body"]:
            stats.added()
        else:
            stats.stub()
    conn.commit()

    stats.summary(conn)
    catch_up.run(
        conn,
        source,
        catch,
        parser=parse_detail,
        session=session,
        collect=lambda c, _l=lang: cached_entries(c, _l),
    )
    conn.close()


def cached_entries(conn, lang: str) -> dict[str, Entry]:
    """(url -> entry) out of every cached capture of that language's two listing
    pages, for catch_up.

    Not a synthetic url like midiman_de's - these hrefs really were on the page -
    but the same predicament: for 15 of these rows archive.org has zero captures
    of the article itself (CDX confirms it), so the listing capture the text came
    from is the only place the formatting can still be read out of.
    """
    out = {}
    for listing in LANGS[lang]["listing_urls"]:
        for cap_url, content in conn.execute(
            "SELECT url, content FROM page_cache WHERE url LIKE '%id_/' || ?",
            (listing,),
        ):
            m = re.search(r"/web/(\d{14})id_/", cap_url)
            try:
                entries = extract_entries(content, listing, m.group(1) if m else None)
            except Exception as e:
                print(f"\n    {cap_url}: {e}")
                continue
            for e in entries:
                url = e.get("url")
                if not url or not e.get("body_html"):
                    continue
                e["origin_url"] = cap_url
                if url not in out or len(e["body"]) > len(out[url]["body"]):
                    out[url] = e
    return out
