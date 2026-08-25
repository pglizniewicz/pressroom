# pressroom

Recovers historical company press releases — mostly from **dead sites, via the
Wayback Machine** — into one SQLite database (`pressroom.db`) with a full-text
index. One CLI per source plus two readers: `search.py` (CLI) and `serve.py` (a
local read-only HTTP browser on 127.0.0.1, stdlib + vanilla JS). A scraper is the
only thing anyone runs, and a rerun picks up whatever the last one could not get.
No build step, no auth, no tests.

Currently ~6700 rows across 25 sources: Intel, AMD, Creative, TerraTec (5 site
generations), Midiman/M-Audio (5 CMS generations), and Sound on Sound - the
one source that is a magazine rather than a press room, and the only live site
here with content going back to 2000.

## Environment

```bash
uv venv && uv pip install -e ".[globenewswire]"             # Python 3.14
.venv/bin/python scrape_terratec_portal.py --limit 5
.venv/bin/python scrape_terratec_portal.py --offline     # re-extract, no network
.venv/bin/python verify_body_origin.py                   # read-only checks
.venv/bin/python search.py "Radium" --source midiman_com_pressdb
.venv/bin/python serve.py                                   # http://127.0.0.1:8765
```

- **Use `.venv/bin/python`, not bare `python3`.** For a long time `.venv`
  existed and was *empty*, and everything ran on Fedora's system RPMs
  (`python3-requests`, `python3-beautifulsoup4`) — which worked right up to
  the first dependency Fedora does not package. Bare `python3` still imports
  every module, so the mistake is silent: it just cannot reach
  GlobeNewswire. `source .venv/bin/activate.fish` if you would rather type
  `python3`.
- `pyproject.toml` now declares the real dependencies (it said
  `dependencies = []` for a long time while the code imported three).
  `curl_cffi` is an **extra**, not a base dependency — one source needs it and
  imports it lazily; see the GlobeNewswire note under Conventions.
- `pdftotext` (poppler-utils) is shelled out to for PDF press releases.
- **The `sqlite3` CLI is not installed on this machine.** Inspect the DB with
  `python3 -c "import sqlite3; ..."`.

## Layout

Shared modules — each owns exactly one concern. Adding shared logic means a
**new module named for its concern**, never a grab-bag (`common.py` was split
into `fetch.py`/`q4.py` for exactly this reason):

| module | owns |
|---|---|
| `db.py` | the whole schema, migrations, `connect()`/`connect_ro()`, and every read/write helper — including the browser's query shapes |
| `serve.py` | HTTP for the browser: routing, query-param parsing, JSON. No SQL |
| `companies.py` | the source->company taxonomy: which domain tags are one firm. No SQL, no HTTP |
| `wayback.py` | everything that talks to archive.org: CDX queries + cached content fetches |
| `fetch.py` | `HEADERS`, `SLEEP`, `fetch_cached()` — talking to a live site: politeness, and not asking twice |
| `dates.py` | `iso_date()` — the single date parser |
| `encoding.py` | `decode_html()` — bytes to text when the declared charset lies |
| `richtext.py` | `extract()` — a parsed node to `(body, body_html)`: the tag allowlist, the sanitizer, the plain-text renderer. Plus `densest()`/`cut_from()`, which locate the article subtree before it is converted |
| `progress.py` | `Stats` — the shared outcome vocabulary and summary line |
| `q4.py` | the Q4 Inc. IR-platform parser (Intel + AMD only) |
| `attachments.py` | what an attachment's bytes mean: `.pdf`/`.doc` -> text (`plain_text`), or a PDF -> our HTML subset (`to_richtext`). Shells out to `pdftotext`/`antiword`; no network, no SQL. Also the two predicates that ask the same question before and after a fetch: `is_attachment_url()` on an address, `is_attachment()`/`looks_like_html()` on bytes - one magic-byte table, both directions |
| `bodygate.py` | may this text replace what is stored: `safe_to_write`, `strict_same_text`, `same_words`, `not_shorter`, and `text_delta` underneath them. `re` and `collections` only - no SQL, no network, no parser |
| `captures.py` | where a row's bytes are: `origin_key()` (what `body_origin` recorded, falling back to the address the row implies), `own_page()` - the question that picks the gate - and `cached()` |
| `reextract.py` | phase 2 of a scraper's run: the six shapes that used to be a CLI's modes, each taking the parser/collector/fetcher from its caller. Imports no scraper, so every scraper imports it |
| `attachment_crawl.py` | the second contract: phase 2 for the rows whose url is a `.pdf`/`.doc`. The extractor is the parser, the address comes from `body_origin` or a mirror domain, and the network half is opt-in |
| `twins.py` | filling a teaser from its twin row in the same source. No capture involved, which is why it is not part of `reextract.py` - and the only caller `db.clear_body_origin()` has |

**Scripts: `scrape_<source>.py` is the only thing anyone runs.** It owns a
source's whole job - discovery *and* the catch-up over everything an earlier run
could not get - and a rerun is expected to pick up exactly what the last one
missed. `--offline` makes a run free and touches nothing on the network;
`--force` re-extracts every row after a parser change (and asks first);
`--retext` re-derives text after a renderer change; `--seed-cache` fetches
captures and parses nothing.

**There is no `backfill_`, `repair_` or `migrate_` family any more, and
reintroducing one is the smell.** Those prefixes named a *moment* - "the gap has
been filled", "the damage has been undone" - and every one of them outlived it:
`repair_encoding.py`'s own docstring ended up reading "written as a one-off and
no longer one". Two rules replaced them, on 2026-08-25:

- **a pass that has to be re-run after a crawl belongs in the write path or in
  the scraper.** Provenance and the encoding repair both moved into
  `db.store_release`/`upgrade_release` and `richtext.extract()`; the re-extraction
  modes became `reextract.py` and are driven by the scraper that owns the tag.
- **a fix that is genuinely finished gets deleted.** Git holds the code (`git
  show <sha>:repair_cache_hashes.py`), this file holds what it established. The
  five deleted one-shots are recorded in the sections below, with their numbers.

`search.py` and `serve.py` = the two readers; both go through `db.py` and both open the
database read-only, so neither can touch `releases` or the FTS index.
`static/` = the browser's three files (`index.html`, `app.js`, `app.css`),
served from a name whitelist in `serve.py`. `checks.md` = the browser's
site-specific check manifest, in the format the `web-static` skill executes.
`verify_<...>.py` = a read-only check that a claim in the database still holds,
run after anything that could break it - `verify_body_origin.py` (a recorded
origin really produces the body it claims) and `verify_encoding.py` (nothing
repairable is stored, and the two detectors still agree). Both are what a
deleted repair leaves behind: the reporting half survives, the writing half
moved into the write path. `calibrate_<...>.py` = a read-only
review pass over the whole cache, printing
metrics *and* writing a page of full texts: `calibrate_containers.py` for DOM
containers, `calibrate_attachments.py` for the attachment converters. The
second half is not decoration - a column of metrics once said "100% of words
kept" about a conversion that had put the release's headline after the footer.

- `attachments.py` owns attachment extraction, `attachment_crawl.py` owns the
  crawl, and the two scrapers whose tags hold those rows
  (`scrape_midiman_pressdb.py`, `scrape_midiman_media_pr.py`) call it. System binaries, deliberately, in a repo that declares no
  Python dependencies. Dispatch on **magic bytes, not the extension** — CMS-era
  attachments are routinely mislabeled, and 7 of this corpus's ".pdf" URLs are
  an HTML soft-404.

  **PDFs and .doc files take different routes, and that asymmetry is a
  measured decision, not an omission.**

  - **PDF -> `pdftotext -bbox-layout` -> our HTML subset.** Not `-layout`: the
    bbox output is an XML tree of page/block/line/word *with coordinates and no
    composed text at all*, and the structure is derived from geometry. Words
    join into a line when the gap is under 0.15 of the line height and
    non-negative (real word spaces measure 0.20–0.30, letter-spaced display type
    0.08–0.10, and *overlapping* boxes — 97 pairs, `MAC|OS`, `OS|X` — are
    tracked capitals that must not merge). Lines sharing a y-band merge, so a
    list's marker column rejoins its text column. Blocks sort by position, not
    stream order. A line taller than 1.6× the page median outside the margins is
    a heading; one opening with a bullet or number is a list item, nested by
    indent, and a gap over 3× the leading starts a *new* list rather than a
    deeper level. Rotated blocks — the sideways banner these releases print down
    the margin — are dropped, and `rotated_text()` names them so a writer can
    account for what it left out.
  - **.doc -> `antiword -m UTF-8.txt` text, `body_html` NULL, `pre-wrap`.** The
    DocBook route (`antiword -x db`) was built, calibrated over all 67 cached
    Word files and dropped: it flattens nested lists (`GForce_M-Tron Pro_PR6.doc`
    loses both levels), loses paragraph breaks, mangles numbering and drops an
    item, and all it gained was `<strong>` on the headline. One known bad row
    stays: `m-audio_octane_pr.doc`, whose text output interleaves two overlapping
    copies of the release (`$749699.95.use`).

  **A PDF that converts to nothing is reported, never quietly given the text
  route.** A silent fallback would turn a converter failure into a row that
  merely looks worse, and nobody would learn which document broke it.
  `calibrate_attachments.py` reports three conditions with a suggestion each -
  no output, one block for a whole document, retention under 95% - and as of
  2026-08-24 none of them fire.

## Invariants — breaking these fails silently

1. **`fts5(title, body)` column order is load-bearing.** `search.py` calls
   `snippet(releases_fts, 1, ...)`; the `1` is a *positional* ordinal. Swap the
   columns and it starts snippeting titles with no error.
2. **All three FTS triggers must exist** (`releases_ai`/`au`/`ad`), and updates
   and deletes must use the external-content `'delete'` command form with the
   OLD values. Only `releases_ai` existed once, and every UPDATE-based backfill
   left its recovered text unsearchable while `'integrity-check'` kept passing.
   **`integrity-check` passing is not evidence of a healthy index** — it only
   checks internal consistency, not agreement with `releases`. Verify with
   orphan/missing counts and token probes instead.
3. **Never create `releases_au` ahead of a rebuild.** Its `'delete'` subtracts
   postings for `old.body`'s tokens, and on a stale row those are not the
   tokens actually indexed — so an update would actively corrupt the index.
   `_sync_fts_triggers()` keeps repair-then-install in one function so the
   ordering can't be got wrong.
4. **`db.py` imports stdlib only.** `search.py` and `serve.py` are
   deliberately dependency-free, and import `db` for `DB_PATH` and the query
   helpers. Never import `requests` or `bs4` into `db.py`, and never import
   `fetch`/`q4`/a scraper into either reader.
   **A reader opens the database through `connect_ro()`, never `connect()`** —
   the latter runs `init_db()`, i.e. migrations and FTS triggers, which a
   browser has no business doing. `?mode=ro` turns an accidental write into an
   OperationalError instead of a silently damaged index.
5. **`releases.url` is the dedup key** (UNIQUE) and inserts are
   `INSERT OR IGNORE`. Gate any "new" counter on `store_release()`'s bool
   return — an unconditional `count += 1` after it reports phantom inserts on
   every rerun (this was a real bug).

## Conventions

**A duplicate inside one source can be a repair, not a bug.** Across sources
duplication is intended (mirrors, no dedup key). *Within* one source the same
release sometimes exists twice because the CMS published it under two URL
schemes on one domain (`news/en_us-596.html` and
`index.php?do=media.new&ID=596`), and one copy is a listing blurb while the
other is the full article. `twins.py` fills the short one from
its twin with **no network at all** - 111 rows recovered on 2026-08-21. It
never deletes or merges: `releases.url` stays the dedup key, both URLs really
existed, so both rows stay and only `body` (and `detail_id`, to keep provenance
honest) changes. Pairing requires source + collapsed title + an exact,
non-empty date; 56 rows are skipped for having no date, because this CMS reused
headlines across years.

**Every script is idempotent and resumable.** These are hour-long crawls
against a flaky archive; a rerun must pick up exactly what the last one
couldn't get. Commit per row unless a loop batches explicitly
(`commit=False`).

**`releases.grade` records how good a row is; `detail_id` records where its
text came from. They were one field until 2026-08-22 and that was a mistake.**

- `grade` is `full` | `teaser` | `stub` — a verdict about the body. Use
  `stored_grade()`, not `already_stored()`, whenever a row might deserve an
  upgrade later: the scrapers' condition is
  `grade is not None and grade != "teaser"`, and `already_stored()` alone would
  wedge teaser rows permanently. `already_stored()` is right when the loop only
  needs "have I seen this url".
- `detail_id` is an **opaque reference** — a Wayback capture timestamp, or the
  platform's own id on the live sources (Q4's numeric ids, Drupal node ids) —
  and NULL on the 554 rows that never had one. Nothing parses it: reading it is
  `wayback.is_timestamp()`'s job, and only the archive-facing scripts ask.
- `full` is only as good as what the scraper knew. The `media_pr` and `pressdb`
  sources store a capture timestamp on rows whose body is just the listing
  blurb, so `full` there means "not marked otherwise" — `length(body)` stays the
  honest check, which is what the `short` flag is for.

Writers say it explicitly: `store_release(..., grade="teaser")` for a row that
is only a listing blurb, `grade="stub"` for title/date only, and default `full`
otherwise. **An upgrade that replaces a teaser body with the real article must
pass `grade="full"`** - otherwise the row keeps a verdict that stopped being
true and `stored_grade()` hands it to the next run as still-upgradable. Five
writers and four upgrade sites were switched over; nothing writes those two
strings into `detail_id` any more.

The union cost more than it looks: **seven** places re-derived which kind of
value a `detail_id` held, six of them by counting digits (two regexes, a SQL
GLOB, `len()==14 and isdigit()` twice, a bare `isdigit()`, and a string compare
in the browser). Worse, the digit rule quietly **constrained what a new source
was allowed to store** - `scrape_soundonsound`'s docstring says so outright.
`grade` is a column on `releases`, i.e. the first deliberate exception to "no
schema churn on `releases`" below; the migration is idempotent
(`db._migrate_grade`) and moved 554 rows without touching a title or a body.

**A network error is not a verdict.** `wayback.fetch_detail_snapshot()` returns
`(parsed, confirmed)`: `confirmed=False` means archive.org failed, so the
caller must write *nothing* and leave the item open to a full retry. Only a
confirmed absence may be recorded as a fallback. Report it as `uncertain`
(`?`), never as `dead`.

**A source tag identifies a scraper - a CMS generation - not a domain.** That
is the intent, and the tags only look domain-shaped because most scrapers were
pointed at one host. Six of them cover several: `scrape_midiman_media_pr` has
three start urls in its own `DOMAINS` dict (midiman.com, midiman.net,
m-audio.com), one CMS, one parser - and stamps three different tags on the
result. `scrape_midiman_news` does it four ways. Read the other way round, the
tag answers the question it exists for: *what did this scraper work on, what
does it already hold, what could it still fetch, what did it skip after a
change.*

Two consequences, and the first one cost real time. **A file read off a sibling
domain is not a cross-source claim** - 205 attachment rows hold text extracted
from a capture of another host, and treating that as foreign is what left them
without provenance and without markup for a day. But the permission comes from
two different places and the difference is worth keeping straight, because the
first version of this paragraph got it wrong:

- **189 rows: the host is one of that scraper's own start urls.** `media_pr`
  lists all three (midiman.com, midiman.net, m-audio.com) in its `DOMAINS`, so
  a `midiman_net_media_pr` row reading m-audio.com is the scraper fetching from
  its own second entry. Nothing to justify.
- **16 rows: it is not.** `scrape_midiman_pressdb` only ever crawled
  midiman.com and midiman.net, yet 16 of its rows hold text from m-audio.com.
  That permission is `MIRROR_DOMAINS` in the attachment backfill - an
  attachment-level claim that the three hosts served the same release *files*
  through the Midiman -> M-Audio transition - and it is backed per row rather
  than taken on faith: identical path, host swapped, and the extracted text
  character-identical to what the row already held (16 of 16).

Four more rows looked like that class and were not: their sibling-domain capture
is the original server's `509 Bandwidth Limit Exceeded` page, so the recorded
origin was a false statement and got deleted. `attachment_captures` now checks
magic bytes before recording a candidate, the same rule `is_attachment` has
always applied to extraction.

What the browser must not do is call any of this a listing:
`serve.capture_kind()` separates "a copy from another domain" (same file name)
from "a capture of a different page", and the badge says which. The link is
labelled with the *capture's* timestamp too, not the row's `detail_id` - for
these rows the two differ.

**Merging tags is a separate, mechanical change and has not been done.** Ten of
the 25 tags differ only by domain (four scrapers); the other splits are
`_de`/`_en`, where the text genuinely differs and the split is right. Collapsing
the ten would move the panel, the audit, `companies.py` and five `checks.md`
fixtures, so it wants its own pass.

**Duplication across tags is still intended for HTML bodies.** The same release
lives on several mirrors under unrelated URL schemes, their bodies are separate
extractions, and `twins.py` refuses to pair across tags for
exactly that reason - within one tag it fills 111 rows from a twin, across tags
it would invent a fact. The measured picture: 238 groups (493 rows) share a tag
*and* byte-identical text, 123 groups share a domain but not a tag (mostly the
DE/EN pairs), and 219 groups share text across tags.

That makes `source` the right axis for debugging a scraper and the wrong one
for reading the corpus, so **`companies.py` owns a second axis**: 5 firms over
the 25 tags (Intel, Midiman/M-Audio, AMD, TerraTec, Creative, Sound on Sound). The browser
shows firms by default and the flat tag list behind a switch. It is an explicit
table, never a prefix rule - an unmapped source lands in a visible
`inne`/"Nieprzypisane" bucket rather than vanishing from the counts. A company
filter is translated to a list of sources in `serve.py`, so no SQL knows what a
company is.

**A source carries its year range in the panel, a company does not.** The
range is what tells you which CMS generation a tag covers (`midiman_com_media_pr`
2002–2006 against `maudio_com_media_news` 2003–2013), which is a per-source
debugging fact; rolled up to a company it is always "1996–2026" and says
nothing. It sits on its own line under the tag because the longest tag already
fills the 15rem panel, and sharing the line would truncate the one string you
came to read. The full ISO span stays in the `title` on both axes.

**The two panel lists are sorted differently, and that is the point.** The API
sorts both by size, which is right for the audit table and wrong for a picker;
the frontend re-sorts, with "wszystkie" pinned first.

- **companies**: alphabetical (`localeCompare(…, "pl")`), a flat list. You come
  here knowing the name.
- **sources**: grouped under their company, groups alphabetical, and **inside a
  group chronological by `first`**. Alphabetically the tags interleave by
  domain (`maudio_com_*`, `midiman_com_*`, `midiman_net_*`) and the site's
  history is lost; chronologically the group reads as the succession of CMS
  generations it actually is - `terratec_early` 1996, then `terratec_de`,
  then the portal, then `terratec_new_*` 2011. Sorting is on the full ISO
  dates, not the displayed years, so two tags showing "2001–2003" still land
  in the right order. A source with no dated row sorts last: `""` would
  otherwise beat 1996.

The group label needs to out-rank the tags under it, and the first two
attempts show what that means. Muted grey at 0.75rem was *smaller and paler*
than the 0.85rem tag names below it, so it read as a caption on the item above;
it also shared `--muted` with the year lines and competed with them. Full
`--fg`, uppercase with letter-spacing, and a `--group-bg` band fixed it. That
band gets **its own token pair** rather than reusing `--surface`: `--surface`
is the item hover colour, and a label painted with it reads as a hovered row.

The groups are nested `<ul>`s with an `aria-label`, not headings - the panel
already owns the only `h2` in that nav, and five more headings would clutter a
screen reader's outline to say what the list structure already says. **Picking is a switch by default** - one click,
one firm (re-clicking the only picked one clears back to "wszystkie") - and
**Ctrl/Cmd-click** adds or drops one so several can be combined. The `href` is
always the single-select target, so a panel item stays a real link; only the
modifier click is intercepted with `preventDefault()`, which knowingly costs
open-in-new-tab there. Panel items are filter toggles, not destinations.

**The panel is its own scroll container from 60rem up, and `sticky` alone was
not enough.** With only `position: sticky` the wheel over the source list moved
the *results*: a sticky element taller than the viewport travels with the page
until its bottom edge arrives, and it has nothing to scroll of its own. It needs
`max-block-size` + `overflow-y: auto` as well. **Not**
`overscroll-behavior: contain`: measured on 2026-08-22, the companies list fits,
which makes the panel a scroll container with zero range - and Chrome still ends
the chain there, so a wheel over the short panel moved nothing at all. Chaining
once the sources list hits its end is the ordinary sidebar behaviour.
The panel also keeps a `--gap` of `padding-inline-end`, and the grid column is
`calc(15rem + var(--gap))` to pay for it: an overlay scrollbar paints *on top of*
content, so without that strip it crossed the end of a picked item's highlight -
and taking the strip out of the 15rem instead wrapped the three longest tags to
a third line. `scrollbar-gutter: stable` is not the tool: it is defined to
reserve nothing when the scrollbar is an overlay, which this one is
(`offsetWidth == clientWidth`).
Both the sticky offset and that max height are `100dvh` minus the topbar, so
**`--topbar-h` is measured in `app.js` with a `ResizeObserver`** rather than
hardcoded twice: the topbar is not a fixed height, the filter form wraps to a
second row between 60rem and ~72rem. `app.css` keeps the old `8.5rem` as the
fallback for the first paint. Below 60rem the panel is a plain block above the
content and gets none of this.

**The browser's frontend follows the `web-static`/`web-conventions` rules**
(semantic HTML, one `<main>`, no skipped heading levels, real `<label>`s, skip
link, design tokens with a dark set, mobile-first `min-width` breakpoints,
`:focus-visible`, `prefers-reduced-motion`, Baseline-checked CSS) **with one
deliberate deviation: web-static forbids JavaScript entirely and this page is a
JS-rendered SPA.** By that skill's own routing rule the page belongs to
`web-components`. Keep the conventions, and keep reporting the deviation
instead of claiming the skill is green - `checks.md` records it as `[no-js]`.

**Never use `r.text`, and never let BeautifulSoup sniff.** These sites are
pre-UTF-8 or half-converted, and `requests` guesses Latin-1 while bs4 falls
back to chardet — which on this corpus has picked windows-1250 and even
windows-1258 (Vietnamese). Both mangle `™` (`0x99`), `„` (`0x84`) and smart
quotes. `wayback.fetch_snapshot()` returns bytes so the decision is always
explicit, and records three diagnostic encoding signals per capture in
`page_cache`. Two correct choices, per source:

- `encoding.decode_html(content)` for pages that **claim UTF-8** — UTF-8 with a
  per-byte cp1252 fallback. Needed because a page can be UTF-8 with a few
  Word-pasted cp1252 bytes in it, and a strict decode failing sends the whole
  file to chardet. Three stray bytes in one footer corrupted 14 titles this way.
- `BeautifulSoup(content, from_encoding="cp1252")` for sources known to be
  **wholly pre-UTF-8** (2001-era GoLive pages, midiman.de). Don't use
  `decode_html` there: in prose full of accents, two adjacent high bytes can
  coincidentally form a valid UTF-8 sequence and would be honoured as one.

Damage is greppable: `â€` means UTF-8 read as something 8-bit; a raw C1
control character (`\x99` etc.) means cp1252 read as ISO-8859-1. Because
`page_cache` holds the original bytes, both are repairable with no refetch — and
that is now what a rerun of the scraper does, since the repair lives in the write
path.

**The repair is in the write path, so there is nothing to re-run** (since
2026-08-25). It used to be a rule - "re-run `repair_encoding.py` after any pass
that rewrites bodies from the network" - and the rule is exactly what failed: the
2026-08-21 re-extraction brought 10 amd rows and 24 creative rows straight back
after the 2026-08-20 repair had fixed them, because a refetch overwrites a
text-level repair and nobody ran the follow-up.

It has to be at the write, not at the decode, because some of this damage is
*upstream*: ir.amd.com serves `\xc2\x99` — valid UTF-8 for the C1 control
U+0099 — where it means `™`, so decoding it *correctly* still yields a control
character. Two places, each owning what it can keep consistent:

- **`richtext.extract()`** repairs the emitted HTML **before** `to_text()` renders
  from it. That ordering is the point: `body` is by definition
  `to_text(body_html)`, and repairing the two independently could break it -
  `undo_mojibake` accepts a round trip only when every qualifying codepage
  agrees, and text with tags in it can answer that differently from text without.
  Measured after the change: 5919 rows with markup, **0 mismatches**.
- **`db.store_release`/`upgrade_release`** repair a `title` (no markup twin) and a
  `body` whose `body_html` is NULL (attachments, flat fallbacks).

`db.REPAIRS` counts what was undone, by method, and `progress.Stats.summary()`
prints it - a run that fixes 10 amd rows says so on the way past. Silence means
there was nothing to fix. `verify_encoding.py` is the read-only check that
replaced the script's reporting; its expected output is **0 repairable, 1
refused** (#4978's three 0x81 bytes), plus the two detectors agreeing.

**A wrong decode already stored in the DB is undone in text, not refetched.**
`encoding.repair_text()` owns both inversions, and the historical pass that
applied them across the corpus did 157 rows over 8 sources on 2026-08-20: C1 characters go back through cp1252
*per character* — a whole-string `encode("latin-1")` dies on the mixed rows
that need it most — and mojibake is re-encoded with the charset it was misread
as, accepted only when the round trip leaves no markers and every qualifying
codepage agrees. The misreads were cp1252 and, on German prose,
**cp1258** (Vietnamese) — chardet's guess, not a typo. `mac-roman` is
deliberately not a candidate: it re-encodes cleanly and produces garbage.

Detection lives in two places on purpose: `encoding.C1_RE`/`MOJIBAKE_RE` for
the repair, and `db.py`'s `_MOJIBAKE_SQL` for the browser's audit view, because
SQLite has no regex. `verify_encoding.py` compares the two counts on every run
for exactly this reason. Change the rule and change both — and note that they had
in fact drifted: the SQL named five C1 codepoints by hand while the regex
always matched the whole `0x80-0x9F` range, so the audit view reported 12
damaged rows where the repair found 35. `_C1_SQL` now generates all 32
`instr()` calls from the same bounds. The two agree exactly today (1 row).

**A release is stored twice: `body` and `body_html`.** `body` is plain text
with real paragraphs — the FTS index, `search.py`'s snippets and every
`length(body)` heuristic read it. `body_html` is the small allowlisted subset
`richtext.py` emits (`p`, `br`, `h3`/`h4`, `ul`/`ol`/`li`, `blockquote`,
tables, `strong`/`em`, `a`, `img`). Both come out of one `richtext.extract()`
call, and `to_text()` runs on the *cleaned HTML*, never on the source node, so
the indexed text and the displayed markup cannot drift apart.

Never write `soup.get_text(" ", strip=True)` for a body again. That is what
flattened the whole corpus: it replaces every `</p>`, `<li>` and `<br>` with a
single space, so a 4000-character release arrived as one unbroken blob. The
newlines that did survive were the *source file's* line wrapping, which
`strip=True` leaves inside a text node — real structure gone, indentation
kept. `get_text(strip=True)` on a **title** is still right.

Three things `richtext.py` does that are decisions, not cleanup:

- **`<img>` is preserved verbatim** — `src`, `alt`, `title`, `width`,
  `height`, only the scheme filtered. These sites are dead so most of those
  addresses resolve to nothing, but they are the only record of which image
  belonged where. The browser degrades a failed load to a caption; the stored
  markup is untouched.
- **A table with no row wider than one cell is demoted to blocks.** Every one
  of these CMSes laid its pages out in nested tables; a real spec sheet has
  2+ columns and survives. Pruning and demoting alternate in a bounded loop,
  because a page header only looks like a one-cell wrapper *after* its
  image-only rows have been pruned.
- **Empty blocks are pruned, and for the table family an image does not count
  as content.** That is what separates a product photo in its own paragraph
  from a page banner built out of `top.gif` and 1x1 spacers.

**A title comes from markup that means "headline", never from the body.**
54 rows carried an empty `title` until 2026-08-22, when a one-shot pass
recovered 37 of them from `page_cache` with no network at all. The rule it was
built on lives in `reextract._fill_title` now: a title is written **only over an
empty one**, never as a replacement. Four unrelated
causes, which is why one fix would not have done it: `terratec_early` never
extracted a title in the first place (those two 1996-97 pages have no headline
markup - `scrape_terratec_early.headline()` reads it off the rigid
`Presseinformation vom <date>:` dateline instead); `terratec`/`terratec_de` hit
three headline shapes the bold-tag rule cannot see; `terratec_pressen` simply
predates `TITLE_TAG_MONTH_RE`; and `terratec_pressde` has two templates the
`<title>` regexes miss - `print.php`'s `font.print-title`, and one article whose
`<title>` carries no date prefix.

Three rules the repair is built on, each of which cost a measurement:

- **A new extraction rule goes in as a *fallback*, never as a replacement.**
  `scrape_terratec.find_headline` runs only when the caller's own bold-tag rule
  returns "". Tried the other way round first, it filled 8 rows and *changed*
  30 - 12 of them from a correct title to an empty one. As a fallback: 380
  identical, 16 filled, 0 changed, measured over every cached capture of the
  four sources.
- **The walk for a bare-text headline stops at `<p>`, not at "any block".**
  `<tr>`/`<td>` are the container being walked into, so stopping there ends the
  walk before any text; stopping at `<p>` is the whole point, because a cell
  that opens with a paragraph has no headline and descending into it titles the
  row with the release's first sentence. There is a `MAX_HEADLINE` bound behind
  that for whatever slips through.
- **The script writes the title and nothing else.** Several of these captures
  now parse to a better *body* too (portal sid=367 goes from 0 to 4547
  characters), but a body rewrite belongs to the re-extraction shapes, which go
  through `bodygate`. Widening the repair to "everything the parser now returns"
  would be a bulk body update with no gate on it.

17 rows keep an empty title and that is the end state: ten PHP-Nuke skeletons
archive.org captured with no article in them, five 290-byte terratec.de
placeholders, and one French release that opens straight into prose. The
browser renders `(bez tytułu)` for them - checked as `[untitled]`.

A separate, dormant discrepancy this measurement turned up: for **14
`terratec_de` rows the current parser disagrees with the stored title**, mostly
by collapsing a literal `\r\n      ` the listing pass stored inside it, but
twice substantively (#4168 `GeForce FX: Neue Mystify 5800…` vs `GeForce`, #4172
a title vs nothing). Nothing writes them today - no mode of
anything passes a title over a non-empty one - so they are recorded here rather
than fixed blind.

**A row's URL is not always a page that existed.** Two shapes, and they need
opposite treatment:

- *page-derived* - the row has its own capture (`terratec`, `terratec_de`,
  the portals, the 2001 GoLive pages). `--seed-cache`, then `from_cache` with
  the scraper's own parser.
- *listing-derived* - the release only ever existed inside a listing, so the
  scraper minted the URL. `scrape_midiman_de` builds
  `/press/{slug}-{date}`; `scrape_terratec_early` uses `presse2.htm#p20`
  anchors into one page. Their `detail_id` is the *listing* capture's
  timestamp, so a per-row fetch is 64 guaranteed 404s - hence
  a per-row fetch would be pointless. Their scrapers therefore pass a
  *collector* and no `retry_missing`: `from_listings` walks the cached listing
  captures instead and matches back by URL. It only ever UPDATEs: a parse matching no
  stored URL is dropped, never inserted, so a re-extraction cannot mint rows
  under URLs nobody has seen.
- *listing-derived with a real URL* - `terratec_new_de`/`_en`. The hrefs were
  genuinely on the page, so these are not synthetic; archive.org simply never
  captured 15 of them (CDX says zero, confirmed 2026-08-22 - `retry_missing`
  reports all 15 as `dead`). Same treatment as the synthetic ones, opposite
  reason: nothing to fetch because nothing was ever there.

**The listing shape needs both of its guards, and it got them the hard way.**
It was once the one mode with no `body_html IS NULL` cursor of its own and no
length floor, and joining `terratec_new` to it cost data within one run on
2026-08-22: the pass walked all 159 rows, not the 24 pending ones, and **122 full
articles were overwritten by their listing teasers** (#4445 4287 -> 359
characters). This CMS embeds the full text on the listing for recent releases and
truncates older entries, so a listing entry is not automatically the better copy.
`safe_to_write` cannot catch it: it refuses text vanishing from the *middle*, and
a lost tail is `edges_only` - the same signature as correctly dropped nav.

Both guards now live in `reextract.py`, applying to **every** shape rather than
to the one that was caught: the cursor, and `bodygate.not_shorter` under every
other gate including `--force`. Re-checked on 2026-08-25 by running
`--force` over `terratec_new_de` on a copy - the floor held back every listing
entry that was shorter, **0 rows changed, 0 shrank**. The original loss was
restored from the pre-run copy of the DB, which is why that copy is a rule here
and not advice.

**A timestamp does not say which page it is a capture of, and `body_origin`
is where that is written down.** `releases.detail_id` holds the capture a row's
text came from, which for a listing-derived row is the *listing's* timestamp -
honest, and not enough: `serve.wayback_url()` built `web/<ts>/<row url>` from
it, a capture that never existed. That is what #4414 was reported for, and the
capture was never lost - `web/20111011173713/…/presse.html` holds that
release's full text, character for character what the row stores, while CDX has
no capture of the article's own URL at all.

A one-shot pass recovered the pairing from `page_cache` with no
network: candidates are only the cached captures carrying the **same
timestamp** - so `detail_id` still does the identifying, this never goes
looking for a plausible page - and the body is then located in the candidate's
raw bytes via ASCII-only slices decoded latin-1, which is what makes the test
independent of the page's charset. 158 of 541 rows resolved, 0 ties, weakest
match 0.625; one resolved link per source was checked against archive.org and
all 14 answered 200. The threshold is 0.6 rather than 1.0 because a slice can
straddle an entity or a tag on the very page the text came from.

It is its own table, not a column on `releases`: absence has to keep meaning
"no archive link for this row". **Two columns**, and it took two removals to get
there. `page_url` agreed with `origin_url` in 158 of 158 rows because one is a
prefix of the other; `origin_url` survived because it is the whole address and
the `page_cache` key, while rebuilding it the other way would need
`releases.detail_id`, which a recovery can rewrite underneath -
`serve.wayback_url()` therefore never consults the timestamp when an entry
exists. And `matched` - the fraction of body probes found when an address had to
be *searched for* - went once its three classes turned out to be derivable from
the address itself:

    computed   capture_url == web/<detail_id>id_/<url>   1255 rows
    located    the row's url is a .pdf/.doc, bytes found under that path   323
    inferred   neither                                   149

`verify_body_origin.origin_class()` computes that, and it agreed with `matched`
on all 149 rows and on no others before the column was dropped. The number was
never the useful thing: 88 of the 149 scored below 1.0 and are right, four
attachment rows scored a perfect 1.0 and were wrong. What a reader wants is
whether the body can be *produced* from those bytes, which is a different
question and has its own script.

**`verify_body_origin.py` answers it by reproduction** - the source's own parser
over the recorded capture, compared to what the row stores. Read-only. Measured
2026-08-25 over all 1727 entries: **149 of 149 inferred reproduce exactly**,
321 of 323 located ones do (2 differ in whitespace), and 1197 of 1255 computed
ones. Of the rest, 34 have no cached bytes to check against, 21 are
`terratec_early`, whose bodies are anchors into one listing page that only the
url-keyed collector can separate, and **two are known**: #4355, where the page
itself carries `\xc2\x96` where a dash belongs and the row was repaired (so the
database was better than a fresh parse - and since the repair moved into
`richtext.extract()`, a fresh parse now agrees with it, which is why this row
stopped being reported on 2026-08-25), and #6211, whose stored body
is several releases concatenated by an old extraction - rewriting it would delete
text belonging to other rows. Run it after any pass that touches bodies or
origins; it is the guarantee `matched` only pretended to be.

**The pass that reads the bytes records where they came from** (2026-08-25).
`db.record_body_origin()` is now called next to the body write, in the same
transaction, at all five re-extraction sites that have the address in hand:
`reextract`'s `from_cache`, `from_listings` and `retry_missing`, and
`attachment_crawl`'s crawl, cache re-extraction and richtext conversion. The
listing collectors carry `origin_url` on each entry for it - they used to parse
a capture and throw its address away. `run_retext` records nothing on purpose:
its text comes from the stored HTML, no capture involved.

Before this, all 1727 entries were written by a repair pass *after the fact*,
which is why 149 of them had to be found by searching captures for the body's
text, and why four entries pointed at the original server's error page until a
content check caught them. Neither can happen to a row written from now on.

**The scrapers are converted too, since 2026-08-25, and the argument for not
converting them turned out to be wrong in both halves.** It read: "the deliberate
trade - one documented ritual, against threading a `capture_url=` argument
through ~25 `store_release` call sites where passing a platform id by mistake
would silently mint dead links."

- *The argument was never really threaded.* `wayback.sample_all_captures()` and
  `fetch_detail_snapshot()` already know the address they fetched; they stamp it
  onto what they return, so a scraper passes a value that was already in scope.
- *"Silently" is fixed.* `db.is_capture_address()` requires
  `web/<14 digits>id_/…`; anything else - a Q4 numeric id, a Drupal node id, a
  bare row url - raises `ValueError` at the write site. Measured against all
  1727 entries that existed when the guard landed: every one passes, and no
  live-source row has an entry at all.

The repair got one last run before deletion, and the run is the proof it was
spent: **0 rows derived, 144 re-resolved to the same addresses they already
held, 0 changed.** What is left is 36 attachment rows with a timestamp and no
entry, because their bytes are in `page_cache` under no name at all - the
documented end state, not a backlog.

**`page_cache.fetched_at` since 2026-08-25.** Filled at insert by both write
sites; NULL on the 6345 older rows, which is honest - the table never recorded
it, and a cache *hit* is not logged as an attempt either, so "when did these
bytes arrive" had no answer at all. That is what made the question "how did a
`midiman_net_pressdb` row end up with m-audio.com bytes" answerable only from
code and two 404s in `wayback_calls`.

**An entry must be dropped the moment it stops being true.** Any pass that
rewrites a body from a capture of the row's *own* url calls
`db.clear_body_origin()`. `twins.fill` is the one caller: the text it writes
came out of a *sibling row*, so whatever capture was recorded has stopped
describing it. The re-extraction shapes instead *record* the address they read,
which is a no-op on the common path and the point on any path where the key came
from elsewhere. Without one or the other a
later recovery would leave the browser linking a listing for text that no
longer came from one, and nothing would ever notice.

**The table covers every Wayback row, not only the discrepant ones** (1763:
1605 derived, 158 measured), and that is what lets `serve.wayback_url()` be two
lines with no idea what a timestamp looks like - a row with no entry gets no
link, which is the right answer for the live sources too. `matched` carries the
difference between a measured entry and a derived one, and the class is
computable from the address itself (`verify_body_origin.origin_class()`).

**Nothing has to be re-run after a crawl any more.** The pass that reads the
bytes records the address, in the same transaction as the body - see the write
path above - so a new Wayback row arrives with its archive link already correct.

`db.py` joins the table into both the detail row and the list rows, and the
browser names the page in plain text next to the link - an unannotated link to
another page would read as the article's own capture, which is the misreading
this started from. `origin_url` never reaches the JSON: `serve.py` turns it
into `wayback_url` plus `capture_page` and drops it, so the reader has the two
strings it renders and no third representation to keep in agreement.
`capture_page` is **only** set when the capture is of a different page - once
the table covered all 1763 rows, the first cut of this annotated every one of
them, including the 1605 whose capture is of their own page. Checked as
`[capture-of-listing]` and `[capture-of-own-page]`.

**"z listingu" is provenance; "teaser" is a grade. They are different badges
because the obvious shortcut does not survive measurement.** The teaser badge
fires on `detail_id IN ('teaser', 'stub')`, i.e. only where a scraper *knew* the
body was a blurb - and a listing-derived row stores a timestamp instead, so it
can never fire there. Three attempts at deriving the grade, all measured on
terratec_new:

- **the "weiterlesen..." link is not a signal.** All **821 of 821** listing
  entries carry one, including the ones whose text is identical to the article's.
  It means "here is the article page", not "this is truncated". `extract_entries`
  drops it (`a.arrow`) and that is right.
- **truncation is real but era-dependent, not per-row.** Of the 125 releases
  whose listing and article versions are both cached, 61 listing versions are
  >10% shorter - but split by year that is 46/46 in 2007 against 9-38% from
  2008 on, and the median ratio overall is 0.98. Those rows already store the
  article version (the cache reparse upgraded them), so the grade only matters for
  the 11 rows where nothing else exists - and for those there is nothing to
  compare against. #4414 is 2011, in the era where the listing carried the full
  release.
- **"the capture serves several rows" does not identify a listing.** 90 of the
  158 resolved captures look single-row, because the siblings on the same
  listing page resolved to captures of their own.

So the badge states the one thing that is certain - the text was read off a page
that is not this release's own - and leaves the grade alone. `capture_page`
rides on every row (list and detail), so it costs no extra query. Checked as
`[badge-listing]`.

**`reextract.py` has six shapes and they are not interchangeable.** They were a
single script's CLI modes until 2026-08-25; now they are functions a scraper
composes, with the parser passed in rather than looked up in a registry:

| shape | what it needs | what it does |
|---|---|---|
| `from_cache` | the source's parser | reparse the bytes `page_cache` already holds. No network, ever |
| `from_listings` | a url-keyed collector | re-walk cached listing captures for bodies that only ever existed inside one |
| `from_live` | the source's `fetch_body` | re-fetch from a site that is still up, through `fetch_cached`, so a cached page costs nothing |
| `retry_missing` | the source's parser | the row's own capture, then CDX when that 404s. The only shape that can turn "retried forever" into `dead` |
| `seed_cache` | nothing | fetch captures, parse nothing |
| `retext` | nothing but the DB | re-derive `body` from the stored `body_html` |

`catch_up()` composes them in that order - free first, network last - and
`run(conn, source, opts, **pieces)` is the two-line call a scraper makes. A
source with no listing collector passes none and the listing shape does not run:
that is what the old registry's membership tests became.

**Three rules hold inside every shape, and each cost data before it was a rule.**
They are in the library, in one copy, precisely so that a scraper cannot get them
wrong:

1. the cursor is `body_html IS NULL`; `force=True` widens it and says so first.
2. `bodygate.not_shorter` applies **under** every other gate, `force` included.
3. **the gate is chosen by the address, not by a flag.** `captures.own_page()`
   decides: a capture of the row's own url goes through `safe_to_write`, a
   capture of some other page through `strict_same_text` - and if the caller has
   a collector, such a row is skipped here and handled by `from_listings`. This
   replaced a `--relocated` mode plus a registry-membership test, and it caught a
   live bug the moment it landed: the old `--from-cache` would have handed #5012
   (`midiman_net_pressdb`) the *listing* capture its 413-character teaser came
   from, and a whole-page `parse_detail` returns the whole listing - 34k
   characters of other releases' text, which `safe_to_write` reads as a teaser
   recovering its article and allows. One row corpus-wide today; the same shape
   cost 64 rows once.

**`--seed-cache` comes first when a parser is being redesigned** (on the
scraper: `python scrape_terratec_portal.py --seed-cache`). It fetches
and stores, full stop - no parsing, no write to `releases`, so it cannot
damage a row and it needs no parser to exist yet. Without it there is nothing
to calibrate a selector against and every iteration costs another crawl; with
it, 551 captures landed once and all the iteration since has been free.
Two traps that cost real data on the day this was written:

- **A `.pdf`/`.doc` row must never reach an HTML parser.** BeautifulSoup does
  not refuse binary — it returns a document whose `get_text()` is the PDF
  stream decoded as characters, with no exception to catch. 23 rows had
  `%PDF-1.3 %âãÏÓ 6 0 obj…` stored over text `pdftotext` had extracted
  correctly, and it looked like *encoding damage* rather than data loss,
  because a decoded PDF is full of C1 characters. `looks_like_html()` checks
  magic bytes before every parse now. The tell that something was wrong:
  the encoding repair suddenly fixed **0 rows and refused 48 fields** — a repair
  that can no longer fix anything means the damage is not what you think it is.
  `verify_encoding.py` is the same signal now.
- **A timestamp in `detail_id` does not always name a capture of that row's
  own URL.** For pressdb/media_news it can be the *listing* capture the body
  was read from, so the reconstructed capture URL never existed and 404s
  permanently (76 of 83 failures on one run; four rows sharing one
  `ts=20031203030754` is the giveaway). `--wayback` falls back to a CDX probe,
  which turns that into either a real recovery from another capture or a
  confirmed `dead` instead of an `uncertain` that gets retried forever.

**Re-extracting the whole corpus is now free.** Every page fetched since
2026-08-21 is in `page_cache`, so `scrape_<source>.py --offline --force` re-runs
`clean()` over rows that already have `body_html` without a single request:
all 3619 live rows took 2m26s and made zero network calls. That is what the
cache was for. Use `--force` after changing `clean()`, `--retext` after
changing only `to_text()`.

**Nothing is written without passing a gate in `bodygate.py`**, and which gate
is picked from the address (see the three rules above). `safe_to_write` refuses
exactly one thing - text disappearing from the **middle** of a body - and the reason
it took three measurements rather than one is worth keeping:

- a word-multiset comparison is useless here. It flags every join the fix
  causes (`8 th` -> `8th`, `unlocked 1` -> `unlocked1`, both correct) as loss.
  `text_delta` compares word-*character sequences* instead, and strips the
  `1.`/`2.` ordinals `to_text()` prepends.
- `kept` (nothing lost) and `clean` (nothing invented) are not enough:
  **`kept=False, clean=True` is the signature of both "nav was correctly
  dropped" and "a paragraph went missing"**. `edges_only` separates them -
  chrome lives at the edges, a lost paragraph does not.
- two middle-loss cases are allowed by name, each confirmed by a word diff of
  the actual rows: a teaser-grade body being replaced by the real article
  (portal sid=204 held 216 characters of the site's own comment widget where
  the release should have been), and a loss under 2%, which every time was a
  URL path or image alt text sitting mid-page.

Aggregate review beats the per-row test where the per-row test cannot decide:
across 215 terratec/terratec_de captures the removed prefixes were image alt
text and URL paths, and **not one row lost a suffix** - which is what actually
rules out article text having been trimmed off the end.

**`body` is by definition `to_text(body_html)`** wherever `body_html` exists —
verified across all 4007 such rows, zero mismatches. That makes a fix to the
*text renderer alone* free: `scrape_<source>.py --retext` recomputes `body`
from the stored HTML with no network and no per-source parser. Use it after
changing `to_text()`; a change to `clean()` still needs the HTML rebuilt.

`body_html IS NULL` means the row predates this and still renders as
preformatted text; it is the `plain` flag in the audit view, i.e. a progress
bar for the re-extraction shapes, not a defect in the source.

This used to say that attachment rows keep it NULL on purpose, "there is no HTML
behind a PDF". Half of that was never true and the other half still is. A PDF
does carry structure - poppler measures it, and since 2026-08-24 the 73 cached
PDF attachments store real markup. A **.doc** keeps `body_html` NULL, because
`antiword`'s text is all there is for it and `pre-wrap` is the right renderer
for that. The `plain` flag still excludes every `.pdf`/`.doc` url, which is now
a slight over-exclusion: it hides those 73 from the "bez formatowania" count,
where they would legitimately read as done.

**The allowlist lives in two places on purpose**: `richtext._ALLOWED` and
`RICH_TAGS` in `static/app.js`, which rebuilds every node rather than trusting
`innerHTML`. Same arrangement as `encoding.C1_RE` vs `db._MOJIBAKE_SQL`.
Change one and change the other.

**Every parser is DOM-based now (2026-08-21).** The six that used to cut the
body out of the page's *text* were redesigned, and the method matters more
than the result: `calibrate_containers.py` walks **every cached capture** of a
source, finds the smallest element covering that row's stored body, and
reports the distribution. Selectors come from the dominant shape, never from
one sample - which is what stopped `td[valign="top"][width="85%"]` (looks
obvious, misses 36 of 186 portal captures) from being encoded in favour of
`richtext.densest(soup, "td")`, the biggest one, which hit 100%.

Three shapes came out of it:

- **the page is the release** - `scrape_midiman.py` (2001 GoLive). Calibrated
  coverage 1.00: no nav to exclude, so whole-page extraction was right all
  along and only the flattening was wrong. Careful here: this is the one
  parser whose **title** comes out of the text surgery (a prefix slice before
  the dateline), so the flat `get_text` stays, for detection only.
- **the biggest layout table/cell** - `scrape_terratec.py`,
  `scrape_terratec_de.py`, `scrape_terratec_portal.py`. These
  pages have no classes or ids worth keying on, only `width="535"` attributes
  that change between captures of the same site.
- **an HTML fragment that was already sliced** - `scrape_midiman_de.py`'s
  inline blocks. `BLOCK_RE` always returned HTML; flattening it first was
  simply unnecessary.

**A regex that identifies the headline decides which pages exist at all.**
`scrape_terratec_new.extract_entries` recognised a release *only* by an
`<h2>Month YYYY - Title</h2>` heading, and the later captures of that CMS
dropped the date prefix - so those pages parsed to nothing whatsoever, no
warning, no marker: 9 of the 144 cached article captures and 8 listing entries.
The fix keys on the container instead - `div#Content > div.column.span-8`,
present in all 164 cached captures, with the only outside `h2`s being chrome
("Unternehmen" in the menu, "Presse-Kontakt" in the right column) - and takes a
date-less heading there as a headline when it sits in a `div.block` or is that
column's only one. In as a **fallback**, never a replacement, per the rule
above, and measured the same way: over every cached capture, **510 identical,
16 gained, 0 changed, 0 lost**. The date is then simply not on the page and the
row keeps the one its listing gave it.

**The portal's two other discovery channels are in its own scraper** (2026-08-25).
The yearly category listings (`CATEGORY_PAGES`, `from_categories`) and the sids
whose only archived page is the print view (`from_print_views`) were two separate
"backfill" scripts; both stamp the same two tags and both now use this module's
`parse_snapshot`. The print-specific parser they carried is gone, and measuring
settled it rather than taste: over all **81 cached `print.php` captures** the
portal parser never returns an empty body, and the two differ by 1-4 characters
of whitespace - `strict_same_text` passes on every one. So that channel
contributes *discovery*, not parsing. A teaser-grade row is still retried on
every run and upgraded the moment the full article can be reached, which is why
this was never a one-shot.

**Two cuts that look like one.** `richtext.cut_from(soup, pattern)` removes
everything from a marker onward at the element level. The naive version scanned
leaf *elements* and silently did nothing on midiman.de, where "Infos bei:" sits
in a bare text node between two `<br>` - the contact footer survived into 37
bodies that used to have it cut. It has to find the text node, truncate it, and
drop everything after it in document order.

**A parser that finds no container must fall back, never blank.**
`richtext.extract(None)` returns `("", "")`, so a missing container silently
becomes an empty body. Every redesigned parser keeps the old flat text in that
case and leaves `body_html` NULL - a handful of these captures are 290-byte
"page moved" stubs with no table at all.

**A publisher is not a press room, and `soundonsound` is the first one.**
Every other source is one company announcing its own products; this one is a
magazine writing *about* many. That has three consequences worth knowing
before touching it:

- **It gets its own `COMPANIES` entry** even though the axis is labelled
  "firmy". Not cosmetic: an unmapped source falls through to `UNKNOWN`, and
  `"inne"` is not a key of `COMPANIES`, so `serve._sources()` answers **HTTP
  400** the moment anyone clicks it in the panel. The counts stay honest
  either way; the link does not.
- **`Crawl-delay: 30`** in its robots.txt, honoured via
  `fetch_cached(sleep=CRAWL_DELAY)` - the reason that parameter exists. ~820
  requests is a ~7 hour run. Everything lands in `page_cache`, so it is a
  one-time cost and an interrupted run resumes for free.
- **The listing URLs match `Disallow: /*?*f[0]=`; the articles do not.** That
  rule guards against faceted-search crawl traps, not against content. Two
  named facets at one page per 30s was a deliberate call, recorded in the
  scraper's docstring rather than assumed.

Two markup traps, both of which silently produce wrong data rather than
failing: the listing carries **two date formats** (`19/8/26` on news,
`Published March 2000` in `.field--issue-date` on magazine pieces - the second
needs `fmt="%Y-%m"`, or dateutil fills the day in from *today*), and a page
holds 23 `div.views-row` of which only **20 contain `article[about]`** - the
rest are promo blocks, so counting rows gets the page size wrong.

`detail_id` is Drupal's node id (`<article id="node-4935591">`), deliberately
not a timestamp: faking one would have put a dead archive.org link on all 772
rows. That reasoning is now belt *and* braces - the link comes from
`body_origin`, and a live source has no entry there - but it was this
docstring that showed the digit rule had become a constraint on what a source
may store, which is half the reason `grade` got its own column.

**GlobeNewswire needs a browser TLS fingerprint, everything else does not.**
As of 2026 Akamai Bot Manager drops `requests`/`urllib3` at
www.globenewswire.com: DNS and the TLS handshake both succeed, then HTTP/1.1
gets *no response at all* (read timeout) and HTTP/2 gets an immediate
RST_STREAM after ~30 ms. Browser-shaped headers change nothing — the block is
on the TLS/HTTP2 fingerprint, not the User-Agent — so all 53 rows of that
source timed out on 2026-08-21. `curl_cffi` (bindings to the maintained
lexiforest/curl-impersonate fork) gets HTTP 200 on the first try with any
profile, and its `Session` is API-compatible with `requests.Session` for
everything `fetch_cached` uses, so **`fetch.py` needed no change** — the
scraper just builds a different session.

Two things to keep true: the import stays **inside**
`scrape_globenewswire_creative.make_session()`, so the other ~29 modules still
import on a bare system Python; and the impersonation profile is a moving
target — when this source starts timing out again, bump `curl_cffi` and try a
newer profile before suspecting the parser.

**Ask archive.org through `wayback._cdx()`** — the public CDX API. Do not reach
for `__wb/sparkline` or `__wb/calendarcaptures`: they're internal endpoints
needing a forged `Referer`, and cost 3 requests where CDX takes 1.

**Report progress through `progress.Stats`** — no per-script counters or marker
chars. The seven outcomes are fixed so a marker stream is readable without
knowing which script produced it.

**`page_cache` means a parser fix costs no refetch.** Every fetched page's
raw bytes are cached in the DB (not on disk — auxiliary data belongs in the
database). Reparsing is free; treat refetching as a mistake.

The table was called `wayback_cache` and, for a long time, only
`wayback.fetch_snapshot()` used it. The four live sources — intel, amd,
creative, creative_gnw, 61% of the corpus — went straight through
`session.get`, and when the flat-text extraction turned out to be wrong they
all had to be crawled again from scratch. `fetch.fetch_cached()` closes that
hole and the table is named for what it actually holds. **A new scraper that
fetches a page any other way is a bug.**

**Every HTTP attempt against archive.org is logged to `wayback_calls`** (one
row per attempt: `kind` — `cdx_probe`/`cdx_bulk`/`content` —, `url`, `attempt`,
`timeout_budget`, `outcome`, `duration`). Before tuning a `wayback.py` timeout
or sleep constant from a handful of manual `curl` calls, query this table
instead — that's the mistake that got `CDX_TIMEOUT` tuned twice on thin
evidence before this existed. Best-effort and silent on failure, same as
`page_cache`'s encoding-diagnostic columns.

## Working here

- **After any refactor that renames or removes a module-level name, sweep every
  module's import**, not just the file you edited:
  `python3 -c "import importlib,pathlib; [importlib.import_module(p.stem) for p in pathlib.Path('.').glob('*.py')]"`.
  These are ~40 flat modules that import each other by name, there are no tests,
  and grep is not enough — renaming `DETAIL_URL_TMPL` in one scraper silently
  broke a follow-up script that imported it, and it was committed that way. The
  same class of break has happened more than once. The 2026-08-25 cleanup moved
  about fifteen module-level names in one pass and the sweep after every step is
  the only reason it landed intact.
- **The dependency direction is a rule now, not an accident.** A scraper imports
  `reextract`/`bodygate`/`captures`/`twins`; none of those imports a scraper.
  Before, the engine imported thirteen scrapers to build its registries, which
  is why nothing could import it back and why a constant had to be *duplicated*
  into `scrape_midiman_pressdb.py` with a comment apologising for it.
- `pressroom.db` is gitignored, along with `pressroom.db.bak`. `'rebuild'` and
  bulk updates aren't reversible — copy the DB before one.
- archive.org intermittently refuses connections. A run full of `?` markers is
  usually the archive, not the code — confirm with a bare `curl` before
  debugging a parser.
- Not wanted here: an ORM, a query builder, a row dataclass, schema churn on
  `releases`. Six plain functions over plain SQL is the chosen design. One
  deliberate exception, 2026-08-22: `grade`, because the alternative was
  leaving a verdict inside a reference field and seven places guessing which
  was which (see the `grade`/`detail_id` split above). Provenance still goes in
  its own table, not a column - that is what `body_origin` is.
- Every scraper's module docstring records what its source's markup actually
  does, including the quirks that cost time (CMS bugs, retargeted links,
  templates that differ per capture). Keep writing those down there — that
  archaeology is the expensive part of this project, not the code.

## Known open state (re-measured 2026-08-25)

Everything here self-heals on a rerun; it is waiting on archive.org, not on a
code change. Counts rot — re-measure before trusting them.

- **The one-shots and the rituals are gone (2026-08-25).** Twelve files went:
  five finished one-shots deleted (an intel import, a cache-hash fill, a
  co.uk decode fix, the title recovery, a 23-url directory list), two rituals
  moved into the write path, three "backfills" that were a source's primary pass
  renamed or folded into the scraper that owns their tag, and the re-extraction
  engine split into `bodygate.py` / `captures.py` / `reextract.py`. Net -621
  lines. What each of them established is recorded in the sections above; the
  code is in git.

  Verified after, on the real corpus: 6708 rows, 789 without markup, 1727
  origins, 17 empty titles, `grade` full 6154 / teaser 551 / stub 3, FTS token
  counts unmoved (`MobilePre` 56, `Octane` 47, `ArKaos` 148, `Radium` 24,
  `GeForce` 81), `body == to_text(body_html)` on 5919 of 5919 rows.
  `verify_body_origin.py` came out *better* than before the change - 149/149
  inferred, **323/323** located (was 321, two differed in whitespace),
  1198/1255 computed - because a fresh parse of #4355 now reproduces the
  repaired row instead of reintroducing the page's own damage. #6211 remains the
  one row that does not reproduce, for the reason recorded above.

  Two commands changed shape and are worth memorising: **`python
  scrape_<source>.py --offline`** is the free re-extraction of one source (what
  `--from-cache --source X` used to be), and **`--offline --force`** re-runs
  `clean()` over every row of it, still with no requests.

- **`terratec_new_de`/`_en`: done (2026-08-22).** All 159 rows carry
  `body_html`; there is nothing left to fetch for them. 9 came from article
  captures already in `page_cache` once the date-less headline was recognised,
  15 from the German listing captures (30 of them, absent from `page_cache`
  until now - only the English ones had been cached), and CDX confirmed the
  other 15 article URLs were never captured at all. Corpus-wide the count is
  now **6708 rows, 789 without `body_html`** - of which 381 are .pdf/.doc
  attachment rows that have no HTML behind them by construction, so the audit
  view's "bez formatowania" reads **600**, which is the number that means
  outstanding work.
- **Capture links: 158 recovered, 383 still unaddressed (2026-08-22).**
  A one-shot pass resolved which page each listing-derived row's
  timestamp really names; see the `body_origin` section above. The rest keep a
  timestamp whose page is unknown, so their link is still built from the row's
  own URL and may 404: 143 have no cached capture under that timestamp to test
  against - `--seed-cache` on the *listing* URL would open those up - and for
  240 the body is in none of the captures that do share it. Re-running the
  repair after any pass that caches more captures is free.
- **Titles: done (2026-08-22).** 37 of 54 empty titles recovered from cache,
  17 are the confirmed end state - see the headline section above. The pass is
  confirmed again on 2026-08-25, the last time that pass ran before deletion:
  `17d`, nothing written.
- **One free body recovery is now waiting.** Teaching the portal parser
  `print.php` and the date-less `<title>` also made `terratec_pressde` sid=367
  parse to a 4547-character body where the row holds 0. Deliberately not
  written by the title repair - `scrape_terratec_portal.py --offline` is what
  owns it, because it goes through `bodygate`. Measured 2026-08-25: that sid is
  one of the 9 whose cached capture the parser finds nothing in, so the free
  recovery is not there after all.
- **`terratec_pressde` 300 rows (95 teaser), `terratec_pressen` 141 (52)** after
  a full pass over the category-listing channel on 2026-08-21: +97 rows, and the
  first pass to write correct text, since the cp1252 fix landed the day before.
  10 sids came back `uncertain` (archive.org refused mid-run) and a rerun
  retries exactly those.
- **Encoding damage: fixed (2026-08-20).** 156 of 157 damaged rows repaired by
  the encoding repair; every sniffing decode site closed. The one left is
  `midiman_net_pressdb` #4978, whose only damage is three 0x81 bytes — cp1252
  does not define that byte, so there is nothing to decode it *to* and the
  script refuses it by design. It still shows in the audit view.
- **`scrape_midiman.py` (2001 era) is barely populated** — `midiman_net` 1 row,
  `maudio_com` 0, `midiman_com` 9. Mostly genuine: midiman.net has only ~5
  press pages ever captured with HTTP 200 and its whole `midiman/html/press/`
  directory is 404s. Low yield per request, so it loses to other work whenever
  archive.org is throttling. One prefix crawl also died mid-run, so a rerun has
  something real to pick up.
- **The `media_news` teasers are finished, not pending (2026-08-21).** This
  file called them "the big remaining block". They are not. All 404 were
  worked and exactly 1 row came back:
  - **253 (63%) use the `news/en_us-<N>.html` scheme**, which has zero Wayback
    captures, ever. Known for `midiman_couk_news`; it holds on all four
    domains. They are not even probed - there is no ID to probe with.
  - the other 149 use `index.php?do=media.new&ID=<hex>` and were probed. A
    direct CDX spot check of 8: **0 captures, 0 probe errors**, so this is
    genuine absence, not archive.org having a bad day.
  A teaser in these four sources is the expected end state now. Measure the
  URL scheme before spending an hour of crawl on a block like this - that hour
  bought one row, and one query up front would have predicted it.
- **73 PDF attachment rows carry real markup since 2026-08-24.**
  The richtext conversion (`attachment_crawl.write_richtext`) converted every cached PDF
  attachment through `attachments.to_richtext()`: 73 rows gained `body_html`
  (11 paragraphs and a 7-item list on #4986, an `ol` of 6 on #5003, four nested
  `ul`s on #5572), `body` became `to_text(body_html)` as everywhere else, and
  the FTS token counts did not move (`MobilePre` 56, `Octane` 47, `ArKaos` 148
  before and after) because the words are the same - only their arrangement
  changed. Nothing needed a decision: all 73 converted and passed the gate.

  **The gate is a multiset of word characters, and it is the fourth attempt.**
  `bodygate.wordchars` (one implementation, shared, not copied) drops
  indentation, wrapping, bullets and to_text()'s ordinals; comparing the
  *multiset* is blind to the two things a converter is allowed to change -
  order and joins - while still refusing a document that lost a paragraph.
  Character *sequence* refused 13 of 73 for pure reordering (`pdftotext
  -layout` puts a superscript on its own line; the structured route puts it back
  beside its number), `text_delta`'s subsequence test broke on the same thing,
  and word coverage refused 10 more for joins (`Composer` + `®` + `system`
  arriving as `Composer®system`). Two allowances, both named and bounded: the
  rotated banner the converter says it dropped, and **markers that became
  structure** - 6 digits absorbed into an `<ol>` and 9 Courier `o`s into a
  second-level `<ul>`, capped at two characters per list item so it can never
  excuse a missing word.

  What this leaves: 153 `.doc` rows on the text route by decision, 30 rows whose
  bytes are not cached at all, and 2 whose cached file is a soft-404. Checked as
  `[body-richtext]`; `[body-layout]` moved its fixture to a .doc row, since the
  PDF it used now renders as markup.
- **The layout fix reached the corpus on 2026-08-23, 140 rows of 381.**
  `normalize()` stopped flattening `pdftotext -layout`/`antiword` output long
  ago, but nothing re-ran the extraction, so **380 of 381 attachment rows still
  held text with not one newline in it** - every spec sheet a single run-on
  line, rendered through a `pre-wrap` box with no layout left to show. The 140
  rows whose bytes are in `page_cache` were re-extracted for free by
  `attachment_crawl.reextract_from_cache`; the gate is
  `strict_same_text`, so the only thing that could change was whitespace
  (verified: 140/140 identical modulo whitespace before writing, and the
  character sequence unchanged after). The remaining **240 need a refetch** and
  33 of those are confirmed never archived, so this is not "pending work" so
  much as the ceiling of what the cache can pay for. Checked as `[body-layout]`.
  One thing the recovered layout exposes rather than causes: `#5914`, a Word
  attachment, interleaves text from overlapping boxes (`New 8x8 Digital
  PreamSophisticated new entryp`). The characters are byte-identical to what was
  stored flat, so that is antiword reading a text-box layout, not a regression.
- **The 211 rows whose bytes we only held under a mirror domain: crawled, and
  their own urls are not there (2026-08-25).** `--no-own-bytes` walked every
  one, ~3 hours against a throttling archive (23 HTTP 503s, each costing a
  120-second cooldown). Yield of new files: **zero**. The single new
  `page_cache` entry is 380 bytes of `509 Bandwidth Limit Exceeded` - and that
  page is `Apache/1.3.27 Server at www.m-audio.com`, i.e. the *original* server
  over quota in 2003, faithfully archived, not an archive.org error. Exactly the
  soft-404-as-HTTP-200 class `is_attachment` exists for, and it rejected it.

  What the run did deliver, as a side effect of falling back to the mirror
  candidate whose bytes were already cached: **201 rows re-extracted with their
  layout intact**, every one character-identical to what it replaced. Attachment
  rows carrying line structure went from 141 to **342 of 381**. `--from-cache`
  would have done that offline; what the crawl bought is the *knowledge* that
  the own-url captures do not exist, which was previously an assumption.

  4 rows stayed `uncertain` on transient CDX failures. Given zero yield across
  211, a rerun is not worth an hour - but it is free to leave open, and the
  selector finds them again.
- **The 30 attachment rows a crawl could still help are dead, confirmed
  2026-08-25.** `attachment_crawl.missing_bytes_rows` - a selector
  for the rows whose bytes are in `page_cache` under no name at all, not their
  own capture and not a mirror's - walked all 30 twice. First run: 26 never
  archived, 4 `uncertain`, and the four were `connection_error` on `cdx_bulk`,
  not an answer. Second run after `curl` confirmed archive.org was up again: 28
  never archived, 2 `uncertain`. Those last two were then probed by hand across
  all three mirror domains - `M-Audio_SonicReFills_PR.pdf` and
  `M-Audio_ProKeys88_PRv5.doc`, six CDX queries - and **none of the six has a
  single HTTP-200 capture, ever**. So the listing teaser (45-756 characters) is
  the end state for those 30 rows, and 219 logged attempts bought nothing,
  which was the honest answer rather than a failure.

  The whole attachment picture, 381 rows: **72 PDFs carry real markup**, 121
  PDFs have bytes only under a mirror domain and wait on the one-row-many-urls
  decision, 153 .doc rows keep the text route, 30 are dead, 5 are a soft-404
  under a .pdf name. Nothing here is waiting on archive.org any more.
- **The attachment backfill is done, and what is left is gone.**
  The short-rows selector (`attachment_crawl.backfill(only_short=True)`, now
  reached by `scrape_midiman_pressdb.py --attachments`) attempted, on 2026-08-21, the 36
  remaining short `media_pr`/`pressdb` rows: **33 confirmed never archived** -
  CDX has no 200 capture for those .doc/.pdf URLs, ever - and 3 `uncertain`
  from a flaky archive.org, which a rerun retries. So a short body in those
  sources is now the expected state, not pending work. `--only-short` is the
  flag that makes this a minutes-long retry instead of an hours-long crawl.
- **`media_news` teasers are the big remaining block**: `midiman_net_media_news`
  179, `midiman_com_media_news` 122, `maudio_com_media_news` 86. Down 111 from
  `twins.py` (below); the rest need their detail captures.

- **Formatting recovery: 4060 of 5935 rows done, 1875 still flat (2026-08-21).**
  Every live source is complete — intel, amd, creative and creative_gnw, 3619
  rows — plus the 441 wayback rows whose capture was already cached. The live
  crawl took ~2h at ~28 rows/min and seeded `page_cache`, which is why
  `pressroom.db` went from 112 MB to 330 MB and why every re-extraction since
  has been free. Intel's quarterly results tables render as tables now.
  A second round (`--wayback`, then a teaser pass over all four `media_news`
  sources) recovered 171 more, confirmed 72 as never archived, and established
  that the remaining teasers are unrecoverable - see the `media_news` entry
  below.
  A third round redesigned the six text-surgery parsers (see the DOM section
  above) after `--seed-cache` fetched 551 captures to calibrate against:
  **4815 of 5936 rows now carry `body_html`, 740 do not.** That last number
  excludes .pdf/.doc rows, which have no HTML behind them and never will.
  Track it at `#audit` -> "bez formatowania", or `#flags=plain&order=date`.

  What the 740 are, and none of it is pending work in the ordinary sense:
  - **~415 `media_news` teasers** - confirmed unrecoverable, see below.
  - **~285 portal rows** (`terratec_pressde` 205, `terratec_pressen` 80) with
    either no timestamp to address a capture with, or a capture archive.org
    no longer serves. Two `--seed-cache` passes converged: the second added
    2 captures and left 119 `uncertain`, so the well is close to dry.
  - the rest is scattered single rows whose capture 404s.

  The whole corpus can now be re-extracted for free (`--force`, `--listings`,
  `--retext`), so the next parser fix costs no requests at all.
- **`wayback_calls` finally has data**: 792 attempts logged, 642 ok, 78
  connection errors, 70 timeouts, 2x 503 - i.e. ~19% of attempts failed on a
  normal 2026-08-21 run. Query this table before tuning a timeout, which is
  what it exists for.

A quirk worth knowing when reading these numbers: a `media_pr` row stores a
Wayback timestamp in `detail_id` even when its body is only the listing teaser,
because the timestamp refers to the *listing* capture. So a teaser-grade row is
not always `grade = 'teaser'` — check `length(body)` too.
