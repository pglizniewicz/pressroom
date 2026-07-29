#!/usr/bin/env python3
"""One-off scrape of TerraTec's earliest press page (terratec.de, 1996-1997) -
a single hand-authored HTML page listing every release with anchor tags,
plus a short-lived English mirror covering just the earliest 5 entries.

Not a crawler like the other scrapers: these are two known-good Wayback URLs
given directly, so no timemap/sparkline lookup is involved.

Usage:
  python scrape_terratec_early.py
"""

import re

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

import db
import wayback

SOURCE = "terratec_early"

PAGES = [
    {
        "lang": "de",
        "base_url": "http://www.terratec.de/presse2.htm",
        "wayback_url": "https://web.archive.org/web/19980115084201id_/http://www.terratec.de/presse2.htm",
        "timestamp": "19980115084201",
        "date_marker": r"Presseinformation vom ([\d.]+):",
        "anchor_re": r'<A NAME="(p\d+)">',
        "anchor_fmt": "#{}",
    },
    {
        "lang": "en",
        "base_url": "http://www.terratec.de/e/presse.htm",
        "wayback_url": "https://web.archive.org/web/19961129051606id_/http://www.terratec.de:80/e/presse.htm",
        "timestamp": "19961129051606",
        "date_marker": r"Press-Release as of\s*([\d.]+):",
        "anchor_re": r'<A NAME="(\d+)">',
        "anchor_fmt": "#{}",
    },
]

def find_anchor_for(pos: int, anchors: list) -> str:
    best = None
    for apos, aname in anchors:
        if apos <= pos and pos - apos < 100:
            best = aname
    return best


def extract_entries(html: str, page: dict) -> list:
    anchors = [(m.start(), m.group(1)) for m in re.finditer(page["anchor_re"], html)]
    dates = [(m.start(), m.group(1)) for m in re.finditer(page["date_marker"], html)]

    entries = []
    for i, (pos, date_str) in enumerate(dates):
        chunk_end = dates[i + 1][0] if i + 1 < len(dates) else len(html)
        chunk_html = html[pos:chunk_end]
        body = BeautifulSoup(chunk_html, "html.parser").get_text(" ", strip=True)

        try:
            date = du.parse(date_str, dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            date = ""

        anchor = find_anchor_for(pos, anchors)
        url = page["base_url"] + (page["anchor_fmt"].format(anchor) if anchor else "")
        citation = page["wayback_url"].replace("id_", "") + (page["anchor_fmt"].format(anchor) if anchor else "")

        entries.append({
            "date_str": date_str,
            "date": date,
            "body": body,
            "url": url,
            "citation": citation,
            "lang": page["lang"],
        })
    return entries


def scrape() -> None:
    conn = db.connect()
    session = requests.Session()

    all_entries = []
    for page in PAGES:
        # Both pages are required: the cross-language dedup below compares the
        # German page against the English one, so a partial fetch can't be
        # salvaged - bail with a message rather than a traceback or, worse, an
        # IndexError further down.
        try:
            content = wayback.fetch_snapshot(conn, session, page["wayback_url"], timeout=20)
        except Exception as e:
            raise SystemExit(
                f"Could not fetch {page['wayback_url']}: {e}\n"
                "Both the German and English page are needed for the "
                "cross-language dedup - try again later."
            )
        entries = extract_entries(content.decode("cp1252", errors="replace"), page)
        print(f"[{page['lang']}] {len(entries)} entries found", flush=True)
        all_entries.append((page, entries))

    de_page, de_entries = all_entries[0]
    en_page, en_entries = all_entries[1]

    # The English page duplicates the tail of the German page (translated) -
    # skip the German copy of these, keep the English one. Match by
    # normalized ISO date, not raw date_str (formats differ: "27.09.1996"
    # on the German page vs "27.9.96" on the English page for the same day).
    english_dates = {e["date"] for e in en_entries}

    skipped_de = 0
    new_count = 0

    for e in de_entries:
        if e["date"] in english_dates:
            skipped_de += 1
            continue
        if db.store_release(conn, SOURCE, e["url"], date=e["date"], body=e["body"],
                            detail_id=de_page["timestamp"], commit=False):
            new_count += 1

    for e in en_entries:
        if db.store_release(conn, SOURCE, e["url"], date=e["date"], body=e["body"],
                            detail_id=en_page["timestamp"], commit=False):
            new_count += 1

    conn.commit()
    total = db.source_total(conn, SOURCE)
    print(f"\nInserted {new_count} rows ({skipped_de} German duplicates of English entries skipped). Total: {total}")
    conn.close()


if __name__ == "__main__":
    scrape()
