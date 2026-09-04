# Attachments

What an attachment's bytes mean, why a PDF gets markup and a `.doc` does not,
and why dispatch is on magic bytes.

**A PDF carries structure and a .doc does not.** Poppler measures the first, so
`conversion.to_richtext()` asks `pdftotext -bbox-layout` for
page/flow/block/line/word with coordinates and converts that into the same
allowlisted subset every HTML source gets — a paragraph because poppler measured
the gap, a heading because the type is taller, a list item because the line opens
with a marker. Layout metadata, not a heuristic over extracted text. The .doc
equivalent (`antiword -x db`, DocBook) was built and dropped after review: it
flattens nested lists, loses paragraph breaks and mangles numbering, so **a .doc
keeps `plain_text()` and `body_html` NULL**, rendered `pre-wrap`.

**Both offline passes share one cursor, `body_html IS NULL`,** for two different
reasons. Re-extracting text into a row that already carries markup would trade
real paragraphs for a `pre-wrap` blob — a policy, not an edge case the gate should
have to catch. Re-converting one runs the same extractor over the same bytes and
writes them back identical, and `upgrade_release` still returns True because the
UPDATE matched its row — so the counter reported `upgraded` on a run that changed
nothing, and the rule that gates a counter on the write's return value could not
see it. That is how the richtext pass was released without the filter at all:
the error was in the report, never in the corpus. Neither pass has a flag that
widens the cursor; a converter change is a one-off, and widening the query by
hand for that one run is the same answer this project gives to a schema change.

**Two of this module's three writes state `grade="full"`, and the third must
not.** The rule itself is general — an upgrade that replaces a teaser body with
the real article says so, or `stored_grade()` returns the row again — but
which write *is* that upgrade is decided by the gate above it, not by the pass's
name. `write_richtext` compares the two conversions of one PDF to each other and
never to what is stored, and its cursor asks only about `body_html`, so a row
whose `body` is still the listing blurb goes through it; the crawl writes exactly
when the fetched text is longer than the stored one, which is the replacement
itself. `reextract_from_cache` writes only what `strict_same_text` proved is
already stored character for character, and skips a row whose text does not
change — it has established nothing about the grade, and stamping one there
would be as false as withholding it in the other two. These scrapers grade at
insert time, before the attachment has been fetched at all — the state
`length(body)` exists to detect — so what the two grades achieve is the row
that arrives here graded correctly, not a rewrite of the ones already stored.

**Dispatch is on magic bytes, not the extension** — CMS-era attachments are
routinely mislabeled and some of this corpus's `.pdf` URLs are an HTML error
page. CDX's `statuscode:200` is necessary but not sufficient: it proves
archive.org got an answer, not that the answer was the attachment (see the
soft-404 rule in [numbers.md](numbers.md)). Hence
`archive.fetch_best_matching_snapshot`, which walks every capture rather than
stopping at the newest, scoring each one by how much text the file yields —
`attachment_crawl.attachment_score`, with `is_attachment()` as its floor. That
is the same measure the write below it decides on, so the walk cannot pick a
copy its own gate then refuses; a bare bool would tie every capture and let an
early, thinner revision be picked. `domain_variants()` tries the mirror siblings
before giving up.

**The attachment network crawl is opt-in** (`--attachments`) and is the one
exception to "a plain rerun gets everything": nothing records "CDX has no
capture of this url, ever", so a full pass costs hours to rediscover rows already
known dead.

**Two standing decisions about attachment rows are in
[numbers.md](numbers.md)**, with the rest of what the deleted tally section
kept: the rows with no cached bytes are dead, and the rows whose bytes exist
only under a mirror domain have no capture of their own. Both were established
by a full crawl that returned nothing — do not run that crawl again.
