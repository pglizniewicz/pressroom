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
from progress import Stats
import wayback


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


def extract_entries(html: str, base_url: str = None, timestamp: str = None) -> list:
    """Every <h2>Month YYYY - Title</h2> heading on the page, each paired
    with its containing block's link and body text. Works for both listing
    pages (many headings, each in its own div.block) and individual article
    pages (one heading, no div.block wrapper). `base_url` is unused (this
    page's own links are already absolute) - the 3-arg shape matches
    wayback.sample_all_captures' parse_fn contract."""
    soup = BeautifulSoup(html, "html.parser")
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
        body = work.get_text(" ", strip=True)

        entries.append({"url": url, "title": title, "date": date, "body": body, "detail_id": timestamp})

    return entries


def scrape_lang(lang: str, limit: int = None) -> None:
    cfg = LANGS[lang]
    source = cfg["source"]
    conn = db.connect()
    session = requests.Session()

    best = {}  # url -> {title, date, body, detail_id}

    def consider(url, title, date, body, detail_id):
        if not url:
            return
        cur = best.get(url)
        if cur is None or len(body) > len(cur["body"]):
            best[url] = {"title": title, "date": date, "body": body, "detail_id": detail_id}

    # 1. Time-series sample every historical capture of the listing pages.
    # Small and fast (a couple dozen fetches total) - always completes in one
    # go, so do this first and keep the results in memory for step 2 to draw on.
    print(f"[{source}] Sampling listing-page history", flush=True)
    for listing_url in cfg["listing_urls"]:
        print(f"  {listing_url}", flush=True)
        entries = wayback.sample_all_captures(conn, session, listing_url, extract_entries)
        for e in entries:
            consider(e["url"], e["title"], e["date"], e["body"], e["detail_id"])

    # 2. Prefix crawl of individually-archived article pages - the slow part,
    # prone to Wayback's transient rate-limiting, so write incrementally
    # (already_stored dedup) so a rerun resumes instead of redoing everything.
    print(f"\n[{source}] Listing archived articles under {cfg['prefix']}", flush=True)
    prefix_urls = [
        normalize_url(e["original"]) for e in wayback.list_snapshots_by_prefix(cfg["prefix"])
        if ARTICLE_FILE_RE.search(e["original"].split("?", 1)[0])
    ]
    if limit:
        prefix_urls = prefix_urls[:limit]
    print(f"[{source}] {len(prefix_urls)} individually-archived article candidates", flush=True)

    stats = Stats(source)

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

        title, date, body, detail_id = "", "", "", "stub"
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
                title, date, body, detail_id = fetched[0]["title"], fetched[0]["date"], fetched[0]["body"], timestamp

        # A listing-page capture may have a fuller body than the article's own page.
        listed = best.pop(url, None)
        if listed and len(listed["body"]) > len(body):
            title, date, body, detail_id = listed["title"], listed["date"], listed["body"], listed["detail_id"]

        if not title and not body:
            stats.dead()
            continue

        db.store_release(conn, source, url, title=title, date=date, body=body,
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
