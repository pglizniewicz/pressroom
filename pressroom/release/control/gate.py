"""May this text replace what is stored? One module, four gates, no SQL.

Every write that re-extracts a body passes one of these, and which one is not a
detail: the two data-loss incidents in this corpus were both a gate mismatch,
not a missing gate. Each gate says below what it refuses and what it allows;
they are in one module because every allowance cost its own measurement over real
captures, and two copies of that drift silently.

`re` and `collections` are the only imports: no database, no network, no parser.

→ docs/adr/gates.md
"""

import collections
import re
from typing import TypedDict


def wordchars(text: str) -> str:
    """Everything that is not a word character, dropped - so a comparison
    ignores exactly what a re-extraction is supposed to change: indentation,
    line wrapping, bullets, and the joins that come with them (`8 th` -> `8th`).

    Ordered-list markers go first, because the "1. " to_text() prepends to an
    <ol> item is an invented character that would read as the parser making text
    up."""
    text = re.sub(r"(?m)^\s*\d+\.\s", " ", text or "")
    return re.sub(r"\W+", "", text)


class Delta(TypedDict):
    """What `text_delta` measures - a fixed shape, because every gate below
    reads the same six fields."""

    kept: bool
    clean: bool
    edges_only: bool
    old_len: int
    new_len: int
    removed: float


def is_subsequence(small: str, big: str) -> bool:
    it = iter(big)
    return all(ch in it for ch in small)


def text_delta(old: str, new: str) -> Delta:
    """How a re-extraction changed a body, in terms strong enough to gate on.

    Two directions, because the two failure modes are opposite:

      kept   - every character of the old body still appears, in order, in the
               new one. False means text was LOST.
      clean  - every character of the new body came from the old one, in order.
               False means text was INVENTED - the container included a sidebar.

    Which of the two must hold depends on the source: a parser that cut markers
    out of an already-clean body must keep everything, while one whose body was
    the whole page including nav is *supposed* to drop text, so `kept` is false
    by design there and `clean` is the check that means anything.

    `kept=False, clean=True` is the signature of both "nav was correctly
    dropped" and "a paragraph went missing", so never gate on the pair alone.
    `edges_only` separates them: the new body is a contiguous run of the old
    one, i.e. material came off the front and the back and nothing out of the
    middle. Nav and footer are at the edges; a lost paragraph does not.
    """
    o, n = wordchars(old), wordchars(new)
    return {
        "kept": is_subsequence(o, n),
        "clean": is_subsequence(n, o),
        "edges_only": bool(n) and n in o,
        "old_len": len(o),
        "new_len": len(n),
        "removed": (len(o) - len(n)) / len(o) if o else 0.0,
    }


def safe_to_write(old_body: str, new_body: str) -> tuple[bool, str]:
    """(ok, why). The gate every re-extraction passes before it is stored.

    Refuses exactly one thing: text disappearing from the MIDDLE of a body.
    Everything else a correct re-extraction does is allowed, each checked
    against real captures first - text removed at the edges is a container that
    excludes nav, and text added is a teaser-grade row recovering its article.
    A middle deletion has no benign explanation here, so it is held back and
    reported instead of written.
    """
    d = text_delta(old_body, new_body)
    if d["kept"] or d["edges_only"]:
        return True, ""

    # Two middle-loss cases are known-good, each confirmed by a word-level diff
    # of the rows before being allowed here: a teaser-grade body replaced by the
    # real article (what it held was the portal's comment widget, which is not
    # worth keeping), and a loss under 2%, which every time was a url path or
    # image alt text sitting mid-page.
    if d["old_len"] < 400 and d["new_len"] > d["old_len"] * 2:
        return True, ""
    if 0 < d["removed"] <= 0.02:
        return True, ""
    return False, f"ubytek w srodku ({d['removed']:.0%})"


def chars_no_bullets(text: str) -> str:
    """Every non-whitespace character, bullets dropped. Whitespace *placement*
    is the one difference this comparison allows, for the same reason
    text_delta does: `GeForce ™` -> `GeForce™` is the parser getting it right."""
    return "".join((text or "").replace("•", "").split())


def strict_same_text(old_body: str, new_body: str) -> tuple[bool, str]:
    """(ok, why) when the bytes are a capture of a page that is not this row's
    own: the ONLY difference allowed is whitespace placement and the `•`
    markers to_text() puts on list items.

    safe_to_write is the wrong gate there, and it cost rows before that was
    understood: handing a listing capture to a whole-page parser yields the
    longest article on the page, which is somebody else's release, and wholesale
    replacement by a longer text reads to safe_to_write as the teaser-to-article
    upgrade it explicitly allows.

    Character-sequence equality does not admit that case, so the only writes
    this admits are the ones where the current parser reproduces exactly what is
    stored, with only spaces moved.
    """
    return (
        chars_no_bullets(old_body) == chars_no_bullets(new_body),
        "inny ciag znakow",
    )


def same_words(
    text: str, body: str, dropped: list, list_items: int
) -> tuple[bool, str]:
    """(ok, why) for replacing an attachment's flat text with its structured form.

    Compares the **multiset of word characters**, which ignores order and joins
    - exactly what a converter is allowed to change, since the two routes put a
    superscript or a `®` in different places - and still cannot pass a document
    that lost a paragraph. A sequence or subsequence comparison refuses those
    reorderings; see docs/adr/gates.md for the three that were tried.

    Two allowances, both named and bounded:

    - the blocks the converter deliberately dropped, which it must name
      (conversion.rotated_text - the sideways banner);
    - **markers that became structure**: `1)`..`6)` absorbed into an <ol>, the
      Courier `o` of a second-level bullet absorbed into <li>. Bounded by the
      number of list items, so it can never permit a missing word.
    """
    want = collections.Counter(wordchars(text)) - collections.Counter(
        wordchars(" ".join(dropped))
    )
    got = collections.Counter(wordchars(body))

    def only_markers(diff):
        """Whether a difference is nothing but list markers.

        Both directions need the allowance: `wordchars` strips an ordinal only at
        the start of a line and the two routes break lines in different places,
        so prose reading `Mac OS 10.1 Drivers` has its digits stripped on one
        side and kept on the other. Bounded either way by the list items, at two
        characters each.
        """
        return (
            all(ch.isdigit() or ch == "o" for ch in diff)
            and sum(diff.values()) <= max(list_items, 0) * 2
        )

    invented = got - want
    if invented and not only_markers(invented):
        sample = "".join(sorted(invented))[:24]
        return False, f"{sum(invented.values())} znakow z niczego ({sample!r})"

    missing = want - got
    if missing and not only_markers(missing):
        sample = "".join(sorted(missing))[:24]
        return False, f"brak {sum(missing.values())} znakow ({sample!r})"
    return True, ""


def not_shorter(old_body: str, new_body: str) -> tuple[bool, str]:
    """(ok, why). The floor that holds under every other gate, including under
    a forced re-extraction.

    A different copy of a release is not automatically a better one: the
    terratec_new listing carries the full text for recent releases and truncates
    older entries, and an older Wayback capture can be an earlier, shorter
    version of the article. Full articles were overwritten by their listing
    teasers before this was a rule, and `safe_to_write` provably cannot catch it
    - a lost tail is `edges_only`, the same signature as correctly dropped nav.
    """
    if len(new_body or "") < len(old_body or ""):
        return False, "krotszy niz zapisany"
    return True, ""
