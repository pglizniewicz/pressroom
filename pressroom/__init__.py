"""# pressroom
> Historical company press releases, recovered mostly from dead sites through
> the Wayback Machine into one SQLite database with a full-text index: one
> command per scraper, and two readers - a search CLI and a local browser.

This is the system doc: what spans the components and has no other home. Each
component's own contract is its `__init__.py` docstring (D1); the rules a person
follows while working here are `README.md`'s Conventions; the reasoning behind
both is `docs/adr/`, one file per area.

## Components
Every component is `pressroom/<name>/<boundary|control|entity>/`, owns one
responsibility and is named after it; which file inside one owns which job is
`docs/layout.md`. The three layers say who may call what:

- **the boundary is what an outside actor reaches** — a command line, an HTTP
  request. Nothing else runs a scraper or serves a page.
- **control may be called across components; entity owns a table.** A
  component's entity layer holds its table's DDL and the statements that
  change it, and nothing else creates that table.
- **a source component is one of the firms `taxonomy` names**, a root
  component like every other; which components are sources is read off that
  table, and the dependency direction below covers each of them.

**The dependency direction is a rule.** A source component
imports `scraper`, `release`, `fetcher`, `text`, `database`, `reporting` and
`q4`; none of those imports a source component. The one exception is documented
and safe: `provenance/boundary/verification.py` imports source control modules
to reproduce bodies, and nothing imports it back.
`tests/test_import_direction.py` asserts both halves, and reads that list of
components back out of this sentence.

The two readers — `release/boundary/search.py`, which is `pressroom-search`, and
`browser/boundary/http.py`, which is `pressroom-serve` — reach only the
stdlib-only modules S4 names, and never `fetcher/control/politeness.py`, `q4`
or a source component. Every scraper assembles its command line through
`scraper/control/command.py`; no boundary imports another component's boundary.

## System invariants
The system is the whole assembly. Each statement ends with the test that holds
it today; the id is to be embedded there.

- S1 — The system shall run every command as a console script declared in
  `pyproject.toml` that points at a boundary module — `pressroom-<firm>[-<generation>]`
  for a scraper, `pressroom-search` and `pressroom-serve` for the readers,
  `pressroom-verify-*` and `pressroom-calibrate-*` for the read-only passes —
  and never as `python -m pressroom.<bc>.boundary.<x>`.
  (`tests/test_entry_points.py`)
- S2 — The system shall change a table only through the entity layer of the
  component that owns it. (`tests/test_table_ownership.py`)
- S3 — While `--offline` is given, the system shall make no network request.
  (`tests/test_offline_is_offline.py`)
- S4 — The system shall keep every module a reader imports on the stdlib —
  `database/control/`, every `entity/` layer, `release/control/query.py`,
  `taxonomy/` and `text/control/decoding.py`, never `requests` or `bs4` there —
  and a reader shall open the database through `connect_ro()`, never
  `connect()`, and close what it opened inside the request.
  (`tests/test_import_direction.py`, `tests/browser/test_http.py`,
  `tests/release/test_index.py`)
- S5 — The system shall treat `releases.url` as the dedup key, insert with
  `INSERT OR IGNORE`, and gate every "new" counter on the write's return; a
  write that inserted nothing is `skipped`. (`tests/release/test_storage.py`,
  `tests/scraper/test_discovery.py`)
- S6 — If a fetch fails for a network reason, then the system shall write
  nothing, report the item `uncertain` and leave it open to a full retry; only a
  confirmed absence — every capture tried, no attempt cap, no fetch error, no
  CDX listing truncated at `CDX_ROW_LIMIT` — may be `dead`.
  (`tests/scraper/test_discovery.py`, `tests/fetcher/test_archive.py`)
- S7 — The system shall keep the two encoding-damage detectors —
  `decoding.C1_RE`/`MOJIBAKE_RE` and `schema.MOJIBAKE_SQL`, because SQLite has
  no regex — in agreement. (`tests/test_mirrored_rules.py`)
- S8 — The system shall keep the two tag allowlists — `richtext._ALLOWED` and
  `RICH_TAGS` in the browser's script — in agreement.
  (`tests/test_mirrored_rules.py`)
- S9 — When a body is written with a capture address, the system shall record
  the origin in the same transaction, at every site that has the address;
  `run_retext` shall record nothing. (`tests/release/test_storage.py`,
  `tests/scraper/test_catch_up.py`)

## Ubiquitous language
Three words name three different sizes, and five roles sit inside the middle
one. Prose that says "source" for the middle size is the smell.

- **Source component** — the BCE unit: one directory per firm in the root, one
  scraper per generation. One of the firms `taxonomy` names.
- **Scraper** — what a command runs: a crawler, the fetcher, an optional
  converter and a parser, over one pool of urls. One per CMS generation.
- **Crawler** — turns what the scraper starts from into the pool phase 1 loops
  over; may call the fetcher for its listings. Per-scraper.
- **Fetcher** — stores each url's bytes in `page_cache`; the `fetcher` component.
- **Converter** — handles what is not HTML, `.pdf` and `.doc`; the `converter`
  component.
- **Parser** — extracts the release from bytes, one from a page, many from a
  listing; takes bytes and never fetches them. Per-scraper.
- **Collector** — not a fifth role: the listing parser run by phase 2 over the
  captures the crawler stored.
- **Source**, **source tag** — the tag a row carries, one per mirror; a CMS
  generation, not a domain and not a language (D4), so one scraper may stamp
  several.
- **Company** — the second axis: which source tags are one firm. `taxonomy`'s
  explicit table.
- **Release** — one recovered press release, a row in `releases`, unique by url.
- **Grade** — `full` | `teaser` | `stub`: what the scraper could tell about a
  body. `length(body)` is still the check.
- **Reference**, `detail_id` — an opaque per-source id; nothing parses it.
- **Capture** — one archived copy of a url at a timestamp,
  `web/<14 digits>id_/…`.
- **Origin** — the capture a body was read out of; `body_origin`, absent when
  there is none.
- **Gate** — what decides whether a text may replace what is stored; `release`'s.
- **Phase 1**, **phase 2** — discovery over the pool, then the catch-up over
  everything an earlier run could not get; the two halves of every scraper's
  command, `scraper`'s.
- **Read-only pass** — `pressroom-verify-*` re-checks a claim after anything
  that could break it; `pressroom-calibrate-*` prints metrics and a page of full
  texts. Neither writes.
- **Reader** — `pressroom-search` and `pressroom-serve`; reads, never writes.

## Decisions
- D1 — A component's spec is its `__init__.py` docstring, Markdown in the
  `/sbce` form — the one-line responsibility, `## Boundary`, `## Requirements`
  as EARS statements under stable `Rn.m` ids, `## Decisions`, `## Out of scope`
  — and this docstring is the system doc. `browser` is the first written out;
  the others still carry the one-liner alone. _(why: every component already
  has the file and its one-liner, pydoc renders it, and no new file kind enters
  the tree; rejected: a `package-info.md` beside the code, and a rulebook in the
  agent's `CLAUDE.md`, which no person reads — `docs/adr/layout-and-naming.md`)_
- D2 — There is no `backfill_`, `repair_` or `migrate_` family and there are no
  schema migrations: a pass that has to be re-run after a crawl belongs in the
  write path or in the scraper, a finished fix is deleted, and each `entity/`
  layer's `SCHEMA_SQL` is the complete definition of its table; a schema change
  is an edit to that DDL plus one-off SQL typed by hand. _(why: a finished fix
  stays in git history, and a migration would be a second definition of the
  table; rejected: repair scripts, a migrations directory —
  `docs/adr/layout-and-naming.md`)_
- D3 — Plain SQL, one function per statement: no ORM, no query builder, no row
  dataclass, no schema churn on `releases`. `grade` is the one exception, and
  provenance went into its own table. _(why: an entity here is the module that
  owns a table — its DDL and the statements that change it — and the pattern
  asks for state and behaviour, not for classes; rejected: an ORM, a `Release`
  row class — `docs/adr/layout-and-naming.md`, `docs/adr/working-here.md`)_
- D4 — A source tag identifies a scraper, a CMS generation: not a domain and not
  a language. One generation over two languages is one tag (`pressemit`); the
  language is a property of a row, visible in its url; tags per mirror stay
  split. _(why: the tag is what a parser is written against; rejected: a tag per
  domain, a tag per language — `docs/adr/sources-and-tags.md`)_

## Stack
- Python 3.14; stdlib plus `requests`, `bs4` and `dateutil`, with `curl_cffi`
  as the `globenewswire` extra. SQLite with FTS5. `pdftotext` and `antiword`
  shelled out to for attachments.
- Skills: `/sbce` for the specs and `/bce` for the layering; `web-static` for
  the browser's page, with `checks.md` as its manifest.
- Tests: `.venv/bin/python -m unittest discover -s tests`. Lint: `uvx ruff`.
"""
