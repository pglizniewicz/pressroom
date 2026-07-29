# pressroom

Recovers historical company press releases — mostly from **dead sites, via the
Wayback Machine** — into one SQLite database (`pressroom.db`) with a full-text
index. A set of one-shot CLI scripts plus `search.py`. No server, no web UI, no
API, no tests.

Currently ~5000 rows across 20 sources: Intel, AMD, Creative, TerraTec (5 site
generations), Midiman/M-Audio (5 CMS generations).

## Environment

```bash
uv venv && uv pip install requests beautifulsoup4 python-dateutil   # Python 3.14
python3 scrape_terratec_portal.py --limit 5
python3 search.py "Radium" --source midiman_com_pressdb
```

- `pyproject.toml` declares `dependencies = []` — **that is wrong**, the code
  needs `requests`, `beautifulsoup4`, `python-dateutil`. Not yet fixed.
- `pdftotext` (poppler-utils) is shelled out to for PDF press releases.
- **The `sqlite3` CLI is not installed on this machine.** Inspect the DB with
  `python3 -c "import sqlite3; ..."`.

## Layout

Shared modules — each owns exactly one concern. Adding shared logic means a
**new module named for its concern**, never a grab-bag (`common.py` was split
into `fetch.py`/`q4.py` for exactly this reason):

| module | owns |
|---|---|
| `db.py` | the whole schema, migrations, `connect()`, and every read/write helper |
| `wayback.py` | everything that talks to archive.org: CDX queries + cached content fetches |
| `fetch.py` | `HEADERS`, `SLEEP` — HTTP politeness, nothing else |
| `dates.py` | `iso_date()` — the single date parser |
| `encoding.py` | `decode_html()` — bytes to text when the declared charset lies |
| `progress.py` | `Stats` — the shared outcome vocabulary and summary line |
| `q4.py` | the Q4 Inc. IR-platform parser (Intel + AMD only) |

Scripts: `scrape_<source>.py` = a source's primary pass. `backfill_<...>.py` =
a follow-up pass that upgrades rows an earlier pass could only store as
teasers. `repair_<...>.py` / `migrate_<...>.py` = one-time fixes and imports,
kept rather than deleted so the change is reproducible. `search.py` = the only
reader.

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
4. **`db.py` imports stdlib only.** `search.py` is deliberately
   dependency-free, and imports `db` for `DB_PATH`. Never import `requests` or
   `bs4` into `db.py`, and never import `fetch`/`q4`/a scraper into `search.py`.
5. **`releases.url` is the dedup key** (UNIQUE) and inserts are
   `INSERT OR IGNORE`. Gate any "new" counter on `store_release()`'s bool
   return — an unconditional `count += 1` after it reports phantom inserts on
   every rerun (this was a real bug).

## Conventions

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

**Never use `r.text`, and never let BeautifulSoup sniff.** These sites are
pre-UTF-8 or half-converted, and `requests` guesses Latin-1 while bs4 falls
back to chardet — which on this corpus has picked windows-1250 and even
windows-1258 (Vietnamese). Both mangle `™` (`0x99`), `„` (`0x84`) and smart
quotes. `wayback.fetch_snapshot()` returns bytes so the decision is always
explicit, and records three diagnostic encoding signals per capture in
`wayback_cache`. Two correct choices, per source:

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
`wayback_cache` holds the original bytes, both are repairable with no refetch —
see `repair_couk_news_encoding.py`.

**Ask archive.org through `wayback._cdx()`** — the public CDX API. Do not reach
for `__wb/sparkline` or `__wb/calendarcaptures`: they're internal endpoints
needing a forged `Referer`, and cost 3 requests where CDX takes 1.

**Report progress through `progress.Stats`** — no per-script counters or marker
chars. The seven outcomes are fixed so a marker stream is readable without
knowing which script produced it.

**`wayback_cache` means a parser fix costs no refetch.** Every fetched
snapshot's raw bytes are cached in the DB (not on disk — auxiliary data belongs
in the database). Reparsing is free; treat refetching as a mistake.

## Working here

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

## Known open state (2026-07-29)

Everything here self-heals on a rerun; it is waiting on archive.org, not on a
code change. Counts rot — re-measure before trusting them.

- **`terratec_pressde` (~85/229) and `terratec_pressen` (~50/115)** are still
  teaser-only. Run `backfill_terratec_teasers.py`.
- **~150 rows carry encoding damage** in `terratec_*`, `creative` and `amd` —
  two shapes, neither the same as the `midiman_couk_news` one already fixed:
  raw C1 control characters (cp1252 read as ISO-8859-1) in the TerraTec and
  Creative scrapers, which pass raw bytes to bs4; and `Ã`-style mojibake in
  `terratec_new_de`. `amd`'s ten come through `q4.py`'s `r.text` against the
  live site. Diagnosed but **not fixed** — see the encoding convention above.
- **`scrape_midiman.py` (2001 era) is barely populated** — `midiman_net` 1 row,
  `maudio_com` 0, `midiman_com` 9. Mostly genuine: midiman.net has only ~5
  press pages ever captured with HTTP 200 and its whole `midiman/html/press/`
  directory is 404s. Low yield per request, so it loses to other work whenever
  archive.org is throttling. One prefix crawl also died mid-run, so a rerun has
  something real to pick up.
- **`midiman_couk_news` has 22 teaser rows left, of which 18 are unrecoverable**
  by construction: later captures linked articles via a `news/en_us-<N>.html`
  scheme that has zero Wayback captures, ever. Only 4 are worth retrying.
- **`media_pr` rows may still be ~150-character teasers.** Their real text is in
  linked .doc/.pdf attachments; `backfill_midiman_attachments.py` recovers it
  (~50x longer bodies) but the full pass is ~380 rows and slow.

A quirk worth knowing when reading these numbers: a `media_pr` row stores a
Wayback timestamp in `detail_id` even when its body is only the listing teaser,
because the timestamp refers to the *listing* capture. So teaser-grade rows do
not always show up as `detail_id = 'teaser'` — check `length(body)` too.
