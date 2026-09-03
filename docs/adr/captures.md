# Captures

Why every fetched byte lives in the database, and how archive.org is asked.

**`page_cache` means a parser fix costs no refetch.** Every fetched page's raw
bytes live in the DB (not on disk — auxiliary data belongs in the database), so
reparsing is free and refetching is a mistake. The table was called
`wayback_cache` when only `archive.fetch_snapshot()` used it; the four live
scrapers went straight through `session.get`, and when the flat-text extraction
turned out to be wrong they all had to be crawled again from scratch.
`politeness.fetch_cached()` closed that hole. **A new scraper that fetches a page
any other way is a bug.**

**Ask archive.org through `archive._cdx()`** — the public CDX API. Not
`__wb/sparkline` or `__wb/calendarcaptures`: internal endpoints needing a forged
`Referer`, and 3 requests where CDX takes 1.

**Every HTTP attempt against archive.org is logged to `wayback_calls`.** Before
tuning a timeout or sleep constant in `archive.py` from a handful of manual
`curl` calls, query this table — that is the mistake that got `CDX_TIMEOUT` tuned
twice on thin evidence before this existed.

**Re-extracting the whole corpus is free.** Everything fetched is in
`page_cache`, so `pressroom-<scraper> --offline --force` re-runs `clean()` over
rows that already have `body_html` without a single request.

## Which capture of a url is the right one

**HTTP 200 is not the article.** `get_latest_working_snapshot()` asks CDX for the
*last* matching row — the newest capture that answered 200 — and for a press
release on a domain that was later rebuilt, that is the modern site's shell
page. It parses to nothing, so `fetch_detail_snapshot` returned `({}, True)`, a
*confirmed* verdict, and `catch_up.retry_missing` wrote `dead` on a row whose
article was sitting in an older capture the whole time. The row then kept a body
nothing could reproduce: the bytes it came from were never cached, and every
rerun re-fetched and re-cached the useless shell instead.

**The listing path had already been immunised against exactly this and the
detail path had not.** `media_news.discover_listing_best` says so in a comment —
by 2019 that listing url answered 200 with m-audio.com's home page, so it uses
`sample_all_captures`. `fetch_first_matching_snapshot` was the second answer,
written for attachments: a validity-checked walk, but newest-first and capped at
six attempts. Three answers to one question, and the detail path had the weakest.
There is one now.

**Oldest-first, because a press release is not edited after publication.** The
earliest capture is normally the article itself and the least contaminated by a
later redesign wrapping it in navigation. That rule has two failure modes and
each gets one probe rather than a full walk: a crawl that arrived before the page
had settled (the capture a year on), and a correction published onto a site that
then died before the year was out (the last capture that still scores). **A tie
goes to the earlier**, so only a real advantage moves the default. What is left
uncovered is a version that existed only in the middle and beat both ends; the
trailer below is what would show that happening, and a full walk over every
capture is the next step if it does.

**The attempt cap had to go, because it made `confirmed` a different claim.**
`exhaustive and not had_error` meant "as far as we bothered to look", so `dead`
was a verdict the walk had not earned. Uncapped it is `not had_error`, plus one
new guard: `list_all_captures` asks CDX for `CDX_ROW_LIMIT` rows, and a walk over
a list that came back full has not seen everything either. The cost is bounded by
`page_cache` — a walk is paid once and every rerun of it is free, sleep included —
and the two end-walks are trimmed to meet in the middle, so even a url whose every
capture is a shell is fetched once through.

**The score is the caller's, and it cannot be bytes.** The 2024 shell is 40 KB and
the 2012 article 22 KB, so the obvious measure is the measure backwards; `archive.py`
has no way to know better, and keeping it ignorant is the property being preserved.
One `score(content) -> int` rather than a predicate plus a comparator: the detail
scorer has to parse anyway to answer "is there an article here", a number is what
the trailer prints, and 0 as the rejection makes admission and ranking one call.
It must agree with the gate the write goes through — `gate.not_shorter` for a body,
the length comparison in `catch_up_network_source` for an attachment — or the walk
picks a copy its own gate then refuses. Two scorers differ on purpose:
`discovery._detail_score` gives a title-only capture one point, because
`from_candidates` stores that as the `stub` phase 2 comes back to, and a scorer
that rejected it would turn every such row into `dead`.

**"No usable capture" and "no capture" are two answers, and collapsing them
costs rows.** The walk returns `content=None` with the earliest timestamp still
in hand when captures of the url exist and not one of them scored;
`timestamp` is None only when the archive never saw the url at all.
`from_candidates` needs exactly that difference — the first is the `stub` phase
2 comes back to, carrying whatever title a listing knew, and the second is
`dead` and no row. The first cut of this change returned `(None, None, True)`
for both and quietly turned every such release into a dead end.

**A probe that wins is reported, and a probe that could not be fetched is
reported too.** `reporting/entity/selection.py` collects one record per url —
which capture was taken, which was passed over, both scores, and why — and
`Stats.summary()` drains it after the counts. Same mechanism and same reason as
`decoding.REPAIRS` above: the walk is the only place that knows, and a separate
pass would be a rule someone has to remember. It is also the measurement the two
probes have not got yet: they cost fetches to catch cases nobody has counted, so
a full crawl whose trailer stays empty is the argument for deleting them.

**The numbers are in `tests/fetcher/test_archive.py`**, not here — the 915-vs-0
pair the whole change rests on is a test that can fail, which is what
[numbers.md](numbers.md) asks for.
