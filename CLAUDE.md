# pressroom

Recovers historical company press releases — mostly from **dead sites, via the
Wayback Machine** — into one SQLite database (`pressroom.db`) with a full-text
index. One command per scraper plus two readers: `pressroom-search` (CLI) and
`pressroom-serve` (a local read-only HTTP browser on 127.0.0.1, stdlib +
vanilla JS). A scraper is the only thing anyone runs, and a rerun picks up
whatever the last one could not get. No build step, no auth, no dependencies
beyond three libraries and one extra.

## Environment

```bash
uv venv && uv pip install -e ".[globenewswire]"      # Python 3.14
pressroom-terratec-portal --limit 5
pressroom-terratec-portal --offline                  # re-extract, no network
pressroom-verify-body-origin                         # read-only checks
pressroom-search "Radium" --source midiman_com_pressdb
pressroom-serve                                      # http://127.0.0.1:8765
.venv/bin/python -m unittest discover -s tests       # the suite
```

- **Every command is a console script declared in `pyproject.toml`** — 15
  scrapers, 2 readers, 3 `verify` passes, 2 `calibrate` passes — so nothing is
  invoked as `python -m pressroom.<bc>.boundary.<x>`: the layer path is where
  the code lives and has no business in a command line.
  `pressroom-<firm>[-<generation>]` for a scraper, `pressroom-search` /
  `pressroom-serve` for the readers, `pressroom-verify-*` and
  `pressroom-calibrate-*` for the read-only passes. **After a rename or a new
  boundary, re-run `uv pip install -e ".[globenewswire]"`** or the script will
  not exist. `tests/test_entry_points.py` asserts the four families and their
  sizes, because the prose said 22 when there were 23.
- **Use `.venv/bin/python`, not bare `python3`,** whenever you run Python
  directly. `pressroom` is only on the venv's path, so bare `python3` cannot
  import any of it — which is loud, and used to be silent: everything ran on
  Fedora's system RPMs until the first dependency Fedora does not package.
  `source .venv/bin/activate.fish` if you would rather type `python3`.
- **`PRESSROOM_DB` points every command at another database.** That is how a
  whole-corpus `--force` gets rehearsed against a copy without moving a 500 MB
  file in and out of place. `pressroom-serve` also takes `--db`.
- `pyproject.toml` declares the real dependencies, pins package discovery
  (auto-discovery refuses to guess while loose modules and a package coexist,
  which is a state a move through this tree passes through), and ships the
  browser's three static files as package data. `curl_cffi` is an **extra**,
  not a base dependency. **The import stays inside
  `globenewswire.make_session()`**, so a missing `curl_cffi` breaks exactly
  one scraper instead of every module in the tree — `docs/adr/odd-sources.md`.
- `pdftotext` (poppler-utils) and `antiword` are shelled out to for attachments.
- **The `sqlite3` CLI is not installed on this machine.** Inspect the DB with
  `.venv/bin/python -c "import sqlite3; ..."`.

## Where the rules live

**This file is the rulebook: the imperative, one clause of why, and a pointer.**
It is loaded into every session, so anything that must be obeyed without being
looked up is here and nowhere else. The reasoning behind each rule — what was
tried, what it cost, which measurement settled it — is in **`docs/adr/`**, one
file per area, indexed in `docs/adr/README.md`. That archaeology is the expensive
part of this project, so it is kept, not summarised away.

**Read the `docs/adr/` file for an area before changing anything in that area.**
Every rule below points at its own; the pointer is not decoration, because the
rule states *what*, and only the record states what already went wrong when
someone did otherwise.

Three neighbours, each owning something neither of the above does: **a module
docstring** owns what markup one scraper actually meets — per-scraper
archaeology travels with its parser; **`docs/layout.md`** owns the map, which
file inside a component owns which job; and **`checks.md`** owns the rendered
page, one labelled line per observation a browser can contradict.

And **a measurement goes in a test or a verify pass, never into prose.** If you
catch yourself writing a row count into any of these files, that is the signal;
`docs/adr/numbers.md` is the 259 lines that rule replaced.

## Layout

The tree is `pressroom/<business component>/<boundary|control|entity>/`. A
component owns one responsibility and is named after it; the three layers say
who may call what. That is the whole of the convention, and the three rules
that matter in practice are:

- **the boundary is what an outside actor reaches** — a command line, an HTTP
  request. Nothing else runs a scraper or serves a page.
- **control may be called across components; entity owns a table.** A
  component's entity layer holds its table's DDL and the statements that
  change it, and nothing else creates that table.
- **the source components live under `pressroom/sources/`.** That directory
  is not a component — it owns no responsibility, has no layers, and holds
  nothing but them; the convention above simply runs one level down inside it.
  What sits there *is* a source component, which is what makes the dependency
  direction below a fact about a path rather than a list kept by hand.

Adding shared logic means a **new component named for its concern**, never a
grab-bag (`common.py` was split for exactly this reason, and `db.py` after it).

The components: `database`, `release`, `fetcher`, `provenance`, `text`,
`attachment`, `scraping`, `reporting`, `taxonomy`, `browser` and `q4`, plus one
per firm under `sources/` — `intel`, `amd`, `creative`, `terratec`, `maudio`,
`soundonsound`. **Which file inside a component owns which job is
`docs/layout.md`**: a lookup rather than a rule, which is why it is not here, and
`tests/test_layout_map.py` keeps it honest against the tree.

**Three words, three sizes, and they are not synonyms.** A **source component**
is the BCE unit: one directory per firm under `sources/`, holding one scraper
per generation. A **scraper** is what a command runs — a **crawler** that turns
what the scraper starts from into the pool phase 1 loops over (article urls,
or, where a release only ever existed inside a listing, that listing's own
captures walked along time), a **fetcher** that brings each one's bytes back
into `page_cache`, an optional **converter** for what is not HTML, and a
**parser** that lifts the release out of the bytes — one from a page, many from
a listing. A **source** is the tag a row carries, one per mirror, so one scraper
may stamp several. Only the crawler and the parser are per-scraper, and a
parser takes bytes and never fetches them: the fetcher is `fetcher/control/`,
called from `scraping/` and from a crawler reading its listings, never from a
parser; the converter is `attachment/control/conversion.py`; a scraper needing
neither writes neither. A **collector** is not a fifth role: it is the listing
parser run by phase 2 over the captures the crawler knows (`cached_entries`),
and only a scraper whose releases exist inside a listing has one.
**Prose that says "source" for the middle size is the smell, and so is a
`fetch_*` inside a parser.**

**A scraper splits the same way every time**: the crawler and the parser go in
`control/<generation>.py`, and the command line in `boundary/<generation>.py`. The module name is the **CMS generation**, not
the domain, because that is what a source tag identifies — which is why
`sources/terratec/control/pressemit.py` is not called `terratec.py` and
`sources/maudio/control/golive.py` is not called `midiman.py`.

**The markup archaeology travels with the parser.** Every control module's
docstring records what its generation's markup actually does, including the
quirks that cost time; the boundary keeps the one-line summary `--help` shows
and the usage examples. Keep writing those down in the control module — neither
this file nor `docs/adr/` is where a per-scraper quirk goes.

**`boundary/command.py` is the one place a command line is assembled.** A
boundary declares its per-scraper options as values and gets the catch-up flags
for free:

```python
def main():
    command.run(portal.scrape, __doc__, command.LIMIT)
```

An `Option`'s `dest` is the keyword the crawl receives — the flag and the
parameter it feeds are named the same thing on purpose. `run()` takes the
docstring **whole**, never `splitlines()[0]`.

**A scraper's command is the only thing anyone runs.** It owns that scraper's
whole job — discovery *and* the catch-up over everything an earlier run could
not get — and a rerun is expected to pick up exactly what the last one missed.
`--offline` makes a run free and touches nothing on the network (proved, not
assumed — `tests/test_offline_is_offline.py`); `--force` re-extracts every row
after a parser change (and asks first); `--retext` re-derives text after a
renderer change; `--seed-cache` fetches captures and parses nothing.

**There is no `backfill_`, `repair_` or `migrate_` family any more, and
reintroducing one is the smell.** A pass that has to be re-run after a crawl
belongs in the write path or in the scraper; a fix that is genuinely finished
gets deleted, because git holds the code and this file holds the rule it
established. **The same goes for the schema: there are no migrations.** Each
`entity/` layer's `SCHEMA_SQL` is the whole truth about its table; there is one
database, it is current, and a schema change is an edit to that DDL plus
whatever one-off SQL you type by hand — never a `migrate()` left in the tree.

**The read-only passes are a third kind of boundary, next to a scraper and a
reader. They write nothing, ever.** `pressroom-verify-*` checks that a claim
still holds and is run after anything that could break it — `-body-origin` (a
recorded origin really produces the body it claims), `-encoding` (nothing
repairable is stored, and the two detectors still agree), `-names` (the tree's own
names still resolve). `pressroom-calibrate-*` reviews the whole cache and prints
metrics **and** a page of full texts — `-containers` for DOM containers,
`-attachments` for the converters; the second half is not decoration.

**The dependency direction is a rule, not an accident.** A source component
imports `scraping`, `release`, `fetcher`, `text`, `database`, `reporting` and
`q4`; none of those imports a source component. The one exception is documented
and safe: `provenance/boundary/verification.py` imports source control modules
to reproduce bodies, and nothing imports it back.
`tests/test_import_direction.py` asserts both halves, and reads that list of
components back out of this sentence.

→ `docs/adr/layout-and-naming.md`

## Invariants — breaking these fails silently

1. **`fts5(title, body)` column order is load-bearing.**
   `release/control/query.py` calls `snippet(releases_fts, 1, ...)`; the `1` is
   a *positional* ordinal. Swap the columns in `release/entity/schema.py` and it
   starts snippeting titles with no error. The declaration and the ordinal are
   now one component apart rather than three files apart: the CLI reader used to
   carry its own copy of that query.
2. **All three FTS triggers must exist** (`releases_ai`/`au`/`ad`), and updates
   and deletes must use the external-content `'delete'` command form with the
   OLD values. Only `releases_ai` existed once, and every UPDATE-based recovery
   left its text unsearchable while `'integrity-check'` kept passing.
   **`integrity-check` passing is not evidence of a healthy index** — it only
   checks internal consistency, not agreement with `releases`. Verify with
   orphan/missing counts and token probes instead.
3. **A bulk change to `releases` made outside the triggers is followed
   immediately by `index.rebuild_fts()`, before anything updates a row.** The
   change leaves the index stale, and `releases_au`'s `'delete'` then subtracts
   postings for `old.body`'s tokens, which on a stale row are not the tokens
   actually indexed — so the next update actively corrupts the index rather than
   merely leaving it wrong. FTS5 verifies none of this.
4. **The path a reader walks imports stdlib only.** That is
   `database/control/`, every `entity/` layer, `release/control/query.py`,
   `taxonomy/` and `text/control/decoding.py`. The two readers are deliberately
   dependency-free; never import `requests` or `bs4` into any of those, and
   never import `fetcher/control/politeness.py`, `q4` or a source component
   into either reader. `tests/test_import_direction.py` proves that statically,
   which `pressroom-verify-names` cannot: it imports for real, and `requests` is
   installed here.
   **A reader opens the database through `connect_ro()`, never `connect()`** —
   the latter runs `init_db()`, i.e. `CREATE TABLE` and the FTS triggers, which
   a browser has no business doing. `?mode=ro` turns an accidental write into an
   OperationalError instead of a silently damaged index.
   **And it closes what it opened, inside the request** — `ThreadingHTTPServer`
   hands every request a thread of its own and joins none of them, so a
   connection kept past the end of `do_GET` is a descriptor nothing will close.
5. **`releases.url` is the dedup key** (UNIQUE) and inserts are
   `INSERT OR IGNORE`. Gate any "new" counter on `store_release()`'s bool
   return — an unconditional `count += 1` after it reports phantom inserts on
   every rerun (this was a real bug).

## Conventions

**Every script is idempotent and resumable.** These are hour-long crawls
against a flaky archive; a rerun must pick up exactly what the last one
couldn't get. Commit per row unless a loop batches explicitly (`commit=False`).

**A write that has two statements commits through `with conn:`, never a bare
`conn.commit()`** — it rolls back on an exception, and a bare commit cannot, so
a raising second statement used to leave the first in an open transaction for
the next commit on that connection to adopt. That is how a row got stored
without the `body_origin` entry the code had just refused to write. Wherever a
body write and an `origin.record`/`origin.clear` sit together, they are one
transaction and both take `commit=False`.

**A network error is not a verdict.** `discovery.fetch_detail_snapshot()` returns
`(parsed, confirmed)`; `confirmed=False` means archive.org failed, so the caller
writes *nothing* and leaves the item open to a full retry. Only a confirmed
absence may be recorded, and it is `uncertain` (`?`), never `dead`.
**`confirmed=True` now means every capture was tried** — no attempt cap, no
fetch error, no CDX listing truncated at `CDX_ROW_LIMIT` — and not merely that
the newest one came back empty.

**Report progress through `outcome.Stats`** — no per-script counters or marker
chars. The seven outcomes are fixed so a marker stream is readable without
knowing which script produced it.

### grade and detail_id

- `grade` is `full` | `teaser` | `stub`; `detail_id` is an **opaque reference**.
  **Nothing parses `detail_id`** — asking is `address.is_timestamp()`'s job, and
  only the archive-facing code asks.
- **Use `stored_grade()`, not `already_stored()`**, whenever a row might deserve
  an upgrade later. `already_stored()` is right only for "have I seen this url".
- **An upgrade that replaces a teaser body with the real article must pass
  `grade="full"`.**
- **`length(body)` stays the honest check**: `full` is only as good as what the
  scraper knew.

→ `docs/adr/grade-and-detail-id.md`

### Sources, tags and the two axes

- **A source tag identifies a scraper — a CMS generation — not a domain, and
  not a language.** One generation over two languages is **one tag**
  (`pressemit`); the language is a property of a row, legible in its url, and
  the reader loses the ability to filter by it — say so rather than keeping a
  tag for it. Tags per *mirror* are the opposite case and stay split; the line
  between them is `twin.py`'s, below.
- **One crawler owns one pool of urls, and a tag has exactly one writer.** A
  generation on several hosts can still stamp several tags (`golive`,
  `media_pr` — mirrors, not languages); two commands writing one tag is the
  smell, because the dedup then runs in whichever of them remembers to, in one
  direction. **Several discovery channels inside one crawler are the normal
  case** — an index page's links and a CDX listing of the article folder — and
  they are merged into one pool *before* the loop, never fed to it twice.
- **A dedup key that was unique per tag is not unique once tags merge.** A
  filename, a CMS `sid`: unique on one host, repeated on its sibling. Whatever
  the key is, it gains the site as its first half (`pressemit.site_of`), or the
  crawl silently reports the second host's copies already-stored.
- **A file read off a sibling domain is not a cross-source claim**, and the
  permission is not free either: the host is one of that scraper's own start
  urls, or it is `MIRROR_DOMAINS` and the claim is backed **per row**, after
  `attachment_captures` has checked magic bytes.
- **`twin.py` must never pair across tags** — duplication across tags is
  intended. Inside one tag `twin.fill` needs source + collapsed title + an exact,
  non-empty date, touches **no network**, and **never deletes or merges**.
  **That refusal is what decides whether two tags may merge at all**: across
  mirrors the same release carries the same title and date, so merging would
  collide real duplicates; across languages the title differs because the
  language does, so there is nothing to collide. Measure before merging — the
  question is how many (collapsed title, exact date) groups span the two, and
  whether any member is under `SHORT`.
- **`taxonomy/entity/company.py` is an explicit table, never a prefix rule, and
  every source needs an entry.** An unmapped source falls into `inne`, whose slug
  is not a `COMPANIES` key, so `http._sources()` answers **HTTP 400** the moment
  anyone clicks it. No SQL knows what a company is.

→ `docs/adr/sources-and-tags.md`, `docs/adr/odd-sources.md`

### The browser's panel

**A view is painted only by the route that is still current** — every loader
carries its route's `AbortController` signal and rechecks `signal.aborted` after
the awaits before it touches the DOM, because `state` is global and a late
response writes the new view's filters into the old view.

**`checks.md` owns the panel and every rule about it** — one labelled line each,
in the form a browser can contradict, which is a rule that can fail rather than
one restated here. Two consequences travel with it: the frontend keeps
`web-static`/`web-conventions` but is a JS-rendered SPA, so a run can be "all
checks pass" and never "web-static green" (`[no-js]`), and **why** the panel
sorts, labels and scrolls the way it does is the record below — read it before
changing any of it, because three of those decisions look like one-liners and
are not.

→ `docs/adr/browser-panel.md`

### Encoding

- **Never use `r.text`, and never let BeautifulSoup sniff.** Two correct choices,
  per scraper: `decoding.decode_html(content)` for pages that **claim UTF-8**, and
  `BeautifulSoup(content, from_encoding="cp1252")` for sources known to be
  **wholly pre-UTF-8** (2001-era GoLive, midiman.de) — **never `decode_html`
  there**.
- **The repair belongs at the write, not at the decode**, because some damage is
  upstream. It is already there (`richtext.extract()`,
  `storage.store_release`/`upgrade_release`), so **there is nothing to re-run and
  no repair pass to re-add**.
- **A wrong decode already stored is undone in text, never refetched.**
  `mac-roman` is deliberately not a candidate.
- **Detection lives in two places on purpose** — `decoding.C1_RE`/`MOJIBAKE_RE`
  and `schema.MOJIBAKE_SQL`, because SQLite has no regex — **and they must
  agree**: `tests/test_mirrored_rules.py`.

→ `docs/adr/encoding.md`

### Text and markup

- **`body` is by definition `to_text(body_html)`** wherever `body_html` exists,
  and **`to_text()` runs on the cleaned HTML, never the source node**. One
  `extract()` call emits both; they must never be derived separately.
- **A change to `clean()` needs the HTML rebuilt** — `--retext` is enough only
  for a change to the renderer.
- **Never write `soup.get_text(" ", strip=True)` for a body.** On a **title** it
  is still right.
- **`<img>` is preserved verbatim**, only the scheme filtered.
- **`richtext.cut_from()` must find the text node, not the element.**
- **A parser that finds no container must fall back, never blank**: keep the flat
  text and leave `body_html` NULL.
- **Every parser is DOM-based, and a selector comes from the dominant shape,
  never one sample** — `pressroom-calibrate-containers` is how you get it.
- **The allowlist lives in two places on purpose** — `richtext._ALLOWED` and
  `RICH_TAGS` in `static/app.js`, which **rebuilds every node rather than
  trusting `innerHTML`** — **and they must agree**:
  `tests/test_mirrored_rules.py`.
- **A title comes from markup that means "headline", never from the body**, and
  is written **only over an empty one**. **A new extraction rule goes in as a
  fallback, never a replacement.** The walk for a bare-text headline **stops at
  `<p>`, not at "any block"**. **The title pass writes the title and nothing
  else** — a body rewrite goes through the gate.

→ `docs/adr/text-and-markup.md`

### Captures

- **A scraper that fetches a page any way other than `archive.fetch_snapshot()`
  or `politeness.fetch_cached()` is a bug.** Every fetched byte lands in
  `page_cache`, which is what makes a parser fix cost no refetch.
- **Ask archive.org through `archive._cdx()`** — never `__wb/sparkline` or
  `__wb/calendarcaptures`.
- **Query `wayback_calls` before tuning a timeout or a sleep constant.**
- **A live source's `Crawl-delay` is honoured**, through
  `fetch_cached(sleep=...)`.
- **A per-item capture is chosen by `archive.fetch_best_matching_snapshot()`,
  never `get_latest_working_snapshot()`** — the newest HTTP-200 capture of a
  dead article url is the rebuilt site's shell page, which parses to nothing.
  Three samples, scored by the **caller's** `score(content) -> int` (0 rejects):
  the earliest capture that scores, one `LATER_PROBE_YEARS` on, and the last one
  that scores. **A tie goes to the earlier**, so the earliest copy is the
  default. `score` is the caller's because byte length is the measure backwards
  — the shell page is the bigger file — and it must be the measure the write's
  own gate uses, or the gate vetoes what the walk just picked.
  `get_latest_working_snapshot()` is left for a pagination probe and nothing else.
  **`content is None` with a timestamp still in hand means captures exist and
  none scored** — a `stub`, not a `dead`; only a `timestamp` of None says the
  archive never saw the url.
- **When a probe beats the earliest copy, the run says so after the summary** —
  `reporting/entity/selection.py`, drained by `Stats.summary()`. Not an outcome
  and not a marker; the seven are still fixed. Silence means the default held.

→ `docs/adr/captures.md`

### Provenance

- **`body_origin` is the only place a body's capture is written down**, and it is
  its own table so absence keeps meaning "no archive link for this row".
  **`wayback_url()` must never consult `detail_id`** when an entry exists.
- **`origin.record()` goes next to the body write, in the same transaction**, at
  every site that has the address in hand; a listing collector carries
  `origin_url` on each entry. `run_retext` records nothing, on purpose.
- **`address.is_capture_address()` refuses anything that is not
  `web/<14 digits>id_/…`**, so a Q4 or Drupal id raises at the write site instead
  of minting a dead link.
- **An entry must be dropped the moment it stops being true** — `origin.clear()`,
  which is why `twin.fill` calls it.
- **`from_listings` only ever UPDATEs**: a parse matching no stored URL is
  dropped, never inserted, because a row's URL is not always a page that existed.
  A scraper whose releases only ever existed inside a listing **passes a collector
  and no `retry_missing`** — its per-row fetches are guaranteed 404s.
- **`origin_url` never reaches the JSON**, and **`capture_page` is set only when
  the capture is of a different page**.
- **Run `pressroom-verify-body-origin` after any pass that touches bodies or
  origins.**

→ `docs/adr/provenance.md`

### The two phases

**`scraping/control/discovery.py` is phase 1 and has three strategies** —
`from_items`, `from_candidates`, `from_teasers`, plus `capture()` for the three
tails that are genuinely per-scraper; **what each one takes is its own
docstring.** A scraper owns everything above the loop — pagination, the
`no_crawl` guard, `limit`, the dedup that picks the best of several captures —
and nothing below it.

**Three rules hold inside every one of them**, in one copy for the same reason
phase 2's are:

1. **a network error is not a verdict**: nothing is written, the item is
   `uncertain`, and only a confirmed absence may be `dead`.
2. **every counter is gated on the write's return value.** `store_release` is
   `INSERT OR IGNORE`; an unconditional `added()` reports phantom inserts on
   every rerun, and a write that inserted nothing is `skipped`.
3. **a confirmed absence is asked about before an already-stored teaser**, and
   **a bodyless row is never `full`** — `dead` with no row from a live fetch,
   a `stub` carrying no origin from an archived candidate. **A url the archive
   never saw is `dead` unless a listing named it**, and then it is a `stub` of
   that title and date (`from_candidates(stub_if_absent=True)`): the metadata is
   what earns the row, so a url only a folder listing produced keeps nothing.

**`scraping/control/catch_up.py` is phase 2 and its six strategies are not
interchangeable** — `from_cache`, `from_listings`, `from_live`, `retry_missing`,
`seed_cache`, `retext`; **what each one needs is its own docstring.**
`catch_up()` composes them **free first, network last**.

**Three rules hold inside every strategy**, in one copy in the library so a
scraper cannot get them wrong:

1. the cursor is `body_html IS NULL`; `force=True` widens it and says so first.
2. `gate.not_shorter` applies **under** every other gate, `force` included.
3. **the gate is chosen by the address, not by a flag** (`resolution.own_page()`):
   the row's own capture goes through `safe_to_write`, some other page through
   `strict_same_text`, and with a collector such a row is skipped for
   `from_listings`.

**A listing entry is not automatically the better copy**, and `safe_to_write`
provably cannot catch that — which is what rules 1 and 2 are for.

**`--seed-cache` comes first when a parser is being redesigned.** Two things it
exists to keep you away from:

- **A `.pdf`/`.doc` row must never reach an HTML parser.** BeautifulSoup raises
  nothing on binary, so `looks_like_html()` checks magic bytes before every parse.
- **A timestamp in `detail_id` does not always name a capture of that row's own
  URL**, so a reconstructed URL can 404 permanently. CDX is the fallback, and it
  is the only way an absence becomes `dead` rather than `uncertain` forever.

→ `docs/adr/catch-up.md`

### The gates

- **Nothing is written without passing a gate in `release/control/gate.py`.**
  `safe_to_write` refuses exactly one thing: text disappearing from the
  **middle** of a body. Two middle-loss cases are allowed **by name**, and
  nothing else is.
- **`edges_only` is what separates correctly dropped chrome from a lost
  paragraph.** `kept`/`clean` cannot — `kept=False, clean=True` is the signature
  of both — so never gate on them alone.
- **A body is compared by word-character *sequence* (`text_delta`), an attachment
  conversion by *multiset* (`gate.wordchars`).** The two are not interchangeable,
  and every conversion allowance stays named and bounded.

→ `docs/adr/gates.md`

### Attachments

- **A PDF gets markup and a `.doc` does not**: `to_richtext()` off
  `pdftotext -bbox-layout`, while **a `.doc` keeps `plain_text()` and `body_html`
  NULL**.
- **Dispatch is on magic bytes, not the extension**, and **CDX's
  `statuscode:200` is necessary but not sufficient** —
  `archive.fetch_best_matching_snapshot` walks captures exhaustively, scoring
  each by how much text it yields, so a non-match is a verdict and a thinner
  early revision loses to the fuller later one.
- **The attachment network crawl is opt-in** (`--attachments`), the one honest
  exception to "a plain rerun gets everything".

→ `docs/adr/attachments.md`

## Working here

- **`.venv/bin/python -m unittest discover -s tests` first, after anything.** Two
  tiers: hermetic, over the gzipped captures in `tests/fixtures/` — no
  `pressroom.db`, no network — and corpus, in `tests/corpus/`, skipped when there
  is no database.
- **`tests/refresh.py --golden` after an intentional parser change**, then read the
  git diff. `--captures` re-picks the fixtures and needs the corpus.
- **`uvx ruff format .` and `uvx ruff check .` before a commit.** Ruff's defaults,
  nothing of our own — 88 columns, double quotes, and `target-version` read out of
  `requires-python`; the only pinned setting is `select`, so a change to those
  defaults is not silently a change here. `uvx`, never a `dev` extra: a fresh
  checkout still verifies itself with nothing installed. Formatting is proved
  AST-identical, so docstrings and `--help` are untouchable.
- **Ask the PyCharm MCP what it says about the files you touched and their
  neighbours, and read it against the same files at `HEAD`** — the report in this
  tree is never empty, so "is it clean" answers nothing and *what is new* is the
  only question it can answer. Get that baseline by linting the `git show HEAD:`
  versions out of a scratch `dupcheck/`, then delete the directory before the
  real run, because while it exists every file duplicates against its own twin.
  **The same run is what proves the inspection can still speak** — a file with
  nothing to say gets no entry at all, so a quiet report and an unanalysed one
  look identical. `Duplicated code fragment` is what makes the round trip worth
  it: ruff has no copy-paste rule at all, so after a dedup or an extraction the
  one thing it cannot answer is the question the change was asking, and a copy is
  a pair — the other half sits in a file the change never opened. Nothing below
  `warning` is visible this way, it is not a commit gate, and it is skipped where
  no IDE is attached — ruff is the one that gates.
- **`pressroom-verify-names` after any change that renames or moves a module-level
  name.** It is down to the two checks a linter cannot do — it *runs* every import,
  and it catches a local shadowing an imported module. `F821` owns undefined names
  now, and is strictly better at it; do not add that back here. It is still static,
  so two tests cover what it cannot see: `tests/test_offline_is_offline.py` (a
  scraper's phase 1 has to be *run*) and `tests/test_no_dead_module_references.py`
  (prose).
- **The tests do not replace the `verify` and `calibrate` passes, or `checks.md`** —
  those print the per-class report you read when a test goes red.
- **Copy the DB before a `'rebuild'` or a bulk update**; neither is reversible.
  `pressroom.db` and `pressroom.db.bak` are gitignored.
- **A run full of `?` markers is usually the archive, not the code** — archive.org
  intermittently refuses connections, so confirm with a bare `curl` before
  debugging a parser.
- **`tests/` stays outside the package**, so `pressroom-verify-names` sees only the
  package's own modules.
- **Not wanted here: an ORM, a query builder, a row dataclass, schema churn on
  `releases`** — plain SQL, one function per statement. `grade` is the one
  deliberate exception; provenance goes in its own table.

→ `docs/adr/working-here.md`, `docs/adr/layout-and-naming.md`

## Where the numbers live

**Not here, and not in `docs/adr/` either.** The counts are asserted where they
can fail: corpus counts and index health in `tests/corpus/` plus the
`pressroom-verify-*` passes, command and module counts in
`tests/test_entry_points.py`, the rendered page in `checks.md`. **A measurement
goes in a test or a verify pass; the prose gets the rule the measurement
established.** If you catch yourself writing a row count into a `.md` file, that
is the signal.

→ `docs/adr/numbers.md`
