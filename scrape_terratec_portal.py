#!/usr/bin/env python3
"""Scraper for TerraTec's "Presse @ TerraTec" PHP-Nuke portals ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots since
neither site exists any more.

The same PHP-Nuke install ran twice, once per language:
  - pressen.terratec.net (English) -> source terratec_pressen
  - pressde.terratec.net (German)  -> source terratec_pressde
Identical markup, identical URL scheme, so one parser covers both and this
script does both portals per run - the same shape as scrape_midiman_pressdb.py
and scrape_midiman_media_pr.py, which likewise cover several instances of one
system. (These were two near-identical files until the shared parser drifted:
the German copy grew a fix the English copy never got, see END_MARKERS.)

Content likely overlaps with the older static terratec.net/press/pressemit/
archive, but the URL schemes are unrelated so they can't share a dedup key.
Kept as distinct sources, same as creative/creative_gnw.

Usage:
  python scrape_terratec_portal.py             # both portals, every archived article
  python scrape_terratec_portal.py --limit 5   # only the first 5 per portal (testing)
"""

import argparse
import re
import time

import requests
from bs4 import BeautifulSoup

from fetch import SLEEP
from db import already_stored
from dates import iso_date
import db
import richtext
from progress import Stats
import wayback

PORTALS = {
    "terratec_pressen": "http://pressen.terratec.net:80/",
    "terratec_pressde": "http://pressde.terratec.net:80/",
}

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_TAG_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+?)\s*::\s*Press")
TITLE_TAG_MONTH_RE = re.compile(r"([A-Za-z]+\s+\d{4})\s*-\s*(.+?)\s*::\s*Press")

# The sidebar box after the article body isn't labeled consistently across
# captures ("Links!" in German templates, "Related links" seen on the English
# portal's markup bleeding through some snapshots) - cut at whichever comes
# first. Both portals need both markers, which is exactly what the two
# separate copies of this scraper used to get wrong.
END_MARKERS = ["Links!", "Related links"]
END_MARKER_RE = re.compile("|".join(re.escape(m) for m in END_MARKERS))


def list_articles(prefix: str) -> list:
    """Dedup by sid: mode/order/thold don't affect content, first-seen wins."""
    by_sid = {}
    for entry in wayback.list_snapshots_or_exit(prefix):
        url = entry["original"]
        if "name=News" not in url or "file=article" not in url:
            continue
        m = SID_RE.search(url)
        if not m:
            continue
        sid = int(m.group(1))
        by_sid.setdefault(sid, url)
    return [by_sid[sid] for sid in sorted(by_sid)]


def _cut_at_first_marker(text: str) -> str:
    cut = len(text)
    for marker in END_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()


def article_body(soup, heading: str) -> tuple:
    """(body, body_html) for one portal article, from the DOM.

    The same three cuts the text surgery above makes, done on elements instead
    of on a string:

      container  the <td> carrying the most text. Calibrated over all 186
                 cached portal captures: 100% land within 0.85-1.25 of the
                 previously stored body, against 150/186 for the obvious
                 `td[valign="top"][width="85%"]` selector - the attributes are
                 not on every capture, the size is.
      heading    the "{date} - {title}" line, which the container includes and
                 the stored body does not (median coverage was 1.05, and this
                 plus the link block is the 5%).
      tail       everything from the first END_MARKERS element onward.

    Returns ("", None) when there is no container, so the caller can fall back
    to the text path rather than store an empty body.
    """
    td = richtext.densest(soup, "td")
    if td is None:
        return "", None

    work = BeautifulSoup(str(td), "html.parser")
    norm = lambda t: " ".join(t.split())
    target = norm(heading)

    for tag in work.find_all(True):
        if tag.find(True) is None and norm(tag.get_text(" ", strip=True)) == target:
            tag.decompose()
            break

    richtext.cut_from(work, END_MARKER_RE)
    return richtext.extract(work)


def parse_snapshot(content: bytes) -> dict:
    # cp1252 stated, never sniffed - see scrape_terratec.py's parse_snapshot for
    # why (48 rows across pressde/pressen were stored with the cp1252
    # punctuation range as C1 control characters until repair_encoding.py).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")

    # The heading anchor's CSS class isn't present in every capture (some
    # crawls render it as plain bold text instead) - the <title> tag is
    # present and consistently formatted in all captures, so use that.
    title_full = soup.title.get_text(strip=True) if soup.title else ""
    m = TITLE_TAG_RE.match(title_full)
    date_fmt = "%Y-%m-%d"
    if not m:
        # A handful of articles only carry month/year precision, e.g. "June 2007 - Title"
        m = TITLE_TAG_MONTH_RE.match(title_full)
        date_fmt = "%Y-%m"

    title = ""
    date = ""
    body = ""
    body_html = None
    if m:
        date_str, title = m.group(1), m.group(2).strip()
        date = iso_date(date_str, dayfirst=True, fmt=date_fmt)

        heading = f"{date_str} - {title}"
        body, body_html = article_body(soup, heading)
        if not body:
            # No container in this capture - keep the old text surgery rather
            # than store nothing.
            text = soup.get_text(" ", strip=True)
            parts = text.split(heading)
            body = _cut_at_first_marker(parts[-1]) if len(parts) > 1 else text
            body_html = None

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def scrape_portal(source: str, prefix: str, limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    print(f"[{source}] Listing archived articles under {prefix}", flush=True)
    urls = list_articles(prefix)
    if limit:
        urls = urls[:limit]
    print(f"[{source}] {len(urls)} candidate articles", flush=True)

    stats = Stats(source, total=len(urls))

    for url in urls:
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
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if db.store_release(conn, source, url, title=parsed["title"],
                            date=parsed["date"], body=parsed["body"],
                            body_html=parsed["body_html"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    conn.close()


def scrape(limit: int = None) -> None:
    for source, prefix in PORTALS.items():
        scrape_portal(source, prefix, limit=limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape TerraTec's Presse @ TerraTec portals (English + German) via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N candidate articles per portal")
    args = parser.parse_args()
    scrape(limit=args.limit)
