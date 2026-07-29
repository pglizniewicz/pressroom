#!/usr/bin/env python3
"""Backfill terratec_pressen/terratec_pressde from the news module's own
yearly category-listing pages (file=index&catid=N&allstories=1), which show
every article that year as a title + short teaser paragraph - including
sids that never surfaced in the site-wide timemap prefix search at all.

For each teaser sid, tries to recover the full article (via the standard
article URL, then print.php) before falling back to storing the teaser
paragraph itself as the body. Sids already stored in full are skipped, but
a sid previously stored as teaser-only (detail_id == "teaser") is retried
every run and upgraded in place if a full article can now be recovered -
a probe/fetch network error is never allowed to lock in a permanent
teaser-only row, only a confirmed dead end is.

Usage:
  python backfill_terratec_teasers.py
"""

import re
import sqlite3

import requests
from bs4 import BeautifulSoup
from dateutil import parser as du

from db import stored_detail_id
import db
import wayback


# (domain prefix, source, wayback snapshot URL) - id_ baked in directly,
# matching every other hardcoded-URL table in this repo.
CATEGORY_PAGES = [
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031115085312id_/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=13&topic=&allstories=1&menu=300"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031115084858id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=14&topic=&allstories=1&menu=304"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20031213100854id_/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&amp"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20041010061159id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&amp"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730011217id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730010823id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=20&topic=&allstories=1"),
    ("http://pressde.terratec.net:80/", "terratec_pressde",
     "https://web.archive.org/web/20070730010351id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20031001235452id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&menu=2"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20041010063929id_/http://pressen.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=16&topic=&allstories=1&amp"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070808232502id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&menu=2&menu=307"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070808232455id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2&menu=309"),
    ("http://pressen.terratec.net:80/", "terratec_pressen",
     "https://web.archive.org/web/20070630063657id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1&menu=2"),
]

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+)")


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


def recover_full_content(conn: sqlite3.Connection, prefix: str, sid: str, session: requests.Session):
    """Try the standard article URL, then print.php. Returns (result, confirmed):
    result is (timestamp, parsed) or None; confirmed is False if any attempt
    hit a network error (caller must not treat that as a verified dead end -
    only a cleanly-checked "not found" or "fetched fine, no title" counts)."""
    uncertain = False
    for url_tmpl, parse_fn in [
        (f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}", parse_article_snapshot),
        (f"{prefix}print.php?sid={sid}", parse_print_snapshot),
    ]:
        try:
            found = wayback.get_latest_working_snapshot(url_tmpl)
        except Exception:
            uncertain = True
            continue
        if not found:
            continue
        snapshot_url, timestamp = found
        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_fn(content)
        except Exception:
            uncertain = True
            continue
        if parsed.get("title"):
            return (timestamp, parsed), True
    return None, not uncertain


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
    conn = db.connect()
    session = requests.Session()

    all_teasers = {}  # source -> {sid: (date, title, teaser)}
    for prefix, source, url in CATEGORY_PAGES:
        print(f"Fetching {url}", flush=True)
        try:
            content = wayback.fetch_snapshot(conn, session, url, timeout=20)
        except Exception as e:
            print(f"  ERROR fetching category page: {e}")
            continue
        teasers = extract_teasers(content)
        print(f"  {len(teasers)} teasers found")
        all_teasers.setdefault((prefix, source), {}).update(teasers)

    full_recovered = 0
    upgraded = 0
    teaser_only = 0
    skipped = 0
    dead = 0

    for (prefix, source), teasers in all_teasers.items():
        print(f"\n[{source}] {len(teasers)} teaser sids to check", flush=True)

        for sid, (teaser_date, teaser_title, teaser_text) in teasers.items():
            article_url = f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}"

            existing = stored_detail_id(conn, article_url)
            if existing is not None and existing != "teaser":
                skipped += 1
                print(".", end="", flush=True)
                continue

            recovered, confirmed = recover_full_content(conn, prefix, str(sid), session)
            if recovered:
                timestamp, parsed = recovered
                if existing == "teaser":
                    db.upgrade_release(conn, article_url, detail_id=timestamp,
                                       title=parsed["title"], date=parsed["date"],
                                       body=parsed["body"], commit=False)
                    upgraded += 1
                    print("U", end="", flush=True)
                else:
                    db.store_release(conn, source, article_url, title=parsed["title"],
                                     date=parsed["date"], body=parsed["body"],
                                     detail_id=timestamp, commit=False)
                    full_recovered += 1
                    print("+", end="", flush=True)
                conn.commit()
                continue

            if existing == "teaser":
                skipped += 1
                print(".", end="", flush=True)
                continue

            if not confirmed:
                skipped += 1
                print("?", end="", flush=True)
                continue

            if teaser_text:
                db.store_release(conn, source, article_url, title=teaser_title,
                                 date=teaser_date, body=teaser_text, detail_id="teaser")
                teaser_only += 1
                print("t", end="", flush=True)
            else:
                dead += 1
                print("x", end="", flush=True)

    print(
        f"\n\nDone. {full_recovered} recovered in full, {upgraded} teaser(s) upgraded to full text, "
        f"{teaser_only} stored as teaser-only, {skipped} skipped (existing/uncertain), "
        f"{dead} confirmed unrecoverable."
    )
    conn.close()


if __name__ == "__main__":
    backfill()
