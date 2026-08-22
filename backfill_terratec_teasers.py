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

from db import stored_detail_id
from dates import iso_date
import db
import richtext
from progress import Stats
import wayback
from scrape_terratec_portal import parse_snapshot as parse_article_snapshot


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


def extract_teasers(content: bytes) -> dict:
    """Return {sid: (date, title, teaser_text)} for every article on this page."""
    # cp1252 stated, never sniffed: these pages predate UTF-8 and declare no
    # charset, so left to guess bs4 read them as ISO-8859-1 and stored the
    # cp1252 punctuation range as C1 control characters (see
    # repair_encoding.py, which had to undo exactly that).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
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
        date = iso_date(date_str, dayfirst=True)

        title_tr = a.find_parent("tr")
        content_tr = title_tr.find_next_sibling("tr") if title_tr else None
        teaser = teaser_html = ""
        if content_tr:
            content_html = str(content_tr)
            idx = content_html.find('<span class="note">')
            if idx != -1:
                content_html = content_html[:idx]
            teaser, teaser_html = richtext.extract(
                BeautifulSoup(content_html, "html.parser"))

        teasers[sid] = (date, title, teaser, teaser_html)

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


# parse_article_snapshot is scrape_terratec_portal.parse_snapshot, imported
# above rather than reimplemented. It was a near-copy: the same TITLE_TAG_RE
# verbatim, and the same three-step cut - except this copy always split
# "Related links" before "Links!" while the portal's _cut_at_first_marker
# takes whichever comes first, so the two disagreed whenever an article's
# prose contained one of the markers. The portal version also has the
# month-precision title fallback this one never got, and now produces
# body_html from the DOM.


def parse_print_snapshot(content: bytes) -> dict:
    # cp1252 stated, never sniffed: these pages predate UTF-8 and declare no
    # charset, so left to guess bs4 read them as ISO-8859-1 and stored the
    # cp1252 punctuation range as C1 control characters (see
    # repair_encoding.py, which had to undo exactly that).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    title_tag = soup.select_one("font.print-title")
    if not title_tag:
        return {"title": "", "date": "", "body": "", "body_html": ""}
    m = TITLE_RE.match(title_tag.get_text(strip=True))
    if not m:
        return {"title": "", "date": "", "body": "", "body_html": ""}
    date = iso_date(m.group(1), dayfirst=True)
    title = m.group(2).strip()
    body, body_html = richtext.extract(soup.select_one("font.print-normal"))
    return {"title": title, "date": date, "body": body, "body_html": body_html}


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

    for (prefix, source), teasers in all_teasers.items():
        print(f"\n[{source}] {len(teasers)} teaser sids to check", flush=True)
        stats = Stats(source, total=len(teasers))

        for sid, (teaser_date, teaser_title, teaser_text, teaser_html) in teasers.items():
            article_url = f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}"

            existing = stored_detail_id(conn, article_url)
            if existing is not None and existing != "teaser":
                stats.skipped()
                continue

            recovered, confirmed = recover_full_content(conn, prefix, str(sid), session)
            if recovered:
                timestamp, parsed = recovered
                if existing == "teaser":
                    db.upgrade_release(conn, article_url, detail_id=timestamp,
                                       title=parsed["title"], date=parsed["date"],
                                       body=parsed["body"],
                                       body_html=parsed["body_html"] or None,
                                       commit=False)
                    stats.upgraded()
                else:
                    db.store_release(conn, source, article_url, title=parsed["title"],
                                     date=parsed["date"], body=parsed["body"],
                                     body_html=parsed["body_html"] or None,
                                     detail_id=timestamp, commit=False)
                    stats.added()
                conn.commit()
                continue

            # Ordered before the teaser check on purpose: a failed probe is not
            # a verdict, so an already-stored teaser must be reported
            # `uncertain` (a rerun retries it) rather than `skipped`.
            if not confirmed:
                stats.uncertain()
                continue

            if existing == "teaser":
                stats.skipped()
                continue

            if teaser_text:
                db.store_release(conn, source, article_url, title=teaser_title,
                                 date=teaser_date, body=teaser_text,
                                 body_html=teaser_html or None, detail_id="teaser")
                stats.teaser()
            else:
                stats.dead()

        stats.summary(conn)

    conn.close()


if __name__ == "__main__":
    backfill()
