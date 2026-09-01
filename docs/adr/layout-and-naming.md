# Layout and naming

Why the tree is shaped the way it is. `CLAUDE.md` carries the convention itself
— the three layers, how a source component splits — and [../layout.md](../layout.md)
carries the map of what each component owns. What follows is the reasoning,
including the two places this repo knowingly departs from the BCE pattern.

## One place a command line is assembled

**`boundary/command.py` is the one place a command line is assembled.** Sixteen
scrapers each carried a hand-written `__main__`, and eleven were the same five
lines. A boundary now declares its per-source options as values:

```python
def main():
    command.run(portal.scrape, __doc__, command.LIMIT)
```

and gets the catch-up flags for free. An `Option`'s `dest` is the keyword the
crawl receives, which is the intended coupling: the flag and the parameter it
feeds are named the same thing on purpose. `run()` takes the docstring **whole**
and uses its first paragraph — slicing it in the caller was silent, because
`splitlines()[0]` drops the second half of every two-line summary and four of
these sources have one.

## The retired `backfill_` / `repair_` / `migrate_` prefixes

**There is no `backfill_`, `repair_` or `migrate_` family any more, and
reintroducing one is the smell.** Those prefixes named a *moment* — "the gap has
been filled", "the damage has been undone" — and every one of them outlived it:
`repair_encoding.py`'s own docstring ended up reading "written as a one-off and
no longer one". Two rules replaced them:

- **a pass that has to be re-run after a crawl belongs in the write path or in
  the scraper.** Provenance and the encoding repair both moved into
  `storage.store_release`/`upgrade_release` and `richtext.extract()`; the
  re-extraction modes became `scraping/control/catch_up.py` and are driven by
  the source component that owns the tag.
- **a fix that is genuinely finished gets deleted.** Git holds the code (`git
  show <sha>:repair_cache_hashes.py`), `CLAUDE.md` holds the rule it
  established.

The schema migrations went the same way and for the same reason. Every table's
`entity/` module carried an upgrade path beside its `CREATE TABLE` — a
`rename_before_create` for the two tables that had been renamed, a `migrate`
adding whichever columns were missing and dropping the two that were no longer
wanted, and one `PRAGMA user_version` step rebuilding an index built when only
`releases_ai` existed. All of it was already dead: there is one database, on one
machine, and it had every column, every rename and the stamped version. Code
that can lift a database out of a state nothing is in is not caution, it is a
fiction of versioning — and it cost something real, because the version-stamped
rebuild announced itself on every *fresh* database (`user_version` starts at 0),
which is why the suite and `tests/refresh.py` both had to suppress stdout to
stay readable.

What the removal deliberately keeps is the archaeology, here and in the other
records: `captures.md` on `wayback_cache`, `provenance.md` on `body_capture`'s
two removed columns, `grade-and-detail-id.md` on the sentinels that used to live
in `detail_id`. The names belong in the file someone opens to ask *why the
shape is what it is*; they no longer belong in code that pretends it might meet
them again.

## The read-only passes are a third kind of boundary

**The read-only passes are a third kind of boundary, next to a scraper and a
reader.** They write nothing, ever:

- **`pressroom-verify-*`** checks that a claim still holds and is run after
  anything that could break it: `pressroom-verify-body-origin` (a recorded
  origin really produces the body it claims), `pressroom-verify-encoding`
  (nothing repairable is stored, and the two detectors still agree), and
  `pressroom-verify-names` (the tree's own names still resolve). The first two
  are what a deleted repair leaves behind: the reporting half survives, the
  writing half moved into the write path.
- **`pressroom-calibrate-*`** is a review pass over the whole cache, printing
  metrics *and* writing a page of full texts:
  `pressroom-calibrate-containers` for DOM containers,
  `pressroom-calibrate-attachments` for the attachment converters. The second
  half is not decoration — a column of metrics once said "100% of words kept"
  about a conversion that had put the release's headline after the footer.

## Two BCE deviations

**Two BCE deviations, recorded rather than glossed** — the same way `checks.md`
records `[no-js]` against `web-static`:

- **Transactions stay at the write, not in the boundary.** The pattern puts
  transaction wrapping in the boundary; here "commit per row unless a loop
  batches explicitly" is what makes an hour-long crawl against a flaky archive
  resumable, and hoisting the commit would make a run all-or-nothing.
- **No entity classes.** Entities here are the module that owns a table's
  schema and its state changes, over plain sqlite3 rows. A `Release` object
  would be the row dataclass `CLAUDE.md` rules out under "Working here".
  `Grade` is the one closed set modelled as an enum, and it is a `StrEnum`
  precisely so that no call site had to change for it to exist.

## The dependency direction

**The dependency direction is a rule, not an accident.** A source component
imports `scraping`, `release`, `capture`, `text`, `database`, `reporting` and
`q4`; none of those imports a source component. The one exception is documented
and safe: `provenance/boundary/verification.py` imports source control modules
to reproduce bodies, and nothing imports it back.
