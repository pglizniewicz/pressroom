"""Phase 1 of a scraper's run: finding releases and storing them the first time.

`catch_up.py` is phase 2 - everything an earlier run could not get - and it says
so in its first line. This is the half that sentence implies and that did not
exist: sixteen scrapers each wrote the discovery loop out by hand, nineteen
copies of it over fourteen control modules, and the copies drifted. What they
drifted on is not style; it is the three rules below, each of which some copy
got wrong while its siblings carried a comment explaining why it must not be.

**A library, deliberately, on the same terms as catch_up.py**: no `__main__`, no
argparse, no source names, no parser of its own. The caller passes its own
fetcher or parser and its own `Stats`, so this module imports no scraper and a
scraper can import it at the top of the file.

The strategies are named for where the release's text comes from, because that
is the only axis on which the loops genuinely differ:

  from_items      a listing item already carries title/date; the body comes
                  from a live fetch through the caller's fetch_body.
  from_candidates a bare url the crawl found; the archive is asked for its
                  newest working capture and the whole page is parsed.

`capture()` is the second one's middle, exposed because three loops need it
without the tail: what to do when archive.org confirms there is no capture is
genuinely per-source - a verdict of `dead`, a title-only stub, or falling
through to a copy read off a listing page - and the probe/fetch/report half
above that decision was duplicated in five modules regardless.

What stays in the source component is everything above the loop - pagination,
the `catch_up.no_crawl()` guard, `limit`, the dedup that picks the best of
several captures - and every tail that is really per-source.

Three rules hold in every strategy, in one copy so a scraper cannot get them
wrong again:

1. **A network error is not a verdict.** The item is left completely alone and
   reported `uncertain`, so a rerun retries exactly it. Writing an empty row
   instead would be worse than writing nothing: `already_stored()` would skip
   it on every future run, so one timeout would cost the release permanently.
   Only a confirmed absence may be `dead`.
2. **Every counter is gated on the write's return value.** `store_release` is
   `INSERT OR IGNORE`, so an unconditional `stats.added()` after it reports
   phantom inserts on every rerun - three copies of this loop still did that.
   A write that inserted nothing is `skipped`, which is also what keeps an item
   from vanishing out of the summary altogether.
3. **A bodyless row is never `full`.** What the two strategies then do differs,
   and the difference is the archive: a live fetch that came back empty leaves
   nothing to return to, so no row is written at all and the item is `dead`,
   while an archived candidate has a real url and a named capture - so the row
   is written, because it is what lets phase 2 come back for the text later. It
   is a `stub` and it carries no origin, since `body_origin` records the capture
   a body came from and there is no body. What both refuse is the `full` grade
   over no text: that row cannot be told apart from a release which genuinely
   had none, and rule 1 makes it permanent.
"""

import time
from typing import NamedTuple

from pressroom.capture.control import archive

# The *content* interval, not archive.py's CDX one - the same alias and the same
# reason as `archive.CONTENT_SLEEP`: the extra patience after a failed probe is
# aimed at the endpoint doing the rate-limiting.
from pressroom.capture.control.politeness import SLEEP as CONTENT_SLEEP
from pressroom.release.control import storage
from pressroom.scraping.entity.parse import Detail, Entry


def from_items(conn, session, source: str, items, *, fetch_body, stats) -> None:
    """Store each listing item whose body a live fetch can produce.

    `items` are mappings with `url`, `title`, `date` and `detail_id` - what a
    collector reads off a listing page. `fetch_body(conn, session, url)` returns
    `(body, body_html)` and goes through `politeness.fetch_cached`, so an item
    whose page is already in `page_cache` costs no request.

    `stats` comes from the caller because it owns the run's shape: several
    sources drive this loop once per page or once per year and want one summary
    over all of them.
    """
    for item in items:
        if storage.already_stored(conn, item["url"]):
            stats.skipped()
            continue

        try:
            body, body_html = fetch_body(conn, session, item["url"])
        except Exception as e:
            print(f"\n    ERROR fetching {item['url']}: {e}")
            stats.uncertain()
            continue

        if not body:
            stats.dead()
            continue

        if storage.store_release(
            conn,
            source,
            item["url"],
            title=item["title"],
            date=item["date"],
            body=body,
            body_html=body_html,
            detail_id=item["detail_id"],
        ):
            stats.added()
        else:
            stats.skipped()


class Capture(NamedTuple):
    """One capture of one url, fetched and parsed.

    A NamedTuple rather than a dataclass because it is a return value, not an
    entity - `CLAUDE.md` rules out row dataclasses and this is neither a row nor
    a table. `timestamp is None` means archive.org has no working capture of the
    url; the other two fields are then empty with it.

    `parsed` is whatever the caller's `parse` returned, which is a `Detail` for
    every whole-page parser and a list of `Entry` for `terratec/control/cms.py`:
    on that CMS an article's own page is the listing template, so its parser
    yields entries and the loop reads the first one. Naming both is the same
    call `parse.py` makes about `Entry` - the type says the one true thing
    rather than a shape one caller would have to violate.
    """

    parsed: Detail | list[Entry]
    timestamp: str | None
    origin_url: str | None


def capture(conn, session, url: str, parse, *, stats, timeout: int = 20):
    """The newest working capture of `url`, fetched and parsed.

    Returns None when the network failed - `stats.uncertain()` has already been
    reported and nothing may be written for this item in this run, which is
    rule 1. A `Capture` whose `timestamp` is None is the opposite: archive.org
    answered, and the answer is that there is no capture. That is a verdict, and
    what a source does with it differs (`dead`, a title-only stub, or a body
    read off a listing capture instead), so this returns it rather than counting
    it.

    The split is `archive.fetch_detail_snapshot`'s `(parsed, confirmed)` pair
    with the capture's address kept: five loops rebuilt this by hand precisely
    because they needed `snapshot_url` and `timestamp` afterwards.
    """
    try:
        found = archive.get_latest_working_snapshot(url)
    except Exception as e:
        print(f"\n  ERROR probing snapshots for {url}: {e}")
        time.sleep(CONTENT_SLEEP * 2)
        stats.uncertain()
        return None
    if not found:
        return Capture({}, None, None)

    snapshot_url, timestamp = found
    try:
        content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=timeout)
        parsed = parse(content)
    except Exception as e:
        print(f"\n  ERROR fetching {snapshot_url}: {e}")
        stats.uncertain()
        return None
    return Capture(parsed, timestamp, snapshot_url)


def from_candidates(
    conn, session, source: str, urls, *, parse, stats, titles=None
) -> None:
    """Store each candidate url whose own capture the archive still has.

    `parse(content)` is the source's whole-page parser. `titles` is an optional
    {url: title} read off a listing - the headline to fall back on when the
    capture's own markup carries none.

    An empty body is stored, unlike in `from_items`, and the difference is not
    an oversight: the url is real and its capture is named, so the row is what
    lets phase 2 come back for the text later. What it is not is `full` - the
    grade says whether there is an article, so a bodyless row is a `stub` and
    carries no origin, because `body_origin` records the capture a body came
    from and there is no body.
    """
    for url in urls:
        if storage.already_stored(conn, url):
            stats.skipped()
            continue

        found = capture(conn, session, url, parse, stats=stats)
        if found is None:
            continue
        if found.timestamp is None:
            stats.dead()
            continue

        body = found.parsed.get("body") or ""
        title = found.parsed.get("title") or (titles or {}).get(url, "")
        if storage.store_release(
            conn,
            source,
            url,
            title=title,
            date=found.parsed.get("date") or "",
            body=body,
            body_html=found.parsed.get("body_html") or None,
            detail_id=found.timestamp,
            grade="full" if body else "stub",
            origin_url=found.origin_url if body else None,
        ):
            if body:
                stats.added()
            else:
                stats.stub()
        else:
            stats.skipped()
