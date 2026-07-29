#!/usr/bin/env python3
"""Backfill from 5 archived press-release index pages that link out to
individual articles:
  - terratec.de/presse/{pressearchiv,pressemit}.htm  (German office)
  - terratec.net/press/{pressarchive,pressreleases,pressreleases}.htm (newer office)

The .net links mostly corroborate what source="terratec" already has (only
genuine gaps get added there). The .de links are entirely new territory -
same static-page template family, stored under a new source="terratec_de".

Cross-language duplicates between terratec_de and existing English content
are intentionally NOT auto-deduped here (unreliable to match automatically
across languages) - everything gets stored with correct provenance, and the
"prefer the newer office's English version" call is made by hand during
terratec.json curation, same as every prior cross-source dedup in this
project.

Usage:
  python backfill_terratec_de_and_net_gaps.py
"""

import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from fetch import SLEEP
from db import already_stored
import db
from scrape_terratec import parse_snapshot as parse_net_snapshot
import wayback


INDEX_PAGES = [
    ("http://www.terratec.de/presse/", "terratec_de",
     "https://web.archive.org/web/20030303193557id_/http://www.terratec.de/presse/pressearchiv.htm"),
    ("http://www.terratec.de/presse/", "terratec_de",
     "https://web.archive.org/web/20030227043415id_/http://www.terratec.de/presse/pressemit.htm"),
    ("http://www.terratec.net/press/", "terratec",
     "https://web.archive.org/web/20030222201120id_/http://www.terratec.net/press/pressarchive.htm"),
    ("http://www.terratec.net/press/", "terratec",
     "https://web.archive.org/web/20030206053420id_/http://www.terratec.net/press/pressreleases.htm"),
    ("http://www.terratec.net/press/", "terratec",
     "https://web.archive.org/web/20030405064946id_/http://www.terratec.net/press/pressreleases.htm"),
]

# German-domain pages say "TerraTec PresseInfo vom DD.MM.YYYY", in addition to
# the "Presseinformation vom" style already seen on the very-early static page.
DATE_RE_DE = re.compile(r"Presse(?:Info|information) vom\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE)


def extract_links(html: str, base_url: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for row in soup.select("tr"):
        a = row.find("a", href=True)
        if not a or not a["href"].lower().startswith("pressemit/"):
            continue
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        try:
            date = du.parse(date_str, dayfirst=True).strftime("%Y-%m-%d") if date_str else ""
        except Exception:
            date = ""
        entries.append({
            "title": a.get_text(" ", strip=True),
            "date": date,
            "url": base_url + a["href"],
        })
    return entries


def parse_de_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    date = ""
    m = DATE_RE_DE.search(text)
    if m:
        try:
            date = du.parse(m.group(1), dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            date = ""

    title = ""
    bold_tags = soup.find_all(["b", "strong"])
    for i, tag in enumerate(bold_tags):
        t = tag.get_text(strip=True).lower()
        if "presseinfo" in t or "press release" in t:
            if i + 1 < len(bold_tags):
                title = bold_tags[i + 1].get_text(strip=True)
            break

    return {"title": title, "date": date, "body": text}


def already_have_net_filenames(conn: sqlite3.Connection) -> set:
    return {url.rsplit("/", 1)[-1].lower() for url in db.source_urls(conn, "terratec")}


def backfill() -> None:
    conn = db.connect()
    session = requests.Session()

    all_entries = {}  # url -> entry dict (title, date, url, source)
    for base_url, source, wayback_url in INDEX_PAGES:
        print(f"Fetching {wayback_url}", flush=True)
        content = wayback.fetch_snapshot(conn, session, wayback_url, timeout=20)
        for e in extract_links(content, base_url):
            e["source"] = source
            all_entries.setdefault(e["url"], e)

    net_have = already_have_net_filenames(conn)
    candidates = []
    for e in all_entries.values():
        if e["source"] == "terratec" and e["url"].rsplit("/", 1)[-1].lower() in net_have:
            continue
        candidates.append(e)

    print(f"\n{len(candidates)} candidate articles to fetch "
          f"({sum(1 for c in candidates if c['source']=='terratec_de')} terratec_de, "
          f"{sum(1 for c in candidates if c['source']=='terratec')} terratec)", flush=True)

    new_count = 0
    stub_count = 0

    for e in candidates:
        if already_stored(conn, e["url"]):
            print(".", end="", flush=True)
            continue

        parse_fn = parse_net_snapshot if e["source"] == "terratec" else parse_de_snapshot

        try:
            found = wayback.get_latest_working_snapshot(e["url"])
        except Exception as err:
            print(f"\n  ERROR probing snapshots for {e['url']}: {err}")
            time.sleep(SLEEP * 2)
            continue

        if not found:
            db.store_release(conn, e["source"], e["url"], title=e["title"],
                             date=e["date"], detail_id="stub")
            stub_count += 1
            print("s", end="", flush=True)
            continue

        snapshot_url, timestamp = found
        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_fn(content)
        except Exception as err:
            print(f"\n  ERROR fetching {snapshot_url}: {err}")
            continue

        title = parsed["title"] or e["title"]
        date = parsed["date"] or e["date"]
        db.store_release(conn, e["source"], e["url"], title=title, date=date,
                         body=parsed["body"], detail_id=timestamp)
        new_count += 1
        print("+", end="", flush=True)

    for source in ("terratec", "terratec_de"):
        total = db.source_total(conn, source)
        print(f"\n[{source}] total in DB: {total}")

    print(f"\nAdded {new_count} full articles, {stub_count} title-only stubs.")
    conn.close()


if __name__ == "__main__":
    backfill()
