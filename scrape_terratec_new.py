#!/usr/bin/env python3
"""Scraper for TerraTec's 2007-2013 CMS-era press site
(terratec.net/en/company/press/ and /de/unternehmen/presse/).

Two complementary techniques, since neither alone is complete:
  1. Prefix crawl of individually-archived article pages (mostly 2007-2010).
  2. Time-series sampling of the "New Releases" (full text embedded) and
     "Press archive" (title only) listing pages across every historical
     capture, since these pages are a rolling window that drops old
     entries over time - a single "latest" snapshot misses most of it.

No automated English/German dedup - stored separately (source per
language), preference applied by hand during terratec.json curation.

Usage:
  python scrape_terratec_new.py en
  python scrape_terratec_new.py de
"""

import argparse
import re
import time

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from fetch import SLEEP
from db import already_stored
import db
import richtext
from progress import Stats
import wayback
from encoding import decode_html


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
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}

ARTICLE_FILE_RE = re.compile(r"_\d+\.html?(?:$|\?)", re.IGNORECASE)
# "Month YYYY - Title" or "EventName YYYY - Title" (e.g. "CeBIT 2008 - ...")
TITLE_DATE_RE = re.compile(r"^([A-Za-zäöüÄÖÜ]+)\s+(\d{4})\s*-\s*(.+)$")


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


def parse_detail(content: bytes) -> dict:
    """One article page -> {title, date, body, body_html}, for
    backfill_body_html's CACHED_PARSERS.

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
    return {"title": best["title"], "date": best["date"],
            "body": best["body"], "body_html": best["body_html"]}


def extract_entries(content: bytes, base_url: str = None, timestamp: str = None) -> list:
    """Every <h2>Month YYYY - Title</h2> heading on the page, each paired
    with its containing block's link and body text. Works for both listing
    pages (many headings, each in its own div.block) and individual article
    pages (one heading, no div.block wrapper). `base_url` is unused (this
    page's own links are already absolute) - the 3-arg shape matches
    wayback.sample_all_captures' parse_fn contract.

    Takes bytes and decodes them here through encoding.decode_html: these pages
    declare charset=utf-8 and are valid UTF-8, and handing the bytes to
    BeautifulSoup instead let chardet guess - it picked cp1252 on some captures
    and cp1258 (Vietnamese) on others, which is how 25 rows ended up storing
    'FÃ¼hrungsduo' and 'FĂ¼r'. repair_encoding.py undid that; this is why it
    cannot come back."""
    soup = BeautifulSoup(decode_html(content), "html.parser")
    entries = []

    for h2 in soup.find_all("h2"):
        heading = h2.get_text(" ", strip=True)
        if not TITLE_DATE_RE.match(heading):
            continue
        date, title = parse_month_year(heading)

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

        entries.append({"url": url, "title": title, "date": date, "body": body,
                        "body_html": body_html, "detail_id": timestamp})

    return entries


def scrape_lang(lang: str, limit: int = None) -> None:
    cfg = LANGS[lang]
    source = cfg["source"]
    conn = db.connect()
    session = requests.Session()

    best = {}  # url -> {title, date, body, body_html, detail_id}

    def consider(url, title, date, body, body_html, detail_id):
        if not url:
            return
        cur = best.get(url)
        if cur is None or len(body) > len(cur["body"]):
            best[url] = {"title": title, "date": date, "body": body,
                         "body_html": body_html, "detail_id": detail_id}

    # 1. Time-series sample every historical capture of the listing pages.
    # Small and fast (a couple dozen fetches total) - always completes in one
    # go, so do this first and keep the results in memory for step 2 to draw on.
    print(f"[{source}] Sampling listing-page history", flush=True)
    for listing_url in cfg["listing_urls"]:
        print(f"  {listing_url}", flush=True)
        entries = wayback.sample_all_captures(conn, session, listing_url, extract_entries)
        for e in entries:
            consider(e["url"], e["title"], e["date"], e["body"], e["body_html"],
                     e["detail_id"])

    # 2. Prefix crawl of individually-archived article pages - the slow part,
    # prone to Wayback's transient rate-limiting, so write incrementally
    # (already_stored dedup) so a rerun resumes instead of redoing everything.
    print(f"\n[{source}] Listing archived articles under {cfg['prefix']}", flush=True)
    # Not fatal, unlike the single-phase scrapers: step 1 already collected
    # entries that step 3 stores, so losing the prefix crawl must not discard
    # them - degrade to prefix-crawl-free and keep going.
    try:
        snapshots = wayback.list_snapshots_by_prefix(cfg["prefix"])
    except Exception as e:
        print(f"  ERROR listing articles: {e}\n  continuing with listing-page results only")
        snapshots = []
    prefix_urls = [
        normalize_url(e["original"]) for e in snapshots
        if ARTICLE_FILE_RE.search(e["original"].split("?", 1)[0])
    ]
    if limit:
        prefix_urls = prefix_urls[:limit]
    print(f"[{source}] {len(prefix_urls)} individually-archived article candidates", flush=True)

    stats = Stats(source, total=len(prefix_urls))

    for url in prefix_urls:
        if already_stored(conn, url):
            stats.skipped()
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue

        title, date, body, body_html, detail_id = "", "", "", "", "stub"
        if found:
            snapshot_url, timestamp = found
            try:
                content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
                fetched = extract_entries(content)
            except Exception as e:
                print(f"\n  ERROR fetching {snapshot_url}: {e}")
                stats.uncertain()
                continue
            if fetched:
                title, date = fetched[0]["title"], fetched[0]["date"]
                body, body_html, detail_id = fetched[0]["body"], fetched[0]["body_html"], timestamp

        # A listing-page capture may have a fuller body than the article's own page.
        listed = best.pop(url, None)
        if listed and len(listed["body"]) > len(body):
            title, date = listed["title"], listed["date"]
            body, body_html, detail_id = listed["body"], listed["body_html"], listed["detail_id"]

        if not title and not body:
            stats.dead()
            continue

        db.store_release(conn, source, url, title=title, date=date, body=body,
                         body_html=body_html or None,
                         detail_id=detail_id if body else "stub")
        if body:
            stats.added()
        else:
            stats.stub()

    # 3. Any listing-page discoveries never covered by the prefix crawl at all.
    for url, e in best.items():
        if already_stored(conn, url):
            continue
        db.store_release(conn, source, url, title=e["title"], date=e["date"], body=e["body"],
                         body_html=e["body_html"] or None,
                         detail_id=e["detail_id"] if e["body"] else "stub", commit=False)
        if e["body"]:
            stats.added()
        else:
            stats.stub()
    conn.commit()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TerraTec's 2007-2013 CMS-era press site")
    parser.add_argument("lang", choices=["en", "de"])
    parser.add_argument("--limit", type=int, default=None, help="Cap prefix-crawl candidates (testing)")
    args = parser.parse_args()
    scrape_lang(args.lang, limit=args.limit)
