# Text and markup

How a release becomes `body` + `body_html`, and the extraction rules a
measurement established.

**A release is stored twice: `body` and `body_html`.** `body` is plain text with
real paragraphs — FTS, snippets and every `length(body)` heuristic read it.
`body_html` is the small allowlisted subset `richtext.py` emits. Both come out of
one `extract()` call, and `to_text()` runs on the *cleaned HTML*, never the source
node, so the indexed text and the displayed markup cannot drift apart.

**`body` is by definition `to_text(body_html)`** wherever `body_html` exists,
which makes a fix to the *renderer alone* free: `--retext` recomputes `body` from
stored HTML with no network and no parser. A change to `clean()` still needs the
HTML rebuilt.

**Never write `soup.get_text(" ", strip=True)` for a body again.** That is what
flattened the whole corpus: every `</p>`, `<li>` and `<br>` becomes one space, so
a 4000-character release arrives as one blob — and the newlines that remain are
the *source file's* line wrapping, which `strip=True` leaves inside a text node.
Real structure gone, indentation kept. `get_text(strip=True)` on a **title** is
still right.

Three things `richtext.py` does that are decisions, not cleanup:

- **`<img>` is preserved verbatim**, only the scheme filtered. These sites are
  dead so most addresses resolve to nothing, but they are the only record of
  which image belonged where. The browser degrades a failed load to a caption.
- **A table with no row wider than one cell is demoted to blocks** — every one of
  these CMSes laid its pages out in nested tables, and a real spec sheet has 2+
  columns. Pruning and demoting alternate in a bounded loop, because a page
  header only looks like a one-cell wrapper *after* its image-only rows go.
- **Empty blocks are pruned, and for the table family an image is not content** —
  that is what separates a product photo in its own paragraph from a banner built
  out of `top.gif` and 1x1 spacers.

**The allowlist is in two places**, `richtext._ALLOWED` and `RICH_TAGS` in
`static/app.js`; `tests/test_mirrored_rules.py` asserts the pair.

**`richtext.cut_from()` must find the text node, not the element.** The naive
version scanned leaf *elements* and silently did nothing on midiman.de, where
"Infos bei:" sits in a bare text node between two `<br>` — the contact footer
was left in 37 bodies that should have had it cut.

**A parser that finds no container must fall back, never blank.**
`extract(None)` returns `("", "")`, so a missing container silently becomes an
empty body. Every parser keeps the old flat text in that case and leaves
`body_html` NULL; some of these captures are 290-byte "page moved" stubs.

**Open defect: a DOCTYPE leaks into the body of a whole-document parse.**
`richtext.clean()` strips every `Comment` and nothing else of that kind, and
BeautifulSoup's `Doctype` is a string node too. A parser that hands `extract()`
the whole soup because the page *is* the release - `pressdb.py`, `golive.py` -
therefore stores `HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"` as the
first paragraph of `body_html` and the first line of `body`; the golden
`midiman_net_pressdb__detail` shows it, the `midiman_com` sibling has no
doctype and does not. Found in passing while measuring markitdown
([attachments.md](attachments.md)), not yet fixed. The fix belongs in `clean()`
next to the comment pass, never in each parser, and it is a change to `clean()`,
so the HTML has to be rebuilt afterwards - `--retext` is not enough.

**Every parser is DOM-based, and a selector comes from the dominant shape.**
`pressroom-calibrate-containers` walks **every cached capture** of a source,
finds the smallest element covering that row's stored body, and reports the
distribution — a selector comes from the dominant shape, never one sample. That
is what stopped `td[valign="top"][width="85%"]` (looks obvious, misses 36 of 186
portal captures) in favour of `richtext.densest(soup, "td")`, which hit 100%.
Three shapes came out of it: *the page is the release* (`golive.py` — careful,
the one parser whose **title** comes from string slicing, so its flat `get_text`
stays, for detection only), *the biggest layout table/cell* (`pressemit.py`,
`portal.py` — no classes or ids worth keying on), and *an HTML
fragment already sliced* (`presse_de.py`'s `blocks()`).

**A regex that identifies the headline decides which pages exist at all.**
`cms.extract_entries` recognised a release *only* by an `<h2>Month YYYY -
Title</h2>`, and later captures of that CMS dropped the date prefix — so those
pages parsed to nothing whatsoever, no warning, no marker. It keys on the
container now (`div#Content > div.column.span-8`) and takes a date-less heading
as a headline when it sits in a `div.block` or is the column's only one.

**A title comes from markup that means "headline", never from the body**, and is
written **only over an empty one** (`catch_up._fill_title`). Three rules the
recovery was built on, each established by a measurement:

- **A new extraction rule goes in as a *fallback*, never a replacement.**
  `pressemit.find_headline` runs only when the caller's bold-tag rule returns "".
  Tried the other way round it filled 8 rows and *changed* 30 — 12 from a correct
  title to an empty one.
- **The walk for a bare-text headline stops at `<p>`, not at "any block".**
  `<tr>`/`<td>` are the container being walked into, so stopping there ends the
  walk before any text; a cell that opens with a paragraph has no headline, and
  descending into it titles the row with the release's first sentence.
  `MAX_HEADLINE` bounds whatever gets past that.
- **The pass writes the title and nothing else.** Several of those captures parse
  to a better *body* too, but a body rewrite belongs to the re-extraction
  strategies,
  which go through the gate.

A handful of rows keep an empty title and that is the end state — PHP-Nuke
skeletons captured with no article in them, 290-byte placeholders, one release
that opens straight into prose. The browser renders `(bez tytułu)`, checked as
`[untitled]`.
