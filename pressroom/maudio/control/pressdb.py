"""Scraper for Midiman/M-Audio's 2001-2003 "pressdb.php" era
(midiman.net/news/pressdb.php and midiman.com/news/pressdb.php) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

A different system from the earlier golive.py static pages: pressdb.php dumps
the entire press-release history as one long page, one
<table width="780" ...> block per release - date, linked title, one-line
teaser. The linked title points either at an external PDF or at one of the
site's own HTML detail pages (presstemp.php?ID=... or /news/php/<name>.php),
and **both are followed**, from different places: the PDFs by
attachment_crawl, the HTML detail pages by this module. A row keeps the short
listing teaser as its body only when neither yields anything. Following just
one of the two is how rows sat at teaser length with their full text archived
and reachable the whole time.

Every capture is a full re-dump of everything published up to its date, so
`archive.list_all_captures` samples every historical capture of the bare
pressdb.php url - no query-string addressing exists on this script, confirmed
via CDX. Entries are deduped by (title, date) rather than by resolved target
url: the template retargeted the same release's title link from its own
presstemp.php detail page in early captures straight to the PDF in later ones,
so url-based dedup would store it twice. Where both a detail page and a PDF are
seen for one (title, date), the detail page is preferred - both are recoverable, but the
HTML gives clean text where whole-document PDF extraction interleaves the
running header mid-body. Then the longest teaser.

Rerunnable against rows it already stored: a row is retried whenever its stored
body is still teaser-length, because detail_id cannot tell those apart here - it
holds the *listing* capture's timestamp.

Known quirk: one entry (09 Jul 2003, midiman.net) has an absolute url pasted
into what should be a relative path, producing a doubled link. Handled by taking
the last http(s):// occurrence in the href - though that target was never
archived anyway.

Known defect: on a midiman.net detail page the DOCTYPE comes through
`richtext.extract` as the body's first paragraph - docs/adr/text-and-markup.md.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.scraper.control import attachment_crawl
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraper.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.control.outcome import Stats
from pressroom.scraper.control import discovery
from pressroom.scraper.entity.parse import Detail, Entry


DOMAINS = {
    "midiman_net_pressdb": "http://www.midiman.net/news/pressdb.php",
    "midiman_com_pressdb": "http://www.midiman.com/news/pressdb.php",
}

ABS_URL_RE = re.compile(r"https?://")

# A stored body longer than this has had its real text recovered; anything
# shorter is still just the listing blurb, so it is worth another attempt. The
# longest teaser these listings produce is 864 characters.
#
# Duplicated from attachment_crawl rather than
# imported: a scraper importing a constant from a backfill would invert the
# dependency. Same corpus, same rationale - change both together.
RECOVERED_LENGTH = 900

# Attachments belong to attachment_crawl; this scraper only follows its own
# HTML detail pages.
ATTACHMENT_EXTS = (".pdf", ".doc")


def is_html_detail(url: str) -> bool:
    return not url.lower().split("?", 1)[0].endswith(ATTACHMENT_EXTS)


def parse_detail(html: bytes) -> Detail:
    """Full text of a presstemp.php / news/php/<name>.php detail page.

    Takes the whole document's text rather than looking for a container: these
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


def extract_entries(
    html: bytes, base_url: str, timestamp: str | None = None
) -> list[Entry]:
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
            href = href[matches[-1].start() :]
        url = urljoin(base_url, href)

        tr = bold_td.find_parent("tr")
        date_td = tr.find("td", class_="normal-italic") if tr else None
        if date_td is None and tr:
            # Older template variant: date is in its own td.normal-bold
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

        entries.append(
            {
                "title": title,
                "date": date,
                "url": url,
                "body": body,
                "body_html": body_html,
                "detail_id": timestamp,
            }
        )

    return entries


def scrape_domain(
    source: str, listing_url: str, limit: int | None = None, catch: dict | None = None
) -> None:
    conn = connection.connect()
    session = requests.Session()

    print(f"[{source}] Listing historical captures of {listing_url}", flush=True)
    entries = (
        []
        if catch_up.no_crawl(catch)
        else discovery.sample_all_captures(
            conn, session, listing_url, extract_entries, limit=limit
        )
    )

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
        stored_len = storage.stored_body_length(conn, url)
        if stored_len is not None and stored_len >= RECOVERED_LENGTH:
            stats.skipped()
            continue

        # Only HTML detail pages are followed here. A .pdf/.doc URL is an
        # attachment and belongs to attachment_crawl, which
        # already knows how to extract it.
        parsed, confirmed = ({}, True)
        if is_html_detail(url):
            parsed, confirmed = discovery.fetch_detail_snapshot(
                conn, session, url, parse_detail
            )
        body = parsed.get("body") or ""

        if stored_len is None:
            if not body and not confirmed:
                stats.uncertain()
                continue
            # detail_id records where the body actually came from: the detail
            # capture when we recovered one, otherwise the listing capture the
            # teaser was read from.
            storage.store_release(
                conn,
                source,
                url,
                title=title,
                date=date,
                body=body or e["body"],
                body_html=(parsed.get("body_html") if body else e["body_html"]) or None,
                detail_id=parsed.get("detail_id") or e["detail_id"],
                # Only set when the body came out of the detail capture: with no
                # body this is the listing's teaser, whose capture is not this.
                origin_url=parsed.get("origin_url"),
                commit=False,
            )
            stats.added() if body else stats.teaser()
        elif body and len(body) > stored_len:
            # `grade="full"` because this branch *is* the teaser-to-article
            # replacement: the cursor above admitted this row only because its
            # stored body was still listing-length, and the detail capture just
            # produced a longer one. `stored_grade()` cannot say so here - this
            # scraper writes `full` at insert time even over a blurb, which is
            # why the cursor is `length(body)` - so a row that arrives graded
            # correctly is the one this grade keeps that way.
            storage.upgrade_release(
                conn,
                url,
                body=body,
                body_html=parsed.get("body_html") or None,
                detail_id=parsed.get("detail_id"),
                grade="full",
                origin_url=parsed.get("origin_url"),
                commit=False,
            )
            stats.upgraded()
        elif not confirmed:
            stats.uncertain()
        else:
            stats.skipped()
    conn.commit()

    stats.summary(conn)
    catch_up.run(
        conn, source, catch, parser=parse_detail, session=session, twins_too=True
    )
    # Both shapes are under this tag: HTML detail pages, and rows whose url is
    # a .pdf the listing only teased.
    attachment_crawl.catch_up(
        conn, [source], network=bool((catch or {}).get("attachments"))
    )
    conn.close()


def scrape(limit: int | None = None, catch: dict | None = None) -> None:
    for source, listing_url in DOMAINS.items():
        scrape_domain(source, listing_url, limit=limit, catch=catch)
