# Checks

Site-specific checks for the browser served by `pressroom-serve` (http://127.0.0.1:8765),
in the format the `web-static` skill executes: one labelled line, one
observation a browser can contradict. Labels are stable — retire, never reuse.
The id after the label is the requirement the check holds, in the browser's
spec `pressroom/browser/__init__.py` (its D2); `tests/browser/test_spec_trace.py`
keeps the two in agreement, so a new check names its statement and a new
statement under R6–R11 gets a check.

Run them against a plain `pressroom-serve` instance, in the same pass as the
skill's standard checks (console, accessibility snapshot, 375/1280 resize,
`emulate colorScheme: dark`, `lighthouse_audit`).

## Deviations, decided rather than discovered

- [no-js] **web-static's no-JavaScript constraint is knowingly unmet.** The page
  loads `/static/app.js`: hash routing, three views, results rendered from a
  JSON API. By the skill's own routing rule that puts the page under `/web-components`.
  Everything else in web-static/web-conventions applies and is checked below, so
  a run can be "all checks pass" but never "web-static green".
- [no-375-firefox] Firefox headless will not go below a 500 px window, so the
  375 px checks are only meaningful under Chrome DevTools MCP `resize_page`. The
  substitute run reports them as not runnable rather than as passes.

## Structure and accessibility

- [main-once] R6.1 — `/` at 1280px: snapshot contains exactly one `main`
- [skip-link] R6.1 — `/` at 1280px: first tabbable element is the link "Przejdź do treści", target `#content`
- [headings] R6.1 — `/` at 1280px: heading order is h1 "pressroom", h2 view heading, h3 per result — no level skipped. On `#r/<id>` there is no h3 per result; a body's own headings start at h3, below the h2 view heading
- [labels] R6.2 — `/` at 1280px: every form control has an accessible name — "Szukana treść", "Od", "Do", "Sortuj", and the five quality checkboxes
- [nav-labels] R6.2 — `/` at 1280px: each `nav` has its own accessible name ("Firmy i źródła"; on `#r/<id>` also "Sąsiednie komunikaty tego źródła")
- [live-status] R6.2 — `/#q=radium` at 1280px: the status paragraph has `role="status"` and `aria-live="polite"`
- [focus-move] R6.3 — `/` then click a result: focus moves to the view heading, and no focus ring is painted on it
- [table-semantics] R6.4 — `/#audit` at 1280px: the audit table has a `caption`, `th[scope=col]` headers, and `th[scope=row]` per company

## Companies as the primary axis

- [firms-default] R7.1 — `/` at 1280px: the panel heading reads "6 firm" and lists, after "wszystkie", alphabetically: AMD, Creative, Intel, Midiman / M-Audio, Sound on Sound, TerraTec (the API sorts by size; the panel re-sorts by name)
- [sources-grouped] R7.2, R7.9 — `/#view=sources` at 1280px: after "wszystkie" the tags are grouped under six company labels in alphabetical order (AMD, Creative, Intel, Midiman / M-Audio, Sound on Sound, TerraTec), and each group is a nested `ul` carrying that company's `aria-label`. No `h3`/`h4` is added to the panel — the grouping is a list, not an outline
- [sources-chrono] R7.2 — `/#view=sources` at 1280px: within a group the tags run oldest-first by their `first` date — TerraTec reads `terratec_early` 1996–1997, `terratec` 1998–2003, `terratec_pressde` 2001–2007, `terratec_pressen` 2003–2007, `terratec_new_*` 2007–2011. `midiman_net`, whose one row has no date, sorts last inside Midiman / M-Audio rather than first
- [firms-switch] R7.1 — `/` at 1280px: the panel switch has "firmy" with `aria-current="true"` and "źródła" without
- [sources-switch] R7.2 — `/#view=sources` at 1280px: the panel heading reads "24 źródeł (tagi per domenę)"; "źródła" carries `aria-current="true"`
- [source-years] R7.2 — `/#view=sources` at 1280px: every source with a dated row shows its year range on its own line under the tag — `amd` "2007–2026", `midiman_com` just "1999" (one year). `wszystkie` shows none, and neither does `midiman_net`, whose single row has no date. The full ISO span stays in the `title`
- [group-label-rank] R7.9 — `/#view=sources` at 1280px: a company label outranks the tags under it — 12px uppercase, `font-weight: 700`, full `--fg` (not `--muted`, which the year lines already use), and a `--group-bg` band behind it. It must not be smaller-and-paler than the 13.6px tag names, which reads as a caption on the item above instead of a heading over the ones below
- [group-band] R7.9 — `/#view=sources` in both themes: the band colour is `--group-bg` and is **not** `--surface`, which is the item hover colour — a label painted with it would read as a hovered row. Label-on-band contrast stays above AA (measured 14.6:1 light, 12.7:1 dark)
- [company-flat] R7.1 — `/` at 1280px: the company panel is a flat list — no nesting, no group labels
- [company-no-years] R7.1 — `/` at 1280px: the company panel shows no year lines — the range is a per-source debugging fact, and a company spanning 1996–2026 says nothing useful
- [switch-clears] R7.7 — `/#company=terratec` then follow "źródła": the resulting hash carries neither `company=` nor `source=`
- [firm-filter] R7.4 — `/#company=terratec&order=date` at 1280px: heading is "TerraTec", status contains "firmy: TerraTec", and every result's source starts with `terratec`
- [firm-toggle] R7.4 — `/#company=intel,amd&order=date` at 1280px: results contain both "Intel" and "AMD" and no other company
- [pick-single] R7.3 — `/#company=intel,amd&order=date`: plain click on "AMD" in the panel leads to `#company=amd…` alone — one click, one firm
- [pick-deselect] R7.3 — `/#company=intel&order=date`: plain click on "Intel", the only picked firm, clears the filter (hash carries no `company=`)
- [pick-multi] R7.6 — `/#company=intel&order=date`: Ctrl-click (or ⌘-click) on "AMD" leads to `#company=intel,amd…` in the same tab — the default open-in-new-tab is suppressed on panel items only
- [pick-hint] R7.8 — `/` at 1280px: the panel shows the hint "Klik wybiera jedno · Ctrl/⌘ + klik — kilka" under the firmy/źródła switch
- [pick-current] R7.8 — `/#company=intel,amd&order=date` at 1280px: the panel items "Intel" and "AMD" both carry `aria-current="true"` and no other item does; on `/#view=sources&source=terratec_new_de` the `terratec_new_de` item alone carries it
- [intersection] R7.4 — `/#company=amd&source=intel` at 1280px: zero results (the filter is impossible, not ignored)
- [detail-company] R7.5 — `/#r/5021` at 1280px: the article shows "Midiman / M-Audio" linking to `#company=maudio…` and the tag `midiman_com_pressdb` linking to `#source=midiman_com_pressdb…`
- [audit-firms] R7.5 — `/#audit` at 1280px: the default table has 6 data rows and the caption "Braki per firma…"
- [audit-sources] R7.5 — `/#audit&view=sources` at 1280px: the table has 24 data rows and the caption "Braki per źródło…"
- [audit-drill] R7.5 — `/#audit`: the TerraTec "teasery" cell links to a list whose status contains both "firmy: TerraTec" and "filtry: teasery"

## Release body

The body is stored twice: `body` (plain text, what FTS indexes) and
`body_html` (the subset `text/control/richtext.py` emits). A row whose `body_html` is NULL
predates that change and falls back to preformatted text — both paths have to
keep working while the re-scrape runs.

- [body-rich] R8.1 — `/#r/4967` at 1280px: the body renders real elements — a `ul` with `li` children and several `p` — not one text block
- [body-plain] R8.2 — `/#r/6267` at 1280px: this row has no `body_html`; its body is a single `p.body--text`, the computed `white-space` is `pre-wrap`, and there is no `.body--rich`. Any `midiman_net_media_news` row will do; those teasers are confirmed unrecoverable and will stay plain
- [body-sanitized] R8.1 — `/#r/4967`: inside `.body--rich` there is no `script`, no `style`, no element carrying a `class`, `style` or `on*` attribute, and no `a[href^="javascript:"]`
- [body-img] R8.3 — `/#r/5009` at 1280px: the `img` is built from the stored `src` verbatim, carries `loading="lazy"` and `referrerpolicy="no-referrer"`, and — the source site being dead — is replaced on load failure by a `.img-missing` caption naming the address. An image declaring 1–2 px in either dimension is removed instead of captioned; none currently survive pruning, so that branch is not observable in the corpus
- [body-table] R8.3 — `/#r/5010` at 1280px: the two-column photo table inside `.body--rich` scrolls inside its own box — the document itself has no horizontal scroll
- [body-layout] R8.2 — `/#r/5519` at 1280px and 375px: this **.doc**-derived row renders through `p.body--text` with `white-space: pre-wrap` and its **layout is visible** — centred headline, indented subhead, columns intact — and the document has no horizontal scroll at either width. Word files keep it — `antiword`'s text is their only output
- [body-richtext] R8.1 — `/#r/4986` at 1280px: a **PDF**-derived row renders as `.body--rich` with real `p` elements, not one preformatted block, and its paragraphs are flowing text rather than the PDF's line breaks. The pre-wrap fallback stays for .doc. `/#r/5003` is the same for a list: an `ol` with 6 items where the PDF wrote `1)`..`6)`, and `/#r/5572` for a nested one, a `ul` whose items carry their own `ul`
- [audit-plain] R8.5 — `/#audit` at 1280px: a "bez formatowania" card and a "bez form." column are present, and the column's numbers link to `flags=plain`
- [untitled] R8.4 — `/#r/3683` at 1280px: the row has no `title`, so the h2 reads "(bez tytułu)" rather than being empty — a heading with no text is what the corpus is left with on the 17 captures that carry no headline at all - `catch_up._fill_title` writes a title only over an empty one, and these captures contain none. The same fallback covers the result list and both neighbour links

- [badge-listing] R9.1 — `/#r/4414` and `/#source=terratec_new_de&order=date` at 1280px: the row carries a `span.badge.listing` reading "z listingu", with the capture's page url in its `title`, **next to** the size badge rather than instead of it. It is not the `teaser` badge: measured over the 125 terratec_new releases whose listing *and* article versions are both cached, the listing text is a median 0.98 of the article's, so "teaser" would be wrong for most of them. Contrast measured 14.0:1 light, 12.5:1 dark, and `--listing-bg` is cool where the three grading badges are warm
- [capture-of-listing] R9.1 — `/#r/4414` at 1280px: the capture link points at `web/20111011173713/…/presse.html` — the *listing* capture the text was read out of, recorded in `body_origin` — and is followed by the plain text "(capture strony: presse.html)", with the full address in the link's `title`. Built from the row's own url it would be `…/September_2011_-_DAB_…_151650.html`, of which archive.org has no capture at all; that broken link is what this row was reported for
- [capture-of-own-page] R9.2 — `/#r/4376` at 1280px: the `body_origin` entry is of the row's **own** url, so the capture link is `web/20120104073622/<that url>` and carries no "(capture strony: …)" note and no "z listingu" badge. The common case must stay unannotated — 1605 of the 1763 rows with a capture are this case, and the first version of the annotation marked all of them
- [badge-mirror] R9.3 — `/#r/4984` at 1280px: this row's bytes came from the same file on a **sibling domain**, so the badge reads "z innej domeny" (not "z listingu"), the note after the link reads "(kopia z: www.m-audio.com)", and the link label is the **capture's own** timestamp — `capture 20030421210545`, which is what the href opens, not the row's `detail_id` of 20030212170800. Both halves were wrong once: a mirrored file was labelled a listing, and the label named a capture the link does not go to
- [no-capture-no-link] R9.4 — `/#r/<a soundonsound or intel id>` at 1280px: no `body_origin` entry, so no capture link at all — the meta line ends at `detail_id: <the platform's own id>`. The link is built from the recorded capture only; nothing infers one from the shape of a detail_id
- [badge-from-grade] R9.5 — `/#flags=teaser&order=date` at 1280px: every result carries the `teaser` badge, which now reads `row.grade`. Those rows have `detail_id: null` — the sentinel moved to its own column, so a badge keyed on `detail_id` would have silently gone blank on all 554 of them

- [publisher-clickable] R9.5 — `/#view=sources` at 1280px: clicking `soundonsound` in the panel loads results, and `/api/search?company=soundonsound` answers **200, not 400**. An unmapped source is put in the `inne` bucket whose slug is not a `COMPANIES` key, and `http._sources()` rejects it — this check exists because that is the failure mode
- [publisher-no-wayback] R9.4 — `/#r/<a soundonsound id>` at 1280px: the meta line shows `detail_id: <digits>` and **no** capture link — a live source's platform id must never be mistaken for a 14-digit Wayback timestamp

## Search behaviour

- [fts-basic] R10.1 — `/#q=Radium&company=maudio` at 1280px: results appear, each with a `mark` element inside its excerpt
- [fts-hyphen] R10.1 — `/#q=M-Audio` at 1280px: results appear and the status says the query was treated as a phrase — no error, no empty page
- [paging] R10.2 — `/#order=date` at 1280px: pressing "Doładuj następne" grows the result list from 50 to 100 items
- [route-supersedes] R10.3 — `#audit` and then, ~120ms later, `#r/4414`, both driven
  from one `evaluate_script`: three seconds on, the article is still there —
  `article.detail`, the heading is the release's title, and there is no
  `table.audit`. The abandoned request shows as `net::ERR_ABORTED` in the
  network log and the console stays clean, an aborted fetch being no unhandled
  rejection. The reported order too (`#source=terratec_new_de&order=date` then
  `#audit`): the audit stays and no `ol.hits` appears. An abandoned list load
  must not leave `#list` carrying `aria-busy="true"`, and an abandoned
  "Doładuj następne" must not append to the next view — while unraced paging
  still grows the list, so `[paging]` above has to keep passing. Which side is
  slow is a local fact, not a constant: reload with cache ignored first, or the
  page keeps the previous `app.js`
- [badge-agrees] R10.4 — `/#flags=mojibake&order=date` at 1280px: every row returned shows a "kodowanie" badge, and the count equals what `decoding.C1_RE`/`MOJIBAKE_RE` finds over the same bodies — the audit SQL and the repair must not disagree. Currently this is one row, `midiman_net_pressdb` #4978, whose three 0x81 bytes cp1252 cannot decode
- [bad-flag] R3.6 — request `/api/search?flags=teasr`: HTTP 400 naming the valid flags (five, including `plain`)
- [bad-company] R3.6 — request `/api/search?company=nokia`: HTTP 400 naming the valid companies

## Responsive and preferences

- [responsive-375] R11.1 — `/` at 375×667: `.layout` is a single column, no horizontal document scroll
- [responsive-1280] R11.1 — `/` at 1280×800: `.layout` has a panel column of `15rem + --gap` (252px) plus the content column — see `[panel-gutter]` for why it is not a flat 15rem
- [panel-scroll] R11.2 — `/#view=sources` at 1280×800: `#panel` is its own scroll container — `scrollHeight > clientHeight`, its bottom edge sits inside the viewport, and a wheel over it moves `panel.scrollTop` while `document.scrollingElement.scrollTop` stays put. `sticky` alone was not: an element taller than the viewport pins only once its bottom edge arrives, so until then it scrolls with the page and the wheel scrolled the results instead. Offset and height both come from `--topbar-h`, measured in `app.js`
- [panel-chains] R11.2 — `/` at 1280×800: the companies list fits, and a wheel over the panel still scrolls the document. `overscroll-behavior: contain` is absent — measured, Chrome ends the chain even at a scroll container with zero range, which froze the page for any wheel over the short panel
- [panel-gutter] R11.2 — `/#view=sources&source=midiman_net_media_news` at 1280×800: the picked item's highlight ends 12px short of the panel's right edge, so the scrollbar runs beside it rather than across it. The column is `calc(15rem + --gap)`, so the items keep the 15rem the tag names were fitted to and no tag wraps to a third line. Not `scrollbar-gutter: stable`: this scrollbar is an overlay (`offsetWidth == clientWidth`), which paints over content and which a gutter is defined not to reserve for
- [dark] R11.3 — `/` with `colorScheme: dark`: body background is the dark token (rgb(23, 22, 26)), text the light one
- [reduced-motion] R11.3 — stylesheet contains a `prefers-reduced-motion: reduce` block neutralising animations and transitions

## Read-only guarantee

- [read-only] R1.4 — `.venv/bin/python -c "from pressroom.database.control.connection import connect_ro; connect_ro().execute('DELETE FROM releases WHERE id=-1')"` raises `sqlite3.OperationalError: attempt to write a readonly database`
- [static-whitelist] R2.2 — request `/static/../http.py` and `/static/%2e%2e/http.py`: both 404, no file contents
