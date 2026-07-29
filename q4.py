#!/usr/bin/env python3
"""Scraper for press rooms running the Q4 Inc. investor-relations platform.

Q4 hosts IR sites for many public companies on one shared template, so a
single scraper covers all of them - currently intc.com (scrape_intel.py) and
ir.amd.com (scrape_amd.py), which is why those two files are barely more than
a URL plus a call to scrape() here.

Nothing else in this repo uses this module: every other source is a
one-of-a-kind dead site with its own bespoke parser. (This file used to be
called common.py, which was misleading - only its HTTP constants were common,
and those now live in fetch.py.)
"""

import argparse
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import db
from fetch import HEADERS, SLEEP


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

    conn = db.connect(db_path)
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
            if db.already_stored(conn, item["url"]):
                skip_count += 1
                print(".", end="", flush=True)
                continue

            try:
                body = fetch_body(session, item["url"])
            except Exception as e:
                print(f"\n    ERROR fetching {item['url']}: {e}")
                body = ""
            time.sleep(SLEEP)

            if db.store_release(conn, source, item["url"], title=item["title"],
                                date=item["date"], body=body, detail_id=item["detail_id"]):
                new_count += 1
                print("+", end="", flush=True)

        print()

    total_in_db = db.source_total(conn, source)
    print(f"\nDone. Added {new_count} new, skipped {skip_count} existing. Total [{source}] in DB: {total_in_db}")
    conn.close()


def make_arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--pages", type=int, default=None, help="Number of pages to scrape")
    p.add_argument("--start", type=int, default=1, help="Start from this page number")
    return p
