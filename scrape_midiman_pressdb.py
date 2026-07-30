#!/usr/bin/env python3
"""Scraper for Midiman/M-Audio's 2001-2003 "pressdb.php" era
(midiman.net/news/pressdb.php and midiman.com/news/pressdb.php) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

Different system/era from the earlier scrape_midiman.py (which covers the
2001 static GoLive pages under .../html/press/*.htm) - pressdb.php is a PHP
script that dumps the ENTIRE press-release history as one long page, one
<table width="780" ...> block per release (date + linked title + one-line
teaser). The linked title points either at an external PDF or at one of the
site's own HTML detail pages (presstemp.php?ID=... or /news/php/<name>.php).
Both are followed for full text, from different places: the PDFs by
backfill_midiman_attachments.py, the HTML detail pages by this script. Only
when neither yields anything does a row keep the short listing teaser as its
body.

That split is recent. This scraper originally stored teasers and nothing else,
on the reasoning that a PDF was unextractable and the HTML pages could be
followed "later" - and later never came, leaving 40 rows sitting at ~150
characters each with their full text archived and reachable the whole time.

Since every capture is a full re-dump of everything published up to that
date, `wayback.list_all_captures` is used to sample every historical
capture of the bare pressdb.php URL (no query-string addressing exists on
this script - confirmed via CDX). Entries are deduped by (title, date)
rather than by their resolved target URL: the site's own template changed
over the years, retargeting the same release's title link from its own
presstemp.php detail page (early captures) straight to the PDF (later
captures) - deduping by URL alone would store the same release twice. When
both a detail-page URL and a PDF URL are seen for the same (title, date), the
detail-page URL wins: both are recoverable, but the HTML page gives clean text
where whole-document PDF extraction interleaves the running header/footer
mid-body. Then the longest teaser - same `best`-dict idiom as
scrape_terratec_new.py, just keyed differently.

Rerunnable against rows it already stored: a row is retried whenever its
stored body is still teaser-length, since detail_id cannot distinguish those
here (it holds the *listing* capture's timestamp).

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

from dates import iso_date
from encoding import decode_html
import db
from progress import Stats
import wayback


DOMAINS = {
    "midiman_net_pressdb": "http://www.midiman.net/news/pressdb.php",
    "midiman_com_pressdb": "http://www.midiman.com/news/pressdb.php",
}

ABS_URL_RE = re.compile(r"https?://")

# A stored body longer than this has had its real text recovered; anything
# shorter is still just the listing blurb, so it is worth another attempt. The
# longest teaser these listings produce is 864 characters.
#
# Deliberately duplicated from backfill_midiman_attachments.py rather than
# imported: a scraper importing a constant from a backfill would invert the
# dependency. Same corpus, same rationale - change both together.
RECOVERED_LENGTH = 900

# Attachments are somebody else's job (backfill_midiman_attachments.py); this
# scraper only follows its own HTML detail pages.
ATTACHMENT_EXTS = (".pdf", ".doc")


def is_html_detail(url: str) -> bool:
    return not url.lower().split("?", 1)[0].endswith(ATTACHMENT_EXTS)


def parse_detail(html: bytes) -> dict:
    """Full text of a presstemp.php / news/php/<name>.php detail page.

    Takes the whole document's text rather than hunting for a container: these
    pages are bare templated or Word-exported documents with no site navigation
    at all (measured: 12-70 characters of chrome against 2.2-7.8 KB of release),
    and the Word ones vary between MsoBodyText, MsoBlockText and span.normal
    wrappers, so any single selector would miss some. Same approach as
    scrape_midiman.py's 2001-era GoLive pages.

    Only the body is returned. The listing already gave a better title and a
    date, and the detail page carries no date of its own in a parseable place.
    """
    soup = BeautifulSoup(decode_html(html), "html.parser")
    for tag in soup.find_all(["title", "script", "style"]):
        tag.decompose()
    return {"body": " ".join(soup.get_text(" ", strip=True).split())}


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
            date = iso_date(date_str)

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

    # Keyed by (title, date) rather than url: the same release's title link was
    # retargeted over the years (early captures point at the site's own
    # presstemp.php detail page, later captures link the PDF directly) - one
    # release would otherwise show up as two rows. Prefer the HTML detail page
    # over a bare PDF: both are recoverable now, but the HTML page yields clean
    # text, while whole-document PDF extraction interleaves the running
    # header/footer mid-body (see backfill_midiman_attachments.py). Then prefer
    # the longest teaser seen.
    def rank(entry):
        return (0 if entry["url"].lower().endswith(".pdf") else 1, len(entry["body"]))

    best = {}  # (title, date) -> {url, body, detail_id}
    for e in entries:
        key = (e["title"], e["date"])
        cur = best.get(key)
        if cur is None or rank(e) > rank(cur):
            best[key] = {"url": e["url"], "body": e["body"], "detail_id": e["detail_id"]}

    print(f"\n[{source}] {len(best)} distinct release entries found across all captures", flush=True)

    stats = Stats(source, total=len(best))
    for (title, date), e in best.items():
        url = e["url"]
        stored_len = db.stored_body_length(conn, url)
        if stored_len is not None and stored_len >= RECOVERED_LENGTH:
            stats.skipped()
            continue

        # Only HTML detail pages are followed here. A .pdf/.doc URL is an
        # attachment and belongs to backfill_midiman_attachments.py, which
        # already knows how to extract it.
        parsed, confirmed = ({}, True)
        if is_html_detail(url):
            parsed, confirmed = wayback.fetch_detail_snapshot(conn, session, url, parse_detail)
        body = parsed.get("body") or ""

        if stored_len is None:
            if not body and not confirmed:
                stats.uncertain()
                continue
            # detail_id records where the body actually came from: the detail
            # capture when we recovered one, otherwise the listing capture the
            # teaser was read from.
            db.store_release(conn, source, url, title=title, date=date,
                             body=body or e["body"],
                             detail_id=parsed.get("detail_id") or e["detail_id"],
                             commit=False)
            stats.added() if body else stats.teaser()
        elif body and len(body) > stored_len:
            db.upgrade_release(conn, url, body=body,
                               detail_id=parsed.get("detail_id"), commit=False)
            stats.upgraded()
        elif not confirmed:
            stats.uncertain()
        else:
            stats.skipped()
    conn.commit()

    stats.summary(conn)
    conn.close()


def scrape(limit: int = None) -> None:
    for source, listing_url in DOMAINS.items():
        scrape_domain(source, listing_url, limit=limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Midiman/M-Audio's pressdb.php press archive via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Only sample the first N historical captures per domain (testing)")
    args = parser.parse_args()
    scrape(limit=args.limit)
