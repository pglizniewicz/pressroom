"""Scraper for the Midiman/M-Audio "News" section (index.php?do=media.news,
detail pages at index.php?do=media.new&ID=...) across all four domains that
ran it -> unified pressroom.db, sourced entirely from Wayback snapshots.

Same "Hydra Media Labs" CMS as media_pr.py, different section.
Structurally richer than media_pr: each item has its own archived HTML detail
page carrying the FULL article, not an external .doc/.pdf link-out, so no
attachment backfill is needed here.

This covered only midiman.co.uk at first, on the assumption that the other
domains' equivalents were out of scope. They are not - m-audio.com alone has
379 distinct archived detail IDs against co.uk's 114, and none of them had
ever been fetched.

FOUR templates, two per tier. Every capture is fed all of them; whichever era
a capture belongs to yields something and the rest yield nothing, so nothing
has to know which era it is looking at (same idiom as media_pr's two):

  listing, pre-redesign  <span class="boldtext">, "Date - Title" in the anchor
  listing, Avid era      <div id="short-news"> + #news-title/#news-short-content
  detail,  pre-redesign  td.boldtextgray title; body in p.normaltext on
                         co.uk but td.normaltext on the other three domains
  detail,  Avid era      div#news-page-title (span.redtext headline +
                         span.greytext subtitle) and div#news-content

The ID scheme changed with the redesign too: 32-hex through ~2007, plain
integers afterwards, and the ID spaces are per-domain (the same integer is a
different article on a different domain, or nothing at all).

Two-tier discovery, same idiom as terratec/control/portal.py:
  1. Sample every historical capture of the listing (bare do=media.news and
     its &show=all variant) into a (date, title) -> {href, teaser} map -
     listing entries carry a date and teaser the detail page does not.
  2. Fetch each entry whose href carries a recoverable ID, falling back to the
     listing teaser when the detail page was never archived.
  3. Prefix-crawl do=media.new&ID= (default on, --no-prefix-crawl to disable).
     The detail pages were crawled far more densely than the few listing
     captures ever link to, and on these three added domains that is where
     essentially all the content comes from - see the dead-end note below.
     Prefix-only IDs have no listing metadata, so their date is recovered by
     matching the detail page's own title against the listing map; left blank
     when there is no match, a genuine gap rather than a parse failure.

Confirmed dead end, and the reason step 3 carries the weight: later captures
link articles as news/en_us-<N>.html, a scheme with zero Wayback captures on
any domain, ever. The Avid-era listings link that way exclusively - even
midiman.net's own listing points at m-audio.com/news/en_us-1930.html - so
those listings are usable for date/title/teaser metadata but not for reaching
the article. Deduped by (date, title), preferring whichever variant carries a
recoverable ID.

Listing captures are sampled, never resolved through
get_latest_working_snapshot: by 2019 do=media.news answered HTTP 200 with
m-audio.com's modern home page, so the newest working capture is not a listing
at all.

Out of scope: locale variants (&setlocale=en_gb/fr_fr/...) and the separate
m-audio.jp domain, both of which exist in Wayback and are most likely
translations or duplicates of what is already covered here.
"""

import re
import sqlite3
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.release.control.storage import already_stored
from pressroom.release.control.storage import stored_grade
from pressroom.text.control.dating import iso_date
from pressroom.text.control.decoding import decode_html
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail, Entry

# source tag -> that domain's index.php front controller.
#
# The tags for the three added domains follow the CMS action they come from
# (do=media.news -> _media_news), matching how media_pr.py names
# do=media.media_pr -> _media_pr. midiman_couk_news predates that convention
# and keeps its tag rather than rewriting the source of 114 existing rows.
# maudio_com_news is NOT reused here: that tag belongs to the unrelated modern
# m-audio.com/news blog (news_blog.py, 2014-2019).
DOMAINS = {
    "midiman_couk_news": "http://www.midiman.co.uk/index.php",
    "midiman_net_media_news": "http://www.midiman.net/index.php",
    "midiman_com_media_news": "http://www.midiman.com/index.php",
    "maudio_com_media_news": "http://www.m-audio.com/index.php",
}

# Both ID schemes the CMS used: 32-hex through ~2007, plain integers after the
# Avid-era redesign. Anchored on `ID=` and terminated so a hex ID can't be
# partly matched as a numeric one.
ID_HREF_RE = re.compile(r"ID=([0-9a-f]{32}|\d+)\b")
DATE_TITLE_RE = re.compile(r"^([A-Za-z]+ \d{1,2},\s*\d{4})\s*-\s*(.+)$")


def listing_urls(base: str) -> list[str]:
    return [f"{base}?do=media.news", f"{base}?do=media.news&show=all"]


def detail_url(base: str, hexid: str) -> str:
    return f"{base}?do=media.new&ID={hexid}"


def detail_prefix(base: str) -> str:
    return f"{base}?do=media.new&ID="


def _entries_boldtext(soup: BeautifulSoup, base_url: str) -> list[Entry]:
    """Pre-redesign listing: <span class="boldtext"> per item, "Date - Title"
    in the anchor, teaser in a following <span class="normaltext">."""
    entries = []
    for span in soup.find_all("span", class_="boldtext"):
        a = span.find("a", href=True)
        if not a:
            continue
        m = DATE_TITLE_RE.match(a.get_text(" ", strip=True))
        if not m:
            continue
        date_str, title = m.groups()

        teaser = teaser_html = ""
        br = span.find_next_sibling("br")
        if br:
            teaser_span = br.find_next_sibling("span", class_="normaltext")
            if teaser_span:
                teaser, teaser_html = richtext.extract(teaser_span)

        entries.append(
            {
                "date": iso_date(date_str),
                "title": title.strip(),
                "href": urljoin(base_url, a["href"]),
                "teaser": teaser,
                "teaser_html": teaser_html,
            }
        )
    return entries


def _entries_short_news(soup: BeautifulSoup, base_url: str) -> list[Entry]:
    """Avid-era listing: <div id="short-news"> per item, with #news-title and
    #news-short-content.

    media_pr.py parses the same container, but deliberately not
    the same way and the two are not shared: there, #news-title holds the date
    in its own <strong> and the title in the anchor. Here both hold the single
    string "Date - Title", so it goes through DATE_TITLE_RE instead - feeding
    this markup to that parser would glue the date onto every title.
    """
    entries = []
    for div in soup.find_all("div", id="short-news"):
        title_div = div.find("div", id="news-title")
        if not title_div:
            continue
        a = title_div.find("a", href=True)
        if not a:
            continue
        m = DATE_TITLE_RE.match(title_div.get_text(" ", strip=True))
        if not m:
            continue
        date_str, title = m.groups()

        content_div = div.find("div", id="news-short-content")
        teaser, teaser_html = richtext.extract(content_div)

        entries.append(
            {
                "date": iso_date(date_str),
                "title": title.strip(),
                "href": urljoin(base_url, a["href"]),
                "teaser": teaser,
                "teaser_html": teaser_html,
            }
        )
    return entries


def extract_entries(
    html: bytes, base_url: str, timestamp: str | None = None
) -> list[Entry]:
    # decode_html, not raw bytes: these pages declare utf-8 and are utf-8
    # except for a few Word-pasted cp1252 bytes, which used to make bs4 fall
    # back to chardet and decode the whole file as windows-1250/1258.
    # See decoding.py.
    soup = BeautifulSoup(decode_html(html), "html.parser")
    # Both templates are tried on every capture: whichever era the capture
    # belongs to yields entries and the other yields none, so there is no need
    # to know which is which up front - same idiom as media_pr.py's two
    # templates.
    return _entries_boldtext(soup, base_url) + _entries_short_news(soup, base_url)


def rank(entry: dict):
    return (1 if ID_HREF_RE.search(entry["href"]) else 0, len(entry["teaser"]))


def _detail_boldtextgray(soup: BeautifulSoup) -> Detail:
    """Pre-redesign detail page. The body cell is <p class="normaltext"> on
    midiman.co.uk but <td class="normaltext"> on the other three domains -
    same CMS, different table markup, so accept either."""
    title_td = soup.select_one("td.boldtextgray")
    if not title_td:
        return {}
    # The title cell also carries the teaser in a nested span; drop it so the
    # title doesn't absorb it.
    teaser_span = title_td.find("span", class_="normaltext")
    if teaser_span:
        teaser_span.decompose()

    body_el = soup.select_one("p.normaltext") or soup.select_one("td.normaltext")
    body, body_html = richtext.extract(body_el)
    return {
        "title": title_td.get_text(" ", strip=True),
        "body": body,
        "body_html": body_html,
    }


def _detail_news_page(soup: BeautifulSoup) -> Detail:
    """Avid-era detail page: <div id="news-page-title"> holds the headline in
    span.redtext and a one-line subtitle in span.greytext; the release itself
    is in <div id="news-content">."""
    title_div = soup.find("div", id="news-page-title")
    content_div = soup.find("div", id="news-content")
    if not title_div and not content_div:
        return {}

    title = ""
    if title_div:
        headline = title_div.find("span", class_="redtext")
        title = (headline or title_div).get_text(" ", strip=True)

    body, body_html = richtext.extract(content_div)
    return {"title": title, "body": body, "body_html": body_html}


def parse_detail(html: bytes) -> Detail:
    soup = BeautifulSoup(decode_html(html), "html.parser")
    for template in (_detail_boldtextgray, _detail_news_page):
        parsed = template(soup)
        if parsed.get("body"):
            return parsed
    return {"title": "", "body": "", "body_html": ""}


def discover_listing_best(
    conn: sqlite3.Connection,
    source: str,
    base: str,
    limit: int | None = None,
) -> dict[tuple[str, str], dict[str, str]]:
    session = requests.Session()
    best = {}  # (date, title) -> {href, teaser}

    for listing_url in listing_urls(base):
        print(f"[{source}] Listing historical captures of {listing_url}", flush=True)
        # sample_all_captures, never get_latest_working_snapshot: by 2019 this
        # URL answered HTTP 200 with m-audio.com's modern home page, so the
        # newest working capture is not a listing at all. Sampling every
        # capture means the genuine older ones are parsed regardless.
        entries = archive.sample_all_captures(
            conn, session, listing_url, extract_entries, limit=limit
        )
        for e in entries:
            key = (e["date"], e["title"])
            cur = best.get(key)
            if cur is None or rank(e) > rank(cur):
                best[key] = {"href": e["href"], "teaser": e["teaser"]}

    print(
        f"\n[{source}] {len(best)} distinct listing entries found across all captures",
        flush=True,
    )
    return best


def discover_prefix_ids(source: str, base: str) -> set[str]:
    prefix = detail_prefix(base)
    print(f"[{source}] Listing archived pages under {prefix}", flush=True)
    try:
        snapshots = archive.list_snapshots_by_prefix(prefix)
    except Exception as e:
        print(f"  ERROR listing prefix: {e}")
        return set()
    ids = set()
    for entry in snapshots:
        m = ID_HREF_RE.search(entry["original"])
        if m:
            ids.add(m.group(1))
    print(f"[{source}] {len(ids)} distinct archived detail-page IDs found", flush=True)
    return ids


def scrape_domain(
    source: str,
    base: str,
    limit: int | None = None,
    catch: dict | None = None,
    prefix_crawl: bool = True,
) -> None:
    conn = connection.connect()
    session = requests.Session()

    # Same miss as golive's, and worse: neither channel was guarded, so
    # `--offline` on this source walked every listing capture and then a CDX
    # prefix listing. An empty `best` leaves the loops below untouched, which
    # is how the guard stays a one-line change.
    offline = catch_up.no_crawl(catch)
    best = {} if offline else discover_listing_best(conn, source, base, limit=limit)
    id_to_key = {}
    for key, e in best.items():
        m = ID_HREF_RE.search(e["href"])
        if m:
            id_to_key[m.group(1)] = key
    title_to_date = {title: date for (date, title) in best}

    extra_ids = set()
    if prefix_crawl and not offline:
        extra_ids = discover_prefix_ids(source, base) - set(id_to_key)

    work = list(best.items())
    extra_ids_list = sorted(extra_ids)
    if limit:
        work = work[:limit]
        extra_ids_list = extra_ids_list[:limit]

    # Both work lists are sized before Stats so the heartbeat's percentage and
    # ETA span the whole run, not just the listing loop below.
    stats = Stats(source, total=len(work) + len(extra_ids_list))

    for (date, title), e in work:
        m = ID_HREF_RE.search(e["href"])
        url = detail_url(base, m.group(1)) if m else e["href"]

        existing = stored_grade(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        parsed, confirmed = (
            ({}, True)
            if not m
            else archive.fetch_detail_snapshot(conn, session, url, parse_detail)
        )

        if parsed.get("body"):
            if existing == "teaser":
                # No title=/date=: the listing page's values are better than
                # the detail page's, so only the body is upgraded.
                storage.upgrade_release(
                    conn,
                    url,
                    detail_id=parsed["detail_id"],
                    body=parsed["body"],
                    body_html=parsed["body_html"],
                    grade="full",
                    commit=False,
                )
                stats.upgraded()
            else:
                storage.store_release(
                    conn,
                    source,
                    url,
                    title=title,
                    date=date,
                    body=parsed["body"],
                    body_html=parsed["body_html"],
                    detail_id=parsed["detail_id"],
                    commit=False,
                )
                stats.added()
            conn.commit()
            continue

        # Before the teaser check, not after: a probe that failed on the
        # network is not a verdict on this row. Reporting an already-stored
        # teaser as `skipped` here would file a retryable failure under
        # "already as good as it gets", hiding exactly the rows a rerun exists
        # to pick up.
        if not confirmed:
            stats.uncertain()
            continue

        if existing == "teaser":
            stats.skipped()
            continue

        if e["teaser"]:
            storage.store_release(
                conn,
                source,
                url,
                title=title,
                date=date,
                body=e["teaser"],
                body_html=e["teaser_html"] or None,
                grade="teaser",
            )
            stats.teaser()
        else:
            stats.dead()

    for hexid in extra_ids_list:
        url = detail_url(base, hexid)

        if already_stored(conn, url):
            stats.skipped()
            continue

        parsed, confirmed = archive.fetch_detail_snapshot(
            conn, session, url, parse_detail
        )
        if not parsed.get("body") or not parsed.get("title"):
            if confirmed:
                stats.dead()
            else:
                stats.uncertain()
            continue

        date = title_to_date.get(parsed["title"], "")
        storage.store_release(
            conn,
            source,
            url,
            title=parsed["title"],
            date=date,
            body=parsed["body"],
            body_html=parsed["body_html"],
            detail_id=parsed["detail_id"],
        )
        stats.added()

    stats.summary(conn)
    catch_up.run(
        conn, source, catch, parser=parse_detail, session=session, twins_too=True
    )
    conn.close()


def scrape(
    limit: int | None = None,
    prefix_crawl: bool = True,
    sources: list | None = None,
    catch: dict | None = None,
) -> None:
    for source in sources or DOMAINS:
        scrape_domain(
            source, DOMAINS[source], limit=limit, prefix_crawl=prefix_crawl, catch=catch
        )
