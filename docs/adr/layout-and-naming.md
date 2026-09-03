# Layout and naming

Why the tree is shaped the way it is. `CLAUDE.md` carries the convention itself
— the three layers, how a scraper splits — and [../layout.md](../layout.md)
carries the map of what each component owns. What follows is the reasoning,
including the two places this repo knowingly departs from the BCE pattern.

## Three sizes, and the word that named all of them

`source` named three different things here, and they are three different sizes.
A **source component** is the BCE unit, one directory per firm under `sources/`.
A **scraper** is what one command runs, one per CMS generation, so a component
holds several. A **source** is the tag a row carries, one per mirror, so a
scraper stamps several. Nothing was wrong with the code — the prose said
"source" for all three, and the sentence that did the damage was `CLAUDE.md`'s
own layout rule, "a source component splits the same way every time: the crawl,
the parser and the listing collector go in `control/<generation>.py`". That
describes a scraper, at the one size the word did not have.

**A scraper is a crawler, a fetcher, an optional converter and a parser**, and
the four names were read out of what the tree already said rather than picked
freely. `crawler`, `converter` and `parser` each already meant exactly that
here — "one crawler owns one pool of urls" was already the rule. The stage that
brings the bytes back is a **fetcher**, not a "scraper": `scraper` already means
the whole, in every rule and every source docstring that mentions it, and
`fetch_` is already the verb the code uses for the stage — `fetch_snapshot`,
`fetch_cached`, `fetch_body`, and `discovery.py`'s own "the caller passes its
own fetcher or parser". That was the only collision in the four, and naming it
`fetcher` is what let the whole keep the name it had.

Only two of the four are per-scraper: the crawler and the parser. The fetcher is
`fetcher/control/` and the converter `converter/control/conversion.py`, and
neither varies by source — `sources/amd` and `sources/intel` have no `control/`
layer at all, both halves coming from `q4`, and `terratec/control/early.py`
opens by saying it is not a crawler like the others.

Three things kept their names, each of which could have gone the other way:

- **`releases.source` and `--source`.** The tag is per mirror and one scraper
  may own several, so the column does not hold a scraper's name — renaming it to
  match this vocabulary would have made a false claim in every place it appears,
  and the true one is already in `CLAUDE.md`: a source tag identifies a scraper.
- **`pressroom/sources/`.** "A source component" is a fact about a path, read
  back out of the tree by `tests/test_import_direction.py`, and the directory
  groups scrapers by whose press room they recover — which is what a source is.
- **`scraper`, not `harvester`.** `harvester` is the web-archiving word for the
  whole and is free in this tree, so it was the alternative worth measuring; it
  lost because `scraper` already carries the meaning in the rulebook, in the
  ADRs and in every source docstring, and because the one thing that made the
  word ambiguous was the fetch stage, which now has its own name.

## The tree catches up with the vocabulary

The split above was prose only, and for one commit the tree said the old words
while the rulebook said the new ones. What the tree had to change was decided on
one rule: **a word the rulebook uses for a role has a place named for it, and
"it is a role inside X" is not an answer.** BCE names a component for its
responsibility and the vocabulary names the responsibilities, so a reader
entering `pressroom/` should meet the same words. `scraper/`, `fetcher/` and
`converter/` sit in the root now and read as the sentence in `CLAUDE.md`; a
`sources/<firm>/control/<generation>.py` is a crawler and a parser, which is
where the rule had always put them.

Two things had to become true in code first, because the roles leaked across
component boundaries in both directions and renaming the directories would have
named the leaks:

- **A parser in a scraper called the fetcher.** Four scrapers — `q4`,
  `soundonsound`, both `creative` modules — carried a `fetch_body(conn, session,
  url)` of one shape: `fetch_cached`, `decode_html`, one selector,
  `richtext.extract`. Named for the fetcher, four fifths parser, and the reason
  `tests/parsers.py` had a third route, `live`, that had to be handed a seeded
  `page_cache` and `session=None` to prove it stayed off the network. Each is a
  `parse_detail(content) -> Detail` now, the shape the archived ten already had;
  `discovery.from_items` and `catch_up.from_live` fetch through
  `politeness.fetch_cached` themselves and hand the bytes over, with the site's
  Crawl-delay passed as a value. The five live fixtures are detail fixtures,
  byte-identical under a new name, and the proof that a parser cannot fetch is
  its signature. (`fetch_body` is quoted above as evidence for the verb
  `fetch_`; the evidence stands, the function does not.)
- **The fetcher's module called a parser.** `archive.sample_all_captures` walked
  a listing's captures and parsed each; `archive.fetch_detail_snapshot` chose
  the best capture and parsed it. Both are a fetch composed with a parse —
  siblings of `discovery.capture()` — and both live in `scraper/control/
  discovery.py` now. `archive.py` takes no parser anywhere: `fetch_best_matching_
  snapshot` takes a scorer, which [captures.md](captures.md) says is the
  caller's measure, not a parse.

Then three components moved whole. `capture/` became `fetcher/` — both
fetchers, `page_cache`, `wayback_calls` and the address arithmetic, because the
table stays with the component that writes it; the domain noun *capture* is
untouched in every function name and in `captures.md`, whose area is which
capture is right, not who goes and gets it. `attachment/` became `converter/` —
the two converters, the magic-byte table, and `pressroom-calibrate-converters`;
the word for the *rows* (`attachment_crawl.py`, `--attachments`,
[attachments.md](attachments.md)) is the separate question of what a `.pdf` that
is the release itself should be called, and was left where it was. `scraping/`
became `scraper/` — the loops that compose the four roles, phase 1, phase 2,
`twin`, `attachment_crawl`, the parse types and the command line, which is what
a scraper *is*, with the fifteen concrete ones under `sources/`. "scraping" had
been the whole's activity naming its connective tissue, the size error in a
path; `run/` was the first candidate and lost because it is not one of the
words the tree was missing.

Three decisions inside this one, each measured before it was taken:

- **The crawler has its place already, and CDX stays with the fetcher.** Every
  scraper has a crawler; what differs is the pool — article urls off index
  pages, a CDX folder listing, a dropdown or a paginated live list; for
  `pressdb`, `media_pr`, `presse_de` and `cms`'s first channel the listing's own
  captures walked along time (`sample_all_captures`); for `early` two addresses
  given by hand. It lives in `control/<generation>.py`, as the rule says. The
  CDX listing it calls stays in `fetcher/control/archive.py`: at the move the
  listing functions had 21 calls from crawlers in nine scraper modules and 10
  from inside `archive.py` itself — `fetch_best_matching_snapshot` walks
  `list_all_captures` — and both halves share the cooldown, the error classifier
  and the `wayback_calls` log. A `crawler/` holding CDX alone would be one
  module through which two components write one log. It is the one place where
  a role's tool sits under another role's name, recorded so it reads as a
  choice rather than an oversight.
- **A collector is a composition, not a fifth role.** `cached_entries(conn) ->
  {url: Entry}` in `presse_de`, `early` and `cms` is the listing parser run by
  phase 2 over the captures the crawler knows, out of `page_cache` — the same
  fusion `fetch_body` was, from the other side. It stays fused because the three
  differ in how they find their captures (LIKE by domain, an exact capture
  address, LIKE by suffix) and one decodes cp1252 first; the shared part is ten
  lines behind a signature no fourth scraper would use. The word survives with a
  definition in the rulebook, and the one collision went: `soundonsound`'s
  `collect()` was a crawler and is `candidates()`, pressemit's word for the job.
- **`sources` stayed, and not because it was not measured** — the bullet that
  records it is with the others on that directory, below.

## One place a command line is assembled

**`boundary/command.py` is the one place a command line is assembled.** Sixteen
scrapers each carried a hand-written `__main__`, and eleven were the same five
lines. A boundary now declares its per-scraper options as values:

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
  re-extraction modes became `scraper/control/catch_up.py` and are driven by
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
  `pressroom-calibrate-converters` for the attachment converters. The second
  half is not decoration — a column of metrics once said "100% of words kept"
  about a conversion that had put the release's headline after the footer.

## The source components moved under `sources/`

`pressroom/` used to hold the firms — `intel`, `amd`, `creative`, `terratec`,
`maudio`, `soundonsound` — in the same row as `database`, `release`, `capture`
and the rest. They are a minority of the components and most of what anyone
runs, so the root read as a list of companies with the architecture filed
somewhere among it. They sit under `pressroom/sources/` now, and what is left in
the root is the shared components, `q4`, and one directory that is not one.

The tidiness is the reason it was raised. The reason it was worth doing is
underneath it: **"a source component" stopped being a list of names and became
a fact about a path.** That list was literal in three independent places —
`CLAUDE.md`'s roll-call, `SOURCE_COMPONENTS` in
`tests/test_import_direction.py`, and the company slugs in
`taxonomy/entity/company.py` — with nothing checking the first against the
third, so a seventh firm had to be added by hand in two of them before the
dependency direction below would cover it at all. The test reads the directory
now, and a firm that is in the tree is in the rule.

Four decisions inside that one, each of which could have gone the other way:

- **`sources`, not `scrapers`.** The rename the vocabulary seems to ask for, and
  the size error again: `scrapers/terratec/` says one scraper where there are
  four. Every other word for the largest size is taken — `firms` (next),
  `companies` (the reader's axis in `taxonomy/entity/company.py`, whose six keys
  are these six components but whose word belongs to the reader), `publishers`
  ([odd-sources.md](odd-sources.md)'s opposite of a press room), `sites`
  (`pressemit.SITES`, a host), `origins` (`body_origin`). "Source", in the plain
  sense of where the releases come from, fits TerraTec and Sound on Sound alike,
  and "source component" is what the test and this file already call the unit.
  That a grouping directory sits between the package and its components is the
  BCE deviation the next bullet is, and it stands.
- **`sources`, not `firms`.** `soundonsound` is a magazine, not a company whose
  press releases these are — and a source tag names a scraper, not a domain, so
  the directory is named for what the components are rather than for who they
  are about.
- **`sources/` is a grouping directory, not a component.** It gets no layers and
  no row in `../layout.md`; its `__init__.py` says what it is not, the way
  `integrity.py` does. A component owns one responsibility, and "the firms" is
  not one — inventing a responsibility for it would be `common.py` again, in a
  directory instead of a module.
- **`q4` stayed in the root.** It is the Q4 Inc. platform parser two firms
  delegate to, and the direction paragraph below already puts it on the library
  side of the rule. Moving it under `sources/` would have made "nothing outside
  `sources/` imports anything inside it" false the day it landed, for a module
  that is a dependency of two source components rather than being one.

What the move cost was one thing worth naming, because it would have passed
silently. `tests/test_import_direction.py` derived a module's component and
layer positionally, from `parts[0]` and `parts[1]` of its path under
`pressroom/`. Left alone, every source module would have reported the component
`sources` with the layer `terratec`, `SOURCE_COMPONENTS` would have matched
none of them, and the four isolation classes would have gone green by asserting
over an empty set. `_inside_component()` drops the grouping segment, and
`ImportGraphTest` now refuses an empty `pressroom/sources/` for the same reason
it refuses a graph with no edges: a rule that cannot fail is not a rule.

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
imports `scraper`, `release`, `fetcher`, `text`, `database`, `reporting` and
`q4`; none of those imports a source component. The one exception is documented
and safe: `provenance/boundary/verification.py` imports source control modules
to reproduce bodies, and nothing imports it back.
