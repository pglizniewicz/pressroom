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
where that is written down.** The browser's archive link used to be built as
`web/<ts>/<row url>` from `detail_id`, a capture that never existed, while the
listing capture holding that release's full text sat in `page_cache` all along.

It is its own table, not a column: absence has to keep meaning "no archive link
for this row". **Two columns**, after two removals. `page_url` was a prefix of
`origin_url`, and `origin_url` survived because it is the whole address and the
`page_cache` key — rebuilding it the other way would need `detail_id`, which a
recovery can rewrite underneath, so the link is `address.viewer_url(origin_url)`
and never consults the timestamp when an entry exists. `matched` went once its three classes turned out to be
derivable from the address, which `reproduction.origin_class()` computes
(`computed` = the address the row implies, `located` = a .pdf/.doc found under
its own path, `inferred` = neither). The score was never the useful thing: most
inferred rows fall below 1.0 and are right, and four attachment rows scored a
perfect 1.0 and were wrong.

**`pressroom-verify-body-origin` answers the real question by reproduction** —
the scraper's own parser over the recorded capture, against what the row stores.
Read-only; run it after any pass that touches bodies or origins. One row is known
never to reproduce: its stored body is several releases concatenated by an old
extraction, so rewriting it would delete text belonging to other rows
(`KNOWN_IRREPRODUCIBLE` in `tests/corpus/test_corpus.py`).

**The pass that reads the bytes records where they came from.** `origin.record()`
sits next to the body write, in the same transaction, at every site that has the
address in hand; the listing collectors carry `origin_url` on each entry for it,
where they used to parse a capture and throw its address away. `run_retext`
records nothing — its text comes from stored HTML, no capture
involved. **So nothing has to be re-run after a crawl.**
`address.is_capture_address()` requires `web/<14 digits>id_/…`, so a Q4 id, a
Drupal node id or a bare row url raises `ValueError` at the write site rather
than minting a dead link.

"In the same transaction" was a claim before it was a fact. `storage` ran the
row write, then the origin write, then `conn.commit()` — and a bare commit has
no failure branch. When `is_capture_address()` raised on a Q4 id, the release
INSERT was left in an open transaction: not written, not rolled back, and the
next `commit()` from anywhere on that connection adopted it. The row then
existed with no `body_origin`, which is the exact false statement the guard
above had just refused to make. `with conn:` is the fix and the whole of it —
it commits on success and **rolls back on an exception**, which is the part
`conn.commit()` cannot express. `tests/release/test_storage.py` holds it, and
that test was checked against the old code first: it fails there.

**An entry must be dropped the moment it stops being true.** `twin.fill` calls
`origin.clear()` because the text it writes came out of a *sibling row*, so
whatever capture was recorded has stopped describing it. Without that, the
browser would link a listing for text that no longer came from one.

That clear is in the same transaction as the upgrade, and had to be moved into
one. `twin.fill` used to let `upgrade_release` commit the new body on its own
and clear the entry afterwards, under a trailing `conn.commit()` at the end of
the loop. Between those two points the row held the sibling's text and still
advertised its old capture — a window the crawl could die inside, leaving
behind precisely the false statement this section is about. Both calls now pass
`commit=False` and one `with conn:` closes over the pair.

`attachment_crawl.py` was the last split pair, and it survived the other two
being fixed because it looked harmless. Its two offline passes read `origin_url`
out of a JOIN on `body_origin`, so the second commit only ever restated the value
the first had just read - a window with nothing to lose inside it, and nothing to
report it. The network path is where the shape was real: there the address comes
back from the capture walk, often under a mirror domain, and a crash between the
two commits left the row holding the PDF's text while still advertising whatever
capture it had before. All three now pass `origin_url=` to `upgrade_release` and
inherit its transaction, which also puts `is_capture_address()` on a path that
never had it. `tests/scraper/test_attachment_crawl.py` holds it; against the old
code nothing raises at all, because `origin.record` does not validate - only the
write site does.

The table covers every Wayback row, which is what lets the browser's link be
`address.viewer_url(origin_url)` with no idea what a timestamp looks like — no entry, no link, the right
answer for a live source too. `origin_url` never reaches the JSON:
`http.py` turns it into `wayback_url` plus `capture_page` (through `address` and
`provenance.control.resolution`, whose facts they are) and drops it, and
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
