"""Scraper for Midiman/M-Audio's 2004-2013 "media_pr" era
(index.php?do=media.media_pr on midiman.net, midiman.com, m-audio.com) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

A different, later CMS from both golive.py (2001 static pages) and pressdb.py
(2001-2003 pressdb.php) - this one is a bespoke in-house system
(self-identified in an HTML comment as built by "Hydra Media Labs"), reached
via a index.php?do=<section>.<action> front controller.
Same overall shape as pressdb.php though: one ever-growing listing page per
domain, no pagination, no per-release detail page - the title just links
straight out to an external .doc/.pdf under /images/en/press_releases/, which
this scraper does not follow - so the rows it stores hold only the listing
teaser, averaging ~150 characters. Recovering the real text from those
attachments (roughly 2/3 .doc, 1/3 .pdf) is
attachment_crawl's job.

Two markup templates exist across time on the SAME endpoint (both are tried
on every fetched capture - whichever matches yields entries, the other
yields none, no need to know which era a given capture belongs to):
  - 2004-2007: <td class="normaltextgraybold">MM/DD/YYYY</td> + a sibling
    <td class="normaltext"><a>Title</a></td>, teaser in the next <tr>.
  - 2008+ redesign: <div id="short-news"> containing #news-title (date +
    linked title, date in the first <strong>) and #news-short-content
    (teaser).
The earliest m-audio.com capture (2004-05-02) is a Flash-detection redirect
stub with neither pattern present - naturally yields zero entries, no
special-casing needed.

Since every capture is a full re-dump of everything published up to that
date (confirmed: the 2007 midiman.net capture is a strict superset of the
2006 one), `archive.list_all_captures` samples every historical capture per
domain. Entries are deduped by (title, date) rather than raw URL, keeping
the longest teaser seen - there's no HTML-vs-binary axis to prefer here
(every link is equally an external .doc/.pdf), unlike pressdb.php's rank().

Source tags are per-domain (midiman_net_media_pr / midiman_com_media_pr /
maudio_com_media_pr), same policy as every other source in this repo.
m-audio.com is by far the deepest source (34 captures 2004-2013, growing to
~120+ releases) - the other two domains are shallower mirrors of the same
underlying press history, kept separate since URL-based dedup can't cross
domains.
"""

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.release.control.storage import already_stored
from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.scraping.control import attachment_crawl
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Entry


DOMAINS = {
    "midiman_net_media_pr": "http://www.midiman.net/index.php?do=media.media_pr",
    "midiman_com_media_pr": "http://www.midiman.com/index.php?do=media.media_pr",
    "maudio_com_media_pr": "http://www.m-audio.com/index.php?do=media.media_pr",
}


def _dedupe_repeated_title(title: str) -> str:
    """The source occasionally stores a title twice concatenated, truncated
    at a fixed byte limit mid-repeat (confirmed live: "...Microphone
    PreampM-Audio Unveils New Octane 8-Channel Microphon", the same 100-char
    cut on all 3 domains) - collapse it back to the first copy."""
    chunk = title[:20]
    if len(chunk) < 20:
        return title
    pos = title.find(chunk, 20)
    return title[:pos].strip() if pos != -1 else title


def extract_entries_template_a(soup: BeautifulSoup, base_url: str) -> list[Entry]:
    entries = []
    for date_td in soup.find_all("td", class_="normaltextgraybold"):
        tr = date_td.find_parent("tr")
        if not tr:
            continue
        title_td = tr.find("td", class_="normaltext")
        a = title_td.find("a", href=True) if title_td else None
        if not a:
            continue
        title = _dedupe_repeated_title(a.get_text(strip=True))
        if not title:
            continue
        url = urljoin(base_url, a["href"])
        date = iso_date(date_td.get_text(strip=True))

        teaser = teaser_html = ""
        next_tr = tr.find_next_sibling("tr")
        if next_tr:
            teaser_td = next_tr.find("td", class_="normaltext")
            if teaser_td:
                teaser, teaser_html = richtext.extract(teaser_td)

        entries.append(
            {
                "title": title,
                "date": date,
                "url": url,
                "body": teaser,
                "body_html": teaser_html,
            }
        )
    return entries


def extract_entries_template_b(soup: BeautifulSoup, base_url: str) -> list[Entry]:
    entries = []
    for div in soup.find_all("div", id="short-news"):
        title_div = div.find("div", id="news-title")
        if not title_div:
            continue
        a = title_div.find("a", href=True)
        date_strong = title_div.find("strong")
        if not a or not date_strong:
            continue
        title = _dedupe_repeated_title(a.get_text(strip=True))
        if not title:
            continue
        url = urljoin(base_url, a["href"])
        date_str = date_strong.get_text(strip=True).rstrip(" -").strip()
        date = iso_date(date_str)

        teaser = teaser_html = ""
        content_div = div.find("div", id="news-short-content")
        if content_div:
            teaser, teaser_html = richtext.extract(content_div)

        entries.append(
            {
                "title": title,
                "date": date,
                "url": url,
                "body": teaser,
                "body_html": teaser_html,
            }
        )
    return entries


def extract_entries(html: bytes, base_url: str, timestamp: str = None) -> list[Entry]:
    # decode_html rather than letting bs4 sniff: verified a no-op on every
    # currently cached capture, but two of them are already not valid utf-8,
    # so the chardet fallback is one stray byte away. See decoding.py.
    soup = BeautifulSoup(decode_html(html), "html.parser")
    entries = extract_entries_template_a(soup, base_url) + extract_entries_template_b(
        soup, base_url
    )
    for e in entries:
        e["detail_id"] = timestamp
    return entries


def scrape_domain(
    source: str, listing_url: str, limit: int = None, catch: dict = None
) -> None:
    conn = connection.connect()
    session = requests.Session()

    print(f"[{source}] Listing historical captures of {listing_url}", flush=True)
    entries = (
        []
        if catch_up.no_crawl(catch)
        else archive.sample_all_captures(
            conn, session, listing_url, extract_entries, limit=limit
        )
    )

    best = {}  # (title, date) -> {url, body, body_html, detail_id}
    for e in entries:
        key = (e["title"], e["date"])
        cur = best.get(key)
        if cur is None or len(e["body"]) > len(cur["body"]):
            best[key] = {
                "url": e["url"],
                "body": e["body"],
                "body_html": e["body_html"],
                "detail_id": e["detail_id"],
            }

    print(
        f"\n[{source}] {len(best)} distinct release entries found across all captures",
        flush=True,
    )

    stats = Stats(source, total=len(best))
    for (title, date), e in best.items():
        url = e["url"]
        if already_stored(conn, url):
            stats.skipped()
            continue
        if storage.store_release(
            conn,
            source,
            url,
            title=title,
            date=date,
            body=e["body"],
            body_html=e["body_html"],
            detail_id=e["detail_id"],
            commit=False,
        ):
            stats.added()
    conn.commit()

    stats.summary(conn)
    # This CMS links out to .doc/.pdf files and the listing gives only a
    # ~150-character teaser, so an attachment row's phase 2 is the only way it
    # ever gets its real text. Every row of this source is one of those.
    attachment_crawl.catch_up(
        conn, [source], network=bool((catch or {}).get("attachments"))
    )
    # No HTML parser for this tag - the listing teaser is all there ever was on
    # the page, and the real text is in the attachment. What phase 2 can still
    # do here is the twin fill: this CMS published some releases under two url
    # schemes on one domain.
    catch_up.run(conn, source, catch, twins_too=True)
    conn.close()


def scrape(limit: int = None, catch: dict = None) -> None:
    for source, listing_url in DOMAINS.items():
        scrape_domain(source, listing_url, limit=limit, catch=catch)
