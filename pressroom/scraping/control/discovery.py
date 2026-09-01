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
3. **An empty body is `dead`, never a stored row.** A row with a full grade and
   no text is indistinguishable from a release that genuinely had none, and
   rule 1 makes it permanent.
"""

from pressroom.release.control import storage


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
