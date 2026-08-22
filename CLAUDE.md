# pressroom

Recovers historical company press releases — mostly from **dead sites, via the
Wayback Machine** — into one SQLite database (`pressroom.db`) with a full-text
index. A set of one-shot CLI scripts plus two readers: `search.py` (CLI) and
`serve.py` (a local read-only HTTP browser on 127.0.0.1, stdlib + vanilla JS).
No third-party dependencies, no build step, no auth, no tests.

Currently ~6700 rows across 25 sources: Intel, AMD, Creative, TerraTec (5 site
generations), Midiman/M-Audio (5 CMS generations), and Sound on Sound - the
one source that is a magazine rather than a press room, and the only live site
here with content going back to 2000.

## Environment

```bash
uv venv && uv pip install -e ".[globenewswire]"             # Python 3.14
.venv/bin/python scrape_terratec_portal.py --limit 5
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

Scripts: `scrape_<source>.py` = a source's primary pass. `backfill_<...>.py` =
a follow-up pass that upgrades rows an earlier pass could only store as
teasers. `repair_<...>.py` / `migrate_<...>.py` = one-time fixes and imports,
kept rather than deleted so the change is reproducible. `search.py` and
`serve.py` = the two readers; both go through `db.py` and both open the
database read-only, so neither can touch `releases` or the FTS index.
`static/` = the browser's three files (`index.html`, `app.js`, `app.css`),
served from a name whitelist in `serve.py`. `checks.md` = the browser's
site-specific check manifest, in the format the `web-static` skill executes.

- `pdftotext -layout` extracts PDF attachments, `antiword -m UTF-8.txt` the
  .doc ones (`backfill_midiman_attachments.py`). System binaries, deliberately,
  in a repo that declares no Python dependencies. Dispatch on **magic bytes,
  not the extension** — CMS-era attachments are routinely mislabeled.

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
other is the full article. `backfill_twin_bodies.py` fills the short one from
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

**`detail_id` records how good a row is:** a Wayback timestamp = full text
recovered from that capture; `"teaser"` = only a listing blurb;
`"stub"` = title/date only. Use `stored_detail_id()`, not `already_stored()`,
whenever a row might deserve an upgrade later — `already_stored()` would wedge
teaser rows permanently.

**A network error is not a verdict.** `wayback.fetch_detail_snapshot()` returns
`(parsed, confirmed)`: `confirmed=False` means archive.org failed, so the
caller must write *nothing* and leave the item open to a full retry. Only a
confirmed absence may be recorded as a fallback. Report it as `uncertain`
(`?`), never as `dead`.

**Source tags are per-domain, not per-brand** (`midiman_net_pressdb`,
`maudio_com_media_pr`, `terratec_pressde`). The same release genuinely exists
on several mirrors under unrelated URL schemes; there is no reliable
cross-domain dedup key, so duplication across sources is intended, not a bug.

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
`page_cache` holds the original bytes, both are repairable with no refetch —
see `repair_couk_news_encoding.py`.

**Re-run `repair_encoding.py` after any pass that rewrites bodies from the
network.** Some of this damage is *upstream*, not ours: ir.amd.com serves
`\xc2\x99` — valid UTF-8 for the C1 control U+0099 — where it means `™`, so
decoding it correctly still yields a control character. The 2026-08-21
`backfill_body_html.py` run brought 10 amd rows and 24 creative rows straight
back after the 2026-08-20 repair had fixed them, because a refetch overwrites a
text-level repair. The script now repairs `body_html` alongside `body`, so the
two representations stay in agreement.

**A wrong decode already stored in the DB is undone in text, not refetched.**
`encoding.repair_text()` owns both inversions and `repair_encoding.py` applies
them (157 rows, 8 sources, 2026-08-20): C1 characters go back through cp1252
*per character* — a whole-string `encode("latin-1")` dies on the mixed rows
that need it most — and mojibake is re-encoded with the charset it was misread
as, accepted only when the round trip leaves no markers and every qualifying
codepage agrees. The misreads were cp1252 and, on German prose,
**cp1258** (Vietnamese) — chardet's guess, not a typo. `mac-roman` is
deliberately not a candidate: it re-encodes cleanly and produces garbage.

Detection lives in two places on purpose: `encoding.C1_RE`/`MOJIBAKE_RE` for
the repair, and `db.py`'s `_MOJIBAKE_SQL` for the browser's audit view, because
SQLite has no regex. Change the rule and change both — and note that they had
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

**A row's URL is not always a page that existed.** Two shapes, and they need
opposite treatment:

- *page-derived* - the row has its own capture (`terratec`, `terratec_de`,
  the portals, the 2001 GoLive pages). `--seed-cache`, then `CACHED_PARSERS`.
- *listing-derived* - the release only ever existed inside a listing, so the
  scraper minted the URL. `scrape_midiman_de` builds
  `/press/{slug}-{date}`; `scrape_terratec_early` uses `presse2.htm#p20`
  anchors into one page. Their `detail_id` is the *listing* capture's
  timestamp, so a per-row fetch is 64 guaranteed 404s - hence
  `SYNTHETIC_URL_SOURCES`. `--listings` walks the cached listing captures
  instead and matches back by URL. It only ever UPDATEs: a parse matching no
  stored URL is dropped, never inserted, so a re-extraction cannot mint rows
  under URLs nobody has seen.

**`backfill_body_html.py` has six modes and they are not interchangeable.**
`--force` re-runs `clean()` (needs the HTML), `--retext` re-runs only
`to_text()` (needs nothing but the DB), `--from-cache` parses what `page_cache`
already holds, `--wayback` fetches a row's own capture when it does not,
`--seed-cache` fetches captures and parses nothing, and `--listings` handles
the listing-derived sources.

**`--seed-cache` comes first when a parser is being redesigned.** It fetches
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
  `repair_encoding.py` suddenly repaired **0 rows and refused 48 fields** — a
  repair script that can no longer fix anything means the damage is not what
  you think it is.
- **A timestamp in `detail_id` does not always name a capture of that row's
  own URL.** For pressdb/media_news it can be the *listing* capture the body
  was read from, so the reconstructed capture URL never existed and 404s
  permanently (76 of 83 failures on one run; four rows sharing one
  `ts=20031203030754` is the giveaway). `--wayback` falls back to a CDX probe,
  which turns that into either a real recovery from another capture or a
  confirmed `dead` instead of an `uncertain` that gets retried forever.

**Re-extracting the whole corpus is now free.** Every page fetched since
2026-08-21 is in `page_cache`, so `backfill_body_html.py --force` re-runs
`clean()` over rows that already have `body_html` without a single request:
all 3619 live rows took 2m26s and made zero network calls. That is what the
cache was for. Use `--force` after changing `clean()`, `--retext` after
changing only `to_text()`.

**Nothing is written without passing `safe_to_write()`.** It refuses exactly
one thing - text disappearing from the **middle** of a body - and the reason
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
*text renderer alone* free: `backfill_body_html.py --retext` recomputes `body`
from the stored HTML with no network and no per-source parser. Use it after
changing `to_text()`; a change to `clean()` still needs the HTML rebuilt.

`body_html IS NULL` means the row predates this and still renders as
preformatted text; it is the `plain` flag in the audit view, i.e. a progress
bar for `backfill_body_html.py`, not a defect in the source. PDF-derived rows
(`backfill_midiman_attachments.py`) keep it NULL on purpose — there is no HTML
behind a PDF, and `pdftotext -layout` output wants `white-space: pre-wrap`
exactly as it is.

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
  `backfill_terratec_de_and_net_gaps.py`, `scrape_terratec_portal.py`. These
  pages have no classes or ids worth keying on, only `width="535"` attributes
  that change between captures of the same site.
- **an HTML fragment that was already sliced** - `scrape_midiman_de.py`'s
  inline blocks. `BLOCK_RE` always returned HTML; flattening it first was
  simply unnecessary.

`backfill_terratec_teasers.parse_article_snapshot` is gone: it now *imports*
`scrape_terratec_portal.parse_snapshot`. It had been a near-copy with the same
TITLE_TAG_RE verbatim, no month-precision fallback, and markers cut in a
different order.

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
not a timestamp: 14 digits means "Wayback capture" everywhere here, and faking
one would put a dead archive.org link on all 772 rows.

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
  These are ~29 flat modules that import each other by name, there are no tests,
  and grep is not enough — renaming `DETAIL_URL_TMPL` in one scraper silently
  broke a repair script that imported it, and it was committed that way. The
  same class of break has happened more than once.
- `pressroom.db` is gitignored, along with `pressroom.db.bak`. `'rebuild'` and
  bulk updates aren't reversible — copy the DB before one.
- archive.org intermittently refuses connections. A run full of `?` markers is
  usually the archive, not the code — confirm with a bare `curl` before
  debugging a parser.
- Not wanted here: an ORM, a query builder, a row dataclass, schema churn on
  `releases`. Six plain functions over plain SQL is the chosen design.
- Every scraper's module docstring records what its source's markup actually
  does, including the quirks that cost time (CMS bugs, retargeted links,
  templates that differ per capture). Keep writing those down there — that
  archaeology is the expensive part of this project, not the code.

## Known open state (2026-08-21)

Everything here self-heals on a rerun; it is waiting on archive.org, not on a
code change. Counts rot — re-measure before trusting them.

- **`terratec_pressde` 300 rows (95 teaser), `terratec_pressen` 141 (52)** after
  a full `backfill_terratec_teasers.py` pass on 2026-08-21: +97 rows, and the
  first pass to write correct text, since the cp1252 fix landed the day before.
  10 sids came back `uncertain` (archive.org refused mid-run) and a rerun
  retries exactly those.
- **Encoding damage: fixed (2026-08-20).** 156 of 157 damaged rows repaired by
  `repair_encoding.py`; every sniffing decode site closed. The one left is
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
- **The attachment backfill is done, and what is left is gone.**
  `backfill_midiman_attachments.py --only-short` (2026-08-21) attempted the 36
  remaining short `media_pr`/`pressdb` rows: **33 confirmed never archived** -
  CDX has no 200 capture for those .doc/.pdf URLs, ever - and 3 `uncertain`
  from a flaky archive.org, which a rerun retries. So a short body in those
  sources is now the expected state, not pending work. `--only-short` is the
  flag that makes this a minutes-long retry instead of an hours-long crawl.
- **`media_news` teasers are the big remaining block**: `midiman_net_media_news`
  179, `midiman_com_media_news` 122, `maudio_com_media_news` 86. Down 111 from
  `backfill_twin_bodies.py` (below); the rest need their detail captures.

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
because the timestamp refers to the *listing* capture. So teaser-grade rows do
not always show up as `detail_id = 'teaser'` — check `length(body)` too.
