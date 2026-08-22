# Checks

Site-specific checks for the browser served by `serve.py` (http://127.0.0.1:8765),
in the format the `web-static` skill executes: one labelled line, one
observation a browser can contradict. Labels are stable — retire, never reuse.

Run them against a plain `python3 serve.py` instance, in the same pass as the
skill's standard checks (console, accessibility snapshot, 375/1280 resize,
`emulate colorScheme: dark`, `lighthouse_audit`).

## Deviations, decided rather than discovered

- [no-js] **web-static's no-JavaScript constraint is knowingly unmet.** The page
  loads `/static/app.js`: hash routing, three views, results rendered from a
  JSON API. By the skill's own routing rule that is `/web-components` territory.
  Everything else in web-static/web-conventions applies and is checked below, so
  a run can be "all checks pass" but never "web-static green".
- [no-375-firefox] Firefox headless will not go below a 500 px window, so the
  375 px checks are only meaningful under Chrome DevTools MCP `resize_page`. The
  substitute run reports them as not runnable rather than as passes.

## Structure and accessibility

- [main-once] `/` at 1280px: snapshot contains exactly one `main`
- [skip-link] `/` at 1280px: first tabbable element is the link "Przejdź do treści", target `#content`
- [headings] `/` at 1280px: heading order is h1 "pressroom", h2 view heading, h3 per result — no level skipped. On `#r/<id>` there is no h3 per result; a body's own headings start at h3, below the h2 view heading
- [labels] `/` at 1280px: every form control has an accessible name — "Szukana treść", "Od", "Do", "Sortuj", and the five quality checkboxes
- [nav-labels] `/` at 1280px: each `nav` has its own accessible name ("Firmy i źródła"; on `#r/<id>` also "Sąsiednie komunikaty tego źródła")
- [live-status] `/#q=radium` at 1280px: the status paragraph has `role="status"` and `aria-live="polite"`
- [focus-move] `/` then click a result: focus lands on the view heading, and no focus ring is painted on it
- [table-semantics] `/#audit` at 1280px: the audit table has a `caption`, `th[scope=col]` headers, and `th[scope=row]` per company

## Companies as the primary axis

- [firms-default] `/` at 1280px: the panel heading reads "6 firm" and lists, after "wszystkie", alphabetically: AMD, Creative, Intel, Midiman / M-Audio, Sound on Sound, TerraTec (the API sorts by size; the panel re-sorts by name)
- [sources-grouped] `/#view=sources` at 1280px: after "wszystkie" the tags are grouped under six company labels in alphabetical order (AMD, Creative, Intel, Midiman / M-Audio, Sound on Sound, TerraTec), and each group is a nested `ul` carrying that company's `aria-label`. No `h3`/`h4` is added to the panel — the grouping is a list, not an outline
- [sources-chrono] `/#view=sources` at 1280px: within a group the tags run oldest-first by their `first` date — TerraTec reads `terratec_early` 1996–1997, `terratec_de`/`terratec` 1998–2003, `terratec_pressde` 2001–2007, `terratec_new_*` 2007–2011. `midiman_net`, whose one row has no date, sorts last inside Midiman / M-Audio rather than first
- [firms-switch] `/` at 1280px: the panel switch has "firmy" with `aria-current="true"` and "źródła" without
- [sources-switch] `/#view=sources` at 1280px: the panel heading reads "25 źródeł (tagi per domenę)"; "źródła" carries `aria-current="true"`
- [source-years] `/#view=sources` at 1280px: every source with a dated row shows its year range on its own line under the tag — `amd` "2007–2026", `midiman_com` just "1999" (one year). `wszystkie` shows none, and neither does `midiman_net`, whose single row has no date. The full ISO span stays in the `title`
- [group-label-rank] `/#view=sources` at 1280px: a company label outranks the tags under it — 12px uppercase, `font-weight: 700`, full `--fg` (not `--muted`, which the year lines already use), and a `--group-bg` band behind it. It must not be smaller-and-paler than the 13.6px tag names, which reads as a caption on the item above instead of a heading over the ones below
- [group-band] `/#view=sources` in both themes: the band colour is `--group-bg` and is **not** `--surface`, which is the item hover colour — a label painted with it would read as a hovered row. Label-on-band contrast stays above AA (measured 14.6:1 light, 12.7:1 dark)
- [company-flat] `/` at 1280px: the company panel is a flat list — no nesting, no group labels
- [company-no-years] `/` at 1280px: the company panel shows no year lines — the range is a per-source debugging fact, and a company spanning 1996–2026 says nothing useful
- [switch-clears] `/#company=terratec` then follow "źródła": the resulting hash carries neither `company=` nor `source=`
- [firm-filter] `/#company=terratec&order=date` at 1280px: heading is "TerraTec", status contains "firmy: TerraTec", and every result's source starts with `terratec`
- [firm-toggle] `/#company=intel,amd&order=date` at 1280px: results contain both "Intel" and "AMD" and no other company
- [pick-single] `/#company=intel,amd&order=date`: plain click on "AMD" in the panel leads to `#company=amd…` alone — one click, one firm
- [pick-deselect] `/#company=intel&order=date`: plain click on "Intel", the only picked firm, clears the filter (hash carries no `company=`)
- [pick-multi] `/#company=intel&order=date`: Ctrl-click (or ⌘-click) on "AMD" leads to `#company=intel,amd…` in the same tab — the default open-in-new-tab is suppressed on panel items only
- [pick-hint] `/` at 1280px: the panel shows the hint "Klik wybiera jedno · Ctrl/⌘ + klik — kilka" under the firmy/źródła switch
- [intersection] `/#company=amd&source=intel` at 1280px: zero results (the filter is impossible, not ignored)
- [detail-company] `/#r/5021` at 1280px: the article shows "Midiman / M-Audio" linking to `#company=maudio…` and the tag `midiman_com_pressdb` linking to `#source=midiman_com_pressdb…`
- [audit-firms] `/#audit` at 1280px: the default table has 6 data rows and the caption "Braki per firma…"
- [audit-sources] `/#audit&view=sources` at 1280px: the table has 25 data rows and the caption "Braki per źródło…"
- [audit-drill] `/#audit`: the TerraTec "teasery" cell links to a list whose status contains both "firmy: TerraTec" and "filtry: teasery"

## Release body

The body is stored twice: `body` (plain text, what FTS indexes) and
`body_html` (the subset `richtext.py` emits). A row whose `body_html` is NULL
predates that change and falls back to preformatted text — both paths have to
keep working while the re-scrape runs.

- [body-rich] `/#r/4967` at 1280px: the body renders real elements — a `ul` with `li` children and several `p` — not one text block
- [body-plain] `/#r/6267` at 1280px: this row has no `body_html`; its body is a single `p.body--text`, the computed `white-space` is `pre-wrap`, and there is no `.body--rich`. Fixture moved off #3573 on 2026-08-22 — that row was the flat GlobeNewswire release this work started from, and it has since been recovered, so it no longer exercises the fallback. Any `midiman_net_media_news` row will do; those teasers are confirmed unrecoverable and will stay plain
- [body-sanitized] `/#r/4967`: inside `.body--rich` there is no `script`, no `style`, no element carrying a `class`, `style` or `on*` attribute, and no `a[href^="javascript:"]`
- [body-img] `/#r/5009` at 1280px: the `img` is built from the stored `src` verbatim, carries `loading="lazy"` and `referrerpolicy="no-referrer"`, and — the source site being dead — is replaced on load failure by a `.img-missing` caption naming the address. An image declaring 1–2 px in either dimension is removed instead of captioned; none currently survive pruning, so that branch is not observable in the corpus
- [body-table] `/#r/5010` at 1280px: the two-column photo table inside `.body--rich` scrolls inside its own box — the document itself has no horizontal scroll
- [audit-plain] `/#audit` at 1280px: a "bez formatowania" card and a "bez form." column are present, and the column's numbers link to `flags=plain`

- [publisher-clickable] `/#view=sources` at 1280px: clicking `soundonsound` in the panel loads results, and `/api/search?company=soundonsound` answers **200, not 400**. An unmapped source lands in the `inne` bucket whose slug is not a `COMPANIES` key, and `serve._sources()` rejects it — this check exists because that is the failure mode
- [publisher-no-wayback] `/#r/<a soundonsound id>` at 1280px: the meta line shows `detail_id: <digits>` and **no** capture link — a live source's platform id must never be mistaken for a 14-digit Wayback timestamp

## Search behaviour

- [fts-basic] `/#q=Radium&company=maudio` at 1280px: results appear, each with a `mark` element inside its excerpt
- [fts-hyphen] `/#q=M-Audio` at 1280px: results appear and the status says the query was treated as a phrase — no error, no empty page
- [paging] `/#order=date` at 1280px: pressing "Doładuj następne" grows the result list from 50 to 100 items
- [badge-agrees] `/#flags=mojibake&order=date` at 1280px: every row returned shows a "kodowanie" badge, and the count equals what `encoding.C1_RE`/`MOJIBAKE_RE` finds over the same bodies — the audit SQL and the repair must not disagree. After the 2026-08-21 re-repair this is one row, `midiman_net_pressdb` #4978, whose three 0x81 bytes cp1252 cannot decode
- [bad-flag] request `/api/search?flags=teasr`: HTTP 400 naming the valid flags (five, including `plain`)
- [bad-company] request `/api/search?company=nokia`: HTTP 400 naming the valid companies

## Responsive and preferences

- [responsive-375] `/` at 375×667: `.layout` is a single column, no horizontal document scroll
- [responsive-1280] `/` at 1280×800: `.layout` has a 15rem panel column plus the content column
- [dark] `/` with `colorScheme: dark`: body background is the dark token (rgb(23, 22, 26)), text the light one
- [reduced-motion] stylesheet contains a `prefers-reduced-motion: reduce` block neutralising animations and transitions

## Read-only guarantee

- [read-only] `python3 -c "import db; db.connect_ro().execute('DELETE FROM releases WHERE id=-1')"` raises `sqlite3.OperationalError: attempt to write a readonly database`
- [static-whitelist] request `/static/../db.py` and `/static/%2e%2e/db.py`: both 404, no file contents
