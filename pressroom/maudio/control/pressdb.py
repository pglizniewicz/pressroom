"""Scraper for Midiman/M-Audio's 2001-2003 "pressdb.php" era
(midiman.net/news/pressdb.php and midiman.com/news/pressdb.php) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

Different system/era from the earlier golive.py (which covers the 2001 static
GoLive pages under .../html/press/*.htm) - pressdb.php is a PHP
script that dumps the ENTIRE press-release history as one long page, one
<table width="780" ...> block per release (date + linked title + one-line
teaser). The linked title points either at an external PDF or at one of the
site's own HTML detail pages (presstemp.php?ID=... or /news/php/<name>.php).
Both are followed for full text, from different places: the PDFs by
attachment_crawl, the HTML detail pages by this module. Only
when neither yields anything does a row keep the short listing teaser as its
body.

That split is recent. This scraper originally stored teasers and nothing else,
on the reasoning that a PDF was unextractable and the HTML pages could be
followed "later" - and later never came, leaving 40 rows sitting at ~150
characters each with their full text archived and reachable the whole time.

Since every capture is a full re-dump of everything published up to that
date, `archive.list_all_captures` is used to sample every historical
capture of the bare pressdb.php URL (no query-string addressing exists on
this script - confirmed via CDX). Entries are deduped by (title, date)
rather than by their resolved target URL: the site's own template changed
over the years, retargeting the same release's title link from its own
presstemp.php detail page (early captures) straight to the PDF (later
captures) - deduping by URL alone would store the same release twice. When
both a detail-page URL and a PDF URL are seen for the same (title, date), the
detail-page URL wins: both are recoverable, but the HTML page gives clean text
where whole-document PDF extraction interleaves the running header/footer
mid-body. Then the longest teaser - same `best`-dict idiom as
terratec/control/cms.py, just keyed differently.

Rerunnable against rows it already stored: a row is retried whenever its
stored body is still teaser-length, since detail_id cannot distinguish those
here (it holds the *listing* capture's timestamp).

Source tags are per-domain (midiman_net_pressdb / midiman_com_pressdb), same
policy as golive.py and consistent with the terratec_pressde /
terratec_pressen precedent (distinct source per distinct system+domain).
The two domains' listings overlap heavily (most releases were cross-posted)
- not deduped across domains, by design, same as everywhere else in this repo.

Known quirk: one entry (09 Jul 2003, midiman.net) has a copy-paste bug in its
href - an absolute URL pasted into what should be a relative path, producing
a doubled/invalid link. Fixed by taking the last http(s):// occurrence in
the href, but note the underlying target was never actually archived anyway.
"""


import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.scraping.control import attachment_crawl
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail, Entry


DOMAINS = {
    "midiman_net_pressdb": "http://www.midiman.net/news/pressdb.php",
    "midiman_com_pressdb": "http://www.midiman.com/news/pressdb.php",
}

ABS_URL_RE = re.compile(r"https?://")

# A stored body longer than this has had its real text recovered; anything
# shorter is still just the listing blurb, so it is worth another attempt. The
# longest teaser these listings produce is 864 characters.
#
# Deliberately duplicated from attachment_crawl rather than
# imported: a scraper importing a constant from a backfill would invert the
# dependency. Same corpus, same rationale - change both together.
RECOVERED_LENGTH = 900

# Attachments are somebody else's job (attachment_crawl); this
# scraper only follows its own HTML detail pages.
ATTACHMENT_EXTS = (".pdf", ".doc")


def is_html_detail(url: str) -> bool:
    return not url.lower().split("?", 1)[0].endswith(ATTACHMENT_EXTS)


def parse_detail(html: bytes) -> Detail:
    """Full text of a presstemp.php / news/php/<name>.php detail page.

    Takes the whole document's text rather than hunting for a container: these
    pages are bare templated or Word-exported documents with no site navigation
    at all (measured: 12-70 characters of chrome against 2.2-7.8 KB of release),
    and the Word ones vary between MsoBodyText, MsoBlockText and span.normal
    wrappers, so any single selector would miss some. Same approach as
    golive.py's 2001-era GoLive pages.

    Only the body is returned. The listing already gave a better title and a
    date, and the detail page carries no date of its own in a parseable place.
    """
    soup = BeautifulSoup(decode_html(html), "html.parser")
    for tag in soup.find_all(["title", "script", "style"]):
        tag.decompose()
    body, body_html = richtext.extract(soup)
    return {"body": body, "body_html": body_html}


def extract_entries(html: bytes, base_url: str,
                    timestamp: str = None) -> list[Entry]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="cp1252")
    entries = []

    for bold_td in soup.find_all("td", class_="normal-bold"):
        a = bold_td.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue

        href = a["href"]
        matches = list(ABS_URL_RE.finditer(href))
        if matches and matches[-1].start() > 0:
            href = href[matches[-1].start():]
        url = urljoin(base_url, href)

        tr = bold_td.find_parent("tr")
        date_td = tr.find("td", class_="normal-italic") if tr else None
        if date_td is None and tr:
            # Older template variant: date lives in its own td.normal-bold
            # (same class as the title cell) instead of a td.normal-italic.
            for candidate in tr.find_all("td", class_="normal-bold"):
                if candidate is bold_td:
                    continue
                if re.match(r"^\d{1,2}\s+\w+\s+\d{4}$", candidate.get_text(strip=True)):
                    date_td = candidate
                    break
        date_str = date_td.get_text(strip=True) if date_td else ""
        date = ""
        if date_str:
            date = iso_date(date_str)

        table = bold_td.find_parent("table")
        body = ""
        body_html = ""
        if table:
            body_td = table.find("td", class_="normal")
            if body_td:
                body, body_html = richtext.extract(body_td)

        entries.append({"title": title, "date": date, "url": url, "body": body,
                        "body_html": body_html, "detail_id": timestamp})

    return entries


def scrape_domain(source: str, listing_url: str, limit: int = None,
                  catch: dict = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    print(f"[{source}] Listing historical captures of {listing_url}", flush=True)
    entries = [] if catch_up.no_crawl(catch) else archive.sample_all_captures(
        conn, session, listing_url, extract_entries, limit=limit)

    # Keyed by (title, date) rather than url: the same release's title link was
    # retargeted over the years (early captures point at the site's own
    # presstemp.php detail page, later captures link the PDF directly) - one
    # release would otherwise show up as two rows. Prefer the HTML detail page
    # over a bare PDF: both are recoverable now, but the HTML page yields clean
    # text, while whole-document PDF extraction interleaves the running
    # header/footer mid-body (see attachment_crawl). Then prefer
    # the longest teaser seen.
    def rank(entry):
        return (0 if entry["url"].lower().endswith(".pdf") else 1, len(entry["body"]))

    best = {}  # (title, date) -> {url, body, detail_id}
    for e in entries:
        key = (e["title"], e["date"])
        cur = best.get(key)
        if cur is None or rank(e) > rank(cur):
            best[key] = {"url": e["url"], "body": e["body"],
                         "body_html": e["body_html"], "detail_id": e["detail_id"]}

    print(f"\n[{source}] {len(best)} distinct release entries found across all captures", flush=True)

    stats = Stats(source, total=len(best))
    for (title, date), e in best.items():
        url = e["url"]
        stored_len = storage.stored_body_length(conn, url)
        if stored_len is not None and stored_len >= RECOVERED_LENGTH:
            stats.skipped()
            continue

        # Only HTML detail pages are followed here. A .pdf/.doc URL is an
        # attachment and belongs to attachment_crawl, which
        # already knows how to extract it.
        parsed, confirmed = ({}, True)
        if is_html_detail(url):
            parsed, confirmed = archive.fetch_detail_snapshot(conn, session, url, parse_detail)
        body = parsed.get("body") or ""

        if stored_len is None:
            if not body and not confirmed:
                stats.uncertain()
                continue
            # detail_id records where the body actually came from: the detail
            # capture when we recovered one, otherwise the listing capture the
            # teaser was read from.
            storage.store_release(conn, source, url, title=title, date=date,
                             body=body or e["body"],
                             body_html=(parsed.get("body_html") if body
                                        else e["body_html"]) or None,
                             detail_id=parsed.get("detail_id") or e["detail_id"],
                             commit=False)
            stats.added() if body else stats.teaser()
        elif body and len(body) > stored_len:
            storage.upgrade_release(conn, url, body=body,
                               body_html=parsed.get("body_html") or None,
                               detail_id=parsed.get("detail_id"), commit=False)
            stats.upgraded()
        elif not confirmed:
            stats.uncertain()
        else:
            stats.skipped()
    conn.commit()

    stats.summary(conn)
    catch_up.run(conn, source, catch, parser=parse_detail, session=session,
                  twins_too=True)
    # Both shapes live under this tag: HTML detail pages, and rows whose url is
    # a .pdf the listing only teased.
    attachment_crawl.catch_up(conn, [source],
                              network=bool((catch or {}).get("attachments")))
    conn.close()


def scrape(limit: int = None, catch: dict = None) -> None:
    for source, listing_url in DOMAINS.items():
        scrape_domain(source, listing_url, limit=limit, catch=catch)
