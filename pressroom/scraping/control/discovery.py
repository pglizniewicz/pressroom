"""Phase 1 of a scraper's run: finding releases and storing them the first time.

A library on the same terms as catch_up.py, which is phase 2: no `__main__`, no
argparse, no source names, no parser of its own. The caller passes its own
fetcher or parser and its own `Stats`, so this module imports no scraper and a
scraper can import it at the top of the file.

The strategies are named for where the release's text comes from, because that
is the only axis on which the loops genuinely differ. What stays in the source
component is everything above the loop - pagination, the `catch_up.no_crawl()`
guard, `limit`, the dedup that picks the best of several captures - and every
tail that is really per-scraper.

Three rules hold in every strategy, in one copy here so a scraper cannot get
them wrong again: a network error is not a verdict, so the item is left alone
and reported `uncertain` and only a confirmed absence may be `dead`; every
counter is gated on the write's return value, because `store_release` is
`INSERT OR IGNORE`; and a confirmed absence is asked about before an
already-stored teaser, while a bodyless row is never `full` - that row cannot be
told apart from a release which genuinely had none.

→ docs/adr/catch-up.md
"""

from typing import NamedTuple

from pressroom.capture.control import address
from pressroom.capture.control import archive
from pressroom.release.control import storage
from pressroom.scraping.entity.parse import Detail


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
    """

    parsed: Detail
    timestamp: str | None
    origin_url: str | None


def _detail_score(parsed) -> int:
    """A capture with an article beats one with only a headline, which beats one
    with neither.

    The middle rung is not a rounding. `from_candidates` stores a title-only
    capture as a `stub` - the row phase 2 comes back to - so a scorer that
    rejected it outright would turn every one of those into `dead` and lose the
    row. `archive.fetch_detail_snapshot` keeps the body-only rule instead,
    because it returns `{}` for a bodyless parse by contract and walking further
    for a headline would buy it nothing.
    """
    return len(parsed.get("body") or "") or bool(parsed.get("title"))


def capture(conn, session, url: str, parse, *, stats, timeout: int = 20):
    """The best capture of `url` this parser can use, fetched and parsed.

    Returns None when the network failed - `stats.uncertain()` has already been
    reported and nothing may be written for this item in this run: a network
    error is not a verdict. A `Capture` whose `timestamp` is None is the opposite: archive.org
    answered, and the answer is that there is no usable capture. That is a
    verdict, and what a source does with it differs (`dead`, a title-only stub,
    or a body read off a listing capture instead), so this returns it rather
    than counting it.

    The split is `archive.fetch_detail_snapshot`'s `(parsed, confirmed)` pair
    with the capture's address kept: five loops rebuilt this by hand precisely
    because they needed `snapshot_url` and `timestamp` afterwards.
    """
    content, timestamp, confirmed = archive.fetch_best_matching_snapshot(
        conn, session, url, lambda c: _detail_score(parse(c)), timeout=timeout
    )
    if content is None and not confirmed:
        stats.uncertain()
        return None
    if timestamp is None:
        return Capture({}, None, None)
    # Captures exist and none of them carried anything a parser could use. Still
    # a `Capture` with an address on it, because the caller's answer to that is
    # a `stub` - the row that lets phase 2 come back - and not the `dead` a url
    # the archive never saw gets.
    parsed = parse(content) if content is not None else {}
    return Capture(parsed, timestamp, address.snapshot_url(timestamp, url))


def from_candidates(
    conn,
    session,
    source: str,
    urls,
    *,
    parse,
    stats,
    titles=None,
    dates=None,
    stub_if_absent: bool = False,
) -> None:
    """Store each candidate url whose own capture the archive still has.

    `parse(content)` is the source's whole-page parser. `titles` and `dates` are
    optional {url: value} read off a listing - what to fall back on when the
    capture's own markup carries neither.

    An empty body is stored, unlike in `from_items`, and the difference is not
    an oversight: the url is real and its capture is named, so the row is what
    lets phase 2 come back for the text later. What it is not is `full` - the
    grade says whether there is an article, so a bodyless row is a `stub` and
    carries no origin, because `body_origin` records the capture a body came
    from and there is no body.

    `stub_if_absent` is about the other absence, the one where the archive never
    saw the url at all. A url a *listing* named is a release whose title and
    date are real even with no capture behind them, so with this on it becomes a
    `stub` carrying just those; a url nothing but a folder listing named has
    nothing to keep, so it stays `dead`. Off by default because the metadata is
    what earns the row, and three sources pass `titles` without meaning this.
    """
    for url in urls:
        if storage.already_stored(conn, url):
            stats.skipped()
            continue

        found = capture(conn, session, url, parse, stats=stats)
        if found is None:
            continue
        listed_title = (titles or {}).get(url, "")
        listed_date = (dates or {}).get(url, "")
        if found.timestamp is None:
            if not (stub_if_absent and (listed_title or listed_date)):
                stats.dead()
                continue
            if storage.store_release(
                conn,
                source,
                url,
                title=listed_title,
                date=listed_date,
                grade="stub",
            ):
                stats.stub()
            else:
                stats.skipped()
            continue

        body = found.parsed.get("body") or ""
        title = found.parsed.get("title") or listed_title
        if storage.store_release(
            conn,
            source,
            url,
            title=title,
            date=found.parsed.get("date") or listed_date,
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


def from_teasers(
    conn,
    session,
    source: str,
    entries,
    *,
    parse,
    stats,
    fetch_detail=None,
    prefer_parsed: bool = False,
) -> None:
    """Upgrade each listing entry with its detail capture, or store the teaser.

    An entry provides `url`, `title`, `date`, `teaser` and `teaser_html`, and
    may provide `origin_url` - the capture the teaser itself was read out of.
    Nothing else is required of it, same rule as a phase-2 collector's.

    `prefer_parsed` is the one axis these loops genuinely differ on: with it the
    detail page's headline and date win where it has them, and the upgrade
    carries them; without it the listing's values are kept, because on two of
    these CMSes the listing states them better than the article page does - and
    the upgrade then writes the body alone.

    `fetch_detail(conn, session, url, parse)` returns `(parsed, confirmed)` and
    defaults to `archive.fetch_detail_snapshot`; it is a parameter because one
    source has to try two addresses per release.
    """
    fetch_detail = fetch_detail or archive.fetch_detail_snapshot

    for entry in entries:
        url = entry["url"]
        existing = storage.stored_grade(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        parsed, confirmed = fetch_detail(conn, session, url, parse)

        if parsed.get("body"):
            title, date = entry.get("title") or "", entry.get("date") or ""
            if prefer_parsed:
                title = parsed.get("title") or title
                date = parsed.get("date") or date
            if existing == "teaser":
                storage.upgrade_release(
                    conn,
                    url,
                    title=title if prefer_parsed else None,
                    date=date if prefer_parsed else None,
                    body=parsed["body"],
                    body_html=parsed["body_html"],
                    detail_id=parsed["detail_id"],
                    origin_url=parsed.get("origin_url"),
                    grade="full",
                )
                stats.upgraded()
                continue
            if storage.store_release(
                conn,
                source,
                url,
                title=title,
                date=date,
                body=parsed["body"],
                body_html=parsed["body_html"],
                detail_id=parsed["detail_id"],
                origin_url=parsed.get("origin_url"),
            ):
                stats.added()
            else:
                stats.skipped()
            continue

        # A confirmed absence is asked about before an already-stored teaser.
        if not confirmed:
            stats.uncertain()
            continue

        if existing == "teaser":
            stats.skipped()
            continue

        if not entry.get("teaser"):
            stats.dead()
            continue

        if storage.store_release(
            conn,
            source,
            url,
            title=entry.get("title") or "",
            date=entry.get("date") or "",
            body=entry["teaser"],
            body_html=entry.get("teaser_html") or None,
            grade="teaser",
            origin_url=entry.get("origin_url"),
        ):
            stats.teaser()
        else:
            stats.skipped()
