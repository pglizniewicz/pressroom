# Decision records

The archaeology. Every file here holds decisions this corpus paid for, together
with the measurement that bought each one — the encoding repair's cp1258, the
122 articles a listing strategy overwrote, the four gate attempts before
`wordchars`. This is the expensive part of the project; the code is the cheap
part.

**Four places, one job each.** Prose here rots the same way a comment rots, so
it matters which of the four a sentence belongs in:

| where | what belongs there |
|---|---|
| `CLAUDE.md` | the **rule**, in the imperative, plus one clause of why and a pointer to here. It is loaded into every session, so anything that must be obeyed without being looked up lives there and nowhere else |
| `docs/adr/*.md` | the **reasoning and the evidence** — what was tried, what it cost, which measurement settled it. Read the file for an area *before* changing anything in that area |
| a module docstring | what one **source's markup** actually does. Per-source archaeology travels with its parser, never into these files |
| [`../layout.md`](../layout.md) | the **map** — which file inside a component owns which job. A lookup, not a rule and not a reason; `tests/test_layout_map.py` holds it to the tree |

**A rule with no record here is fine; a record with no rule is a warning.** If a
file below says something a future reader must *do* and `CLAUDE.md` does not say
it, the rule is invisible in practice — move it up.

**Topic files, not numbered ADRs.** Each of these is a cluster of decisions about
one area rather than a single dated choice, and they get amended as measurements
land. Filenames are the stable handle — rename only when the area itself is
renamed, and never reuse a name for a different area.

**A measurement goes in a test or a verify pass, not into prose.** See
[numbers.md](numbers.md); that rule replaced 259 lines of rotted tallies and it
applies to these files too.

## Index

| file | area |
|---|---|
| [layout-and-naming.md](layout-and-naming.md) | why the tree is shaped this way, the retired `backfill_`/`repair_` prefixes, the two BCE deviations, the dependency direction |
| [grade-and-detail-id.md](grade-and-detail-id.md) | why a verdict and a reference are two columns |
| [sources-and-tags.md](sources-and-tags.md) | a tag is a CMS generation, not a domain; intended duplication; `MIRROR_DOMAINS`; the two axes |
| [captures.md](captures.md) | why every fetched byte is in the database, and how archive.org is asked |
| [encoding.md](encoding.md) | `r.text` never, the two correct decodes, the repair in the write path |
| [text-and-markup.md](text-and-markup.md) | `body` + `body_html`, the richtext decisions, how a title is found |
| [provenance.md](provenance.md) | `body_origin`, the three URL shapes, what the two removed columns cost |
| [catch-up.md](catch-up.md) | a run's two phases: why the six strategies are not interchangeable, the three rules inside each, the 122 articles, and what the hand-written copies of phase 1 drifted on |
| [gates.md](gates.md) | `safe_to_write` and `wordchars` — what each refuses, and every named allowance |
| [attachments.md](attachments.md) | PDF gets markup, `.doc` does not, dispatch is on magic bytes |
| [browser-panel.md](browser-panel.md) | why the panel sorts, labels and scrolls the way it does |
| [odd-sources.md](odd-sources.md) | GlobeNewswire's TLS fingerprint; Sound on Sound, the one publisher |
| [working-here.md](working-here.md) | the two test tiers, what each `verify` pass sees and its blind spot, the standing don'ts |
| [numbers.md](numbers.md) | where counts live, and the four standing decisions that are rules |

`checks.md`, at the repo root, is the fifth: it owns the rendered page — one
labelled line per observation a browser can contradict. Decisions *about* the
page are in [browser-panel.md](browser-panel.md).
