"""What can go wrong in a conversion, measured: the interleaving artefacts the
text route produces, the structure the layout route loses, and the thresholds
that turn those counts into a verdict.

`pressroom-calibrate-converters` reports these over every cached attachment;
the measures are here so the boundary walks the cache, prints, and writes the
review page, and decides nothing. A defect that is a converter's *own* threshold
(rotated blocks, margins, gaps) stays in `conversion.py`, where the converter
reads it; this module only counts.
"""

import collections
import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from pressroom.converter.control import conversion

# Interleaving signals: the text route merges overlapping copies of a document
# into strings that exist in no version of it. See conversion.py's docstring.
DUP_RE = re.compile(r"\b(\w+ \w+) \1\b", re.I)
MERGE_RE = re.compile(r"\d{2,}\.\d{2}\.\w|\d{3,}\.\d{2}\.")
CASE_RE = re.compile(r"[a-z]{3}[A-Z][a-z]{2}")
# A word split across a line break and rejoined with the space still in it.
# `X- and`, `X- or`, `X- to` are excluded because they are correct English, not
# damage: "61- and 88-note models".
HYPHEN_RE = re.compile(r"\w- (?!and\b|or\b|to\b|through\b)\w")

# The gate the writing pass would use. Retention alone is not enough: the one
# document it rejects (0.68) is the one where the structured output is right.
MIN_RETENTION = 0.90

# A PDF whose structured output is one block while the text route has many
# lines: not empty, but degenerate - every paragraph break lost. Worth the same
# human decision as an empty conversion, and invisible to the retention measure,
# which counts words rather than structure.
DEGENERATE_MIN_LINES = 10

# Below this, the structured route lost enough words that somebody should look
# at the document rather than trust the gate.
REVIEW_RETENTION = 0.95

# Same choice as conversion.py: poppler's bbox tree is XML read
# with html.parser, because this repo declares no lxml.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def artefacts(text: str) -> tuple[int, int, int]:
    words = text.split()
    return (
        len(DUP_RE.findall(" ".join(words))),
        len(MERGE_RE.findall(text)),
        sum(1 for w in words if CASE_RE.search(w)),
    )


def rotated_blocks(content: bytes) -> int:
    """How many blocks conversion._pdf_fragment drops as sideways text. Counted
    here rather than returned from there: the converter has no business growing
    a diagnostics channel for one review script."""
    xml = conversion._run(["pdftotext", "-bbox-layout", "-", "-"], content)
    soup = BeautifulSoup(xml, "html.parser")
    n = 0
    for b in soup.find_all("block"):
        h = float(b.get("ymax", 0)) - float(b.get("ymin", 0))
        w = float(b.get("xmax", 0)) - float(b.get("xmin", 0))
        if h > conversion.ROTATED_MIN_HEIGHT and w < h * conversion.ROTATED_MAX_ASPECT:
            n += 1
    return n


def top_level_blocks(body_html: str) -> int:
    """How many elements the fragment has at its top level - one is the
    degenerate case DEGENERATE_MIN_LINES names."""
    return len(BeautifulSoup(body_html, "html.parser").find_all(recursive=False))


def first_heading_index(body_html: str) -> int:
    """Position of the first h3 among the fragment's top-level elements, or -1.

    The measure that would have caught the reading-order bug: a press release's
    headline is at the top of the document, so a first heading sitting at
    element 12 means the blocks were emitted in the wrong order.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    for i, el in enumerate(soup.find_all(recursive=False)):
        if el.name == "h3":
            return i
    return -1


def furniture(body_html: str) -> int:
    """Short paragraphs or headings that appear more than once - a running
    header or footer that survived into the body ("Press Release", the footer
    URL). Counted as the number of surplus copies.

    Not keyed to the page count: counting pages from the raw PDF
    bytes does not agree with what poppler reports, and the measure printed 0
    while the review page plainly showed the repeats. A measure that can be
    wrong in the reassuring direction is worse than no measure.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    texts = [el.get_text(" ", strip=True) for el in soup.find_all(["p", "h3"])]
    short = [t for t in texts if 0 < len(t) < 60]
    return sum(n - 1 for n in collections.Counter(short).values() if n > 1)
