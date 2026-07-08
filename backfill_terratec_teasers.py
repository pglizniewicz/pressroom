#!/usr/bin/env python3
"""Backfill terratec_pressen/terratec_pressde from the news module's own
yearly category-listing pages (file=index&catid=N&allstories=1), which show
every article that year as a title + short teaser paragraph - including
sids that never surfaced in the site-wide timemap prefix search at all.

For each teaser sid not already in pressroom.db, tries to recover the full
article (via the standard article URL, then print.php) before falling back
to storing the teaser paragraph itself as the body.

Usage:
  python backfill_terratec_teasers.py
"""

import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from common import HEADERS, SLEEP, init_db
import wayback

DB_PATH = Path(__file__).parent / "pressroom.db"

# (domain prefix, source, wayback snapshot URL) - URLs and timestamps as given,
# already resolved to a working capture; id_ is inserted before fetching.
CATEGORY_PAGES = [
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031115085312/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=13&topic=&allstories=1&menu=300"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031115084858/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=14&topic=&allstories=1&menu=304"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031213100854/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&amp"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20041010061159/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&amp"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730011217/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730010823/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=20&topic=&allstories=1"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730010351/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20031001235452/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&menu=2"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20041010063929/http://pressen.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=16&topic=&allstories=1&amp"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070808232502/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&menu=2&menu=307"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070808232455/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2&menu=309"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070630063657/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1&menu=2"),
]

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+)")


def with_id_modifier(wayback_url: str) -> str:
    return re.sub(r"(/web/\d+)/", r"\1id_/", wayback_url, count=1)


def stored_sids(conn: sqlite3.Connection, source: str) -> set:
    sids = set()
    for (url,) in conn.execute("SELECT url FROM releases WHERE source = ?", (source,)):
        m = SID_RE.search(url)
        if m:
            sids.add(int(m.group(1)))
    return sids


def extract_teasers(html: str) -> dict:
    """Return {sid: (date, title, teaser_text)} for every article on this page."""
    soup = BeautifulSoup(html, "html.parser")
    teasers = {}

    for a in soup.select("a.pn-title"):
        href = a.get("href", "")
        m_sid = SID_RE.search(href)
        if not m_sid:
            continue
        sid = int(m_sid.group(1))

        m_title = TITLE_RE.match(a.get_text(strip=True))
        if not m_title:
            continue
        date_str, title = m_title.group(1), m_title.group(2).strip()
        try:
            date = du.parse(date_str, dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            date = ""

        title_tr = a.find_parent("tr")
        content_tr = title_tr.find_next_sibling("tr") if title_tr else None
        teaser = ""
        if content_tr:
            content_html = str(content_tr)
            idx = content_html.find('<span class="note">')
            if idx != -1:
                content_html = content_html[:idx]
            teaser = BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)

        teasers[sid] = (date, title, teaser)

    return teasers


def recover_full_content(prefix: str, sid: str, session: requests.Session):
    """Try the standard article URL, then print.php. Returns parsed dict or None."""
    for url_tmpl, parse_fn in [
        (f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}", parse_article_snapshot),
        (f"{prefix}print.php?sid={sid}", parse_print_snapshot),
    ]:
        try:
            found = wayback.get_latest_working_snapshot(url_tmpl)
        except Exception:
            found = None
        if not found:
            continue
        snapshot_url, timestamp = found
        try:
            r = session.get(snapshot_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            parsed = parse_fn(r.text)
        except Exception:
            continue
        time.sleep(SLEEP)
        if parsed.get("title"):
            return timestamp, parsed
    return None


TITLE_TAG_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+?)\s*::\s*Press")


def parse_article_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_full = soup.title.get_text(strip=True) if soup.title else ""
    m = TITLE_TAG_RE.match(title_full)
    if not m:
        return {"title": "", "date": "", "body": ""}
    date_str, title = m.group(1), m.group(2).strip()
    try:
        date = du.parse(date_str, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        date = ""
    heading = f"{date_str} - {title}"
    text = soup.get_text(" ", strip=True)
    parts = text.split(heading)
    body = parts[-1].split("Related links")[0].split("Links!")[0].strip() if len(parts) > 1 else text
    return {"title": title, "date": date, "body": body}


def parse_print_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.select_one("font.print-title")
    if not title_tag:
        return {"title": "", "date": "", "body": ""}
    m = TITLE_RE.match(title_tag.get_text(strip=True))
    if not m:
        return {"title": "", "date": "", "body": ""}
    try:
        date = du.parse(m.group(1), dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        date = ""
    title = m.group(2).strip()
    body_tag = soup.select_one("font.print-normal")
    body = body_tag.get_text(" ", strip=True) if body_tag else ""
    return {"title": title, "date": date, "body": body}


def backfill() -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    session = requests.Session()

    all_teasers = {}  # source -> {sid: (date, title, teaser)}
    for prefix, source, url in CATEGORY_PAGES:
        print(f"Fetching {url}", flush=True)
        try:
            r = session.get(with_id_modifier(url), headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"  ERROR fetching category page: {e}")
            continue
        time.sleep(SLEEP)
        teasers = extract_teasers(r.text)
        print(f"  {len(teasers)} teasers found")
        all_teasers.setdefault((prefix, source), {}).update(teasers)

    full_recovered = 0
    teaser_only = 0
    dead = 0

    for (prefix, source), teasers in all_teasers.items():
        have = stored_sids(conn, source)
        new_sids = sorted(set(teasers) - have)
        print(f"\n[{source}] {len(new_sids)} teaser sids not already in the DB", flush=True)

        for sid in new_sids:
            article_url = f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}"
            teaser_date, teaser_title, teaser_text = teasers[sid]

            recovered = recover_full_content(prefix, str(sid), session)
            if recovered:
                timestamp, parsed = recovered
                conn.execute(
                    "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
                    (source, timestamp, parsed["title"], parsed["date"], article_url, parsed["body"]),
                )
                full_recovered += 1
                print("+", end="", flush=True)
            elif teaser_text:
                conn.execute(
                    "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
                    (source, "teaser", teaser_title, teaser_date, article_url, teaser_text),
                )
                teaser_only += 1
                print("t", end="", flush=True)
            else:
                dead += 1
                print("x", end="", flush=True)
            conn.commit()

    print(
        f"\n\nDone. {full_recovered} recovered in full, {teaser_only} stored as teaser-only, "
        f"{dead} unrecoverable."
    )
    conn.close()


if __name__ == "__main__":
    backfill()
