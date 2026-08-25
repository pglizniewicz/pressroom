#!/usr/bin/env python3
"""What an attachment's bytes mean: .pdf/.doc -> text, or -> our HTML subset.

Owns one concern - reading a press release out of a binary attachment - for
both callers that need it: `backfill_midiman_attachments.py` (the crawl) and
`calibrate_attachments.py` (the review). Everything here shells out to the two
system binaries this repo has always relied on, `pdftotext` (poppler-utils) and
`antiword`; nothing here talks to the network or the database.

Two levels of answer, and the difference is what we ask the *tool* for:

- `plain_text()` asks for text. `pdftotext -layout` and `antiword -t` fake the
  document's structure with spaces and line breaks, so the result is stored
  with `body_html` NULL and rendered through `white-space: pre-wrap`. This is
  what the corpus held until 2026-08-23.
- `to_richtext()` asks `pdftotext -bbox-layout` for output that *carries* the
  structure - page/flow/block/line/word with coordinates - and converts it into
  the same allowlisted subset `richtext.py` emits for every HTML source. A
  paragraph is a paragraph because poppler measured the gap between lines, a
  heading is one because the type is taller, a list item is one because the line
  opens with a marker. Layout metadata, not a heuristic over extracted text.

**PDFs only.** The equivalent route for .doc (`antiword -x db`, DocBook) was
built and dropped after review: it flattens nested lists, loses paragraph
breaks and mangles numbering, and all it gained was `<strong>` on the headline.
See to_richtext's docstring for the documents that decided it. Word files keep
plain_text().
"""

import re
import statistics
import subprocess
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from encoding import decode_html
import richtext

PDF_MAGIC = b"%PDF"
# OLE2 compound document header - the real Word 97-2003 container.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Dispatch is on magic bytes, NOT the extension: CMS-era attachments are
# routinely mislabeled, and 7 of this corpus's ".pdf" URLs are an HTML error
# page. Feeding one of those to a parser is how 23 rows once ended up with
# '%PDF-1.3 %âãÏÓ 6 0 obj...' stored as their body.
_TIMEOUT = 30

# A list marker at the start of a line. Two families, because they map to two
# elements: bullets to <ul>, numbers to <ol>.
BULLET_RE = re.compile(r"^[•·⁃▪●\u2022]\s+|^[-*]\s+")
ORDERED_RE = re.compile(r"^\(?(\d{1,2})[.)]\s+")
# Word's second-level bullet renders as a Courier "o". Accepted as a marker
# ONLY inside an already-open list and only when the line is indented past that
# list's own left edge - on its own, "o " at the start of a line is just a word.
NESTED_BULLET_RE = re.compile(r"^o\s+")

# How much deeper a marker line must sit to count as a nested level, and how
# much shallower to close one. Measured on M-Audio_ProFire 610_PR15.pdf, where
# level one starts at x=108.2 and level two at x=144.2.
LEVEL_INDENT = 12.0

# A rotated block: poppler reports the sideways sidebar text these press
# releases print down the page margin as one narrow, page-tall block whose
# "lines" are 100pt+ high. Left in, the height rule below reads it as the
# biggest heading in the document - and because it is its own flow, poppler
# emits it after the footer, so the release's real headline lands at the end.
# Both were live defects before this rule existed.
ROTATED_MIN_HEIGHT = 80.0
ROTATED_MAX_ASPECT = 0.25

# A block whose bottom edge is above this, or whose top edge is below
# page_height minus this, is page furniture (the "Press Release" banner, the
# footer URL). It stays in the text - it is not navigation - but it may not
# become a heading.
MARGIN_TOP = 90.0
MARGIN_BOTTOM = 60.0

# How much taller than the page's median line a line must be to read as a
# heading. Calibrate with calibrate_attachments.py rather than by taste.
HEADING_FACTOR = 1.6
HEADING_MAX_CHARS = 200

# Two lines belong to the same visual line when their vertical extents overlap
# by at least this fraction of the shorter one. That is what reunites a list's
# marker column with its text column - see _visual_lines.
SAME_LINE_OVERLAP = 0.6

# Words closer together than this fraction of the line's height are one word:
# poppler splits letter-spaced display type into fragments, and joining every
# fragment with a space produced `Portable digital pi a nos with built- in a
# udio inte rf a ces`.
#
# Both numbers come from the histogram of every word gap in every cached PDF,
# not from taste. Real word spaces cluster at 0.20-0.30 of the line height
# (`aluminum|cone` 0.20, `M-Audio|Unveils` 0.25); the gaps inside a letter-
# spaced word sit at 0.08-0.10. 0.2 was tried first and was wrong: it swallowed
# the 0.20 bucket, and corpus-wide word retention fell to a 0.871 median.
#
# The gap must also be non-negative, and that exclusion is the whole reason
# tracked ALL-CAPS headlines survive: poppler reports *overlapping* boxes for
# them - 97 pairs corpus-wide, `MIDIMAN|DISTRIBUTES`, `MAC|OS`, `OS|X` - which
# a threshold alone would merge into `MACOSXDRIVERS`. What we do want to merge
# is small and positive: `pi|a`, `info@m|-audio.net`, `Naka|-Ku`.
WORD_GAP_RATIO = 0.15

# A vertical gap larger than this multiple of the page's usual leading ends a
# paragraph.
PARAGRAPH_GAP = 1.6

# A continuation line of a list item is indented at least this far past the
# marker's own left edge.
CONTINUATION_INDENT = 6.0

# A marker line this much further down than the page's usual leading starts a
# NEW list rather than continuing - or deepening - the open one. Without it,
# indentation alone decided nesting, and the address footer (whose lines happen
# to start with a bullet and sit further right than the content list) became a
# sub-list inside the last spec item: `75Hz low-frequency roll-off switch USA:
# 45 E. St. Joseph Street...`. Measured on gtmic041102pr.pdf, where list items
# are 2.4pt apart and the footer is 17-49pt below the item above it.
LIST_BREAK_GAP = 3.0


def _run(cmd: list, data: bytes) -> str:
    """Feed `data` to an extractor on stdin; "" on any failure.

    The output goes through encoding.decode_html, not bytes.decode: pdftotext
    and antiword hand back whatever the document held, and this corpus is full
    of cp1252 bytes inside files that claim UTF-8. Same rule as everywhere else
    here - the decode is a decision, never a guess.
    """
    try:
        result = subprocess.run(cmd, input=data, capture_output=True, timeout=_TIMEOUT)
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return decode_html(result.stdout)


def kind_of(content: bytes) -> str:
    """What these bytes actually are: 'pdf' | 'doc' | a reportable non-type.

    Named rather than boolean so a mislabeled file is reported instead of
    silently yielding an empty body. `rtf` and `html (soft-404?)` are the two
    that turn up: antiword refuses RTF and there is no unrtf here, and HTML
    under a .pdf URL is the original server's soft-404 served as HTTP 200 -
    which CDX's statuscode filter cannot catch, and which must never be
    returned as text, because an error page easily outruns a teaser and would
    pass a length-based guard.
    """
    head = content[:8]
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(OLE2_MAGIC):
        return "doc"
    stripped = content.lstrip()
    if stripped[:5].lower() == b"{\\rtf":
        return "rtf (no extractor)"
    if stripped[:1] == b"<":
        return "html (soft-404?)"
    return f"unrecognised ({bytes(head[:4])!r})"


def is_attachment(content: bytes) -> bool:
    """True if these bytes are a type plain_text() can pull real text from.

    Drives fetch_first_matching_snapshot's walk-back: a capture that is HTML (a
    soft-404) fails this, so the walk-back tries an older capture instead of
    settling for a wrong-typed page. Checked on the raw bytes rather than by
    calling plain_text and looking at `kind` - that would run
    pdftotext/antiword once to classify and again to extract.
    """
    return kind_of(content) in ("pdf", "doc")


# The extensions that mean "this url is a file, not a page". A predicate about
# the *address*, next to the predicate about the bytes, because the two are the
# same question asked before and after a fetch - and because three modules used
# to import it from a scraper, which pointed the dependency the wrong way.
# db.py spells the same rule in SQL (`_FLAG_SQL["plain"]`) because it may not
# import this module; change one and change the other.
ATTACHMENT_EXTS = (".pdf", ".doc")


def is_attachment_url(url: str) -> bool:
    """Whether this address names a file rather than an HTML page."""
    return (url or "").lower().split("?", 1)[0].endswith(ATTACHMENT_EXTS)


# Everything else a fetch can return that is not text to parse. Kept beside
# PDF_MAGIC/OLE2_MAGIC rather than in the caller: one table, both directions of
# the question - "is this an attachment I can extract" and "is this safe to
# hand to an HTML parser".
_BINARY_MAGIC = (PDF_MAGIC, OLE2_MAGIC, b"PK\x03\x04",
                 b"\x1f\x8b", b"GIF8", b"\x89PNG", b"\xff\xd8\xff")


def looks_like_html(content: bytes) -> bool:
    """Whether these bytes are worth handing to an HTML parser at all.

    BeautifulSoup never refuses input: give it a PDF and it returns a document
    whose get_text() is the binary decoded as characters. There is no parse
    error to catch, so the check has to happen before the parse.
    """
    return bool(content) and not content.lstrip()[:8].startswith(_BINARY_MAGIC)


def normalize(text: str) -> str:
    r"""Tidy an extractor's output without flattening it.

    This used to be `re.sub(r"\s+", " ", text)`, which threw away the one thing
    `pdftotext -layout` and `antiword` are asked for: the layout. Only trailing
    spaces, form feeds (pdftotext's page breaks) and runs of blank lines go.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def plain_text(content: bytes) -> tuple:
    """(text, kind) - the text-only route, layout faked with whitespace.

    Rows stored from this route keep `body_html` NULL and render through
    `white-space: pre-wrap`, which is the right renderer for column layout and
    the wrong one for prose. It stays the fallback for whatever to_richtext()
    cannot convert.
    """
    k = kind_of(content)
    if k == "pdf":
        return normalize(_run(["pdftotext", "-layout", "-", "-"], content)), k
    if k == "doc":
        return normalize(_run(["antiword", "-m", "UTF-8.txt", "-"], content)), k
    return "", k


def rotated_text(content: bytes) -> list:
    """The text of every block _pdf_fragment drops as sideways type.

    Exposed so a writing pass can *name* what it left out instead of trusting
    that it was decoration. It is not: on 33 of the 81 cached PDFs the dropped
    block is the only character-level difference between the two routes, and it
    carries a phrase of its own - `MIDIMAN DISTRIBUTES ABLETON SOFTWARE` beside
    a headline reading `MIDIMAN Assumes Distribution of Ableton`. A vertical
    marketing banner, not a duplicate.
    """
    soup = BeautifulSoup(_run(["pdftotext", "-bbox-layout", "-", "-"], content),
                         "html.parser")
    out = []
    for block in soup.find_all("block"):
        x0, y0 = float(block.get("xmin", 0)), float(block.get("ymin", 0))
        x1, y1 = float(block.get("xmax", 0)), float(block.get("ymax", 0))
        if (y1 - y0) > ROTATED_MIN_HEIGHT and (x1 - x0) < (y1 - y0) * ROTATED_MAX_ASPECT:
            words = [w.get_text() for w in block.find_all("word") if w.get_text().strip()]
            if words:
                out.append(" ".join(words))
    return out


def _page_lines(page) -> list:
    """Every text line of one page as (y0, y1, [(x, word), ...]), rotated blocks
    dropped, words kept separate with their own x.

    Two levels of poppler's grouping are deliberately distrusted here:

    - **blocks**, because they split a numbered list down the middle: the
      markers `1) 2) 3)` become one block at x=45 and their sentences another at
      x=63, both spanning the same rows. Reading those as two paragraphs turned
      every numbered list in this corpus into mush.
    - **lines**, because a superscript is its own line. `4`+`th`+`-order` is
      three pieces on two lines, and the `th` sorts *before* the sentence it
      belongs to, which is how a bulleted line once arrived as
      `th • onboard DSP manages…` - marker no longer at the start, so no list.

    Words carry their own left and right edge, so _visual_lines can put them
    back in reading order *and* tell a word space from letter spacing.
    """
    out = []
    for block in page.find_all("block"):
        x0, y0 = float(block.get("xmin", 0)), float(block.get("ymin", 0))
        x1, y1 = float(block.get("xmax", 0)), float(block.get("ymax", 0))
        if (y1 - y0) > ROTATED_MIN_HEIGHT and (x1 - x0) < (y1 - y0) * ROTATED_MAX_ASPECT:
            continue
        for line in block.find_all("line"):
            words = [(float(w.get("xmin", 0)), float(w.get("xmax", 0)), w.get_text())
                     for w in line.find_all("word") if w.get_text().strip()]
            if not words:
                continue
            out.append((float(line.get("ymin", 0)), float(line.get("ymax", 0)), words))
    return out


def _visual_lines(lines: list) -> list:
    """Lines that share a row, merged into one and re-ordered left to right.

    Returns (y0, y1, x0, text, height). The x-ordering is the point: `1)` and
    `Import QuickTime movies…` are two poppler lines at the same y in different
    blocks, and a superscript `th` is a third - on the page they are one line,
    and only sorting the words by x puts them back in the order a reader sees.
    Height is taken as the tallest contributing line, which is what the heading
    rule needs; a superscript must not shrink its own line.
    """
    lines = sorted(lines, key=lambda t: (round(t[0], 1), t[2][0][0]))
    bands = []
    for y0, y1, words in lines:
        if bands:
            by0, by1, bwords = bands[-1]
            overlap = min(y1, by1) - max(y0, by0)
            if overlap > 0 and overlap >= SAME_LINE_OVERLAP * min(y1 - y0, by1 - by0):
                bands[-1] = (min(y0, by0), max(y1, by1), bwords + words)
                continue
        bands.append((y0, y1, list(words)))
    out = []
    for y0, y1, words in bands:
        words.sort(key=lambda w: w[0])
        height = y1 - y0
        parts = []
        for x_from, x_to, word in words:
            gap = x_from - parts[-1][1] if parts else None
            if gap is not None and 0 <= gap < height * WORD_GAP_RATIO:
                parts[-1] = (parts[-1][0], x_to, parts[-1][2] + word)
            else:
                parts.append((x_from, x_to, word))
        text = " ".join(w for _a, _b, w in parts).strip()
        if text:
            out.append((y0, y1, words[0][0], text, height))
    return out


# A compound word broken at its own hyphen across a line end: the previous line
# closes with `…-` and the next opens with a letter or digit. Joined without the
# space, hyphen kept - measured over every cached PDF, all 140 occurrences in 61
# documents are compounds (`M- Audio’s`, `24- bit/192kHz`, `best- of-class`,
# `award- winning`), not one is a syllable break where the hyphen would have to
# go. Fired only at a line join, never over finished text, so a legitimate
# `X- Y` inside one line is untouched.
_HYPHEN_END_RE = re.compile(r"\w-$")
_WORD_START_RE = re.compile(r"^[\w(]")


def _append_line(parts: list, text: str) -> None:
    """Add one line to a paragraph or list item under construction."""
    if parts and _HYPHEN_END_RE.search(parts[-1]) and _WORD_START_RE.match(text):
        parts[-1] += text
    else:
        parts.append(text)


def _pdf_fragment(content: bytes) -> str:
    """pdftotext -bbox-layout -> an HTML fragment, before richtext sanitises it.

    Works line by line, not block by block (see _page_lines), and decides three
    things from geometry alone: a taller-than-usual line outside the margins is
    a heading, a line opening with a marker starts a list item, and a vertical
    gap wider than the page's usual leading ends a paragraph. Nothing here reads
    the text itself except for the marker at its start.
    """
    xml = _run(["pdftotext", "-bbox-layout", "-", "-"], content)
    # html.parser, not "xml": this repo declares no lxml, and the fragment is
    # simple enough. Attribute names arrive lowercased, hence `ymin`/`ymax`.
    soup = BeautifulSoup(xml, "html.parser")
    out = []

    for page in soup.find_all("page"):
        page_height = float(page.get("height", 792))
        lines = _visual_lines(_page_lines(page))
        if not lines:
            continue
        median_height = statistics.median(h for *_rest, h in lines)
        gaps = [lines[i][0] - lines[i - 1][1] for i in range(1, len(lines))]
        leading = statistics.median([g for g in gaps if g >= 0] or [median_height * 0.3])

        para = []
        # One entry per open list level: its tag, its left edge, and its items.
        # A stack rather than a single list because these press releases nest
        # two deep - "6 unique inputs including:" over "o 2 XLR/TS combo
        # jacks..." - and a flat <ul> loses which item the sub-points belong to.
        stack = []

        def flush_para():
            if para:
                out.append("<p>" + " ".join(para) + "</p>")
                para.clear()

        def close_level():
            level = stack.pop()
            html = (f"<{level['tag']}>"
                    + "".join(f"<li>{t}</li>" for t in level["items"])
                    + f"</{level['tag']}>")
            if stack:
                stack[-1]["items"][-1] += html   # nested inside its parent item
            else:
                out.append(html)

        def flush_list():
            while stack:
                close_level()

        prev_bottom = None
        for y0, y1, x0, text, height in lines:
            text = text.strip()
            if not text:
                continue
            gap = 0.0 if prev_bottom is None else y0 - prev_bottom
            prev_bottom = y1
            in_margin = y1 < MARGIN_TOP or y0 > page_height - MARGIN_BOTTOM

            bullet, ordered = BULLET_RE.match(text), ORDERED_RE.match(text)
            # Deeper than the OUTERMOST level, not than the current one: the
            # second and later sub-items sit at the same x as the sub-list they
            # belong to, and comparing against stack[-1] made every one of them
            # after the first fall out into a paragraph.
            nested = (NESTED_BULLET_RE.match(text) if stack
                      and x0 > stack[0]["x"] + LEVEL_INDENT else None)
            marker = bullet or ordered or nested
            if marker:
                tag = "ol" if ordered else "ul"
                flush_para()
                if stack and gap > leading * LIST_BREAK_GAP:
                    flush_list()          # too far below to be the same list
                while stack and x0 < stack[-1]["x"] - LEVEL_INDENT:
                    close_level()
                if stack and abs(x0 - stack[-1]["x"]) <= LEVEL_INDENT:
                    if stack[-1]["tag"] != tag:      # bullets became numbers
                        close_level()
                        stack.append({"tag": tag, "x": x0, "items": []})
                elif stack and x0 > stack[-1]["x"] + LEVEL_INDENT:
                    stack.append({"tag": tag, "x": x0, "items": []})
                elif not stack:
                    stack.append({"tag": tag, "x": x0, "items": []})
                stack[-1]["items"].append(text[marker.end():].strip())
                continue

            # A line under an open list, indented past its marker and following
            # closely, is the rest of that item rather than a new paragraph.
            if (stack and stack[-1]["items"] and x0 > stack[-1]["x"] + CONTINUATION_INDENT
                    and gap <= leading * PARAGRAPH_GAP):
                item = [stack[-1]["items"][-1]]
                _append_line(item, text)
                stack[-1]["items"][-1] = " ".join(item).strip()
                continue

            flush_list()
            heading = (not in_margin and height > median_height * HEADING_FACTOR
                       and len(text) < HEADING_MAX_CHARS)
            if heading:
                flush_para()
                out.append(f"<h3>{text}</h3>")
                continue
            if para and gap > leading * PARAGRAPH_GAP:
                flush_para()
            _append_line(para, text)

        flush_list()
        flush_para()
    return "".join(out)


def to_richtext(content: bytes) -> tuple:
    """(body, body_html, kind) for a PDF, or ("", "", kind) for anything else.

    **PDF only, and .doc is excluded on purpose.** The DocBook route
    (`antiword -x db`) was built, calibrated over all 67 cached Word documents
    and then dropped, because reviewing the full texts side by side showed it
    losing structure the text output keeps:

      - nested lists are flattened - antiword emits one `<itemizedlist>` with
        "200+ total tape banks, including:" and its sub-points as siblings
        (GForce_M-Tron Pro_PR6.doc), while `-t` shows both levels by indent;
      - paragraph breaks are lost, so a release arrives as one block
        (M-Audio_Vista_PR.doc's neighbours);
      - list numbering comes out wrong and an item goes missing
        (8-16-06_avid_supports_ mactel.doc).

    What it gained was `<strong>` for the bold headline, which is decoration.
    So .doc keeps `plain_text()`, `body_html` NULL, and the pre-wrap renderer -
    with one known bad row: m-audio_octane_pr.doc, whose text output interleaves
    two overlapping copies of the release (`$749699.95.use`). One document out
    of 67 is not a reason to lose two list levels in the other 66.

    The PDF route finishes through `richtext.extract()`, so the output is the
    same allowlisted subset as every HTML source and `body` stays exactly
    `to_text(body_html)` - the invariant the whole corpus is checked against.

    **A PDF that comes back empty is a condition to report, never a fallback to
    take.** For .doc, plain_text() is the chosen route; for a PDF it would be a
    silent downgrade - the row would quietly land in the corpus as a pre-wrap
    blob and nobody would ever learn that the converter failed on it. A caller
    writing to the database must leave such a row alone and surface it, so the
    choice between "use the text for this one" and "fix the converter" is made
    by a person looking at the document. calibrate_attachments.py reports these
    under "decisions needed"; as of 2026-08-24 there are none - all 81 cached
    PDFs convert.
    """
    k = kind_of(content)
    if k != "pdf":
        return "", "", k
    fragment = _pdf_fragment(content)
    if not fragment:
        return "", "", k
    body, body_html = richtext.extract(BeautifulSoup(fragment, "html.parser"))
    return body, body_html, k
