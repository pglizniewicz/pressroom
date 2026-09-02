# A run's two phases

Phase 1 discovers and stores, phase 2 finishes what it could not get. Both are
libraries now, `scraping/control/discovery.py` and
`scraping/control/catch_up.py`, and both hold their rules in one copy for the
same reason — the reason being what happened while phase 1 did not.

## The catch-up

The six strategies a rerun composes, and the three rules that hold inside every
one of them. Rules 1 and 2 were bought with 122 articles.

**What each strategy needs and does is its own docstring**, next to the code.
What is here is why the set is split at all, and what picking the wrong member
of it cost.

**Three rules hold inside every strategy, and each cost data before it was a
rule.** They live in the library, in one copy, so a scraper cannot get them
wrong:

1. the cursor is `body_html IS NULL`; `force=True` widens it and says so first.
2. `gate.not_shorter` applies **under** every other gate, `force` included.
3. **the gate is chosen by the address, not by a flag.** `resolution.own_page()`
   decides: the row's own capture goes through `safe_to_write`, some other page
   through `strict_same_text` — and if the caller has a collector, such a row is
   skipped here for `from_listings`. This caught a live bug the moment it landed:
   the old `--from-cache` would have handed a `midiman_net_pressdb` row the
   *listing* capture its teaser came from, and a whole-page `parse_detail`
   returns the whole listing — 34k characters of other releases' text, which
   `safe_to_write` reads as a teaser recovering its article and allows. The same
   strategy cost 64 rows once.

**Rules 1 and 2 were bought with 122 articles.** The listing strategy once had no
cursor and no length floor, so joining `terratec_new` to it walked all 159 rows
instead of the 24 pending ones and **122 full articles were overwritten by their
listing teasers**, one going 4287 -> 359 characters: this CMS embeds the full
text on the listing for recent releases and truncates older entries, so a listing
entry is not automatically the better copy. `safe_to_write` provably cannot catch
that — a lost tail is `edges_only`, the same signature as correctly dropped nav.
The loss was restored from a pre-run copy of the DB, which is why that copy is a
rule here and not advice.

**`--seed-cache` comes first when a parser is being redesigned.** It fetches and
stores, full stop — no parsing, no write to `releases` — so it cannot damage a
row and needs no parser to exist yet. Two traps that cost real data:

- **A `.pdf`/`.doc` row must never reach an HTML parser.** BeautifulSoup does not
  refuse binary; it returns a document whose `get_text()` is the PDF stream
  decoded as characters, with no exception to catch. 23 rows had `%PDF-1.3 %âãÏÓ
  6 0 obj…` stored over text `pdftotext` had extracted correctly, and it looked
  like *encoding damage* rather than data loss, because a decoded PDF is full of
  C1 characters. `looks_like_html()` checks magic bytes before every parse now.
  The tell: the encoding repair suddenly fixed **0 rows and refused 48 fields** —
  a repair that can no longer fix anything means the damage is not what you think.
- **A timestamp in `detail_id` does not always name a capture of that row's own
  URL.** For pressdb/media_news it can be the *listing* capture, so the
  reconstructed URL never existed and 404s permanently (several rows sharing one
  timestamp is the giveaway). The CDX fallback turns that into a real recovery or
  a confirmed `dead`, instead of an `uncertain` retried forever.

`body_html IS NULL` means the row predates all this and renders preformatted; it
is the `plain` flag in the audit view, a progress bar rather than a defect. The
flag excludes every `.pdf`/`.doc` url, a slight over-exclusion now that the
cached PDFs carry markup.

## Phase 1, and what nineteen copies of it drifted on

`catch_up.py` opens with *"A scraper's first phase discovers and stores. This is
the second"*, and for as long as it said so the first half existed only as a loop
written out by hand in every scraper: nineteen copies over fourteen control
modules, twelve of which PyCharm reported as `Duplicated code fragment`, the
longest at 35 lines. The reason to consolidate them was never the line count.
**It is that the copies disagreed, and each disagreement was a bug in whichever
copy lost.**

Four of them, and all four were found by reading the copies side by side rather
than by any test going red:

- **`maudio/presse_de.py` asked whether a row already held a teaser before
  asking whether the probe was confirmed.** Its three siblings ask in the other
  order and each carries a comment explaining that they must — "a failed probe
  is not a verdict, so an already-stored teaser must be reported `uncertain`
  rather than `skipped`". Under the reversed order a network error on a
  teaser-grade row was filed under "already as good as it gets" — which is the
  one bucket that means "nothing left to do here", so the summary a human reads
  after an hour of crawling understated by exactly the rows worth re-running.
- **`archive.fetch_detail_snapshot` threw away the capture's address.** It
  annotated `detail_id` onto the parse and dropped `snap_url`, so no caller
  could obey the rule that a body's capture is recorded at the write site, in
  the same transaction — and for four maudio tags, none did.
  `terratec/portal.py` is the tell: it grew its own `recover_article` returning
  `snapshot_url`, and it is the only one of these loops that ever passed
  `origin_url`. The corpus showed no gap, because phase 2 filled these in behind
  the crawl; what made it worth fixing anyway is that a row discovered *today*
  gets `body_html` and so never enters phase 2's `body_html IS NULL` cursor. The
  gap was permanent for every future row, and invisible in every past one.
- **Five copies bumped a counter unconditionally after an `INSERT OR IGNORE`**,
  which is Invariant 5's phantom insert. `soundonsound/magazine.py` was the copy
  that had it right, comment and all, and its two extra arms — an empty body is
  `dead`, a write that inserted nothing is `skipped` — became the library's
  defaults rather than flags, because a rule only one copy remembered is the
  definition of a rule that belongs in the library.
- **A bodyless row was stored as `full`.** Eleven rows in
  `terratec_pressde`/`_pressen` say `full` over an empty body, all from this
  loop; `cms.py` and `presse.py` computed `"full" if body else "stub"` and the
  rest did not. What the two halves of the library then do differs on purpose: a
  live fetch that came back empty leaves nothing to return to, so nothing is
  written at all, while an archived candidate has a real url and a named capture
  and the row is what lets phase 2 come back for the text.

**The strategies are named for where the release's text comes from**, which is
the only axis the copies differed on that was not a bug: `from_items` (a listing
item, body from a live fetch), `from_candidates` (a bare url, the earliest
capture of it that parses to anything, checked against two more — see
[captures.md](captures.md)) and `from_teasers` (a listing entry that carries a
teaser, which the detail capture may upgrade). **`capture()` is exposed
separately because the split is at a seam, not at a line count**: the
probe/fetch/report half is identical in five modules, and what happens when
archive.org confirms there is *no* capture genuinely differs — `dead`, a
title-only stub, or falling through to a listing copy — so that decision stays
with the source and the half above it does not.

**One of those three answers has since moved into the library, as
`from_candidates(stub_if_absent=…)`.** It had to, because one crawler can hold
candidates from two channels at once: a url a *listing* named is a release whose
title and date are real even when its page was never captured, while a url only
a folder listing produced has nothing to keep — and CDX having just listed it
makes an absence there near-impossible anyway. So the flag does not decide it;
the metadata does, and the flag is what keeps three scrapers that pass `titles`
without meaning any of this on the old verdict. `dates` arrived beside `titles`
in the same change, for the listings that state a date the article page does
not.

**Only one flag survived**, `prefer_parsed`, and it is the one real question:
whether the detail page states the headline and date better than the listing
does. On the m-audio blog it does; on `media_news` the listing wins and the
upgrade writes the body alone. `presse_de` used to be the third answer to that
question and is now none of them: the one page it ever fetched as a detail turned
out to be a third listing, so the scraper passes a collector and no parser at all.

**What proved it: the corpus, on a copy.** Deleting five rows per source and
re-running the scraper brings them back identical to the byte — title, date,
grade, body length, markup length, `detail_id` **and** `origin_url` — and the
one row whose probe failed mid-rehearsal was reported `uncertain`, written
nowhere, and picked up by the next run and nothing else. That is the whole
contract of a resumable crawl, observed rather than asserted.

Two of those fields have since stopped being part of the claim, and the
rehearsal is still worth running for the rest. The identity of `detail_id` and
`origin_url` was a statement about the *newest working capture* rule, which no
longer selects anything ([captures.md](captures.md)); re-run today, a row whose
earliest good capture is not its newest legitimately comes back naming a
different one. That is the change working, not the rehearsal failing. What the
rehearsal still proves is what it was for: nothing is written twice, and a row
whose fetch failed is left for the next run and touched nowhere else.
