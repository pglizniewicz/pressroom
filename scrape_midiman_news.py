#!/usr/bin/env python3
"""Scraper for Midiman/M-Audio's UK site "News" section
(midiman.co.uk/index.php?do=media.news, detail pages at
index.php?do=media.new&ID=<32-hex>) -> unified pressroom.db, sourced
entirely from Wayback Machine snapshots.

Same "Hydra Media Labs" CMS era as scrape_midiman_media_pr.py, but a
different section of the site: midiman.co.uk apparently never got a formal
"Press Releases" section, only this "News" one. Structurally richer than
media_pr though - each listed item has its own archived HTML detail page
(do=media.new&ID=<hex>) containing the FULL article body, not just an
external .doc/.pdf link-out, so full text is recoverable directly here with
no follow-up backfill needed (unlike scrape_midiman_media_pr.py).

Two-tier discovery, same idiom as scrape_terratec_pressde.py/
backfill_terratec_teasers.py:
  1. Sample every historical capture of the listing page (both the bare
     do=media.news and its &show=all variant) to build a (date, title) ->
     {href, teaser} map - listing entries carry a date and teaser the
     detail page itself doesn't.
  2. For each entry whose href is a recoverable ID=<hex> link, try to fetch
     its own detail-page capture for the full body, falling back to the
     listing teaser if the detail page was never archived.
  3. Bonus discovery (default on, --no-prefix-crawl to disable): prefix-crawl
     do=media.new&ID= directly - it was archived far more densely (114
     distinct IDs) than the handful of listing captures ever link to, same
     "the folder was crawled independently of the index snapshots" idiom as
     scrape_midiman.py's PREFIX_CRAWL_ROOTS. These prefix-only IDs have no
     listing metadata, so their date is recovered by matching the detail
     page's own title against the listing-derived map (best-effort; left
     blank if no match, a genuine gap, same convention as the Arcadia-
     dateline gap in scrape_midiman.py).

Confirmed quirk, same shape as scrape_midiman_pressdb.py's title-link-
retargeting bug: some &show=all captures link an article via the recoverable
do=media.new&ID=<hex> scheme, others (later captures) via a
news/en_us-<N>.html scheme confirmed to have zero Wayback captures ever -
a dead end. Deduped by (date, title), preferring whichever variant carries
a recoverable ID.

Locale variants (&setlocale=en_gb/en_us/fr_fr/... exist in Wayback) and
midiman.net's own, much larger media.new/media.news sections are out of
scope for this pass - not requested.

Usage:
  python scrape_midiman_news.py                    # everything
  python scrape_midiman_news.py --limit 5          # cap listing entries and prefix-crawl IDs (testing)
  python scrape_midiman_news.py --no-prefix-crawl   # skip the ID= prefix-crawl bonus discovery
"""

import argparse
import re
import sqlite3
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from db import stored_detail_id
from dates import iso_date
import db
from progress import Stats
import wayback

SOURCE = "midiman_couk_news"

LISTING_URLS = [
    "http://www.midiman.co.uk/index.php?do=media.news",
    "http://www.midiman.co.uk/index.php?do=media.news&show=all",
]
DETAIL_URL_TMPL = "http://www.midiman.co.uk/index.php?do=media.new&ID={}"
DETAIL_PREFIX = "http://www.midiman.co.uk/index.php?do=media.new&ID="

ID_HREF_RE = re.compile(r"ID=([0-9a-f]{32})")
DATE_TITLE_RE = re.compile(r"^([A-Za-z]+ \d{1,2},\s*\d{4})\s*-\s*(.+)$")


def extract_entries(html: bytes, base_url: str, timestamp: str = None) -> list:
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for span in soup.find_all("span", class_="boldtext"):
        a = span.find("a", href=True)
        if not a:
            continue
        m = DATE_TITLE_RE.match(a.get_text(" ", strip=True))
        if not m:
            continue
        date_str, title = m.groups()
        date = iso_date(date_str)
        href = urljoin(base_url, a["href"])

        teaser = ""
        br = span.find_next_sibling("br")
        if br:
            teaser_span = br.find_next_sibling("span", class_="normaltext")
            if teaser_span:
                teaser = teaser_span.get_text(" ", strip=True)

        entries.append({"date": date, "title": title.strip(), "href": href, "teaser": teaser})
    return entries


def rank(entry: dict):
    return (1 if ID_HREF_RE.search(entry["href"]) else 0, len(entry["teaser"]))


def parse_detail(html: bytes) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_td = soup.select_one("td.boldtextgray")
    body_p = soup.select_one("p.normaltext")

    title = ""
    if title_td:
        teaser_span = title_td.find("span", class_="normaltext")
        if teaser_span:
            teaser_span.decompose()
        title = title_td.get_text(" ", strip=True)

    body = body_p.get_text(" ", strip=True) if body_p else ""
    return {"title": title, "body": body}


def discover_listing_best(conn: sqlite3.Connection, limit: int = None) -> dict:
    session = requests.Session()
    best = {}  # (date, title) -> {href, teaser}

    for listing_url in LISTING_URLS:
        print(f"[{SOURCE}] Listing historical captures of {listing_url}", flush=True)
        entries = wayback.sample_all_captures(conn, session, listing_url, extract_entries, limit=limit)
        for e in entries:
            key = (e["date"], e["title"])
            cur = best.get(key)
            if cur is None or rank(e) > rank(cur):
                best[key] = {"href": e["href"], "teaser": e["teaser"]}

    print(f"\n[{SOURCE}] {len(best)} distinct listing entries found across all captures", flush=True)
    return best


def discover_prefix_ids() -> set:
    print(f"[{SOURCE}] Listing archived pages under {DETAIL_PREFIX}", flush=True)
    try:
        snapshots = wayback.list_snapshots_by_prefix(DETAIL_PREFIX)
    except Exception as e:
        print(f"  ERROR listing prefix: {e}")
        return set()
    ids = set()
    for entry in snapshots:
        m = ID_HREF_RE.search(entry["original"])
        if m:
            ids.add(m.group(1))
    print(f"[{SOURCE}] {len(ids)} distinct archived detail-page IDs found", flush=True)
    return ids


def scrape(limit: int = None, prefix_crawl: bool = True) -> None:
    conn = db.connect()
    session = requests.Session()

    best = discover_listing_best(conn, limit=limit)
    id_to_key = {}
    for key, e in best.items():
        m = ID_HREF_RE.search(e["href"])
        if m:
            id_to_key[m.group(1)] = key
    title_to_date = {title: date for (date, title) in best}

    extra_ids = set()
    if prefix_crawl:
        extra_ids = discover_prefix_ids() - set(id_to_key)

    stats = Stats(SOURCE)

    work = list(best.items())
    if limit:
        work = work[:limit]

    for (date, title), e in work:
        m = ID_HREF_RE.search(e["href"])
        url = DETAIL_URL_TMPL.format(m.group(1)) if m else e["href"]

        existing = stored_detail_id(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        parsed, confirmed = ({}, True) if not m else wayback.fetch_detail_snapshot(conn, session, url, parse_detail)

        if parsed.get("body"):
            if existing == "teaser":
                # No title=/date=: the listing page's values are better than
                # the detail page's, so only the body is upgraded.
                db.upgrade_release(conn, url, detail_id=parsed["detail_id"],
                                   body=parsed["body"], commit=False)
                stats.upgraded()
            else:
                db.store_release(conn, SOURCE, url, title=title, date=date,
                                 body=parsed["body"], detail_id=parsed["detail_id"], commit=False)
                stats.added()
            conn.commit()
            continue

        if existing == "teaser":
            stats.skipped()
            continue

        if not confirmed:
            stats.uncertain()
            continue

        if e["teaser"]:
            db.store_release(conn, SOURCE, url, title=title, date=date,
                             body=e["teaser"], detail_id="teaser")
            stats.teaser()
        else:
            stats.dead()

    extra_ids_list = sorted(extra_ids)
    if limit:
        extra_ids_list = extra_ids_list[:limit]

    for hexid in extra_ids_list:
        url = DETAIL_URL_TMPL.format(hexid)

        existing = stored_detail_id(conn, url)
        if existing is not None:
            stats.skipped()
            continue

        parsed, confirmed = wayback.fetch_detail_snapshot(conn, session, url, parse_detail)
        if not parsed.get("body") or not parsed.get("title"):
            if confirmed:
                stats.dead()
            else:
                stats.uncertain()
            continue

        date = title_to_date.get(parsed["title"], "")
        db.store_release(conn, SOURCE, url, title=parsed["title"], date=date,
                         body=parsed["body"], detail_id=parsed["detail_id"])
        stats.added()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Midiman/M-Audio UK 'News' section via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Cap listing captures sampled and prefix-crawl IDs processed (testing)")
    parser.add_argument("--no-prefix-crawl", dest="prefix_crawl", action="store_false", help="Skip the ID= prefix-crawl bonus discovery")
    args = parser.parse_args()
    scrape(limit=args.limit, prefix_crawl=args.prefix_crawl)
