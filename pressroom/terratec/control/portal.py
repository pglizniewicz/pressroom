"""Scraper for TerraTec's "Presse @ TerraTec" PHP-Nuke portals ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots since
neither site exists any more.

The same PHP-Nuke install ran twice, once per language:
  - pressen.terratec.net (English) -> source terratec_pressen
  - pressde.terratec.net (German)  -> source terratec_pressde
Identical markup, identical URL scheme, so one parser covers both and this
script does both portals per run - the same shape as maudio/control/pressdb.py
and maudio/control/media_pr.py, which likewise cover several instances of one
system. (These were two near-identical files until the shared parser drifted:
the German copy grew a fix the English copy never got, see END_MARKERS.)

Content likely overlaps with the older static terratec.net/press/pressemit/
archive, but the URL schemes are unrelated so they can't share a dedup key.
Kept as distinct sources, same as creative/creative_gnw.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from pressroom.capture.control.politeness import SLEEP
from pressroom.release.control.storage import already_stored
from pressroom.release.control.storage import stored_grade
from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail, Entry

PORTALS = {
    "terratec_pressen": "http://pressen.terratec.net:80/",
    "terratec_pressde": "http://pressde.terratec.net:80/",
}

SID_RE = re.compile(r"sid=(\d+)(?:&|$)")
TITLE_TAG_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+?)\s*::\s*Press")
TITLE_TAG_MONTH_RE = re.compile(r"([A-Za-z]+\s+\d{4})\s*-\s*(.+?)\s*::\s*Press")

# The same "{date} - {title}" shape as above, read off an `a.pn-title` link on a
# category listing instead of out of a <title>, so there is no site name to stop
# at. Used only by extract_teasers, and it was left behind in the file that
# function came from when that file was deleted on 2026-08-25 - so the category
# channel raised NameError on every run from then until this was restored.
TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(.+)")

# Everything from the site name onward in a <title>. What is left in front of it
# is the article heading - usually "{date} - {title}", but one capture carries
# the headline with no date on it at all, and the two regexes above only match
# the dated form. Splitting on the suffix instead of requiring the prefix keeps
# that row's title, and still yields "" on the captures where PHP-Nuke rendered
# the page skeleton with no article in it - there the <title> *starts* with the
# site name, so nothing precedes the split.
SITE_SUFFIX_RE = re.compile(r"\s*::\s*Press")

# The printer-friendly view (print.php?sid=N) is a different template: no site
# name in the <title>, and the heading in a font.print-title instead. Same
# "{date} - {title}" text, so one regex reads both.
PRINT_TITLE_SEL = "font.print-title"
HEADING_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4}|[A-Za-z]+\s+\d{4})\s*-\s*(.+)$", re.S)

# The sidebar box after the article body isn't labeled consistently across
# captures ("Links!" in German templates, "Related links" seen on the English
# portal's markup bleeding through some snapshots) - cut at whichever comes
# first. Both portals need both markers, which is exactly what the two
# separate copies of this scraper used to get wrong.
END_MARKERS = ["Links!", "Related links"]
END_MARKER_RE = re.compile("|".join(re.escape(m) for m in END_MARKERS))


def list_articles(prefix: str) -> list[str]:
    """Dedup by sid: mode/order/thold don't affect content, first-seen wins."""
    by_sid = {}
    for entry in archive.list_snapshots_or_exit(prefix):
        url = entry["original"]
        if "name=News" not in url or "file=article" not in url:
            continue
        m = SID_RE.search(url)
        if not m:
            continue
        sid = int(m.group(1))
        by_sid.setdefault(sid, url)
    return [by_sid[sid] for sid in sorted(by_sid)]


def _cut_at_first_marker(text: str) -> str:
    cut = len(text)
    for marker in END_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()


def article_body(soup, heading: str) -> tuple[str, str | None]:
    """(body, body_html) for one portal article, from the DOM.

    The same three cuts the text surgery above makes, done on elements instead
    of on a string:

      container  the <td> carrying the most text. Calibrated over all 186
                 cached portal captures: 100% land within 0.85-1.25 of the
                 previously stored body, against 150/186 for the obvious
                 `td[valign="top"][width="85%"]` selector - the attributes are
                 not on every capture, the size is.
      heading    the "{date} - {title}" line, which the container includes and
                 the stored body does not (median coverage was 1.05, and this
                 plus the link block is the 5%).
      tail       everything from the first END_MARKERS element onward.

    Returns ("", None) when there is no container, so the caller can fall back
    to the text path rather than store an empty body.
    """
    td = richtext.densest(soup, "td")
    if td is None:
        return "", None

    work = BeautifulSoup(str(td), "html.parser")

    def norm(t):
        return " ".join(t.split())

    target = norm(heading)

    for tag in work.find_all(True):
        if tag.find(True) is None and norm(tag.get_text(" ", strip=True)) == target:
            tag.decompose()
            break

    richtext.cut_from(work, END_MARKER_RE)
    return richtext.extract(work)


def parse_snapshot(content: bytes) -> Detail:
    # cp1252 stated, never sniffed - see pressemit.py's parse_snapshot for
    # why (48 rows across pressde/pressen were stored with the cp1252
    # punctuation range as C1 control characters until the repair landed).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")

    # The heading anchor's CSS class isn't present in every capture (some
    # crawls render it as plain bold text instead) - the <title> tag is
    # present and consistently formatted in all captures, so use that.
    title_full = soup.title.get_text(strip=True) if soup.title else ""
    m = TITLE_TAG_RE.match(title_full)
    date_fmt = "%Y-%m-%d"
    if not m:
        # A handful of articles only carry month/year precision, e.g. "June 2007 - Title"
        m = TITLE_TAG_MONTH_RE.match(title_full)
        date_fmt = "%Y-%m"

    date_str = ""
    title = ""
    if m:
        date_str, title = m.group(1), m.group(2).strip()
    else:
        # Neither dated form matched. Two templates still carry the heading:
        # print.php in its own element, and the ordinary article view in a
        # <title> that just has no date on it. Both are read the same way -
        # take the heading text, then split a date prefix off it if there is
        # one. A month-name date the parser cannot read (German "Mai 2007")
        # leaves date empty and the title intact, exactly as the dated paths
        # above already do.
        printed = soup.select_one(PRINT_TITLE_SEL)
        heading_text = (
            printed.get_text(" ", strip=True)
            if printed
            else SITE_SUFFIX_RE.split(title_full, 1)[0].strip()
        )
        h = HEADING_RE.match(heading_text)
        if h:
            date_str, title = h.group(1), h.group(2).strip()
            date_fmt = "%Y-%m-%d" if "." in date_str else "%Y-%m"
        else:
            title = heading_text

    date = ""
    body = ""
    body_html = None
    if title:
        if date_str:
            date = iso_date(date_str, dayfirst=True, fmt=date_fmt)

        heading = f"{date_str} - {title}" if date_str else title
        body, body_html = article_body(soup, heading)
        if not body:
            # No container in this capture - keep the old text surgery rather
            # than store nothing.
            text = soup.get_text(" ", strip=True)
            parts = text.split(heading)
            body = _cut_at_first_marker(parts[-1]) if len(parts) > 1 else text
            body_html = None

    return {"title": title, "date": date, "body": body, "body_html": body_html}


# The yearly category listings (file=index&catid=N&allstories=1) - one archived
# capture per category, id_ baked in, matching every other hardcoded-url table
# here. This is the channel that finds sids the timemap prefix search never
# surfaced at all, and it is irreplaceable archaeology: nobody is going to redo
# the sweep that found these twelve captures.
CATEGORY_PAGES = [
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20031115085312id_/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=13&topic=&allstories=1&menu=300",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20031115084858id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=14&topic=&allstories=1&menu=304",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20031213100854id_/http://pressde.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&amp",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20041010061159id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&amp",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20070730011217id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20070730010823id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=20&topic=&allstories=1",
    ),
    (
        "http://pressde.terratec.net:80/",
        "terratec_pressde",
        "https://web.archive.org/web/20070730010351id_/http://pressde.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2",
    ),
    (
        "http://pressen.terratec.net:80/",
        "terratec_pressen",
        "https://web.archive.org/web/20031001235452id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=15&topic=&allstories=1&menu=2",
    ),
    (
        "http://pressen.terratec.net:80/",
        "terratec_pressen",
        "https://web.archive.org/web/20041010063929id_/http://pressen.terratec.net:80/modules.php?op=modload&name=News&file=index&catid=16&topic=&allstories=1&amp",
    ),
    (
        "http://pressen.terratec.net:80/",
        "terratec_pressen",
        "https://web.archive.org/web/20070808232502id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=17&topic=&allstories=1&menu=2&menu=307",
    ),
    (
        "http://pressen.terratec.net:80/",
        "terratec_pressen",
        "https://web.archive.org/web/20070808232455id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=18&topic=&allstories=1&menu=2&menu=309",
    ),
    (
        "http://pressen.terratec.net:80/",
        "terratec_pressen",
        "https://web.archive.org/web/20070630063657id_/http://pressen.terratec.net/modules.php?op=modload&name=News&file=index&catid=19&topic=&allstories=1&menu=2",
    ),
]


def extract_teasers(content: bytes) -> dict[str, Entry]:
    """Return {sid: (date, title, teaser_text)} for every article on this page."""
    # cp1252 stated, never sniffed: these pages predate UTF-8 and declare no
    # charset, so left to guess bs4 read them as ISO-8859-1 and stored the
    # cp1252 punctuation range as C1 control characters (see
    # the encoding repair, which had to undo exactly that).
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
                BeautifulSoup(content_html, "html.parser")
            )

        teasers[sid] = (date, title, teaser, teaser_html)

    return teasers


def stored_sids(conn, source: str) -> set[int]:
    return {
        int(m.group(1))
        for url in storage.source_urls(conn, source)
        if (m := SID_RE.search(url))
    }


def print_only_sids(prefix: str, have: set) -> list[int]:
    """Sids whose print.php view is archived, minus the ones already stored.

    The third discovery channel. Some articles have a working capture of
    `print.php?sid=N` and none of the article page itself, and this reads them
    out of the same timemap response `list_articles` already asks for - so it
    costs no extra CDX query.
    """
    sids = set()
    for entry in archive.list_snapshots_or_exit(prefix):
        url = entry["original"]
        if "/print.php" not in url:
            continue
        if m := SID_RE.search(url):
            sids.add(int(m.group(1)))
    return sorted(sids - have)


def recover_article(
    conn, session, prefix: str, sid
) -> tuple[tuple[str, Detail, str] | None, bool]:
    """((timestamp, parsed), confirmed) for one sid: the article page first, then
    its print view.

    `confirmed` is False when any attempt hit a network error, so the caller must
    not record a dead end - a probe failure is not a verdict. Both urls are
    parsed with this module's own parse_snapshot: measured over all 81 cached
    print.php captures, it handles the print template, and the separate
    print-only parser this replaces differed by 1-4 characters of whitespace.
    """
    uncertain = False
    for url in (
        f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}",
        f"{prefix}print.php?sid={sid}",
    ):
        try:
            found = archive.get_latest_working_snapshot(url)
        except Exception:
            uncertain = True
            continue
        if not found:
            continue
        snapshot_url, timestamp = found
        try:
            content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception:
            uncertain = True
            continue
        if parsed.get("title"):
            return (timestamp, parsed, snapshot_url), True
    return None, not uncertain


def from_categories(conn, session, source: str, prefix: str) -> None:
    """The category-listing channel: every sid that appears on a yearly listing,
    recovered in full where possible and stored as its teaser where not.

    A teaser-grade row is retried on every run and upgraded in place the moment
    the full article can be reached - which is why this was never a one-shot. A
    network error is reported `uncertain`, never allowed to lock in a teaser.
    """
    teasers = {}
    for page_prefix, page_source, url in CATEGORY_PAGES:
        if page_source != source:
            continue
        try:
            content = archive.fetch_snapshot(conn, session, url, timeout=20)
        except Exception as e:
            print(f"\n  ERROR fetching category page: {e}")
            continue
        teasers.update(extract_teasers(content))
    if not teasers:
        return

    print(f"[{source}] {len(teasers)} sids z rocznych listingow", flush=True)
    stats = Stats(source, total=len(teasers))
    for sid, (t_date, t_title, t_text, t_html) in teasers.items():
        article_url = f"{prefix}modules.php?op=modload&name=News&file=article&sid={sid}"
        existing = stored_grade(conn, article_url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        recovered, confirmed = recover_article(conn, session, prefix, sid)
        if recovered:
            timestamp, parsed, snapshot_url = recovered
            if existing == "teaser":
                storage.upgrade_release(
                    conn,
                    article_url,
                    detail_id=timestamp,
                    title=parsed["title"],
                    date=parsed["date"],
                    body=parsed["body"],
                    body_html=parsed["body_html"] or None,
                    grade="full",
                    origin_url=snapshot_url,
                )
                stats.upgraded()
            else:
                storage.store_release(
                    conn,
                    source,
                    article_url,
                    title=parsed["title"],
                    date=parsed["date"],
                    body=parsed["body"],
                    body_html=parsed["body_html"] or None,
                    detail_id=timestamp,
                    origin_url=snapshot_url,
                )
                stats.added()
            continue

        # Ordered before the teaser check on purpose: a failed probe is not a
        # verdict, so an already-stored teaser is `uncertain` (a rerun retries
        # it) rather than `skipped`.
        if not confirmed:
            stats.uncertain()
            continue
        if existing == "teaser":
            stats.skipped()
            continue
        if t_text:
            storage.store_release(
                conn,
                source,
                article_url,
                title=t_title,
                date=t_date,
                body=t_text,
                body_html=t_html or None,
                grade="teaser",
            )
            stats.teaser()
        else:
            stats.dead()

    stats.summary(conn)


def from_print_views(conn, session, source: str, prefix: str) -> None:
    """The print.php channel: sids whose only archived page is the print view."""
    sids = print_only_sids(prefix, stored_sids(conn, source))
    if not sids:
        return
    print(f"[{source}] {len(sids)} sids tylko w widoku print.php", flush=True)
    stats = Stats(source, total=len(sids))
    for sid in sids:
        print_url = f"{prefix}print.php?sid={sid}"
        try:
            found = archive.get_latest_working_snapshot(print_url)
        except Exception as e:
            print(f"\n  ERROR probing {print_url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found
        try:
            content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue
        if not parsed.get("title"):
            stats.dead()
            continue
        # Stored under the print url, because that is the page that existed:
        # the article url has no capture, and minting a row under an address
        # nobody has seen is what SYNTHETIC_URL_SOURCES exists to warn about.
        if storage.store_release(
            conn,
            source,
            print_url,
            title=parsed["title"],
            date=parsed["date"],
            body=parsed["body"],
            body_html=parsed["body_html"] or None,
            detail_id=timestamp,
            origin_url=snapshot_url,
        ):
            stats.added()
        else:
            stats.skipped()
    stats.summary(conn)


def scrape_portal(
    source: str, prefix: str, limit: int = None, catch: dict = None
) -> None:
    conn = connection.connect()
    session = requests.Session()

    print(f"[{source}] Listing archived articles under {prefix}", flush=True)
    urls = [] if catch_up.no_crawl(catch) else list_articles(prefix)
    if limit:
        urls = urls[:limit]
    print(f"[{source}] {len(urls)} candidate articles", flush=True)

    stats = Stats(source, total=len(urls))

    for url in urls:
        if already_stored(conn, url):
            stats.skipped()
            continue

        try:
            found = archive.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if storage.store_release(
            conn,
            source,
            url,
            title=parsed["title"],
            date=parsed["date"],
            body=parsed["body"],
            body_html=parsed["body_html"],
            detail_id=timestamp,
        ):
            stats.added()

    stats.summary(conn)

    # Two more discovery channels, both of which used to be their own
    # "backfill" script: the yearly category listings, and the sids whose only
    # archived page is the print view.
    if not catch_up.no_crawl(catch):
        from_categories(conn, session, source, prefix)
        from_print_views(conn, session, source, prefix)

    catch_up.run(conn, source, catch, parser=parse_snapshot, session=session)
    conn.close()


def scrape(limit: int = None, catch: dict = None) -> None:
    for source, prefix in PORTALS.items():
        scrape_portal(source, prefix, limit=limit, catch=catch)
