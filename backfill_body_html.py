#!/usr/bin/env python3
"""Re-extract `body` and fill `body_html` for rows stored before richtext.py.

Every scraper here used to finish with `soup.get_text(" ", strip=True)`, so
the whole corpus is stored as one flat blob per release: no paragraphs, no
bullet lists, no headings, no links. richtext.py fixes that going forward;
this pass fixes what is already in the database.

Two modes, because the corpus splits cleanly in two:

  (default)     the live sources - intel, amd, creative, creative_gnw, 3619
                rows, 61% of the corpus. These were never cached (the
                page_cache convention was written for archive.org and quietly
                not applied here), so the page has to be fetched once. It is
                cached on the way through, so this is the last time.

  --seed-cache  fetch each addressable row's own capture into page_cache and
                do nothing else - no parsing, no write to `releases`. The step
                that has to come first when a parser is about to be redesigned:
                these sources' captures mostly predate the cache (terratec_de
                1 of 126, midiman_de 0 of 64), so without it there is nothing
                to design a DOM selector against, and every iteration would
                cost another crawl. Needs no parser, so it runs for sources
                that have none yet - which is exactly why --wayback cannot do
                this job: it skips them before it fetches anything.

  --wayback     the wayback sources whose row already carries a 14-digit
                capture timestamp but whose capture is not in page_cache:
                fetch that one capture and reparse it. Closes the gap between
                the other two modes - such a row is invisible to --from-cache
                (nothing to parse) and its own scraper skips it (it already
                is graded 'full', so its own scraper calls it done).
                Caches on the way through, so it is a one-time cost.

  --retext      recompute `body` from the `body_html` already stored, for
                every row that has one. No network and no per-source parser:
                `body` is by definition richtext.to_text(body_html), so a fix
                to the text renderer alone costs nothing to apply. Use this
                after changing to_text(), not after changing clean().

  --from-cache  the Wayback sources. Where the row's capture is already in
                page_cache the reparse is free - no network at all - and this
                mode does nothing else, so it can be run any time regardless
                of what archive.org is doing. Only sources whose parser finds
                the article in the DOM are covered; see CACHED_PARSERS.

Idempotent and resumable, like every other pass here: `body_html IS NULL` is
itself the cursor, and a network error writes nothing, so the row stays open
for a full retry on the next run. Reported as `uncertain` (?), never `dead` -
a refused connection is not a verdict.

Usage:
  python backfill_body_html.py                       # all live sources
  python backfill_body_html.py --source creative_gnw --limit 1
  python backfill_body_html.py --from-cache          # wayback, no network
"""

import argparse
import functools
import re

import requests

import wayback
import db
import q4
import richtext
import scrape_creative
import scrape_globenewswire_creative
import scrape_maudio_news
import backfill_terratec_de_and_net_gaps
import scrape_midiman
import scrape_midiman_de
import scrape_midiman_news
import scrape_terratec
import scrape_terratec_early
import scrape_terratec_new
import scrape_terratec_portal
import scrape_midiman_pressdb
import scrape_soundonsound
from scrape_midiman_pressdb import is_html_detail
from progress import Stats

# source -> the scraper function that turns its detail URL into (body, html).
# Each one goes through fetch.fetch_cached, so a rerun after another parser
# change will be free.
LIVE_FETCHERS = {
    "intel": q4.fetch_body,
    "amd": q4.fetch_body,
    "creative": scrape_creative.fetch_body,
    "creative_gnw": scrape_globenewswire_creative.fetch_body,
    "soundonsound": scrape_soundonsound.fetch_body,
}

# source -> how to build its HTTP session. Only GlobeNewswire needs anything
# other than requests, and it needs it badly - see its make_session docstring.
# One session per source rather than one per run, so an unreachable host
# cannot take the others down with it.
SESSION_FACTORIES = {
    "creative_gnw": scrape_globenewswire_creative.make_session,
}

# source -> the scraper's own detail parser, for the cached-reparse mode.
# The scraper function, not a selector copied out of it: finding the article
# in these captures is several paragraphs of per-CMS knowledge (a fallback
# chain of three classes here, "the whole page minus title/script/style"
# there), and a second copy of that in this file is precisely the kind of
# duplicate that goes stale silently.
#
# A source absent from this map is SKIPPED, never reparsed from soup.body:
# the whole page carries that CMS's nav, sidebar and footer, so the "upgrade"
# would replace a clean body with page chrome. The sources still missing here
# are the ones whose parser cuts the body out of the page's *text* with marker
# strings rather than out of the DOM - terratec (2002), terratec_portal,
# terratec_early, midiman (2001 GoLive) and midiman_de's inline blocks. Those
# need their parser redesigned, not a line added here.
CACHED_PARSERS = {
    "midiman_com_pressdb": scrape_midiman_pressdb.parse_detail,
    "midiman_net_pressdb": scrape_midiman_pressdb.parse_detail,
    "midiman_net_media_news": scrape_midiman_news.parse_detail,
    "midiman_com_media_news": scrape_midiman_news.parse_detail,
    "maudio_com_media_news": scrape_midiman_news.parse_detail,
    "midiman_couk_news": scrape_midiman_news.parse_detail,
    "maudio_com_news": scrape_maudio_news.parse_detail,
    "midiman_de": scrape_midiman_de.parse_generic_page,
    # Redesigned 2026-08-21 from text surgery to DOM containers. The selectors
    # were calibrated over every cached capture (calibrate_containers.py), not
    # picked from one sample - see richtext.densest for what that changed.
    "terratec": scrape_terratec.parse_snapshot,
    "terratec_de": backfill_terratec_de_and_net_gaps.parse_de_snapshot,
    "midiman_com": scrape_midiman.parse_snapshot,
    "midiman_net": scrape_midiman.parse_snapshot,
    "terratec_pressde": scrape_terratec_portal.parse_snapshot,
    "terratec_pressen": scrape_terratec_portal.parse_snapshot,
    "terratec_new_de": scrape_terratec_new.parse_detail,
    "terratec_new_en": scrape_terratec_new.parse_detail,
}


def safe_to_write(old_body: str, new_body: str) -> tuple:
    """(ok, why). The gate every re-extraction passes before it is stored.

    Refuses exactly one thing: text disappearing from the MIDDLE of a body.
    Everything else a correct re-extraction does is allowed, and each was
    checked against real captures before being let through:

      removed at the edges  a container that excludes nav. Over 215
                            terratec/terratec_de captures the removed prefixes
                            are image alt text and URL paths, and not one row
                            lost a suffix.
      added                 a teaser-grade row recovering its full article
                            (#3769 went 216 -> 2314 characters), or the
                            character test tripping over a join that changed
                            no word at all.

    A middle deletion has no benign explanation here, so it is held back and
    reported instead of written.
    """
    d = text_delta(old_body, new_body)
    if d["kept"] or d["edges_only"]:
        return True, ""

    # Two middle-loss cases are known-good, and both were confirmed by a
    # word-level diff of the actual rows before being written down here:
    #
    #  - the old body was teaser-grade and the new one is the real article.
    #    terratec_pressen sid=204 held 216 characters of the portal's own
    #    comment widget ("Login Create account Comments Threshold 1 0 1 2 3
    #    4 5 No comments...") where the release should have been; the DOM pass
    #    returns 2314 characters of press release. Refusing that would be
    #    protecting nonsense.
    #  - a loss under 2% of the body. Every instance measured was a URL path
    #    or image alt text sitting mid-page: terratec drbox1.htm loses exactly
    #    `http wm terrafinal presse pressemit 128i_pci` and nothing else.
    if d["old_len"] < 400 and d["new_len"] > d["old_len"] * 2:
        return True, ""
    if 0 < d["removed"] <= 0.02:
        return True, ""
    return False, f"ubytek w srodku ({d['removed']:.0%})"


# A row is "relocated" when the capture its body came from is not a capture of
# its own url. Spelled out here rather than compared in Python because pending()
# has to select on it; it is the same address wayback.snapshot_url builds.
_RELOCATED_SQL = ("AND c.origin_url <> 'https://web.archive.org/web/' || "
                  "r.detail_id || 'id_/' || r.url ")


def pending(conn, sources, limit=None, force=False, relocated=False):
    """(url, source, detail_id) for every row still stored as a flat blob, or
    every row of those sources when `force` - which is cheap now that every
    page fetched since 2026-08-21 is in page_cache, so a re-extraction after a
    parser fix costs no requests at all.

    `relocated` narrows it to the rows whose body came off a *different* page
    than their own url (body_origin records which), i.e. exactly the rows a
    derived capture key cannot reach. Worth its own selector because
    "re-extract those" is a precise job: --force alone would rewrite every
    other row of the source in the same run.
    """
    placeholders = ",".join("?" * len(sources))
    sql = "SELECT r.url, r.source, r.detail_id FROM releases r "
    if relocated:
        sql += "JOIN body_origin c ON c.url = r.url "
    sql += f"WHERE r.source IN ({placeholders}) "
    if not force:
        sql += "AND r.body_html IS NULL "
    if relocated:
        sql += _RELOCATED_SQL
    sql += "ORDER BY r.source, r.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, tuple(sources)).fetchall()


# Magic bytes, not the extension. CLAUDE.md says this about attachments and
# it is right: a .php URL on these CMSes routinely served a PDF or a Word
# document. Skipping on the URL alone let 23 rows have a raw '%PDF-1.3 %âãÏÓ 6
# 0 obj...' stored as their body, overwriting text pdftotext had extracted
# correctly - and it read as encoding damage rather than as the data loss it
# was, because a decoded PDF stream is full of C1 characters.
_BINARY_MAGIC = (b"%PDF", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"PK\x03\x04",
                 b"\x1f\x8b", b"GIF8", b"\x89PNG", b"\xff\xd8\xff")


def _wordchars(text: str) -> str:
    """Everything that is not a word character, dropped. Comparing these
    strings is deliberately blind to exactly the things a re-extraction is
    supposed to change - indentation, line wrapping, the bullets and ordinals
    to_text() prepends - and to the joins it causes, where `8 th` becomes
    `8th` and `unlocked 1` becomes `unlocked1` because a <sup> stopped being
    padded with spaces. A word-multiset comparison flags all of those as loss;
    a character-sequence comparison does not.

    Ordered-list markers are stripped first: to_text() prepends "1. ", "2. "
    to <ol> items, and those digits are invented characters that would
    otherwise read as the parser having made text up."""
    text = re.sub(r"(?m)^\s*\d+\.\s", " ", text or "")
    return re.sub(r"\W+", "", text)


def _is_subsequence(small: str, big: str) -> bool:
    it = iter(big)
    return all(ch in it for ch in small)


def text_delta(old: str, new: str) -> dict:
    """How a re-extraction changed a body, in terms strong enough to gate on.

    Two directions, because the two failure modes are opposite:

      kept   - every character of the old body still appears, in order, in the
               new one. False means text was LOST.
      clean  - every character of the new body came from the old one, in order.
               False means text was INVENTED - the container grabbed a sidebar,
               or the parser latched onto the wrong element.

    Which of the two must hold depends on the source, and that is not a detail
    to paper over. For a parser that cut markers out of an already-clean body
    (terratec_portal, midiman_de inline) the new body must keep everything.
    For one whose body was the WHOLE PAGE including nav (terratec, terratec_de,
    midiman 2001), the redesign is supposed to drop text, so `kept` will be
    false by design and `clean` is the check that means anything.

    But `kept=False, clean=True` is the signature of BOTH "nav was correctly
    dropped" and "a paragraph went missing" - the pair alone cannot tell them
    apart. `edges_only` is what separates them: True when the new body is a
    contiguous run of the old one, i.e. material came off the front and the
    back and nothing was taken out of the middle. Nav and footer live at the
    edges; a lost paragraph does not. For the whole-page sources that is the
    check with actual teeth.
    """
    o, n = _wordchars(old), _wordchars(new)
    return {
        "kept": _is_subsequence(o, n),
        "clean": _is_subsequence(n, o),
        "edges_only": bool(n) and n in o,
        "old_len": len(o),
        "new_len": len(n),
        "removed": (len(o) - len(n)) / len(o) if o else 0.0,
    }


def looks_like_html(content: bytes) -> bool:
    """Whether these bytes are worth handing to an HTML parser at all.

    BeautifulSoup never refuses input: give it a PDF and it returns a document
    whose get_text() is the binary decoded as characters. There is no parse
    error to catch, so the check has to happen before the parse.
    """
    return bool(content) and not content.lstrip()[:8].startswith(_BINARY_MAGIC)


# Sources whose `releases.url` was minted by the scraper rather than observed
# on the web. scrape_midiman_de builds `/press/{slugified-title}-{date}` for a
# release that only ever existed *inside* a listing page, and its detail_id is
# that listing's capture timestamp - so the reconstructed capture URL is a page
# that never existed and 404s every time. 64 rows, 64 guaranteed-wasted
# requests against a throttling archive.
#
# Their content is recoverable, just not this way: the listing captures are
# already in page_cache, so the fix is to re-walk those, not to fetch per row.
SYNTHETIC_URL_SOURCES = {"midiman_de"}


def capture_url(detail_id: str, url: str) -> str:
    """The page_cache key for a capture of this row's own url, or "" when the
    detail_id is not a capture timestamp at all (the live sources' platform ids,
    or no reference).

    Both halves belong to wayback.py; this is the two of them in the order every
    caller here needs them, which is why it stays a named function rather than
    being spelled out at four call sites. It is a *derivation* though, and only
    right when the capture is of the row's own url - prefer origin_key(), which
    asks the database first."""
    return wayback.snapshot_url(detail_id, url) if wayback.is_timestamp(detail_id) else ""


def origin_key(conn, url: str, detail_id: str) -> str:
    """The page_cache key for this row's body, recorded if we know it.

    body_origin answers for 1522 rows, including the 149 whose text came off a
    *different* page - a listing, a print view, another release's page - which a
    derivation from (detail_id, url) can only get wrong. It also covers the
    attachment rows, whose real capture timestamp differs from the listing
    timestamp their detail_id holds. Falls back to the derivation so a row the
    provenance pass has not reached yet still works.
    """
    row = conn.execute("SELECT origin_url FROM body_origin WHERE url = ?",
                       (url,)).fetchone()
    return row[0] if row else capture_url(detail_id, url)


def run_live(conn, sources, limit=None, force=False) -> None:
    rows = pending(conn, sources, limit, force)
    stats = Stats(total=len(rows))
    print(f"[body_html] {len(rows)} rows to re-extract from "
          f"{', '.join(sorted(sources))}", flush=True)

    sessions = {}
    for url, source, _ in rows:
        if source not in sessions:
            sessions[source] = SESSION_FACTORIES.get(source, requests.Session)()
        try:
            body, body_html = LIVE_FETCHERS[source](conn, sessions[source], url)
        except Exception as e:
            print(f"\n    {url}: {e}")
            stats.uncertain()
            continue
        if not body_html:
            # The page came back but the container was not in it - a layout
            # change or a redirect to a landing page. Not a network problem,
            # but not something to overwrite a good body with either.
            stats.dead()
            continue
        db.upgrade_release(conn, url, body=body, body_html=body_html)
        stats.upgraded()

    stats.summary()


def _chars_no_bullets(text: str) -> str:
    """Every non-whitespace character, bullets dropped. Whitespace *placement*
    is the one thing this comparison forgives, which is the same call
    text_delta makes and for the same reason: `8 th` -> `8th` and
    `GeForce \u2122` -> `GeForce\u2122` are the parser getting it right, not text
    changing."""
    return "".join((text or "").replace("\u2022", "").split())


def strict_same_text(old_body: str, new_body: str) -> tuple:
    """(ok, why) for the --relocated path: the ONLY difference allowed is
    whitespace placement and the `\u2022` markers to_text() puts on list items.

    safe_to_write is the wrong gate there and this cost 64 rows before it was
    understood. A relocated row's capture is of a *listing*, a print view, or
    another release's page; handing such a capture to a whole-page parse_detail
    yields the longest article on it, which for a listing is somebody else's
    release. safe_to_write let that through - it refuses text lost from the
    *middle*, and wholesale replacement by a longer text reads as the
    teaser-to-article upgrade it explicitly allows. Five midiman_de rows ended
    up sharing one body that belonged to none of them.

    Character-sequence equality cannot be fooled that way: another release's
    text is a different sequence, so the only writes this admits are the ones
    where the current parser reproduces exactly what is stored, character for
    character, with only spaces moved.
    """
    return (_chars_no_bullets(old_body) == _chars_no_bullets(new_body),
            "inny ciag znakow")


def run_cached(conn, sources, limit=None, force=False, relocated=False) -> None:
    """Reparse from page_cache only. Never touches the network, so a row whose
    capture was never cached is `skipped`, not `uncertain` - there is nothing
    here for a rerun to retry until the capture itself is fetched."""
    rows = pending(conn, sources, limit, force, relocated)
    stats = Stats(total=len(rows))
    print(f"[body_html] {len(rows)} rows pending; reparsing whatever "
          f"page_cache already holds", flush=True)

    unported = sorted({s for _, s, _ in rows if s not in CACHED_PARSERS})
    if unported:
        print(f"  no DOM-based parser, skipping: {', '.join(unported)}", flush=True)

    stored = dict(conn.execute("SELECT url, COALESCE(body, '') FROM releases"))
    held = []

    for url, source, detail_id in rows:
        if source not in CACHED_PARSERS:
            stats.skipped()
            continue
        key = origin_key(conn, url, detail_id)
        if (not relocated and source in LISTING_SOURCES
                and key and key != capture_url(detail_id, url)):
            # The recorded origin is a page holding many releases, and
            # CACHED_PARSERS[source] parses a whole page: it would hand back the
            # biggest article on that listing, which belongs to another row.
            # --listings is the url-keyed pass that can tell them apart, and
            # --relocated has a gate strict enough not to care.
            stats.skipped()
            continue
        row = conn.execute("SELECT content FROM page_cache WHERE url = ?",
                           (key,)).fetchone() if key else None
        if row is None:
            stats.skipped()
            continue
        if not looks_like_html(row[0]):
            stats.dead()
            continue
        parsed = CACHED_PARSERS[source](row[0])
        body, body_html = parsed.get("body") or "", parsed.get("body_html") or ""
        if not body_html:
            # The capture is cached but this parser finds nothing in it - a
            # template variant it does not cover. Not retryable by refetching.
            stats.dead()
            continue
        gate = strict_same_text if relocated else safe_to_write
        ok, why = gate(stored.get(url, ""), body)
        if not ok:
            held.append((url, why))
            stats.skipped()
            continue
        db.upgrade_release(conn, url, body=body, body_html=body_html)
        # Record the address this text was actually read from, in the same
        # transaction as the text. Not clear_body_origin, which the first cut
        # of this had: the key this mode reads *is* the recorded entry, so
        # clearing it deletes a true statement and takes the row's archive link
        # with it (38 rows lost their link that way before the count gave it
        # away). Recording it again is a no-op on the common path and the point
        # on any path where the key came from elsewhere.
        db.record_body_origin(conn, url, key)
        stats.upgraded()

    stats.summary()
    if held:
        print(f"\nWSTRZYMANE przez bramke: {len(held)} - nic nie zapisano")
        for url, why in held[:20]:
            print(f"  {why:26} {url}")


def run_retext(conn, limit=None) -> None:
    """Re-derive `body` from the stored `body_html`. Rows whose text does not
    change are `skipped`, so a rerun right after one prints all dots."""
    sql = "SELECT url, body, body_html FROM releases WHERE body_html IS NOT NULL ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = Stats(total=len(rows))
    print(f"[body_html] re-deriving body for {len(rows)} rows with stored HTML",
          flush=True)

    for url, body, body_html in rows:
        text = richtext.to_text(body_html)
        if text == (body or ""):
            stats.skipped()
            continue
        db.upgrade_release(conn, url, body=text)
        stats.upgraded()

    stats.summary()


def _midiman_de_entries(conn):
    """(url -> entry) for midiman_de, out of every cached midiman.de capture."""
    out = {}
    for cap_url, content in conn.execute(
            "SELECT url, content FROM page_cache WHERE url LIKE '%midiman.de%'"):
        m = re.search(r"/web/(\d{14})id_/", cap_url)
        try:
            entries = scrape_midiman_de.parse_page(
                content, "http://www.midiman.de/", m.group(1) if m else None)
        except Exception as e:
            print(f"\n    {cap_url}: {e}")
            continue
        for e in entries:
            if e.get("kind") != "inline" or not e.get("body_html"):
                continue
            url = (f"http://www.midiman.de/press/"
                   f"{scrape_midiman_de._slugify(e['title'])}-{e['date']}")
            # The capture the entry was parsed out of, carried so run_listings
            # can record where the body came from instead of leaving that to a
            # later inference pass.
            e["capture_url"] = cap_url
            if url not in out or len(e["body"]) > len(out[url]["body"]):
                out[url] = e
    return out


def _terratec_early_entries(conn):
    """(url -> entry) for terratec_early. Every row is an anchor (`#p20`) into
    one of two listing pages, so one capture yields many rows."""
    out = {}
    for page in scrape_terratec_early.PAGES:
        row = conn.execute("SELECT content FROM page_cache WHERE url = ?",
                           (page["wayback_url"],)).fetchone()
        if row is None:
            continue
        html = row[0].decode("cp1252", errors="replace")
        for e in scrape_terratec_early.extract_entries(html, page):
            if e.get("body_html"):
                e["capture_url"] = page["wayback_url"]
                out[e["url"]] = e
    return out


def _terratec_new_entries(conn, lang):
    """(url -> entry) for terratec_new_<lang>, out of every cached capture of
    that language's two listing pages.

    Not a synthetic URL like midiman_de's - these hrefs really were on the
    page - but the same predicament: for 15 of these rows archive.org has zero
    captures of the article itself (CDX confirms it, and --wayback records the
    dead end), so the listing capture the text came from is the only place the
    formatting can still be read out of.
    """
    out = {}
    for listing in scrape_terratec_new.LANGS[lang]["listing_urls"]:
        for cap_url, content in conn.execute(
                "SELECT url, content FROM page_cache WHERE url LIKE '%id_/' || ?",
                (listing,)):
            m = re.search(r"/web/(\d{14})id_/", cap_url)
            try:
                entries = scrape_terratec_new.extract_entries(
                    content, listing, m.group(1) if m else None)
            except Exception as e:
                print(f"\n    {cap_url}: {e}")
                continue
            for e in entries:
                url = e.get("url")
                if not url or not e.get("body_html"):
                    continue
                e["capture_url"] = cap_url
                if url not in out or len(e["body"]) > len(out[url]["body"]):
                    out[url] = e
    return out


LISTING_SOURCES = {
    "midiman_de": _midiman_de_entries,
    "terratec_early": _terratec_early_entries,
    "terratec_new_de": functools.partial(_terratec_new_entries, lang="de"),
    "terratec_new_en": functools.partial(_terratec_new_entries, lang="en"),
}


def run_listings(conn, limit=None, force=False) -> None:
    """Re-extract sources whose body never came from a page of its own.

    Two shapes end up here. midiman_de's inline releases only ever existed
    *inside* a listing page, so
    the scraper mints `/press/{slug}-{date}` for them. CACHED_PARSERS cannot
    reach those rows - it is keyed by the row's own capture, and that capture
    does not exist - which is why they were the one source --seed-cache had to
    skip entirely.

    The listing captures are in page_cache, though, so this walks those and
    matches what it parses back to existing rows by the same synthetic URL the
    scraper builds. No network. Only ever an UPDATE: a parse that matches no
    stored URL is dropped rather than inserted, so re-extraction cannot mint
    rows under URLs nobody has seen.

    terratec_new_de/_en are the other shape: real article URLs that archive.org
    never captured, so --from-cache has nothing keyed to them and --wayback can
    only confirm the dead end. Their listing captures hold the full release
    text, which is why these rows had a body at all.

    Two guards, both of which this mode was missing until 2026-08-22 and both
    of which cost data the moment terratec_new joined it:

    - **only rows with `body_html IS NULL`**, the same cursor every other mode
      uses. Without it the pass walked all 159 terratec_new rows, including the
      135 whose body came from the article's *own* capture.
    - **never a shorter body than the one stored.** A listing entry is not
      automatically the better copy: on this CMS the listing carries the full
      text for recent releases and a truncated one for older entries, so 122
      full articles were overwritten by their teasers. `safe_to_write` cannot
      catch that - it refuses text vanishing from the *middle*, and a lost tail
      is `edges_only`, which is exactly what dropped nav is. Same guard as
      run_wayback's, for the same reason.
    """
    for source, collect in LISTING_SOURCES.items():
        stored = dict(conn.execute(
            "SELECT url, COALESCE(body, '') FROM releases WHERE source = ?", (source,)))
        pending_urls = {u for (u,) in conn.execute(
            "SELECT url FROM releases WHERE source = ? AND body_html IS NULL", (source,))}
        target = stored if force else pending_urls
        found = collect(conn)
        items = [(u, e) for u, e in found.items() if u in target]
        if limit:
            items = items[:limit]
        print(f"[listings] {source}: {len(found)} sparsowanych, "
              f"{len(items)} pasuje do {len(target)} wierszy "
              f"{'w źródle' if force else 'bez formatowania'}", flush=True)

        stats = Stats(source, total=len(items))
        held = []
        for url, e in items:
            if len(e["body"]) < len(stored[url]):
                held.append((url, "krótszy niż zapisany"))
                stats.skipped()
                continue
            ok, why = safe_to_write(stored[url], e["body"])
            if not ok:
                held.append((url, why))
                stats.skipped()
                continue
            db.upgrade_release(conn, url, body=e["body"], body_html=e["body_html"])
            if e.get("capture_url"):
                db.record_body_origin(conn, url, e["capture_url"])
            stats.upgraded()

        stats.summary(conn)
        if held:
            print(f"WSTRZYMANE przez bramke: {len(held)}")
            for url, why in held[:10]:
                print(f"  {why:26} {url}")


def run_seed_cache(conn, sources, limit=None, force=False) -> None:
    """Fetch captures into page_cache. Writes nothing to `releases`.

    Deliberately incapable of damaging a row: it never parses and never calls
    upgrade_release. The point is to turn 'redesigning this parser needs
    another crawl' into 'redesigning this parser is free', once.
    """
    rows = pending(conn, sources, limit, force)
    todo = [(u, s, d) for u, s, d in rows
            if capture_url(d, u) and is_html_detail(u)
            and s not in SYNTHETIC_URL_SOURCES]
    stats = Stats(total=len(todo))
    print(f"[seed-cache] {len(rows)} rows pending, {len(todo)} addressable; "
          f"fetching captures into page_cache", flush=True)

    session = requests.Session()
    for url, source, detail_id in todo:
        key = capture_url(detail_id, url)
        if conn.execute("SELECT 1 FROM page_cache WHERE url = ?", (key,)).fetchone():
            stats.skipped()
            continue
        try:
            content = wayback.fetch_snapshot(conn, session, key)
        except Exception as e:
            print(f"\n    {url}: {e}")
            stats.uncertain()
            continue
        stats.added() if looks_like_html(content) else stats.dead()

    stats.summary()


def run_wayback(conn, sources, limit=None, force=False) -> None:
    """Fetch the row's own capture, then reparse it with the source's parser.

    The timestamp in detail_id names a capture, so try that one first - it is
    a single request and no CDX round trip. It is not always a capture *of
    this URL*, though: the pressdb/media_news scrapers store the *listing*
    capture's timestamp on a row whose body came from that listing, so the
    reconstructed URL is one that never existed and 404s permanently. 76 of
    the 83 failures on the second run of this mode were exactly that.

    So a failure falls back to wayback.fetch_detail_snapshot, which asks CDX
    what captures of this URL actually exist. That turns a permanent 404 into
    either a real recovery from a different capture (detail_id is updated to
    say which, so provenance stays honest) or a confirmed `dead` - which
    matters more than it sounds, because without it these rows stay
    `uncertain` and every future run retries them forever. Rows without a timestamp (teaser/stub) are skipped: finding a capture
    for those is a CDX search, which is their own scraper's job. So are
    attachment URLs - a .pdf/.doc row's body came out of pdftotext/antiword and
    has no HTML behind it by construction, so body_html stays NULL there on
    purpose. Fetching those was 61 wasted requests against a throttling archive
    on this mode's first run.
    """
    rows = pending(conn, sources, limit, force)
    stats = Stats(total=len(rows))
    print(f"[body_html] {len(rows)} rows; fetching each one's own capture",
          flush=True)

    session = requests.Session()
    for url, source, detail_id in rows:
        if source not in CACHED_PARSERS:
            stats.skipped()
            continue
        key = capture_url(detail_id, url)
        if not key or not is_html_detail(url) or source in SYNTHETIC_URL_SOURCES:
            # teaser/stub or a platform id (no capture named), a .pdf/.doc
            # attachment (nothing to parse as HTML), or a URL the scraper
            # invented (nothing to fetch).
            stats.skipped()
            continue
        base = CACHED_PARSERS[source]

        def parser(content, _base=base):
            return _base(content) if looks_like_html(content) else {}

        new_detail_id = None
        try:
            parsed = parser(wayback.fetch_snapshot(conn, session, key))
        except Exception:
            # The named capture is not there. Ask CDX for one that is.
            parsed, confirmed = wayback.fetch_detail_snapshot(conn, session, url, parser)
            if not parsed:
                # confirmed=False is a network hiccup, not a verdict: write
                # nothing so a rerun retries. confirmed=True means CDX has no
                # capture of this URL at all - record it and stop retrying.
                stats.dead() if confirmed else stats.uncertain()
                continue
            new_detail_id = parsed.get("detail_id")
            # A different capture is not automatically a better one: it can be
            # an earlier, shorter version of the article, or a soft-404 the
            # parser still finds text in. Formatting is not worth losing text
            # over, so refuse anything shorter than what is already stored.
            # (Same guard as backfill_midiman_attachments' length check.)
            stored = db.stored_body_length(conn, url) or 0
            if len(parsed.get("body") or "") < stored:
                stats.skipped()
                continue

        body, body_html = parsed.get("body") or "", parsed.get("body_html") or ""
        if not body_html:
            stats.dead()
            continue
        db.upgrade_release(conn, url, body=body, body_html=body_html,
                           detail_id=new_detail_id)
        # This body came out of a capture of the row's own url, fetched just
        # now, so whatever was recorded before (a listing, an older capture)
        # has stopped describing it. Record what it actually was.
        db.record_body_origin(conn, url, key if new_detail_id is None
                               else wayback.snapshot_url(new_detail_id, url))
        stats.upgraded()

    stats.summary()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", help="comma-separated source tags (default: the live ones)")
    p.add_argument("--limit", type=int, help="stop after this many rows")
    p.add_argument("--from-cache", action="store_true",
                   help="reparse page_cache only, no network")
    p.add_argument("--wayback", action="store_true",
                   help="fetch each row's own capture from archive.org, then reparse")
    p.add_argument("--seed-cache", action="store_true",
                   help="fetch captures into page_cache only; parses nothing, writes nothing")
    p.add_argument("--listings", action="store_true",
                   help="re-extract listing-derived sources (midiman_de, "
                        "terratec_early, terratec_new_*) from cached listings")
    p.add_argument("--retext", action="store_true",
                   help="recompute body from the stored body_html, no network")
    p.add_argument("--relocated", action="store_true",
                   help="only rows whose body came off a different page than "
                        "their own url (see body_origin)")
    p.add_argument("--force", action="store_true",
                   help="re-extract rows that already have body_html (free "
                        "for anything in page_cache)")
    args = p.parse_args()

    conn = db.connect()
    if args.listings:
        run_listings(conn, args.limit, args.force)
        conn.close()
        return
    if args.retext:
        run_retext(conn, args.limit)
        conn.close()
        return

    if args.source:
        sources = [s.strip() for s in args.source.split(",") if s.strip()]
    elif args.seed_cache:
        # Every wayback source, parser or not - that is the whole point.
        sources = [s for (s,) in conn.execute("SELECT DISTINCT source FROM releases")
                   if s not in LIVE_FETCHERS]
    elif args.from_cache or args.wayback:
        sources = sorted(CACHED_PARSERS)
    else:
        sources = sorted(LIVE_FETCHERS)

    unknown = [s for s in sources if s not in LIVE_FETCHERS]
    if unknown and not (args.from_cache or args.wayback or args.seed_cache):
        p.error(f"no live fetcher for: {', '.join(unknown)} "
                f"(known: {', '.join(sorted(LIVE_FETCHERS))}); "
                f"use --from-cache for the wayback sources")

    if args.seed_cache:
        run_seed_cache(conn, sources, args.limit, args.force)
    elif args.wayback:
        run_wayback(conn, sources, args.limit, args.force)
    elif args.from_cache:
        run_cached(conn, sources, args.limit, args.force, args.relocated)
    else:
        run_live(conn, sources, args.limit, args.force)
    conn.close()


if __name__ == "__main__":
    main()
