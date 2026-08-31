"""Phase 2 of a scraper's run: everything an earlier run could not get.

A scraper's first phase discovers and stores. This is the second: it walks the
rows that phase left incomplete and finishes them - reparsing bytes page_cache
already holds (free), re-walking cached listings, re-deriving text from stored
markup, and only then asking archive.org for what is genuinely missing. A plain
re-run of a scraper does all of it, which is why none of this is a script anyone
schedules.

**A library, deliberately: no `__main__`, no argparse, no source names.** The
caller passes its own parser, its own listing collector, its own live fetcher -
so this module imports no scraper and a scraper can import it at the top of the
file. The registry it replaces (`source -> parser` for 16 tags) sat in a module
that imported 13 scrapers, which is why nothing could import it back.

Six strategies, and they are not interchangeable:

  from_cache      reparse the bytes we already have. No network, ever.
  from_listings   re-walk cached listing captures and match entries back to
                  rows by url - for bodies that only ever existed inside a
                  listing page.
  from_live       re-fetch from a site that is still up (through fetch_cached,
                  so a cached page costs nothing).
  retry_missing   fetch the row's own capture, then ask CDX when that 404s.
                  The only strategy that can turn "retried forever" into
                  `dead`.
  seed_cache      fetch captures and parse nothing - for redesigning a parser
                  without paying for a crawl per iteration.
  retext          re-derive `body` from the stored `body_html`. Needs no bytes
                  at all.

A title is filled only where the row has none (`_fill_title`), which is the one
thing these strategies write besides the body - and the rule that a new
extraction rule goes in as a *fallback*, never a replacement, is why.

Three rules hold in every one of them, and each cost data before it was a rule:

1. **The cursor is `body_html IS NULL`** for that source. `force=True` widens it
   to every row and says so before writing. Without a cursor, a listing pass
   walked all 159 terratec_new rows instead of the 24 pending ones.
2. **`gate.not_shorter` always**, including under `force`. That is what
   stops a listing teaser replacing the full article it truncates, and
   `safe_to_write` provably cannot: a lost tail is `edges_only`, the same
   signature as correctly dropped nav.
3. **The gate is chosen by the address, not by a flag.** Bytes that are a
   capture of the row's own url go through `safe_to_write`; bytes from some
   other page (a listing, a print view) go through `strict_same_text`, and if
   the caller has a url-keyed collector those rows are skipped here and handled
   by `from_listings` instead. A whole-page parser fed a listing capture returns
   the longest article on it - somebody else's release, 64 rows of it.

A network error is never a verdict: a failed fetch writes nothing and reports
`uncertain`, so a rerun retries exactly that row. Only a confirmed absence is
`dead`.
"""

import sys

from pressroom.capture.control import address
from pressroom.attachment.control import conversion
from pressroom.release.control import gate
from pressroom.provenance.control import resolution
from pressroom.release.control import storage
from pressroom.text.control import richtext
from pressroom.scraping.control import twin
from pressroom.capture.control import archive
from pressroom.reporting.entity.outcome import Stats


def pending(conn, source: str, *, force: bool = False,
            limit=None) -> list[tuple[str, str | None]]:
    """(url, detail_id) for the rows of `source` this phase still owes work on.

    Without `force` that is `body_html IS NULL` - the rows stored before
    richtext.py, or whose capture the earlier run could not reach. With `force`
    it is every row of the source, which is what a `clean()` change needs and
    which costs no requests because the bytes are cached.
    """
    sql = "SELECT url, detail_id FROM releases WHERE source = ?"
    if not force:
        sql += " AND body_html IS NULL"
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (source,)).fetchall()


def _stored(conn, source: str) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT url, COALESCE(body, '') FROM releases WHERE source = ?", (source,)))


def _report_held(held: list) -> None:
    if not held:
        return
    print(f"WSTRZYMANE przez bramke: {len(held)} - nic nie zapisano")
    for url, why in held[:20]:
        print(f"  {why:26} {url}")


def _fill_title(conn, url: str, parsed: dict):
    """A title for a row that has none, or None.

    **Fill-if-empty only, never a replacement.** This is what a deleted repair
    pass did in one shot (37 of 54 empty titles recovered from cache, 17 rows
    that carry no headline markup at all being the end state), and the reason it
    only ever wrote over an empty title is measured: the same rule tried as a
    replacement filled 8 rows and *changed* 30, twelve of them from a correct
    title to an empty one. There are also 14 terratec_de rows where the current
    parser disagrees with the stored title, twice substantively - that wants its
    own measured pass, not a side effect of a re-extraction.
    """
    title = (parsed.get("title") or "").strip()
    if not title:
        return None
    row = conn.execute("SELECT COALESCE(title, '') FROM releases WHERE url = ?",
                       (url,)).fetchone()
    return title if row is not None and not row[0].strip() else None


def _write(conn, url, body, body_html, *, origin_url=None, detail_id=None,
           title=None) -> None:
    """One write per row: text, markup, verdict and provenance in one call.

    `grade="full"` is not optional here - this is the pass that replaces a
    teaser body with the real article, and a row keeping a verdict that stopped
    being true gets handed to the next run as still-upgradable.
    """
    storage.upgrade_release(conn, url, body=body, body_html=body_html, title=title,
                       detail_id=detail_id, grade="full", origin_url=origin_url)


def from_cache(conn, source: str, parser, *, has_collector: bool = False,
               force: bool = False, limit=None) -> None:
    """Reparse whatever page_cache already holds for this source. No network.

    A row whose capture was never cached is `skipped`, not `uncertain` - there
    is nothing here for a rerun to retry until the capture itself is fetched.

    `has_collector` says the caller also has a url-keyed listing collector, so
    rows whose bytes are a capture of another page are left to `from_listings`
    rather than fed to this whole-page parser.
    """
    rows = pending(conn, source, force=force, limit=limit)
    stored = _stored(conn, source)
    stats = Stats(source, total=len(rows))
    print(f"[{source}] faza 2 z cache: {len(rows)} wierszy"
          f"{' (force: wszystkie)' if force else ' bez body_html'}", flush=True)

    held = []
    for url, detail_id in rows:
        if conversion.is_attachment_url(url):
            stats.skipped()          # a file, not a page - the attachment pass owns it
            continue
        key = resolution.origin_key(conn, url, detail_id)
        own = resolution.own_page(conn, url, detail_id)
        if has_collector and key and not own:
            stats.skipped()
            continue
        content = resolution.cached(conn, key)
        if content is None:
            stats.skipped()
            continue
        if not conversion.looks_like_html(content):
            stats.dead()
            continue
        parsed = parser(content)
        body, body_html = parsed.get("body") or "", parsed.get("body_html") or ""
        if not body_html:
            # The capture is cached but this parser finds nothing in it - a
            # template variant it does not cover. Not retryable by refetching.
            stats.dead()
            continue
        # The gate is chosen by the address, not by a flag: bytes that are a
        # capture of the row's own url go through safe_to_write, bytes from some
        # other page through strict_same_text.
        chosen = gate.safe_to_write if own else gate.strict_same_text
        for check in (gate.not_shorter, chosen):
            ok, why = check(stored.get(url, ""), body)
            if not ok:
                held.append((url, why))
                stats.skipped()
                break
        else:
            # Record the address this text was read from, in the same
            # transaction as the text. Not a clear: the key this strategy reads
            # *is* the recorded entry, so clearing it deletes a true statement
            # and takes the row's archive link with it (38 rows lost their link
            # that way before the count gave it away).
            _write(conn, url, body, body_html, origin_url=key,
                   title=_fill_title(conn, url, parsed))
            stats.upgraded()

    stats.summary(conn)
    _report_held(held)


def from_listings(conn, source: str, collect, *, force: bool = False,
                  limit=None) -> None:
    """Re-walk cached listing captures and match what they hold back to rows.

    Two strategies need this. midiman_de's inline releases only ever existed
    *inside* a listing page, so the scraper mints `/press/{slug}-{date}` for
    them and no capture of that url can exist. terratec_new_de/_en are the
    opposite: real article urls archive.org simply never captured, whose text
    came off the listing because that is where it was.

    `collect(conn) -> {url: {"body", "body_html", "origin_url"}}` is the
    caller's, because finding releases inside a listing is per-CMS knowledge.

    Only ever an UPDATE: an entry matching no stored url is dropped, never
    inserted, so a re-extraction cannot mint rows under urls nobody has seen.
    """
    stored = _stored(conn, source)
    pending_urls = {u for u, _ in pending(conn, source)}
    target = set(stored) if force else pending_urls
    found = collect(conn)
    items = [(u, e) for u, e in found.items() if u in target]
    if limit:
        items = items[:limit]

    stats = Stats(source, total=len(items))
    print(f"[{source}] faza 2 z listingow: {len(found)} sparsowanych, "
          f"{len(items)} pasuje do {len(target)} wierszy "
          f"{'w zrodle' if force else 'bez body_html'}", flush=True)

    held = []
    for url, e in items:
        body = e.get("body") or ""
        for check in (gate.not_shorter, gate.safe_to_write):
            ok, why = check(stored.get(url, ""), body)
            if not ok:
                held.append((url, why))
                stats.skipped()
                break
        else:
            _write(conn, url, body, e.get("body_html") or "",
                   origin_url=e.get("origin_url"),
                   title=_fill_title(conn, url, e))
            stats.upgraded()

    stats.summary(conn)
    _report_held(held)


def from_live(conn, source: str, fetch_body, session, *, force: bool = False,
              offline: bool = False, limit=None) -> None:
    """Re-fetch a row from a site that is still up.

    `fetch_body(conn, session, url) -> (body, body_html)` goes through
    fetch.fetch_cached, so a page fetched once is free forever after - which is
    why this is also the offline strategy for the live sources: with
    `offline=True`, a row whose page is not in page_cache is skipped rather
    than fetched.

    No provenance is recorded: a live page is not an archive capture, and a row
    with no body_origin entry is exactly how the browser knows not to offer an
    archive link.
    """
    rows = pending(conn, source, force=force, limit=limit)
    stored = _stored(conn, source)
    stats = Stats(source, total=len(rows))
    print(f"[{source}] faza 2 z zywej strony: {len(rows)} wierszy"
          f"{' (tylko cache)' if offline else ''}", flush=True)

    held = []
    for url, _detail_id in rows:
        if offline and not conn.execute(
                "SELECT 1 FROM page_cache WHERE url = ?", (url,)).fetchone():
            stats.skipped()
            continue
        try:
            body, body_html = fetch_body(conn, session, url)
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
        for check in (gate.not_shorter, gate.safe_to_write):
            ok, why = check(stored.get(url, ""), body)
            if not ok:
                held.append((url, why))
                stats.skipped()
                break
        else:
            _write(conn, url, body, body_html)
            stats.upgraded()

    stats.summary(conn)
    _report_held(held)


def retry_missing(conn, source: str, parser, session, *, limit=None) -> None:
    """Fetch the row's own capture, then ask CDX when that capture is not there.

    The timestamp in detail_id names a capture, so try that one first - a single
    request and no CDX round trip. It is not always a capture *of this url*,
    though: a scraper that read a release out of a listing stores the listing's
    timestamp, so the reconstructed address is one that never existed and 404s
    permanently (76 of 83 failures on one run were exactly that).

    So a failure falls back to archive.fetch_detail_snapshot, which asks CDX
    what captures of this url actually exist. That turns a permanent 404 into
    either a real recovery from a different capture - detail_id is updated to
    say which, so provenance stays honest - or a confirmed `dead`, which matters
    more than it sounds: without it these rows stay `uncertain` and every future
    run retries them forever.

    Rows with no timestamp (teaser/stub) are skipped: finding a capture for
    those is a CDX search, which is the scraper's first phase, not this one. So
    are attachment urls - their text came out of pdftotext/antiword and there is
    no HTML behind them by construction.
    """
    rows = pending(conn, source, limit=limit)
    stored = _stored(conn, source)
    stats = Stats(source, total=len(rows))
    print(f"[{source}] faza 2 z archiwum: {len(rows)} wierszy do sprobowania",
          flush=True)

    def guarded(content, _p=parser):
        return _p(content) if conversion.looks_like_html(content) else {}

    for url, detail_id in rows:
        key = address.capture_key(detail_id, url)
        if not key or conversion.is_attachment_url(url):
            stats.skipped()
            continue
        new_detail_id = None
        try:
            parsed = guarded(archive.fetch_snapshot(conn, session, key))
        except Exception:
            parsed, confirmed = archive.fetch_detail_snapshot(conn, session, url, guarded)
            if not parsed:
                # confirmed=False is a network hiccup, not a verdict: write
                # nothing so a rerun retries. confirmed=True means CDX has no
                # capture of this url at all - record it and stop retrying.
                stats.dead() if confirmed else stats.uncertain()
                continue
            new_detail_id = parsed.get("detail_id")
            key = address.snapshot_url(new_detail_id, url)

        body, body_html = parsed.get("body") or "", parsed.get("body_html") or ""
        if not body_html:
            stats.dead()
            continue
        ok, why = gate.not_shorter(stored.get(url, ""), body)
        if not ok:
            # A different capture is not automatically a better one: it can be
            # an earlier, shorter version of the article, or a soft-404 the
            # parser still finds text in. Formatting is not worth losing text.
            stats.skipped()
            continue
        _write(conn, url, body, body_html, origin_url=key, detail_id=new_detail_id,
               title=_fill_title(conn, url, parsed))
        stats.upgraded()

    stats.summary(conn)


def seed_cache(conn, source: str, session, *, force: bool = False,
               limit=None) -> None:
    """Fetch captures into page_cache. Writes nothing to `releases`.

    Deliberately incapable of damaging a row: it never parses and never calls
    upgrade_release. The point is to turn "redesigning this parser needs another
    crawl" into "redesigning this parser is free", once.

    A source whose row urls were minted by its scraper has nothing to seed - the
    address would be a page that never existed - so the scraper simply does not
    call this.
    """
    rows = pending(conn, source, force=force, limit=limit)
    todo = [(u, d) for u, d in rows
            if address.capture_key(d, u) and not conversion.is_attachment_url(u)]
    stats = Stats(source, total=len(todo))
    print(f"[{source}] seed-cache: {len(rows)} wierszy, {len(todo)} adresowalnych",
          flush=True)

    for url, detail_id in todo:
        key = address.capture_key(detail_id, url)
        if conn.execute("SELECT 1 FROM page_cache WHERE url = ?", (key,)).fetchone():
            stats.skipped()
            continue
        try:
            content = archive.fetch_snapshot(conn, session, key)
        except Exception as e:
            print(f"\n    {url}: {e}")
            stats.uncertain()
            continue
        stats.added() if conversion.looks_like_html(content) else stats.dead()

    stats.summary()


def retext(conn, source: str, *, limit=None) -> None:
    """Re-derive `body` from the stored `body_html`. No bytes needed at all.

    `body` is by definition `to_text(body_html)` wherever the markup exists, so
    a fix to the text renderer alone costs nothing to apply. Rows whose text does
    not change are `skipped`, so a rerun right after one prints all dots.
    """
    sql = ("SELECT url, body, body_html FROM releases "
           "WHERE source = ? AND body_html IS NOT NULL ORDER BY id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (source,)).fetchall()
    stats = Stats(source, total=len(rows))
    print(f"[{source}] retext: {len(rows)} wierszy z zapisanym HTML", flush=True)

    for url, body, body_html in rows:
        text = richtext.to_text(body_html)
        if text == (body or ""):
            stats.skipped()
            continue
        storage.upgrade_release(conn, url, body=text)
        stats.upgraded()

    stats.summary()


def catch_up(conn, source: str, *, parser=None, collect=None, fetch_body=None,
             session=None, force: bool = False, yes: bool = False,
             offline: bool = False, only_retext: bool = False,
             seed: bool = False, limit=None, attachments: bool = False,
             twins_too: bool = False) -> None:
    """Phase 2 for one source, cheapest route first. The call a scraper makes.

    The scraper states what it has - its parser, its listing collector, its live
    fetcher - and this runs the strategies those make possible, in the order that
    spends the least: stored markup, then cached bytes, then the network. A
    source with no listing collector simply passes none, and the listing strategy
    does not run; that is what the old registry's membership tests became.

    `attachments` is accepted and ignored here: it belongs to the other
    contract (attachment_crawl), and the scrapers pass one options dict to both.

    `offline=True` is the honest name for "touch nothing on the network": the
    free strategies run and the two that fetch are skipped. `seed=True` is the
    opposite special case - fetch captures for a parser redesign and parse
    nothing - so it runs alone.
    """
    if force and not confirm_force(conn, source, yes=yes):
        return
    if only_retext:
        retext(conn, source, limit=limit)
        return
    if seed:
        seed_cache(conn, source, session, force=force, limit=limit)
        return

    if parser is not None:
        from_cache(conn, source, parser, has_collector=collect is not None,
                   force=force, limit=limit)
    if collect is not None:
        from_listings(conn, source, collect, force=force, limit=limit)
    if twins_too:
        # Last of the free strategies: a teaser whose twin row in this same
        # source already holds the real article. No capture involved, which is why it
        # lives in its own module.
        twin.fill(conn, source)
    if offline:
        return
    if fetch_body is not None:
        from_live(conn, source, fetch_body, session, force=force, limit=limit)
    if parser is not None:
        retry_missing(conn, source, parser, session, limit=limit)


# The five flags every scraper gets, in one place for the same reason
# outcome.py exists: the vocabulary has to be identical everywhere. A scraper
# adds them with add_flags(p) and turns them into keyword arguments with
# options(args).
def add_flags(parser) -> None:
    parser.add_argument("--force", action="store_true",
                        help="re-extract every row of this source, not just the "
                             "ones without markup (after changing the parser)")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before a --force rewrite")
    parser.add_argument("--retext", action="store_true",
                        help="only re-derive body from the stored HTML "
                             "(after changing to_text)")
    parser.add_argument("--offline", action="store_true",
                        help="catch up from page_cache only, no network")
    parser.add_argument("--seed-cache", action="store_true",
                        help="fetch captures into page_cache and parse nothing")
    parser.add_argument("--no-catch-up", action="store_true",
                        help="crawl only, skip phase 2")
    parser.add_argument("--attachments", action="store_true",
                        help="also crawl archive.org for .pdf/.doc attachments "
                             "(hours, and the last two passes yielded nothing)")


def options(args) -> dict[str, bool]:
    return {
        "catch_up": not args.no_catch_up,
        "force": args.force,
        "yes": args.yes,
        "offline": args.offline,
        "only_retext": args.retext,
        "seed": args.seed_cache,
        # Read by attachment_crawl.catch_up, not by this module: the attachment
        # rows are a second contract, and their network half is opt-in.
        "attachments": getattr(args, "attachments", False),
    }


def confirm_force(conn, source: str, *, yes: bool = False) -> bool:
    """Ask before rewriting rows that already carry markup.

    `--force` used to live on one CLI and now lives on fifteen, and it is the
    flag whose earlier equivalent overwrote 122 full articles with listing
    teasers. The gates still hold underneath, but a bulk rewrite is worth
    stating out loud first. Non-interactive callers pass yes=True; a pipe with
    no tty answers no rather than blocking a cron.
    """
    total, with_html = conn.execute(
        "SELECT count(*), sum(body_html IS NOT NULL) FROM releases WHERE source = ?",
        (source,)).fetchone()
    print(f"[{source}] --force: {total} wierszy, {with_html or 0} z nich ma juz "
          f"body_html i zostanie przepisanych", flush=True)
    if yes:
        return True
    if not sys.stdin.isatty():
        print("  brak terminala - przerywam. Dodaj --yes swiadomie.")
        return False
    return input("  kontynuowac? [y/N] ").strip().lower() in ("y", "yes", "t", "tak")


def run(conn, source: str, opts: dict, **pieces) -> None:
    """catch_up() driven by the flags a scraper parsed. One parameter and one
    call per scraper, so adding phase 2 to a scraper is two lines rather than
    six keyword arguments threaded through its own signature."""
    opts = dict(opts or {})
    if not opts.pop("catch_up", True):
        return
    catch_up(conn, source, **pieces, **opts)


def no_crawl(opts: dict) -> bool:
    """Whether the scraper's own discovery phase should be skipped entirely.

    True for the flags that mean "do not touch the network": --offline (catch up
    from page_cache only), --retext (needs nothing but the database) and
    --seed-cache (fetches captures for known rows, discovers nothing). Applied by
    each scraper to its *candidate list* rather than around its loop - a discovery
    call that returns nothing leaves the loop body untouched, which is how a
    fifteen-file change stays a one-line change per file.

    This is what makes `pressroom-<source> --offline` the free re-extraction of
    one source, which is the workflow that replaced a shared engine's
    `--from-cache --source X`.
    """
    opts = opts or {}
    return bool(opts.get("offline") or opts.get("only_retext") or opts.get("seed"))
