# Provenance

Which capture a body came from, why that is a table rather than a column, and
why it has two columns after two removals.

**A row's URL is not always a page that existed.** Three shapes:

- *page-derived* — the row has its own capture. `--seed-cache`, then `from_cache`.
- *listing-derived* — the release only ever existed inside a listing, so the
  scraper minted the URL. `detail_id` is the *listing's* timestamp, so a per-row
  fetch is guaranteed 404s; these scrapers pass a *collector* and no
  `retry_missing`, and `from_listings` matches entries back by URL. It only ever
  UPDATEs — a parse matching no stored URL is dropped, never inserted, so a
  re-extraction cannot mint rows under URLs nobody has seen.
- *listing-derived with a real URL* (`terratec_new_*`) — the hrefs were genuinely
  on the page; archive.org simply never captured some of them. Same treatment,
  opposite reason: nothing to fetch because nothing was ever there.

**A timestamp does not say which page it is a capture of, and `body_origin` is
where that is written down.** `http.wayback_url()` used to build
`web/<ts>/<row url>` from `detail_id`, a capture that never existed, while the
listing capture holding that release's full text sat in `page_cache` all along.

It is its own table, not a column: absence has to keep meaning "no archive link
for this row". **Two columns**, after two removals. `page_url` was a prefix of
`origin_url`, and `origin_url` survived because it is the whole address and the
`page_cache` key — rebuilding it the other way would need `detail_id`, which a
recovery can rewrite underneath, so `wayback_url()` never consults the timestamp
when an entry exists. `matched` went once its three classes turned out to be
derivable from the address, which `verification.origin_class()` computes
(`computed` = the address the row implies, `located` = a .pdf/.doc found under
its own path, `inferred` = neither). The score was never the useful thing: most
inferred rows fall below 1.0 and are right, and four attachment rows scored a
perfect 1.0 and were wrong.

**`pressroom-verify-body-origin` answers the real question by reproduction** —
the source's own parser over the recorded capture, against what the row stores.
Read-only; run it after any pass that touches bodies or origins. One row is known
never to reproduce: its stored body is several releases concatenated by an old
extraction, so rewriting it would delete text belonging to other rows
(`KNOWN_IRREPRODUCIBLE` in `tests/corpus/test_corpus.py`).

**The pass that reads the bytes records where they came from.** `origin.record()`
sits next to the body write, in the same transaction, at every site that has the
address in hand; the listing collectors carry `origin_url` on each entry for it,
where they used to parse a capture and throw its address away. `run_retext`
records nothing on purpose — its text comes from stored HTML, no capture
involved. **So nothing has to be re-run after a crawl.**
`address.is_capture_address()` requires `web/<14 digits>id_/…`, so a Q4 id, a
Drupal node id or a bare row url raises `ValueError` at the write site rather
than minting a dead link.

**An entry must be dropped the moment it stops being true.** `twin.fill` calls
`origin.clear()` because the text it writes came out of a *sibling row*, so
whatever capture was recorded has stopped describing it. Without that, the
browser would link a listing for text that no longer came from one.

The table covers every Wayback row, which is what lets `http.wayback_url()` be
two lines with no idea what a timestamp looks like — no entry, no link, the right
answer for a live source too. `origin_url` never reaches the JSON:
`http.py` turns it into `wayback_url` plus `capture_page` and drops it, and
`capture_page` is **only** set when the capture is of a different page (the first
cut annotated every row). Checked as `[capture-of-listing]` and
`[capture-of-own-page]`.

**"z listingu" is provenance; "teaser" is a grade.** Three attempts at deriving
the grade from a listing all failed: the "weiterlesen..." link is on *every*
entry, so it means "here is the article page", not "this is truncated";
truncation is era-dependent rather than per-row, and this CMS embedded the full
release on the listing in its later years; and "the capture serves several rows"
does not identify a listing, because the siblings resolved to captures of their
own. So the badge states the one certain thing — the text was read off a page
that is not this release's own. Checked as `[badge-listing]`.
