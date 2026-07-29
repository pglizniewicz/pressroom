#!/usr/bin/env python3
"""Shared scraper logic for Q4 IR platform press release sites (intc.com, ir.amd.com, …)."""

import argparse
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}
SLEEP = 1.5


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS releases (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT NOT NULL,
            detail_id TEXT,
            title     TEXT,
            date      TEXT,
            url       TEXT UNIQUE,
            body      TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS releases_fts USING fts5(
            title, body,
            content='releases',
            content_rowid='id',
            tokenize='unicode61'
        );

        CREATE TRIGGER IF NOT EXISTS releases_ai
        AFTER INSERT ON releases BEGIN
            INSERT INTO releases_fts(rowid, title, body)
            VALUES (new.id, new.title, new.body);
        END;

        CREATE TABLE IF NOT EXISTS wayback_cache (
            url                TEXT PRIMARY KEY,
            content            BLOB NOT NULL,
            id_content_type    TEXT,
            fw_guessed_charset TEXT,
            bs4_encoding       TEXT
        );
    """)
    conn.commit()

    # wayback_cache may already exist from before these diagnostic columns
    # were added (SQLite has no "ADD COLUMN IF NOT EXISTS") - add them if missing.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(wayback_cache)").fetchall()}
    for col in ("id_content_type", "fw_guessed_charset", "bs4_encoding"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE wayback_cache ADD COLUMN {col} TEXT")
    conn.commit()


def already_stored(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM releases WHERE url = ?", (url,)).fetchone() is not None


def stored_detail_id(conn: sqlite3.Connection, url: str):
    """None if no row exists for `url`, else its detail_id - lets a caller
    tell a fully-recovered row apart from a fallback (e.g. detail_id=="teaser"
    or "stub") that's still worth retrying to upgrade on a future run."""
    row = conn.execute("SELECT detail_id FROM releases WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def get_total_pages(session: requests.Session, list_url: str) -> int:
    r = session.get(list_url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    last = 1
    for a in soup.select("ul.pagination li a"):
        m = re.search(r"(\d+)$", a.get_text(strip=True))
        if m:
            last = max(last, int(m.group(1)))
    return last


def _parse_date(time_el) -> str:
    """Extract ISO date from <time> element; fall back to dateutil for freeform text."""
    if time_el is None:
        return ""
    dt_attr = time_el.get("datetime", "")
    if dt_attr:
        return dt_attr[:10]
    text = time_el.get_text(strip=True)
    if not text:
        return ""
    try:
        from dateutil import parser as du
        return du.parse(text).strftime("%Y-%m-%d")
    except Exception:
        return text


def parse_list_page(
    session: requests.Session,
    list_url: str,
    page: int,
    base_url: str,
    container_sel: str = "article.media-container",
    title_link_sel: str = "div.media-title a",
) -> list:
    url = f"{list_url}?page={page}"
    r = session.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    for container in soup.select(container_sel):
        a = container.select_one(title_link_sel)
        time_el = container.select_one("div.date time")
        if not a:
            continue
        href = a["href"]
        if not href.startswith("http"):
            href = base_url + href
        m = re.search(r"/detail/(\d+)/", href)
        detail_id = m.group(1) if m else None
        items.append({
            "url": href,
            "title": a.get_text(strip=True),
            "date": _parse_date(time_el),
            "detail_id": detail_id,
        })
    return items


def fetch_body(session: requests.Session, url: str) -> str:
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    article = soup.select_one("article.full-news-article")
    if not article:
        return ""
    for el in article.select("div.related-documents-line, h1.article-heading"):
        el.decompose()
    return article.get_text(" ", strip=True)


def scrape(
    source: str,
    list_url: str,
    db_path: Path,
    pages: int = None,
    start: int = 1,
    container_sel: str = "article.media-container",
    title_link_sel: str = "div.media-title a",
) -> None:
    base_url = re.match(r"(https?://[^/]+)", list_url).group(1)

    conn = sqlite3.connect(db_path)
    init_db(conn)
    session = requests.Session()

    print("Detecting total page count...", flush=True)
    total = get_total_pages(session, list_url)
    time.sleep(SLEEP)

    end_page = start + pages - 1 if pages else total
    end_page = min(end_page, total)

    print(f"[{source}] Scraping pages {start}–{end_page} of {total} total", flush=True)

    new_count = 0
    skip_count = 0

    for page in range(start, end_page + 1):
        print(f"  Page {page}/{end_page}", end="  ", flush=True)
        try:
            items = parse_list_page(session, list_url, page, base_url, container_sel, title_link_sel)
        except Exception as e:
            print(f"ERROR fetching list: {e}")
            time.sleep(SLEEP * 2)
            continue
        time.sleep(SLEEP)

        for item in items:
            if already_stored(conn, item["url"]):
                skip_count += 1
                print(".", end="", flush=True)
                continue

            try:
                body = fetch_body(session, item["url"])
            except Exception as e:
                print(f"\n    ERROR fetching {item['url']}: {e}")
                body = ""
            time.sleep(SLEEP)

            conn.execute(
                "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
                (source, item["detail_id"], item["title"], item["date"], item["url"], body),
            )
            conn.commit()
            new_count += 1
            print("+", end="", flush=True)

        print()

    total_in_db = conn.execute("SELECT count(*) FROM releases WHERE source = ?", (source,)).fetchone()[0]
    print(f"\nDone. Added {new_count} new, skipped {skip_count} existing. Total [{source}] in DB: {total_in_db}")
    conn.close()


def make_arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--pages", type=int, default=None, help="Number of pages to scrape")
    p.add_argument("--start", type=int, default=1, help="Start from this page number")
    return p
