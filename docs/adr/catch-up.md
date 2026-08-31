# The catch-up

The six strategies a rerun composes, and the three rules that hold inside every
one of them. Rules 1 and 2 were bought with 122 articles.

**The list of the six lives in `scraping/control/catch_up.py`'s docstring**, next
to the code it describes — what each one needs and what it does. It used to be
copied into the prose as well, in two places, and the copies are what argued
themselves out of existence: renaming the concept from "shapes" to "strategies"
had to touch all three, which is one edit for the code and two chances to leave a
document saying something else. `CLAUDE.md` keeps only the names, because the
rules below refer to them.

**What is here is why the set is split at all**, and what picking the wrong
member of it cost.

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
