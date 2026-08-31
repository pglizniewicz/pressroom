# The map: what each component owns

The tree is `pressroom/<business component>/<boundary|control|entity>/`, and the
convention itself — what each layer may call, that a component is named for its
one responsibility, how a source component splits — is in `CLAUDE.md`. The
reasoning behind it is in [adr/layout-and-naming.md](adr/layout-and-naming.md).
This file is the third thing, and only that: **where a given job lives.**

It is a lookup table, so it is the most rot-prone prose in the repo — every rename
has to reach it. `tests/test_layout_map.py` is what makes that loud: every
component directory under `pressroom/` has a row here, every row names a
directory that exists, and every `<component>/<layer>/<file>.py` path below
resolves to a real file.

| component | owns |
|---|---|
| `database` | opening the file and the order migrations run in. No DDL of its own: `control/connection.py` knows no table, `control/migration.py` calls each owner's `SCHEMA_SQL`/`rename_before_create`/`migrate` in a fixed order |
| `release` | the corpus. `entity/schema.py` = `releases` + the FTS index + the audit flags' SQL; `entity/grade.py` = the `full`/`teaser`/`stub` closed set; `control/storage.py` = every write; `control/index.py` = the FTS triggers; `control/query.py` = every read, both readers'; `control/gate.py` = may this text replace what is stored; `boundary/search.py` = the CLI reader |
| `capture` | the bytes. `entity/page.py` = `page_cache` + `content_hash`/`same_bytes`; `entity/call_log.py` = `wayback_calls`; `control/archive.py` = everything that talks to archive.org (CDX + cached content fetches); `control/politeness.py` = `HEADERS`, `SLEEP`, `fetch_cached()` for a live site; `control/address.py` = capture addresses as pure strings |
| `provenance` | which capture a body came from. `entity/origin.py` = `body_origin` + `record`/`clear`/`page_of`; `control/resolution.py` = `origin_key()`, `own_page()` — the question that picks the gate — and `cached()`; `boundary/verification.py` = the read-only proof |
| `text` | bytes and markup to text. `control/decoding.py` = `decode_html()` when the declared charset lies, plus `repair_text`/`repaired` and the `REPAIRS` counter; `control/richtext.py` = `extract()`, the tag allowlist, the sanitizer, the plain-text renderer, plus `densest()`/`cut_from()`; `control/dating.py` = `iso_date()`, the single date parser; `boundary/` = the encoding check and the container calibration |
| `attachment` | what an attachment's bytes mean: `.pdf`/`.doc` -> text (`plain_text`), or a PDF -> our HTML subset (`to_richtext`). Shells out to `pdftotext`/`antiword`; no network, no SQL. Also the two predicates that ask the same question before and after a fetch: `is_attachment_url()` on an address, `is_attachment()`/`looks_like_html()` on bytes — one magic-byte table, both directions |
| `scraping` | the structure a run has, independent of source. `entity/parse.py` = `Detail` and `Entry`, what a source's parser hands back; `control/catch_up.py` = phase 2's six strategies, each taking the parser/collector/fetcher from its caller; `control/attachment_crawl.py` = the second contract, phase 2 for `.pdf`/`.doc` rows; `control/twin.py` = filling a teaser from its twin row; `boundary/command.py` = the command line every scraper has |
| `reporting` | `entity/outcome.py`: `Stats`, the seven fixed outcomes and the summary line |
| `taxonomy` | `entity/company.py`: the source->company table. No SQL, no HTTP |
| `browser` | `boundary/http.py` + `boundary/static/`: routing, query-param parsing, JSON, and the three frontend files. No SQL |
| `q4` | the Q4 Inc. IR-platform parser, shared by exactly two firms |
| `intel`, `amd` | a list url and, for AMD, two selectors. Boundary only: the parsing is `q4`'s |
| `creative` | its own press room (`press`) and the GlobeNewswire wire (`globenewswire`) |
| `terratec` | five site generations: `early` (1996 anchors), `pressemit`, `presse`, `portal` (PHP-Nuke, two languages), `cms` (2007-2013) |
| `maudio` | five CMS generations over four domains: `golive` (2001 static), `pressdb`, `media_pr`, `media_news`, plus `presse_de` (midiman.de) and `news_blog` (the m-audio.com blog) |
| `soundonsound` | `magazine`: the one publisher here |

`pressroom/integrity.py` deliberately has no row: `pressroom-verify-names` walks
the tree rather than belonging to it, so it is not a business component and says
so in its own docstring.
