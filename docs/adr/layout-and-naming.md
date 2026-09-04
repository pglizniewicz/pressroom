# Layout and naming

Why the tree is shaped the way it is. `README.md`'s Conventions and the system
doc carry the convention itself
— the three layers, how a scraper splits — and [../layout.md](../layout.md)
carries the map of what each component owns. What follows is the reasoning,
including what BCE says and this tree does differently, and why.

## Three sizes, and the word that named all of them

`source` named three different things here, and they are three different sizes.
A **source component** is the BCE unit, one directory per firm in the root.
A **scraper** is what one command runs, one per CMS generation, so a component
holds several. A **source** is the tag a row carries, one per mirror, so a
scraper stamps several. Nothing was wrong with the code — the prose said
"source" for all three, and the sentence that caused the confusion was
`CLAUDE.md`'s own layout rule, "a source component splits the same way every
time: the crawl, the parser and the listing collector go in
`control/<generation>.py`". That describes a scraper, at the one size the word
did not have.

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
neither varies by source — `amd` and `intel` had no `control/` of their own
layer at all, both halves coming from `q4`, and `terratec/control/early.py`
opens by saying it is not a crawler like the others.

Three names stayed:

- **`releases.source` and `--source`.** The tag is per mirror and one scraper
  may own several, so the column does not hold a scraper's name — renaming it to
  match this vocabulary would have made a false claim in every place it appears,
  and the true one is already in the system doc (D4): a source tag identifies a
  scraper.
- **"source component", the word.** It named a grouping directory once
  (`pressroom/sources/`, below); the directory is gone and the word stays, for
  the unit the test and this file already call that.
- **`scraper`, not `harvester`.** `harvester` is the web-archiving word for the
  whole and is free in this tree, so it was the alternative worth measuring; it
  was rejected because `scraper` already carries the meaning in the rulebook, in
  the ADRs and in every source docstring, and because the one thing that made
  the word ambiguous was the fetch stage, which now has its own name.

## Renaming the tree to match the vocabulary

The split above was prose only, and for one commit the tree said the old words
while the rulebook said the new ones. What the tree had to change was decided on
one rule: **a word the rulebook uses for a role has a place named for it, and
"it is a role inside X" is not an answer.** BCE names a component for its
responsibility and the vocabulary names the responsibilities, so a reader
entering `pressroom/` should see the same words. `scraper/`, `fetcher/` and
`converter/` are in the root now and read as the sentence in the system doc's
`## Ubiquitous language`; a
`<firm>/control/<generation>.py` is a crawler and a parser, which is
where the rule had always put them.

Two things had to become true in code first, because the roles crossed
component boundaries in both directions and renaming the directories would have
named those crossings:

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
  its signature. The listing parsers of the same four followed one commit
  later: `parse_listing(content)`, with the crawler fetching through
  `politeness.fetch` — the uncached path, because a live listing is never kept
  ([captures.md](captures.md)).
- **The fetcher's module called a parser.** `archive.sample_all_captures` walked
  a listing's captures and parsed each; `archive.fetch_detail_snapshot` chose
  the best capture and parsed it. Both are a fetch composed with a parse —
  siblings of `discovery.capture()` — and both are in `scraper/control/
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
`twin`, `attachment_crawl`, the parse types and the command line — a scraper,
with the fifteen concrete ones in their firms' components. "scraping" had
been the whole's activity naming the code that joins the parts, the size error
in a path; `run/` was the first candidate and was rejected because it is not
one of the words the tree was missing.

Three decisions inside this one:

- **The crawler has its place already, and CDX stays with the fetcher.** Every
  scraper has a crawler; what differs is the pool — article urls off index
  pages, a CDX folder listing, a dropdown or a paginated live list; for
  `pressdb`, `media_pr`, `presse_de` and `cms`'s first channel the listing's own
  captures in time order (`sample_all_captures`); for `early` two addresses
  given by hand. It is in `control/<generation>.py`, as the rule says. The
  CDX listing it calls stays in `fetcher/control/archive.py`: at the move the
  listing functions had 21 calls from crawlers in nine scraper modules and 10
  from inside `archive.py` itself — `fetch_best_matching_snapshot` walks
  `list_all_captures` — and both halves share the cooldown, the error classifier
  and the `wayback_calls` log. A `crawler/` holding CDX alone would be one
  module through which two components write one log. The rulebook's own
  sentence covers it: a crawler may call the fetcher for its listings, and the
  fetcher is what talks to archive.org.
- **A collector is a composition, not a fifth role.** `cached_entries(conn) ->
  {url: Entry}` in `presse_de`, `early` and `cms` is the listing parser run by
  phase 2 over the captures the crawler stored, out of `page_cache` — the same
  fusion `fetch_body` was, from the other side. It stays fused because the three
  differ in how they find their captures (LIKE by domain, an exact capture
  address, LIKE by suffix) and one decodes cp1252 first; the shared part is ten
  lines behind a signature no fourth scraper would use. The word stays, with a
  definition in the rulebook, and the one collision went: `soundonsound`'s
  `collect()` was a crawler and is `candidates()`, pressemit's word for the job.

## Where every command line is built

Sixteen scrapers each carried a hand-written `__main__`, and eleven were the
same five lines; `control/command.py` replaced them all. Two couplings came
with it. An `Option`'s `dest` is the keyword the crawl receives, so the flag and
the parameter it feeds share a name. And `run()` takes the docstring **whole**
and uses its first paragraph, because slicing it in the caller was silent:
`splitlines()[0]` drops the second half of every two-line summary and four of
these sources have one.

## The retired `backfill_` / `repair_` / `migrate_` prefixes

The `backfill_`, `repair_` and `migrate_` prefixes named a *moment* — "the gap has
been filled", "the damage has been undone" — and every one of them stayed past
that moment: `repair_encoding.py`'s own docstring ended up reading "written as a
one-off and no longer one". Two rules replaced them:

- **a pass that has to be re-run after a crawl belongs in the write path or in
  the scraper.** Provenance and the encoding repair both moved into
  `storage.store_release`/`upgrade_release` and `richtext.extract()`; the
  re-extraction modes became `scraper/control/catch_up.py` and are driven by
  the source component that owns the tag.
- **a fix that is genuinely finished gets deleted.** The code stays in git
  history (`git show <sha>:repair_cache_hashes.py`), the system doc holds the
  rule it established (D2).

The schema migrations went the same way and for the same reason. Every table's
`entity/` module carried an upgrade path beside its `CREATE TABLE` — a
`rename_before_create` for the two tables that had been renamed, a `migrate`
adding whichever columns were missing and dropping the two that were no longer
wanted, and one `PRAGMA user_version` step rebuilding an index built when only
`releases_ai` existed. All of it was already dead: there is one database, on one
machine, and it had every column, every rename and the stamped version. Code
that can lift a database out of a state nothing is in is not caution, it is
versioning with nothing to version — and it cost something real, because the
version-stamped rebuild printed a message on every *fresh* database
(`user_version` starts at 0), which is why the suite and `tests/refresh.py` both
had to suppress stdout to stay readable.

What the removal keeps is the history, here and in the other records:
`captures.md` on `wayback_cache`, `provenance.md` on `body_capture`'s two
removed columns, `grade-and-detail-id.md` on the sentinels that used to be in
`detail_id`. The names belong in the file someone opens to ask *why the shape is
what it is*; they no longer belong in code written as if it might still
encounter them.

## The read-only passes are a third kind of boundary

Next to a scraper and a reader, and writing nothing, ever. The two
`pressroom-verify-*` passes over the corpus are what a deleted repair leaves
behind: the reporting half is kept, the writing half moved into the write path.
`pressroom-calibrate-*` prints metrics *and* writes a page of full texts, because
a column of metrics once said "100% of words kept" about a conversion that had
put the release's headline after the footer.

## The source components are root components

`pressroom/` holds the firms — `amd`, `creative`, `intel`, `maudio`,
`soundonsound`, `terratec` — in the same row as `database`, `release`,
`fetcher` and the rest, and it has done so twice: before a grouping directory,
`sources/`, was put between the package and its six most-run components, and
again after that directory went. What the directory achieved was real. The root
had read as a list of companies with the architecture filed somewhere among it,
and "a source component" stopped being a list of names and became a fact about a
path: `tests/test_import_direction.py` read the directory, so a firm in the tree
was in the dependency rule without anyone adding it to a list.

It went because BCE puts a business component directly under the package, and
the directory was the one place this tree said "here we deviate" and left it at
that. A deviation an ADR records is either a decision with its cost stated or
work; this one had a preference attached. So the six are root components, and
what the directory achieved is kept another way:

- **Which components are sources is a fact about the taxonomy.**
  `taxonomy/entity/company.py` has to name every firm anyway — an unmapped
  source is a 400 in the browser — and its six keys are the six components.
  `SOURCE_COMPONENTS` is read off `company.COMPANIES`, and the test asserts each
  key is a root component with a `boundary/`, so a seventh firm is added in one
  place and covered by the direction rule the moment it has a command.
- **The word stays "source component".** `scrapers/terratec/` would say one
  scraper where there are four; `firms/` would call Sound on Sound a company
  whose releases these are. "Source", in the plain sense of where the releases
  come from, fits both, and it is what the test and this file already call the
  unit. With no directory to name, the word is vocabulary and nothing more.
- **`q4` is in the root for the same reason as before.** It is the Q4 platform
  two firms delegate to, on the library side of the direction rule: a dependency
  of two source components rather than one of them.
- **`pressroom/names.py` is at the root and is not a component.** It walks
  the tree rather than belonging to it, which is what BCE reserves the root
  for. It was called `integrity`, a word the rulebook already used for FTS's
  `integrity-check`; its command was always `pressroom-verify-names`.

`_inside_component()` in `tests/test_import_direction.py` reads a module's
component and layer positionally, `parts[0]` and `parts[1]` under `pressroom/`,
with nothing to skip.

## What BCE says here, and why

The pattern is followed where a test can hold it, and every place it is not
followed is here with its reason. A deviation this file records without one is
work, not a decision.

- **Transactions stay at the write, not in the boundary.** The pattern puts
  transaction wrapping in the boundary, around the operation an outside actor
  invokes. The operation here is a crawl that runs for an hour against a flaky
  archive, and "commit per row unless a loop batches explicitly" is what makes
  it resumable: hoisting the commit would make a run all-or-nothing. The
  intent of the rule — one wrapper, decided in one place — is met by
  `storage._transaction`, the only place a write's atomicity is decided; a body
  and its provenance are one transaction there. Kept by decision, with that
  cost stated.
- **Entities are modules, and they have behaviour.** An entity here is the
  module that owns a table: its DDL and the functions that change it —
  `page.store`, `schema.insert`/`upgrade`/`rebuild_fts`, `origin.record`/`clear`,
  `call_log.record`. No control module executes a change to a table
  (`tests/test_table_ownership.py`). What there is not is a row class: a
  `Release` object would be the row dataclass D3 rules out, and the
  pattern asks for state and behaviour, not for classes. `Grade` is the one
  closed set modelled as an enum, and it is a `StrEnum` precisely so that no
  call site had to change for it to exist.
- **A component may have one layer.** `database` and `q4` are control only,
  `taxonomy` entity only, `browser` boundary only, `reporting` control only —
  its summary was filed as an entity once, and it owns no table. The pattern
  allows it: a responsibility that is procedural, or a table, or an entry
  point, needs no more than its own layer.
- **`pressroom-search` is `release`'s boundary; `pressroom-serve` is its own
  component.** The two readers look like one kind of thing filed in two ways,
  and the question was raised (2026-09-04): make a `reader` component, or fold
  the browser into `release`. What decided it was what each one composes. The
  CLI imports `release` and `database` and nothing else: the corpus reached
  from a terminal is a boundary of the corpus. The browser composes `release`,
  `provenance`, `taxonomy`, `fetcher.address` and `database`, and owns a page
  with its own spec and `checks.md`; filed under `release`, the corpus would
  import the second axis and the capture facts, the wrong direction. A `reader`
  component would hold a docstring: what the two share is the corpus's
  answers, owned by `release/control/query.py`, and `connect_ro()`, owned by
  `database`. The pattern allows the asymmetry — control may be called by
  another component directly, the boundary is not a gate — and the one debt it
  left was in `query.py`, which described itself as "the query shapes both
  readers share" and named the browser in its comments: the corpus knowing who
  reads it. That module now states what the corpus answers, in its own words,
  and `cli_search` became `top_matches`, named for the answer, not the caller.
  The cost kept: the browser has no control layer of its own, so a query only
  the page needs still goes into `release`, phrased as a question about the
  corpus.
- **Control prints, and does not ask on its own.** The marker stream and the
  summary line are the product of a run, and `reporting` owns their vocabulary;
  routing them through the boundary would be an event bus for a command-line
  tool. The one question a scraper puts to a person is asked by the scraper's
  boundary: `command.ask_on_tty` is a library function, `command.options` hands
  it over as `confirm` on that boundary's behalf, and `catch_up.confirm_rewrite`
  takes the answer as a callable.
- **`scraper/control/command.py` is control, and `scraper` has no boundary.**
  The module was filed as a boundary first, because it does a boundary's work:
  argparse, the terminal prompt. That cost an exception in `LayerDirectionTest`
  and a sentence in the system doc naming the one boundary other boundaries
  call. What decided it was the pattern's own criterion: a boundary is what an
  outside actor reaches, and no actor reaches this module - no console script
  points at it, fifteen scraper boundaries import it. A module built for other
  components to call is control. `scraper` keeps control and entity only: the
  actor's entry point is each source component's own boundary.
- **The tests reach control and entity.** The pattern has tests as an outside
  actor that reaches only boundaries. This suite is hermetic — gates, parsers
  over gzipped captures, phase strategies over a temp database — and testing a
  pure function through the boundary would mean a console script, a 500 MB
  corpus or the network in every test. Two tests go through the boundary and
  prove what only that route can: `test_offline_is_offline` runs every command,
  `test_entry_points` proves each points at a boundary. Kept by decision.

## A component's spec is its `__init__.py` docstring; the base package's is the system doc

A component's contract — what its boundary promises, never how — is written
into the package's own docstring, as Markdown in the form the `/sbce` skill
prescribes: the one-line responsibility, then `## Boundary` (operations as
verb-noun, transport-neutral), `## Requirements` (EARS statements grouped under
`### Rn`, each carrying a stable id `Rn.m`), an optional `## Decisions`, and
`## Out of scope`. An id is never renumbered and a retired id is never reused,
because a test or a `checks.md` line carries the id of the statement it holds,
and that grep is the whole trace between spec and test. `browser/__init__.py`
is the first spec written out; every other component carries the one-liner
alone, which is the spec's first line once one is written.

One altitude up, `pressroom/__init__.py` is the system doc the same skill
describes: what spans components and has no other home. Its `## Components` is
the wiring — who may import whom, with the dependency-direction paragraph below
repeated there word for word, which is what `tests/test_import_direction.py`
reads; its `## System invariants` are `Sn` statements, each held by a test; its
`## Ubiquitous language` defines the three sizes and the five roles once; its
`## Decisions` are the `Dn` that span components, each with its rejected
alternatives and a pointer to the file here that holds the reasoning. The skill's
litmus sorts every rule: testable behaviour becomes an EARS statement, `Rn.m` in
one component's spec or `Sn` in the system doc; a point-in-time choice with
rejected alternatives becomes a `Dn`; a standing rule that is neither — a
tooling step, a "not wanted here", a coding convention no test holds — is
`README.md`'s `## Conventions`. A rule leaves that section the day it becomes an
`Rn.m` or an `Sn`, so the section shrinks as the specs are written out.

Why the docstring and not a file beside the code: every component already has
`__init__.py`, and it already holds the one-liner the spec opens with — a
second file would be one more thing for the one-liner to drift from; pydoc
renders it, so the published contract and the source of truth are one file;
and no new file kind enters the tree, so `docs/layout.md` and its test have
nothing new to map. Two alternatives were rejected. The `package-info.md` the
`/sbce` skill uses for web stacks, for the drift above. And the arrangement this
repo had until 2026-09-04: the rulebook in `CLAUDE.md`, with this directory as
its record. `CLAUDE.md` is the agent's instruction file — loaded into an agent's
session, read by no person — so a rule there was a rule only an agent could
follow, and the doctrine in `README.md` of this directory said so without anyone
noticing what it meant. `CLAUDE.md` now holds this machine's quirks and the
reading order, and imports the README and the system doc, so an agent still has
both in context while the repo owns the text.

## The dependency direction

**The dependency direction is a rule.** A source component
imports `scraper`, `release`, `fetcher`, `text`, `database`, `reporting` and
`q4`; none of those imports a source component. The one exception is documented
and safe: `provenance/boundary/verification.py` imports source control modules
to reproduce bodies, and nothing imports it back.
