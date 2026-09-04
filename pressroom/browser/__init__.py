"""# Browser
> The local, read-only reader for the corpus: a JSON API over `pressroom.db` and
> the one page that renders it, on this machine only.

## Boundary
- `serve` — start listening for a person's browser, on the local machine only,
  over one database file
- `open-page` — serve the page and its files, and nothing else from disk
- `search-releases` — one page of releases, browsed or matched by full text,
  narrowed by company, source, dates and quality flags
- `show-release` — one release in full, with where its text came from and its
  neighbours in the same source
- `list-sources` — every source tag with its size, date span and gap counts
- `list-companies` — the same rolled up per company, each carrying its sources
- `count-quality` — the corpus-wide gap counters the audit view shows

## Requirements
The BC owns the whole of it, the page included: R1–R5 state what it answers to
a request, R6–R11 what it renders in a person's browser (D1).

### R1: Serve
- R1.1 — The BC shall listen on the local machine only.
- R1.2 — If the database file does not exist, then the BC shall write an error
  naming the file to standard error, exit with a non-zero status, and not
  listen.
- R1.3 — If the port is taken, then the BC shall write an error to standard
  error naming the port and a still-running instance as the likely cause, exit
  with a non-zero status, and not listen.
- R1.4 — When a request is received, the BC shall open the database read-only
  for that request alone and close it before the response is complete, so that
  an attempted write is an error and never a change.
- R1.5 — If a request fails for a reason the BC does not handle, then the BC
  shall answer that one request with an error and keep serving.

### R2: Open the page
- R2.1 — When the root or the index is requested, the BC shall serve the page.
- R2.2 — The BC shall serve exactly three files from disk — the page, its
  script and its stylesheet — and no request path shall reach any other file.
- R2.3 — If any other path is requested, then the BC shall answer "not found"
  as a structured error, never as a traceback.
- R2.4 — The BC shall mark every answer as not to be cached.

### R3: Search releases
- R3.1 — When a search is requested without a text query, the BC shall page
  through the corpus newest first, dateless rows included.
- R3.2 — When a search is requested with a text query, the BC shall match it
  against titles and bodies, rank by relevance unless date order is requested,
  and mark the match in each result's excerpt.
- R3.3 — If the text query is not a valid full-text expression, then the BC
  shall search it as a literal phrase and mark the answer as a phrase search,
  rather than fail or answer empty.
- R3.4 — The BC shall narrow a search by company, by source tag, by date span
  and by quality flag, in any combination; a company and a source narrow to
  their intersection.
- R3.5 — If a company and a source share no row, then the BC shall answer an
  empty page — not the whole corpus, and not an error.
- R3.6 — If a flag, a company, an order or a cursor is not one the BC accepts,
  then the BC shall refuse the request and name the accepted values.
- R3.7 — The BC shall answer at most the requested page size, capped, with a
  cursor for the next page or none; where relevance paging has reached its
  depth cap, the BC shall mark the answer as truncated rather than end the
  paging without notice.
- R3.8 — The BC shall include in each result its company, whether its text is
  damaged, and, for a row whose text came from an archive capture, the
  capture's viewer link and timestamp and — only when the capture is of another
  page — that page and its kind, exactly one of two: a mirror copy of the same
  file, or a listing; the BC shall never include the raw origin address.

### R4: Show a release
- R4.1 — When a release is requested by id, the BC shall answer it in full: its
  text, its markup where stored, whether its text is damaged, its company and
  tag, the same capture facts a result carries, and its previous and next
  release within the same source.
- R4.2 — If the id names no release, then the BC shall answer "not found"; if
  it is not a number, then the BC shall refuse the request.

### R5: Sources, companies, quality
- R5.1 — The BC shall list every source tag with its row count, first and last
  date and per-gap counts, biggest first.
- R5.2 — The BC shall list companies as those rows rolled up, each carrying its
  own sources and its dates widened across them, biggest first.
- R5.3 — The BC shall answer the corpus-wide counters by name — total, teaser,
  stub, with a capture, with a platform id only, short, empty, undated,
  encoding-damaged, still plain — in one answer.

### R6: The page's structure
- R6.1 — The BC shall render the page with one main region, a skip link first
  in tab order, and headings in order with no level skipped.
- R6.2 — The BC shall label every form control and every navigation region,
  and announce a change to the status line without interrupting the reader.
- R6.3 — When a result is opened, the BC shall move focus to the view heading
  without a visible focus ring.
- R6.4 — The BC shall render the audit as a table with a caption, column
  headers and row headers.

### R7: Companies are the primary axis
- R7.1 — When the page is loaded, the BC shall show the company panel: companies
  alphabetically after "all", as a flat list, without year lines.
- R7.2 — While the panel shows sources, the BC shall group the tags under
  company labels in alphabetical order, order them inside a group oldest first
  by first date with undated last, and show each source with its year range.
- R7.3 — When a company or source in the panel is activated by a primary click
  with no modifier key, the BC shall make it the only selection, or clear the
  selection if it was the only selected entry.
- R7.4 — While at least one company is selected, the BC shall narrow the
  results and the status line to it; to the union of several companies; and to
  an empty list for a company crossed with a source outside it.
- R7.5 — The BC shall link each release to its company and its tag, count the
  audit per company by default and per source on request, and link each audit
  cell to the matching list.
- R7.6 — When a company or source in the panel is activated with Ctrl or Cmd
  held, the BC shall add it to the selection, or drop it if it was already
  selected, in the same tab.
- R7.7 — When the panel is switched between companies and sources, the BC
  shall clear the selection of both.
- R7.8 — The BC shall mark every selected entry in the panel as current and
  show a hint naming both gestures.
- R7.9 — While the panel shows sources, the BC shall expose each group to
  assistive technology as a nested list named after its company, add no heading
  to the panel, and distinguish the group's label visually as the heading of the
  tags under it, neither smaller nor paler than they are.

### R8: The release body
- R8.1 — Where a release carries markup, the BC shall render it as elements,
  sanitized: no script, style, class, inline style, event handler or javascript
  link survives.
- R8.2 — Where a release carries only text, the BC shall render it
  preformatted, with its layout intact and no horizontal scroll.
- R8.3 — The BC shall build an image from its stored address, load it lazily
  without a referrer, replace it with a caption naming the address when it fails
  to load, and give a table its own scroll box.
- R8.4 — If a release has no title, then the BC shall render a placeholder in
  the heading rather than an empty heading.
- R8.5 — The BC shall show in the audit the number of rows still rendered as
  plain text, linked to those rows.

### R9: Where the text came from
- R9.1 — Where a release's text was read off a listing capture, the BC shall
  badge it as from a listing, link to that capture and name the capture's page.
- R9.2 — Where the capture is of the release's own page, the BC shall show
  the link with no annotation.
- R9.3 — Where the bytes came from the same file on a sibling domain, the BC
  shall badge the release as a copy from another domain and label the link with
  the capture's own timestamp.
- R9.4 — Where a release has no recorded capture, the BC shall show no archive
  link, whatever its reference looks like.
- R9.5 — The BC shall derive the teaser badge from the row's grade, and make
  a publisher's source selectable like a company's.

### R10: Search on the page
- R10.1 — When a text search is submitted, the BC shall show the results with
  the match marked, and state in the status line when the query was searched as
  a phrase.
- R10.2 — When the next batch of results is requested, the BC shall append it
  to the list.
- R10.3 — If a response arrives for a route that a newer route has superseded,
  then the BC shall discard it: it shall not repaint the current view, append
  anything to it, or leave the list marked busy.
- R10.4 — The BC shall show the encoding badge on exactly the releases the
  answer marks as damaged (R3.8, R4.1), and shall run no detection on the page.

### R11: Fit and preferences
- R11.1 — While the viewport is phone-wide, the BC shall render the page in a
  single column with no horizontal scroll; while it is desktop-wide, as a panel
  column beside the content.
- R11.2 — While the panel is taller than the viewport, the BC shall make the
  panel its own scroll region bounded by the viewport, so that a wheel over it
  scrolls the panel and not the document; while the panel fits the viewport,
  the BC shall neither intercept the wheel event nor stop scroll chaining, so
  that a wheel over it scrolls the document.
- R11.3 — Where the visitor prefers a dark scheme or reduced motion, the BC
  shall follow the preference.

## Decisions
- D1 — Two oracles for one component: R1–R5 are held by
  `tests/browser/test_http.py`; R6–R11 by `checks.md`, the `web-static` skill's
  manifest, each check carrying the id it tests. _(why: the page is boundary,
  and a browser is the only thing that can contradict it; rejected: a spec of
  the API alone with the page out of scope, turning the checks into statements,
  and a second subject for the page — EARS names one system, and the BC is one)_
- D2 — A requirement id is the first word of a test's docstring, or follows the
  check's label in `checks.md`. _(why: names stay descriptive and grep finds the
  id; rejected: ids in test method names)_
- D3 — No authentication. _(why: the listener is local-only, so there is no
  remote caller to authenticate; rejected: a token in the URL)_
- D4 — One database connection per request, opened read-only and closed before
  the response is done. _(why: a browser must not write, and a connection held
  past a request was a descriptor nothing closed, the server joining none of
  its threads; rejected: one connection for the server's lifetime)_
- D5 — The three static files are served by name from a fixed list. _(why: an
  allowlist has no way to express traversal; rejected: serving a directory
  behind a normalised path)_
- D6 — An invalid full-text expression is searched as a literal phrase. _(why:
  a hyphenated product name is the common query here; rejected: an error
  answer, an empty page)_
- D7 — An unknown flag, company, order or cursor is refused. _(why: a dropped
  typo would report the whole corpus as the filtered slice, the one wrong
  answer an audit view must not give; rejected: ignoring the unknown value)_
- D8 — A source group in the panel is a named nested list, not a heading.
  _(why: the panel already carries the page's one panel heading, and a heading
  per company would clutter a screen reader's outline of a navigation region;
  the label's rank is visual only; rejected: a heading per group)_

## Out of scope
- Writing anything, ever: the browser reads. The schema, the index and every
  write are `release`'s.
- The queries themselves: `release/control/query.py` owns the SQL. This BC
  defines the payloads.
- Which source belongs to which company: `taxonomy`'s table.
- What a capture address means: `fetcher/control/address.py` and
  `provenance/control/resolution.py`. This BC carries their answers.
- The search CLI, `pressroom-search`: the other reader, `release`'s boundary.
- Remote access and authentication: there is no remote listener.
"""
