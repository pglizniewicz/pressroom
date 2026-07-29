#!/usr/bin/env python3
"""Scraper for Midiman/M-Audio's 2001-2003 "pressdb.php" era
(midiman.net/news/pressdb.php and midiman.com/news/pressdb.php) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

Different system/era from the earlier scrape_midiman.py (which covers the
2001 static GoLive pages under .../html/press/*.htm) - pressdb.php is a PHP
script that dumps the ENTIRE press-release history as one long page, one
<table width="780" ...> block per release (date + linked title + one-line
teaser). There is no per-release static page on this site: the linked title
almost always points to an external PDF (which this repo doesn't extract
text from - no PDF parsing exists anywhere here) and occasionally a
/news/php/*.php page (not fetched by this script either - a future backfill
script could add full-text extraction for that handful of entries). So the
`body` stored here is only the short listing teaser, not the full release.

Since every capture is a full re-dump of everything published up to that
date, `wayback.list_all_captures` is used to sample every historical
capture of the bare pressdb.php URL (no query-string addressing exists on
this script - confirmed via CDX). Entries are deduped by (title, date)
rather than by their resolved target URL: the site's own template changed
over the years, retargeting the same release's title link from its own
presstemp.php detail page (early captures) straight to the PDF (later
captures) - deduping by URL alone would store the same release twice. When
both a detail-page URL and a PDF URL are seen for the same (title, date),
the detail-page URL wins (it's a potential future full-text source; a PDF
never will be here), then the longest teaser - same `best`-dict idiom as
scrape_terratec_new.py, just keyed differently.

Source tags are per-domain (midiman_net_pressdb / midiman_com_pressdb), same
policy as scrape_midiman.py and consistent with the terratec_pressde /
terratec_pressen precedent (distinct source per distinct system+domain).
The two domains' listings overlap heavily (most releases were cross-posted)
- not deduped across domains, by design, same as everywhere else in this repo.

Known quirk: one entry (09 Jul 2003, midiman.net) has a copy-paste bug in its
href - an absolute URL pasted into what should be a relative path, producing
a doubled/invalid link. Fixed by taking the last http(s):// occurrence in
the href, but note the underlying target was never actually archived anyway.

Usage:
  python scrape_midiman_pressdb.py             # everything
  python scrape_midiman_pressdb.py --limit 3   # only sample first 3 captures per domain (testing)
"""

import argparse
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from db import already_stored
import db
import wayback


DOMAINS = {
    "midiman_net_pressdb": "http://www.midiman.net/news/pressdb.php",
    "midiman_com_pressdb": "http://www.midiman.com/news/pressdb.php",
}

ABS_URL_RE = re.compile(r"https?://")


def extract_entries(html: bytes, base_url: str, timestamp: str = None) -> list:
    soup = BeautifulSoup(html, "html.parser", from_encoding="cp1252")
    entries = []

    for bold_td in soup.find_all("td", class_="normal-bold"):
        a = bold_td.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue

        href = a["href"]
        matches = list(ABS_URL_RE.finditer(href))
        if matches and matches[-1].start() > 0:
            href = href[matches[-1].start():]
        url = urljoin(base_url, href)

        tr = bold_td.find_parent("tr")
        date_td = tr.find("td", class_="normal-italic") if tr else None
        if date_td is None and tr:
            # Older template variant: date lives in its own td.normal-bold
            # (same class as the title cell) instead of a td.normal-italic.
            for candidate in tr.find_all("td", class_="normal-bold"):
                if candidate is bold_td:
                    continue
                if re.match(r"^\d{1,2}\s+\w+\s+\d{4}$", candidate.get_text(strip=True)):
                    date_td = candidate
                    break
        date_str = date_td.get_text(strip=True) if date_td else ""
        date = ""
        if date_str:
            try:
                date = du.parse(date_str).strftime("%Y-%m-%d")
            except Exception:
                date = ""

        table = bold_td.find_parent("table")
        body = ""
        if table:
            body_td = table.find("td", class_="normal")
            if body_td:
                body = body_td.get_text(" ", strip=True)

        entries.append({"title": title, "date": date, "url": url, "body": body, "detail_id": timestamp})

    return entries


def scrape_domain(source: str, listing_url: str, limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    print(f"[{source}] Listing historical captures of {listing_url}", flush=True)
    entries = wayback.sample_all_captures(conn, session, listing_url, extract_entries, limit=limit)

    # Keyed by (title, date) rather than url: the same release's title link
    # was retargeted over the years (early captures point at the site's own
    # presstemp.php detail page, later captures link the PDF directly) - one
    # release would otherwise show up as two rows. Prefer whichever URL is
    # NOT a bare PDF (an HTML detail page can potentially be fetched for full
    # text later; a PDF link is a dead end for this repo, which does no PDF
    # extraction), then the longest teaser seen.
    def rank(entry):
        return (0 if entry["url"].lower().endswith(".pdf") else 1, len(entry["body"]))

    best = {}  # (title, date) -> {url, body, detail_id}
    for e in entries:
        key = (e["title"], e["date"])
        cur = best.get(key)
        if cur is None or rank(e) > rank(cur):
            best[key] = {"url": e["url"], "body": e["body"], "detail_id": e["detail_id"]}

    print(f"\n[{source}] {len(best)} distinct release entries found across all captures", flush=True)

    new_count = 0
    skip_count = 0
    for (title, date), e in best.items():
        url = e["url"]
        if already_stored(conn, url):
            skip_count += 1
            continue
        if db.store_release(conn, source, url, title=title, date=date,
                            body=e["body"], detail_id=e["detail_id"], commit=False):
            new_count += 1
    conn.commit()

    total = db.source_total(conn, source)
    print(f"[{source}] Added {new_count} new, skipped {skip_count} existing. Total [{source}] in DB: {total}")
    conn.close()


def scrape(limit: int = None) -> None:
    for source, listing_url in DOMAINS.items():
        scrape_domain(source, listing_url, limit=limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Midiman/M-Audio's pressdb.php press archive via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only sample the first N historical captures per domain (testing)")
    args = parser.parse_args()
    scrape(limit=args.limit)
