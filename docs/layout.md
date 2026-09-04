# The map: what each component owns

The tree is `pressroom/<business component>/<boundary|control|entity>/`, and the
convention itself — what each layer may call, that a component is named for its
one responsibility, how a scraper splits — is in `CLAUDE.md`. The
reasoning behind it is in [adr/layout-and-naming.md](adr/layout-and-naming.md).
This file is the third thing, and only that: **where a given job is.**

It is a lookup table, so it is the prose most likely to go out of date in the
repo — every rename has to be applied here. `tests/test_layout_map.py` is what
makes a missed one fail: every component directory has a row here, wherever it
sits, every row names a directory that exists, and every
`<component>/<layer>/<file>.py` path below resolves to a real file.

| component | owns |
|---|---|
| `database` | opening the file and the order its tables are made in. No DDL of its own: `control/connection.py` references no table, `control/creation.py` runs each owner's `SCHEMA_SQL` in a fixed order and installs the FTS triggers last |
| `release` | the corpus. `entity/schema.py` = `releases` + the FTS index + the audit flags' SQL; `entity/grade.py` = the `full`/`teaser`/`stub` closed set; `control/storage.py` = every write; `control/index.py` = the FTS triggers; `control/query.py` = every read, both readers'; `control/gate.py` = may this text replace what is stored; `boundary/search.py` = the CLI reader |
| `fetcher` | the bytes, and what fetching them cost. `entity/page.py` = `page_cache` + `content_hash`/`same_bytes`; `entity/call_log.py` = `wayback_calls`; `control/archive.py` = everything that talks to archive.org (CDX + cached content fetches); `control/politeness.py` = `HEADERS`, `SLEEP`, `fetch()` for a live listing, kept nowhere, `fetch_cached()` for a live article, `refetch=` replacing it; `control/address.py` = capture addresses as pure strings, `timestamp_of()` and `viewer_url()` included |
| `provenance` | which capture a body came from. `entity/origin.py` = `body_origin` + `record`/`clear`/`page_of`; `control/resolution.py` = `origin_key()`, `own_page()` — the question that picks the gate — `cached()`, and `capture_page()`/`capture_kind()`, the two strings the browser renders next to a link; `control/reproduction.py` = does a parse reproduce a stored body: `verdict()`, `attachment_verdict()`, and `origin_class()`, how an address was established; `boundary/verification.py` = `pressroom-verify-body-origin`, the walk over `body_origin` and the one dispatch by source tag, the file the direction rule excepts |
| `text` | bytes and markup to text. `control/decoding.py` = `decode_html()` when the declared charset is wrong, plus `repair_text`/`repaired` and the `REPAIRS` counter; `control/richtext.py` = `extract()`, the tag allowlist, the sanitizer, the plain-text renderer, plus `densest()`/`cut_from()`; `control/dating.py` = `iso_date()`, the single date parser; `control/containers.py` = where an article is in a page: `locate()`, `describe()`, `ancestry()`, the measure the container calibration reports; `boundary/encoding_check.py` = `pressroom-verify-encoding`, the walk over every stored field with `decoding.damaged()`/`undefined_bytes()` and the report that the two detectors agree; `boundary/container_calibration.py` = `pressroom-calibrate-containers`, the walk over every cached capture and the report of the dominant shape |
| `converter` | what an attachment's bytes mean: `.pdf`/`.doc` -> text (`plain_text`), or a PDF -> our HTML subset (`to_richtext`). Shells out to `pdftotext`/`antiword`; no network, no SQL. Also the two predicates that ask the same question before and after a fetch: `is_attachment_url()` on an address, `is_attachment()`/`looks_like_html()` on bytes — one magic-byte table, both directions; `attachment_name()`, the one identity a mirrored file keeps across hosts. `control/quality.py` = what can go wrong in a conversion, measured: the interleaving artefacts, the lost structure, and the thresholds that turn them into a pass or fail. `boundary/calibration.py` = `pressroom-calibrate-converters`, the walk over every cached attachment, the printed metrics and the page of full texts |
| `scraper` | what every scraper is, independent of which: the structure of a run. `entity/parse.py` = `Detail` and `Entry`, what a scraper's parser hands back; `control/discovery.py` = phase 1's three strategies, plus the fetch-and-parse every tail shares - `capture()`, `fetch_detail_snapshot()`, and `sample_all_captures()` over a listing's captures; `control/catch_up.py` = phase 2's six strategies, each taking the parser or collector from its caller; `control/attachment_crawl.py` = the second contract, phase 2 for `.pdf`/`.doc` rows; `control/twin.py` = filling a teaser from its twin row; `boundary/command.py` = the command line every scraper has |
| `reporting` | what a run did, in one vocabulary. `control/outcome.py` = `Stats`, the seven fixed outcomes and the summary line; `control/selection.py` = which capture the archive walk did not take, emptied into that summary. No table, so no entity |
| `taxonomy` | `entity/company.py`: the source->company table. No SQL, no HTTP |
| `browser` | `boundary/http.py` + `boundary/static/`: routing, query-param parsing, JSON, and the three frontend files. No SQL, and no facts of its own: the capture annotations come from `provenance` and `fetcher` |
| `q4` | the Q4 Inc. IR-platform parser, shared by exactly two firms. On the library side of the direction rule, where a source may reach it |
| `intel`, `amd` | `control/q4.py` = the list url and, for AMD, two selectors, handed to `q4`'s crawler and parser; `boundary/press.py` = the command |
| `creative` | its own press room (`press`) and the GlobeNewswire wire (`globenewswire`) |
| `terratec` | four site generations: `early` (1996 anchors), `pressemit` (the hand-built template, .net and .de), `portal` (PHP-Nuke, two languages), `cms` (2007-2013) |
| `maudio` | five CMS generations over four domains: `golive` (2001 static), `pressdb`, `media_pr`, `media_news`, plus `presse_de` (midiman.de) and `news_blog` (the m-audio.com blog) |
| `soundonsound` | `magazine`: the one publisher here |

`pressroom/names.py` has no row: not a business component, it walks the tree
rather than belonging to it, and its docstring says so. Its command is
`pressroom-verify-names`.
