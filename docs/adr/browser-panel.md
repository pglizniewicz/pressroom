# The browser's panel

`checks.md` owns *what* the panel does — one labelled line per observation a
browser can contradict. This file is only *why*, including three layout
decisions that each looked like a one-liner and were not.

**A source carries its year range, a company does not** — the range says which
CMS generation a tag covers, which is a per-source debugging fact; rolled up to a
company it is always the whole corpus. It sits on its own line because the
longest tag already fills the 15rem panel.

**The two lists are sorted differently, and that is the point.** The API sorts by
size, right for the audit table and wrong for a picker, so the frontend re-sorts:
companies alphabetical (you arrive knowing the name), sources grouped by company
and **chronological inside a group**, because alphabetically the tags interleave
by domain and the site's history is lost. On full ISO dates, not displayed years;
an undated source sorts last, since `""` beats 1996.

**The group label must out-rank the tags under it.** Muted grey at 0.75rem was
*smaller and paler* than the 0.85rem tags below, so it read as a caption on the
item above; full `--fg` and uppercase on a `--group-bg` band fixed it. That band
needs **its own token pair** — `--surface` is the item hover colour, and a label
painted with it reads as a hovered row. The groups are nested `<ul>`s with an
`aria-label`, not headings: the panel already owns the only `h2` there.

**Picking is a switch by default; Ctrl/Cmd-click adds or drops one.** The `href`
is always the single-select target so a panel item stays a real link; only the
modifier click is intercepted, which knowingly costs open-in-new-tab there.

**The panel is its own scroll container from 60rem up, and `sticky` alone was not
enough** — a sticky element taller than the viewport travels with the page and
has nothing to scroll of its own, so it needs `max-block-size` + `overflow-y:
auto`. **Not** `overscroll-behavior: contain`: the companies list fits, making
the panel a scroll container with zero range, and Chrome ends the chain there
anyway, so a wheel over it would move nothing. The `--gap` of
`padding-inline-end` (paid for by `calc(15rem + var(--gap))`) exists because an
overlay scrollbar paints *on top of* content and `scrollbar-gutter: stable`
reserves nothing for one; taking the strip out of the 15rem instead wrapped the
three longest tags to a third line. Both the sticky offset and the max height are
`100dvh` minus **`--topbar-h`, measured in `app.js` with a `ResizeObserver`**
rather than hardcoded, because the filter form wraps between 60rem and ~72rem.

**The frontend follows `web-static`/`web-conventions` with one deliberate
deviation: web-static forbids JavaScript and this is a JS-rendered SPA.** By that
skill's own routing rule the page belongs to `web-components`. Keep the
conventions and keep reporting the deviation instead of claiming the skill is
green — `checks.md` records it as `[no-js]`.
