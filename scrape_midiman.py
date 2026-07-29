#!/usr/bin/env python3
"""Scraper for Midiman/M-Audio's dead 2001-era press room, sourced entirely
from Wayback Machine snapshots (midiman.net, midiman.com, m-audio.com all
mirrored the same GoLive-generated static pages under slightly different
paths/brandings during the Midiman -> M-Audio transition).

Entry point: 5 known-good index-page snapshots (pressmn.htm), each a GoLive
"URLPopup" dropdown widget listing release pages as relative paths. The
dropdown data is duplicated in two inconsistent places on the page (a
`<csobj data='{...}'>` JSON-ish blob, and the raw <option> tags themselves -
closing tags present in some captures, absent in others) - neither source
alone is complete (one snapshot has an option added by hand that never made
it into the csobj blob), so both are parsed and unioned by resolved URL.

Beyond the 5 known index pages, also prefix-crawls each domain's press/
folder(s) via wayback.list_snapshots_by_prefix, to catch any release pages
that were archived but never linked from one of these particular dropdown
snapshots (the index snapshots are just 5 points-in-time; the folders
themselves were crawled independently and more densely).

Source tags are per-domain (midiman_net / midiman_com / maudio_com), not
per-brand, since GoLive's Midiman/M-Audio branding is used inconsistently
across mirrors and only the domain is a reliable dedup boundary.

Two release-page templates seen:
  - GoLive (majority): heading in <font size="5"><b>, dateline is almost
    always literally "Arcadia, CA" (company HQ) with NO explicit date in
    most cases - this is a genuine data gap, not a parse bug; only
    prsupdac.htm embeds a parenthetical date.
  - Word/mso-export (prdighar.htm only, so far): AP-wire style, ALL-CAPS
    city dateline with an explicit "Month Day, Year".

Known likely-unrecoverable items (attempted anyway, matching the "let
get_latest_working_snapshot return None" convention - no hardcoded
skip-list): prcalfad.htm, prport44.htm, possibly pr2044.htm/prmixm10.htm.
promni.pdf and MORE5.pdf are PDF press releases - out of format scope
(no PDF text extraction anywhere in this repo); skipped by extension
filter before any Wayback lookup is attempted, and NOT counted as "dead"
since they were never attempted.

Usage:
  python scrape_midiman.py                    # everything: 5 index pages + prefix crawl
  python scrape_midiman.py --limit 5          # only process first 5 candidate release URLs (testing)
  python scrape_midiman.py --no-prefix-crawl  # index-page-derived candidates only
"""

import argparse
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from common import SLEEP
from db import already_stored
import db
import wayback

DB_PATH = Path(__file__).parent / "pressroom.db"

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

CSOBJ_RE = re.compile(r'label\s*=\s*&quot;([^&]*)&quot;;\s*url\s*=\s*&quot;([^&]*)&quot;', re.IGNORECASE)
OPTION_RE = re.compile(r'<option\s+value="([^"]+)"[^>]*>(.*?)(?=<option|</select|</OPTION>|$)', re.IGNORECASE | re.DOTALL)

ARCADIA_DATELINE_RE = re.compile(r"Arcadia,\s*Ca(?:lif)?\.?", re.IGNORECASE)
AP_DATELINE_RE = re.compile(r"\b[A-Z]{2,}(?:\s[A-Z]{2,})*,\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)")
DATE_PAREN_RE = re.compile(
    r"\((January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})\)",
    re.IGNORECASE,
)
DATE_BARE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s*(\d{4})",
    re.IGNORECASE,
)

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def _iso_date(month_name: str, day: str, year: str) -> str:
    month = MONTHS.index(month_name.lower()) + 1
    return f"{year}-{month:02d}-{int(day):02d}"


def extract_dropdown_links(html: str, index_original_url: str) -> dict:
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


def parse_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["title", "script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

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

    return {"title": title, "date": date, "body": text}


def scrape(limit: int = None, prefix_crawl: bool = True) -> None:
    conn = sqlite3.connect(DB_PATH)
    db.init_db(conn)
    session = requests.Session()

    candidates = {}  # (source, url) -> title

    for page in INDEX_PAGES:
        print(f"[{page['source']}] Fetching index {page['wayback_url']}", flush=True)
        try:
            content = wayback.fetch_snapshot(conn, session, page["wayback_url"], timeout=20)
            links = extract_dropdown_links(content.decode("cp1252", errors="replace"), page["original"])
        except Exception as e:
            print(f"  ERROR fetching index page: {e}")
            continue
        print(f"  {len(links)} release links found", flush=True)
        for url, title in links.items():
            candidates.setdefault((page["source"], url), title)

    if prefix_crawl:
        for prefix, source in PREFIX_CRAWL_ROOTS:
            print(f"[{source}] Listing archived pages under {prefix}", flush=True)
            try:
                snapshots = wayback.list_snapshots_by_prefix(prefix)
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

    counts = {}

    for item in items:
        source = item["source"]
        url = item["url"]
        c = counts.setdefault(source, {"new": 0, "skip": 0, "dead": 0})

        if already_stored(conn, url):
            c["skip"] += 1
            print(".", end="", flush=True)
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            continue
        if not found:
            c["dead"] += 1
            print("d", end="", flush=True)
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            continue

        title = parsed["title"] or item["title"]

        if db.store_release(conn, source, url, title=title, date=parsed["date"],
                            body=parsed["body"], detail_id=timestamp):
            c["new"] += 1
            print("+", end="", flush=True)

    print()
    for source, c in counts.items():
        total_in_db = db.source_total(conn, source)
        print(
            f"[{source}] Added {c['new']} new, skipped {c['skip']} existing, "
            f"{c['dead']} never archived successfully. Total [{source}] in DB: {total_in_db}"
        )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Midiman/M-Audio press releases via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate pages")
    parser.add_argument(
        "--no-prefix-crawl",
        dest="prefix_crawl",
        action="store_false",
        help="Skip prefix-crawling the press/ folders; only use the 5 known index pages",
    )
    args = parser.parse_args()
    scrape(limit=args.limit, prefix_crawl=args.prefix_crawl)
