#!/usr/bin/env python3
"""HTML fragment -> the two representations a release is stored as.

    body        plain text with real paragraphs, for FTS and the CLI reader
    body_html   a small, attribute-stripped HTML subset, for the browser

Never `soup.get_text(" ", strip=True)` for a body: it replaces every block
boundary with one space, so a 4000-character release comes out as an unbroken
blob, and the newlines that do survive are the source file's line wrapping
rendered verbatim by `white-space: pre-wrap`.

`to_text()` derives the text **from the cleaned HTML**, never from the source
node. That is the whole reason `extract()` exists as one call: the indexed text
and the displayed markup cannot drift apart if one is computed from the other.

Imports bs4, so nothing on a reader's path may import this - CLAUDE.md
invariant 4, held by tests/test_import_direction.py.

The tag allowlist below is mirrored in static/app.js, which rebuilds these nodes
one by one instead of trusting innerHTML. Change one and change the other;
tests/test_mirrored_rules.py fails when they disagree.

→ docs/adr/text-and-markup.md
"""

import re

from bs4 import BeautifulSoup, Comment, NavigableString

from pressroom.text.control import decoding

# Thrown away with their contents. `form` is here because several of these
# CMS templates put a search box inside the article container itself.
_DROP = {
    "script",
    "style",
    "noscript",
    "form",
    "iframe",
    "object",
    "embed",
    "applet",
    "select",
    "option",
    "textarea",
    "button",
    "map",
    "svg",
}

# Kept, but renamed. h1/h2 are pushed down because the detail view's own
# heading is the h2 (app.js setHeading), and web-conventions forbids skipping
# a level - a body that opened with h1 would sit above the page's own title.
_RENAME = {
    "b": "strong",
    "i": "em",
    "u": "em",
    "cite": "em",
    "h1": "h3",
    "h2": "h3",
    "h5": "h4",
    "h6": "h4",
}

# Everything else - div, span, font, center, tt, small, article, section,
# and any tag these 1998-era pages invented - is unwrapped: the element goes,
# its children stay.
_ALLOWED = {
    "p",
    "br",
    "hr",
    "h3",
    "h4",
    "blockquote",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "strong",
    "em",
    "sub",
    "sup",
    "a",
    "img",
}

# Per-tag attribute allowlist. Everything not listed - class, style, id, and
# every on* handler - is dropped. colspan/rowspan stay because a TerraTec spec
# sheet is a real table and collapses into nonsense without them.
_ATTRS = {
    "a": {"href"},
    # <img> is preserved as-is on purpose. These sites are dead, so most of
    # these src values resolve to nothing today - but they are the only record
    # of which image belonged where, and rewriting or dropping them would
    # throw that away for good. Only the scheme is filtered.
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}

_SAFE_SCHEMES = ("http:", "https:", "mailto:", "ftp:")

# Block-level tags that are not in the allowlist. Plain unwrapping glued
# neighbouring paragraphs together with no separator at all, so one that holds
# nothing but inline content becomes a <p> instead; one that wraps other
# blocks is unwrapped as before.
_SOURCE_BLOCKS = {
    "div",
    "center",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "main",
    "address",
    "dl",
    "dt",
    "dd",
    "pre",
    "figure",
    "figcaption",
    "fieldset",
    "legend",
    "caption",
    "nav",
    "details",
    "summary",
}

# Block-level tags, i.e. the ones that end a paragraph. Used by both the
# paragraph reconstruction below and to_text().
_BLOCKS = {
    "p",
    "h3",
    "h4",
    "blockquote",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "hr",
}

# Tag.find() will not take a set.
_BLOCK_TAGS = sorted(_BLOCKS | _SOURCE_BLOCKS)


def _safe_url(value: str) -> bool:
    """Reject javascript: and data: without rejecting the relative paths that
    make up most of this corpus ('images/prod.gif', '/press/foo.html')."""
    v = (value or "").strip().lower()
    if not v:
        return False
    if v.startswith(_SAFE_SCHEMES):
        return True
    # No scheme at all -> relative, therefore harmless.
    return ":" not in v.split("/")[0].split("?")[0].split("#")[0]


def _strip_attrs(tag) -> None:
    keep = _ATTRS.get(tag.name, set())
    for name in list(tag.attrs):
        if name not in keep:
            del tag[name]
    for name in ("href", "src"):
        if name in tag.attrs and not _safe_url(tag[name]):
            del tag[name]


def _own_cells(tr):
    return [c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]


def _demote_layout_tables(soup) -> bool:
    """Turn single-column tables into plain blocks.

    Every one of these sites laid its pages out in nested tables, so an
    article routinely arrives wrapped in <table><tr><td>…</td></tr></table>.
    Rendered with borders and cell padding that reads as a data table, which
    it is not. A table where no row has more than one cell carries no
    row/column relationship at all, so it is demoted to <div> and picked up by
    the ordinary unwrap-or-<p> pass below. A real spec sheet has 2+ columns
    and is left alone.

    Scoped per table via find_parent, so a genuine table nested inside a
    layout wrapper keeps its own structure.
    """
    changed = False
    for table in soup.find_all("table"):
        rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if rows and max(len(_own_cells(tr)) for tr in rows) > 1:
            continue
        changed = True
        parts = [table]
        parts += [
            t
            for t in table.find_all(["thead", "tbody", "tfoot"])
            if t.find_parent("table") is table
        ]
        parts += rows
        parts += [c for tr in rows for c in _own_cells(tr)]
        for tag in parts:
            tag.name = "div"
            tag.attrs = {}
    return changed


def _unwrap_disallowed(soup) -> None:
    """Bottom-up, so unwrapping a <div> inside a <div> cannot skip its parent.
    A block-level tag holding only inline content becomes a <p> rather than
    dissolving into its neighbour; everything else loses the element and keeps
    its children."""
    for tag in reversed(soup.find_all(True)):
        if tag.decomposed:
            continue
        if tag.name not in _ALLOWED:
            if tag.name in _SOURCE_BLOCKS and not tag.find(_BLOCK_TAGS):
                tag.attrs = {}
                tag.name = "p"
            else:
                tag.unwrap()
            continue
        _strip_attrs(tag)
        if tag.name == "a" and not tag.get("href"):
            tag.unwrap()


def _paragraphize(root) -> None:
    """Turn runs of inline content into real <p> elements.

    These CMSes mostly did not use <p> at all: a paragraph break was
    `<br><br>`, and plenty of pages are one long text node per paragraph
    inside a <td>. Walking the top level and flushing a buffer on every block
    boundary or double <br> recovers the structure the author intended.

    Single <br> is left alone - inside an address block or a spec line it is
    the real thing, not a paragraph break.
    """
    soup = root if isinstance(root, BeautifulSoup) else root.find_parent(BeautifulSoup)
    buf, out = [], []

    def flush():
        if not buf:
            return
        # .extract() as well as .pop(): a node dropped from the buffer is still
        # in the tree, and since the blocks around it get moved to the end,
        # a blank left behind resurfaces at the top of the fragment. That is
        # where the stray leading <br/> came from.
        while buf and _is_blank(buf[-1]):
            buf.pop().extract()
        while buf and _is_blank(buf[0]):
            buf.pop(0).extract()
        if not buf:
            return
        p = soup.new_tag("p")
        for node in buf:
            p.append(node.extract())
        buf.clear()
        _trim_edges(p)
        out.append(p)

    # Indexed over a snapshot rather than walked with find_next_sibling():
    # that method skips NavigableStrings, so "A<br>x<br>y<br>B" looked like one
    # run of three <br> and every break in the document got eaten at once.
    kids = list(root.children)
    i = 0
    while i < len(kids):
        child = kids[i]
        name = getattr(child, "name", None)
        if name in _BLOCKS:
            flush()
            out.append(child.extract())
            i += 1
            continue
        if name == "br":
            run, j = 0, i
            while j < len(kids):
                nxt = kids[j]
                nxt_name = getattr(nxt, "name", None)
                if nxt_name == "br":
                    run += 1
                elif nxt_name is not None or str(nxt).strip():
                    break
                j += 1
            if run >= 2:
                for node in kids[i:j]:
                    node.extract()
                flush()
                i = j
                continue
        buf.append(child)
        i += 1

    flush()
    for node in out:
        root.append(node)


def _trim_edges(tag) -> None:
    """Drop the whitespace that sat against the <br> we just consumed. Read
    back through .contents each time: replace_with detaches the node, so a
    cached reference to the last child goes stale after the first call."""
    if tag.contents and isinstance(tag.contents[0], NavigableString):
        tag.contents[0].replace_with(NavigableString(str(tag.contents[0]).lstrip()))
    if tag.contents and isinstance(tag.contents[-1], NavigableString):
        tag.contents[-1].replace_with(NavigableString(str(tag.contents[-1]).rstrip()))


def _is_blank(node) -> bool:
    if isinstance(node, NavigableString):
        return not str(node).strip()
    return getattr(node, "name", None) == "br"


# Table-family tags are pruned on text alone, everything else also counts an
# <img> as content. The distinction is what separates a product photo in its
# own paragraph - which must survive - from a 2003 CMS's page header, which is
# a nested table of banner and 1x1 spacer gifs with no text in it anywhere.
_LAYOUT_ONLY = {"table", "thead", "tbody", "tfoot", "tr", "th", "td"}


def _prune_empty(root) -> None:
    """Drop blocks left with nothing in them: spacer cells, <p></p> emitted by
    a WYSIWYG editor, and the image-only tables these CMSes built their page
    chrome out of.

    Outermost-first, so a header table goes in one decompose() instead of
    being dismantled cell by cell - which is also why every iteration has to
    re-check `decomposed`."""
    for tag in root.find_all(
        [
            "p",
            "li",
            "td",
            "th",
            "tr",
            "table",
            "thead",
            "tbody",
            "tfoot",
            "ul",
            "ol",
            "blockquote",
            "h3",
            "h4",
        ]
    ):
        if tag.decomposed:
            continue
        if tag.get_text(strip=True):
            continue
        if tag.name not in _LAYOUT_ONLY and tag.find("img"):
            continue
        tag.decompose()


def cut_from(soup, pattern) -> bool:
    """Drop everything from the first match of `pattern` onward. True if cut.

    The element-level equivalent of `text[:marker.start()]`, and it has to
    handle the case that broke the naive version: the marker is usually not in
    a tidy leaf element. "Infos bei:" on midiman.de sits in a bare text node
    between two <br>, so a scan over leaf *elements* found nothing and the
    contact footer survived into 37 bodies that used to have it cut.

    So: find the text node, truncate it at the marker, then remove every node
    that follows it in document order. `pattern` is a compiled regex.
    """
    node = soup.find(string=pattern)
    if node is None:
        return False
    tail = [n for n in node.next_elements if hasattr(n, "extract")]
    m = pattern.search(str(node))
    node.replace_with(NavigableString(str(node)[: m.start()]))
    for later in tail:
        if getattr(later, "decomposed", False) or later.parent is None:
            continue
        later.extract()
    return True


def densest(soup, name: str, **attrs):
    """The element of this kind carrying the most text, or None.

    Locating the article is the first half of turning a page into a body, so
    it lives next to the half that converts it. Every caller hands the result
    straight to extract().

    Why "the biggest one" rather than a CSS selector: these are 1998-2005
    table-layout pages, where the only thing distinguishing the article's table
    from the navigation's is a `width="535"` that changes between captures of the
    same site. Calibrated over every cached capture, a width selector misses
    pages that "the biggest table" gets right - a magic number that is right most
    of the time is worse than a rule. `pressroom-calibrate-containers` is how a
    selector is chosen.
    """
    best = best_len = None
    for tag in soup.find_all(name, **attrs):
        n = len(" ".join(tag.get_text(" ", strip=True).split()))
        if best is None or n > best_len:
            best, best_len = tag, n
    return best


def clean(node) -> str:
    """A parsed node (or raw HTML string) -> the stored `body_html` subset.

    Works on a copy: callers routinely keep parsing the same soup afterwards.
    """
    if node is None:
        return ""
    # The container itself is dropped, only its contents are kept: callers
    # always hand us the article wrapper, and half the time that wrapper is a
    # <td>, which cannot legally survive on its own.
    markup = node if isinstance(node, str) else "".join(str(c) for c in node.children)
    soup = BeautifulSoup(markup, "html.parser")

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for tag in soup.find_all(_DROP):
        tag.decompose()

    for tag in soup.find_all(True):
        if tag.name in _RENAME:
            tag.name = _RENAME[tag.name]

    _unwrap_disallowed(soup)

    # Collapse the source file's own line wrapping and indentation. Every
    # meaningful break is an element by now, so any run of whitespace left in
    # a text node is layout noise.
    for text in list(soup.find_all(string=True)):
        collapsed = re.sub(r"\s+", " ", str(text))
        if collapsed != str(text):
            text.replace_with(NavigableString(collapsed))

    # Prune and demote alternately. One pass is not enough: a page-header
    # table has two cells per row, so it survives the demotion test until
    # pruning removes its image-only rows - and what is left behind is a
    # one-cell wrapper that is pure layout and must go too. Bounded rather
    # than while-True: this is a parser for other people's broken markup.
    for _ in range(4):
        _prune_empty(soup)
        if not _demote_layout_tables(soup):
            break
        _unwrap_disallowed(soup)

    _paragraphize(soup)
    _prune_empty(soup)

    # One top-level block per line: storage does not care, but every
    # inspection of this column happens in a terminal.
    return "\n".join(str(c) for c in soup.children if str(c).strip())


# to_text separators, applied before and after the element's own content.
_TEXT_SEP = {
    "p": "\n\n",
    "h3": "\n\n",
    "h4": "\n\n",
    "blockquote": "\n\n",
    "ul": "\n\n",
    "ol": "\n\n",
    "table": "\n\n",
    "hr": "\n\n",
}


def _walk(node, parts: list) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        name = child.name
        if name == "img":
            # Skipped on purpose: `alt` on this corpus is as often 'spacer.gif'
            # or '' as it is a real caption, and `body` feeds the FTS index.
            continue
        if name == "br":
            parts.append("\n")
            continue
        if name == "li":
            ordered = getattr(child.parent, "name", None) == "ol"
            if ordered:
                idx = (
                    sum(
                        1
                        for s in child.previous_siblings
                        if getattr(s, "name", None) == "li"
                    )
                    + 1
                )
                parts.append(f"\n{idx}. ")
            else:
                parts.append("\n• ")
            _walk(child, parts)
            continue
        if name == "tr":
            cells = []
            _walk(child, cells)
            parts.append("".join(cells).rstrip("\t") + "\n")
            continue
        if name in ("td", "th"):
            # A cell is one flat chunk. These templates wrap every cell's
            # content in its own <p>, and letting that emit a paragraph break
            # turned Intel's quarterly results table into a column of
            # disconnected numbers - "36.1%" and "39.2%" two blank lines
            # apart instead of two cells of one row.
            cell = []
            _walk(child, cell)
            parts.append(re.sub(r"\s+", " ", "".join(cell)).strip() + "\t")
            continue
        sep = _TEXT_SEP.get(name)
        if sep:
            parts.append(sep)
            _walk(child, parts)
            parts.append(sep)
        else:
            _walk(child, parts)


def to_text(html: str) -> str:
    """The cleaned fragment -> plain text with the block structure spelled out
    in newlines. This is what goes into `releases.body`, so it is also what
    the FTS index and the CLI reader's snippets see.

    fts5's unicode61 tokenizer treats \\n exactly like a space, so none of
    this changes a single token - only what a human reads.
    """
    if not html:
        return ""
    parts = []
    _walk(BeautifulSoup(html, "html.parser"), parts)
    text = "".join(parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", lambda m: "\t" if "\t" in m.group() else " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(node) -> tuple[str, str]:
    """(body, body_html) for one article container - the call every scraper
    makes. Returns ("", "") for a missing container so a caller can keep its
    existing "no body found" branch.

    A wrong decode is undone here, on the markup, **before** the text is
    rendered from it. That ordering is the point: `body` is by definition
    `to_text(body_html)`, and repairing the two independently could break that
    invariant, because `decoding.undo_mojibake` accepts a round trip only when
    every qualifying codepage agrees and text with tags in it can answer
    differently. Repairing once, upstream of the split, cannot disagree with
    itself. Why at the write at all: some of this damage is upstream and
    survives a correct decode, so it comes back on every refetch.
    """
    html = clean(node)
    fixed = decoding.repair_text(html)
    if fixed:
        html = fixed[0]
        decoding.REPAIRS[fixed[1]] += 1
    return to_text(html), html
