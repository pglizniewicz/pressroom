#!/usr/bin/env python3
"""Scraper for M-Audio's m-audio.com "/news" blog -> unified pressroom.db,
sourced entirely from Wayback Machine snapshots.

Third distinct CMS generation for this company after the 2001-2003 PHP site
(scrape_midiman.py/scrape_midiman_pressdb.py) and the 2004-2013 "Hydra Media
Labs" do=media.media_pr system (scrape_midiman_media_pr.py, source tag
maudio_com_media_pr) - this is inMusic Brands' Foundation-CSS/jQuery blog
built for m-audio.com after acquiring the brand (~2014-launch). Confirmed via
CDX: exactly 25 articles published Oct 2014 - Oct 2019, then the site went
completely dormant - captures from 2020 onward (including a 2025 one) are
the identical frozen 25-article backlog re-skinned with newer chrome (mobile
nav, TypeKit fonts). One scraper, one source, ~25 rows expected total, no
growth expected on reruns.

Listing is paginated, 5 items/page, exactly 5 pages exist (confirmed via
CDX: /news, /news/P5, /news/P10, /news/P15, /news/P20 - /news/P25 has never
been captured). Since the corpus is frozen, a single
wayback.get_latest_working_snapshot() per pagination URL is sufficient to
recover the complete, final 25-article set - no historical-capture sampling
(unlike scrape_midiman_media_pr.py's discover_listing_best) and no
prefix-crawl bonus discovery (unlike scrape_midiman_news.py) are needed;
the whole corpus is exhaustively enumerable from just these 5 fixed URLs.

No structured date field anywhere on the site - dates are embedded as plain
prose in the body ("City, ST, USA - Month D, YYYY." or "City, ST, USA
(Month D, YYYY)-", dash style varies by article/era) and recovered via
DATE_RE, dash-agnostic, tested against both formats.

Same robust two-tier fetch/write pattern as scrape_midiman_news.py:
fetch_detail() distinguishes a network hiccup (confirmed=False - never
write anything, leave the row open to a full retry later) from a
confirmed dead end (confirmed=True - safe to permanently record a
teaser-only fallback or nothing). stored_detail_id() lets the main loop
tell a fully-recovered row apart from a still-upgradeable teaser row.

IMPORTANT: listing/detail HTML must be parsed from raw bytes (r.content),
never r.text. Confirmed empirically: this site's later-era Wayback captures
replay via the id_ raw-content modifier with a bare "Content-Type: text/html"
(no charset param), which makes `requests` guess Latin-1 and mangle the
site's actual UTF-8 bytes (e.g. "M-AUDIO(R)" -> "M-AUDIOÂ®"). BeautifulSoup's
own encoding sniffing on raw bytes gets this right; requests' r.text does not.

Usage:
  python scrape_maudio_news.py              # everything (~25 articles)
  python scrape_maudio_news.py --limit 5    # cap articles processed (testing)
"""

import argparse
import re
import sqlite3
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from db import stored_detail_id
from dates import iso_date
from encoding import decode_html
import db
import richtext
from progress import Stats
import wayback

SOURCE = "maudio_com_news"

LISTING_PAGES = [
    "http://m-audio.com/news",
    "http://m-audio.com/news/P5",
    "http://m-audio.com/news/P10",
    "http://m-audio.com/news/P15",
    "http://m-audio.com/news/P20",
]
DETAIL_URL_TMPL = "http://m-audio.com/news/articles/{}"
SLUG_RE = re.compile(r"/news/articles/([\w-]+)")

MONTHS = (r"(?:January|February|March|April|May|June|July|August|"
          r"September|October|November|December)")
DATE_RE = re.compile(rf"{MONTHS} \d{{1,2}},\s*\d{{4}}")


def _extract_date(text: str) -> str:
    m = DATE_RE.search(text)
    return iso_date(m.group(0)) if m else ""


def parse_listing_page(content: bytes, base_url: str) -> list:
    # These captures are valid utf-8, so decode_html is currently a no-op here
    # - it's used anyway so a single stray cp1252 byte can't silently hand the
    # whole page to chardet. See encoding.py.
    soup = BeautifulSoup(decode_html(content), "html.parser")
    entries = []
    for li in soup.select("ul.large-block-grid-1 li"):
        a = li.select_one("p strong a.link")
        if not a or not a.get("href"):
            continue
        m = SLUG_RE.search(urljoin(base_url, a["href"]))
        if not m:
            continue
        slug = m.group(1)
        title = a.get_text(" ", strip=True)

        p = a.find_parent("p")
        teaser = teaser_html = ""
        if p:
            strong = p.find("strong")
            if strong:
                strong.decompose()
            teaser, teaser_html = richtext.extract(p)

        entries.append({
            "slug": slug,
            "url": DETAIL_URL_TMPL.format(slug),
            "title": title,
            "teaser": teaser,
            "teaser_html": teaser_html,
            "date": _extract_date(teaser),
        })
    return entries


def parse_detail(content: bytes) -> dict:
    soup = BeautifulSoup(decode_html(content), "html.parser")
    container = soup.select_one("div.theme-page")
    if not container:
        return {}
    h5 = container.find("h5")
    title = h5.get_text(" ", strip=True) if h5 else ""
    if h5:
        h5.decompose()
    body, body_html = richtext.extract(container)
    if not body:
        return {}
    return {"title": title, "body": body, "body_html": body_html,
            "date": _extract_date(body)}


def discover_listing(conn: sqlite3.Connection) -> list:
    session = requests.Session()
    by_slug = {}
    for page_url in LISTING_PAGES:
        print(f"[{SOURCE}] Fetching {page_url}", flush=True)
        try:
            found = wayback.get_latest_working_snapshot(page_url)
            if not found:
                print("  no working snapshot found, skipping this page")
                continue
            snapshot_url, _ts = found
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            entries = parse_listing_page(content, page_url)
        except Exception as e:
            print(f"  ERROR: {e} (skipping this page for this run)")
            continue
        for e in entries:
            by_slug[e["slug"]] = e
        print(f"  {len(entries)} entries", flush=True)
    return list(by_slug.values())


def scrape(limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    entries = discover_listing(conn)
    print(f"[{SOURCE}] {len(entries)} distinct articles found across all listing pages", flush=True)
    if limit:
        entries = entries[:limit]

    stats = Stats(SOURCE)

    for e in entries:
        url = e["url"]
        existing = stored_detail_id(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        parsed, confirmed = wayback.fetch_detail_snapshot(conn, session, url, parse_detail)

        if parsed.get("body"):
            title = parsed.get("title") or e["title"]
            date = parsed.get("date") or e["date"]
            if existing == "teaser":
                db.upgrade_release(conn, url, detail_id=parsed["detail_id"], title=title,
                                   date=date, body=parsed["body"],
                                   body_html=parsed["body_html"], commit=False)
                stats.upgraded()
            else:
                db.store_release(conn, SOURCE, url, title=title, date=date,
                                 body=parsed["body"], body_html=parsed["body_html"],
                                 detail_id=parsed["detail_id"], commit=False)
                stats.added()
            conn.commit()
            continue

        # Ordered before the teaser check on purpose: a failed probe is not a
        # verdict, so an already-stored teaser must be reported `uncertain`
        # (a rerun will retry it) rather than `skipped` ("nothing to do").
        if not confirmed:
            stats.uncertain()
            continue

        if existing == "teaser":
            stats.skipped()
            continue

        if e["teaser"]:
            db.store_release(conn, SOURCE, url, title=e["title"], date=e["date"],
                             body=e["teaser"], body_html=e["teaser_html"] or None,
                             detail_id="teaser")
            stats.teaser()
        else:
            stats.dead()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape M-Audio's m-audio.com/news blog via the Wayback Machine")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of articles processed (testing)")
    args = parser.parse_args()
    scrape(limit=args.limit)
