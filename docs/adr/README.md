# Decision records

The record. Every file here holds decisions this corpus's failures established,
together with the measurement behind each one — the encoding repair's cp1258,
the 122 articles a listing strategy overwrote, the four gate attempts before
`wordchars`.

**Six places, one job each.** Prose here goes out of date the same way a
comment does, so it matters which of them a sentence belongs in:

| where | what belongs there |
|---|---|
| `README.md`, `## Conventions` | the **rule** a person follows, in the imperative, plus one clause of why and a pointer to here. A rule about one component leaves it the day it becomes an `Rn.m` in that component's spec |
| `pressroom/__init__.py`, the system doc | what **spans components**: the wiring (who may import whom), the system invariants `Sn` — each held by a test — the vocabulary, and the decisions `Dn` with their rejected alternatives. The reasoning behind each is here |
| a component's `__init__.py` docstring | the component's **spec** — its boundary operations, EARS requirements under stable `Rn.m` ids, its own decisions, what is out of scope — in the `/sbce` form |
| `docs/adr/*.md` | the **reasoning and the evidence** — what was tried, what it cost, which measurement decided it. Read the file for an area *before* changing anything in that area |
| a module docstring | what markup one **scraper** actually meets. That record stays in the parser's docstring, never in these files |
| [`../layout.md`](../layout.md) | the **map** — which file inside a component owns which job. A lookup, not a rule and not a reason; `tests/test_layout_map.py` checks it against the tree |

`CLAUDE.md` is none of the six: it is the agent's file — this machine's quirks
and the reading order — and it imports the README and the system doc so that an
agent has both in context.

**A rule with no record here is fine; a record with no rule is a problem.** If a
file below says something a future reader must *do* and neither `README.md` nor
the owning spec says it, the rule is invisible in practice — move it up.

**Topic files, not numbered ADRs.** Each of these is a cluster of decisions about
one area rather than a single dated choice, and they get amended as measurements
arrive. Filenames are the stable identifier — rename only when the area itself
is renamed, and never reuse a name for a different area.

**A measurement goes in a test or a verify pass, not into prose** —
[numbers.md](numbers.md), and it applies to these files too.

## Index

| file | area |
|---|---|
| [layout-and-naming.md](layout-and-naming.md) | why the tree is shaped this way, the three sizes and the four roles and why the tree is named for them, the retired `backfill_`/`repair_` prefixes, what BCE says here and why, where a component's spec lives, the dependency direction |
| [grade-and-detail-id.md](grade-and-detail-id.md) | why a grade and a reference are two columns |
| [sources-and-tags.md](sources-and-tags.md) | a tag is a CMS generation, not a domain; intended duplication; `MIRROR_DOMAINS`; the two axes |
| [captures.md](captures.md) | why every fetched byte is in the database, how archive.org is asked, and which capture of a url is the right one |
| [encoding.md](encoding.md) | `r.text` never, the two correct decodes, the repair in the write path |
| [text-and-markup.md](text-and-markup.md) | `body` + `body_html`, the richtext decisions, how a title is found |
| [provenance.md](provenance.md) | `body_origin`, the three URL shapes, what the two removed columns cost |
| [catch-up.md](catch-up.md) | a run's two phases: why the six strategies are not interchangeable, the three rules inside each, the 122 articles, and what the hand-written copies of phase 1 drifted on |
| [gates.md](gates.md) | `safe_to_write` and `wordchars` — what each refuses, and every named allowance |
| [attachments.md](attachments.md) | PDF gets markup, `.doc` does not, dispatch is on magic bytes |
| [browser-panel.md](browser-panel.md) | why the panel sorts, labels and scrolls the way it does |
| [odd-sources.md](odd-sources.md) | GlobeNewswire's TLS fingerprint; Sound on Sound, the one publisher |
| [working-here.md](working-here.md) | the two test tiers, what each `verify` pass sees and what it cannot check, the standing don'ts |
| [numbers.md](numbers.md) | where counts go, and the four standing decisions that are rules |

`checks.md`, at the repo root, is the seventh: it holds the rendered page's tests
— one labelled line per observation a browser can contradict, each carrying the
id of the requirement it checks in the browser's spec, `browser/__init__.py`.
Decisions *about* the page are in [browser-panel.md](browser-panel.md).
