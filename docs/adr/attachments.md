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

**Dispatch is on magic bytes, not the extension** — CMS-era attachments are
routinely mislabeled and some of this corpus's `.pdf` URLs are an HTML error
page. CDX's `statuscode:200` is necessary but not sufficient: it proves
archive.org got an answer, not that the answer was the attachment (see the
soft-404 rule in [numbers.md](numbers.md)). Hence
`archive.fetch_first_matching_snapshot`, which walks captures newest-to-oldest
until `is_attachment()` confirms one rather than stopping at the newest;
`domain_variants()` tries the mirror siblings before giving up.

**The attachment network crawl is opt-in** (`--attachments`) and is the one
honest exception to "a plain rerun gets everything": nothing records "CDX has no
capture of this url, ever", so a full pass costs hours to rediscover rows already
known dead.

**Two standing decisions about attachment rows live in
[numbers.md](numbers.md)**, with the other survivors of the deleted tally
section: the rows with no cached bytes are dead, and the rows whose bytes exist
only under a mirror domain have no capture of their own. Both were bought with a
full crawl that returned nothing — do not re-buy either.
