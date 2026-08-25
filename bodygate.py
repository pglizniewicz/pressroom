"""May this text replace what is stored? One module, four gates, no SQL.

Every write that re-extracts a body passes one of these, and which one is not a
detail: the two data-loss incidents in this corpus were both a gate mismatch,
not a missing gate.

  safe_to_write     the general case. Refuses exactly one thing - text
                    disappearing from the MIDDLE of a body.
  strict_same_text  when the bytes are a capture of some *other* page (a
                    listing, a print view). Only whitespace may differ.
  same_words        when the same bytes are read a second way (an attachment's
                    text route against its structured route). Blind to order
                    and to joins, which is what a converter may change.
  not_shorter       the floor that applies under all of them, including under
                    a force re-extraction.

They live together because each allowance in them cost its own measurement over
real captures, and two copies of that drift silently. `re` and `collections` are
the only imports: no database, no network, no parser.
"""

import collections
import re


def wordchars(text: str) -> str:
    """Everything that is not a word character, dropped. Comparing these
    strings is deliberately blind to exactly the things a re-extraction is
    supposed to change - indentation, line wrapping, the bullets and ordinals
    to_text() prepends - and to the joins it causes, where `8 th` becomes
    `8th` and `unlocked 1` becomes `unlocked1` because a <sup> stopped being
    padded with spaces. A word-multiset comparison flags all of those as loss;
    a character-sequence comparison does not.

    Ordered-list markers are stripped first: to_text() prepends "1. ", "2. "
    to <ol> items, and those digits are invented characters that would
    otherwise read as the parser having made text up."""
    text = re.sub(r"(?m)^\s*\d+\.\s", " ", text or "")
    return re.sub(r"\W+", "", text)


def is_subsequence(small: str, big: str) -> bool:
    it = iter(big)
    return all(ch in it for ch in small)


def text_delta(old: str, new: str) -> dict:
    """How a re-extraction changed a body, in terms strong enough to gate on.

    Two directions, because the two failure modes are opposite:

      kept   - every character of the old body still appears, in order, in the
               new one. False means text was LOST.
      clean  - every character of the new body came from the old one, in order.
               False means text was INVENTED - the container grabbed a sidebar,
               or the parser latched onto the wrong element.

    Which of the two must hold depends on the source, and that is not a detail
    to paper over. For a parser that cut markers out of an already-clean body
    (terratec_portal, midiman_de inline) the new body must keep everything.
    For one whose body was the WHOLE PAGE including nav (terratec, terratec_de,
    midiman 2001), the redesign is supposed to drop text, so `kept` will be
    false by design and `clean` is the check that means anything.

    But `kept=False, clean=True` is the signature of BOTH "nav was correctly
    dropped" and "a paragraph went missing" - the pair alone cannot tell them
    apart. `edges_only` is what separates them: True when the new body is a
    contiguous run of the old one, i.e. material came off the front and the
    back and nothing was taken out of the middle. Nav and footer live at the
    edges; a lost paragraph does not. For the whole-page sources that is the
    check with actual teeth.
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


def safe_to_write(old_body: str, new_body: str) -> tuple:
    """(ok, why). The gate every re-extraction passes before it is stored.

    Refuses exactly one thing: text disappearing from the MIDDLE of a body.
    Everything else a correct re-extraction does is allowed, and each was
    checked against real captures before being let through:

      removed at the edges  a container that excludes nav. Over 215
                            terratec/terratec_de captures the removed prefixes
                            are image alt text and URL paths, and not one row
                            lost a suffix.
      added                 a teaser-grade row recovering its full article
                            (#3769 went 216 -> 2314 characters), or the
                            character test tripping over a join that changed
                            no word at all.

    A middle deletion has no benign explanation here, so it is held back and
    reported instead of written.
    """
    d = text_delta(old_body, new_body)
    if d["kept"] or d["edges_only"]:
        return True, ""

    # Two middle-loss cases are known-good, and both were confirmed by a
    # word-level diff of the actual rows before being written down here:
    #
    #  - the old body was teaser-grade and the new one is the real article.
    #    terratec_pressen sid=204 held 216 characters of the portal's own
    #    comment widget ("Login Create account Comments Threshold 1 0 1 2 3
    #    4 5 No comments...") where the release should have been; the DOM pass
    #    returns 2314 characters of press release. Refusing that would be
    #    protecting nonsense.
    #  - a loss under 2% of the body. Every instance measured was a URL path
    #    or image alt text sitting mid-page: terratec drbox1.htm loses exactly
    #    `http wm terrafinal presse pressemit 128i_pci` and nothing else.
    if d["old_len"] < 400 and d["new_len"] > d["old_len"] * 2:
        return True, ""
    if 0 < d["removed"] <= 0.02:
        return True, ""
    return False, f"ubytek w srodku ({d['removed']:.0%})"


def chars_no_bullets(text: str) -> str:
    """Every non-whitespace character, bullets dropped. Whitespace *placement*
    is the one thing this comparison forgives, which is the same call
    text_delta makes and for the same reason: `8 th` -> `8th` and
    `GeForce ™` -> `GeForce™` are the parser getting it right, not text
    changing."""
    return "".join((text or "").replace("•", "").split())


def strict_same_text(old_body: str, new_body: str) -> tuple:
    """(ok, why) when the bytes are a capture of a page that is not this row's
    own: the ONLY difference allowed is whitespace placement and the `•`
    markers to_text() puts on list items.

    safe_to_write is the wrong gate there and this cost 64 rows before it was
    understood. Such a capture is of a *listing*, a print view, or another
    release's page; handing it to a whole-page parse_detail yields the longest
    article on it, which for a listing is somebody else's release.
    safe_to_write let that through - it refuses text lost from the *middle*,
    and wholesale replacement by a longer text reads as the teaser-to-article
    upgrade it explicitly allows. Five midiman_de rows ended up sharing one
    body that belonged to none of them.

    Character-sequence equality cannot be fooled that way: another release's
    text is a different sequence, so the only writes this admits are the ones
    where the current parser reproduces exactly what is stored, character for
    character, with only spaces moved.
    """
    return (chars_no_bullets(old_body) == chars_no_bullets(new_body),
            "inny ciag znakow")


def same_words(text: str, body: str, dropped: list, list_items: int) -> tuple:
    """(ok, why) for replacing an attachment's flat text with its structured form.

    Compares the **multiset of word characters** - the repo's one implementation
    of "the same text, extracted differently" - and that choice is the fourth
    attempt, each earlier one refused by a measurement rather than by taste:

    - **character sequence** refused 13 of 73 rows for losing nothing: the two
      routes order fragments differently, because `pdftotext -layout` puts a
      superscript and a `®` on their own lines while the structured route puts
      them back beside the word they belong to.
    - **subsequence** (text_delta's kept/clean) breaks on the same reordering,
      and the text route is not the reference here - it is the other reading of
      the same bytes.
    - **word coverage** refused 10, all of them joins: `Composer` + `®` +
      `system` arriving as one word `Composer®system`.

    A multiset of characters is blind to order and to joins, which is exactly
    what a converter is allowed to change, and still cannot pass a document that
    lost a paragraph - those characters appear nowhere.

    Two allowances, both named and bounded:

    - the blocks the converter deliberately dropped, which it must name
      (attachments.rotated_text - the sideways banner, the only difference
      between the routes on 33 of the 81 cached PDFs);
    - **markers that became structure**: `1)`..`6)` absorbed into an <ol> and the
      Courier `o` of a second-level bullet absorbed into <li>. Measured: 3 rows,
      6 digits and 9 `o`s. Bounded by the number of list items, so it can never
      excuse a missing word.
    """
    want = collections.Counter(wordchars(text)) \
        - collections.Counter(wordchars(" ".join(dropped)))
    got = collections.Counter(wordchars(body))

    def only_markers(diff):
        """Whether a difference is nothing but list markers.

        Both directions need this allowance, and the second one took a
        measurement to find. `wordchars` strips an ordinal only at the start of
        a line, and the two routes break lines in different places - so prose
        reading `Mac OS 10.1 Drivers` has its digits stripped on one side and
        kept on the other (#5049, 4 digits). Bounded by the list items either
        way, at two characters each, so it can never excuse a missing word.
        """
        return (all(ch.isdigit() or ch == "o" for ch in diff)
                and sum(diff.values()) <= max(list_items, 0) * 2)

    invented = got - want
    if invented and not only_markers(invented):
        sample = "".join(sorted(invented))[:24]
        return False, f"{sum(invented.values())} znakow z niczego ({sample!r})"

    missing = want - got
    if missing and not only_markers(missing):
        sample = "".join(sorted(missing))[:24]
        return False, f"brak {sum(missing.values())} znakow ({sample!r})"
    return True, ""


def not_shorter(old_body: str, new_body: str) -> tuple:
    """(ok, why). The floor that holds under every other gate, including under
    a forced re-extraction.

    A different copy of a release is not automatically a better one: on the
    terratec_new CMS the listing carries the full text for recent releases and
    a truncated one for older entries, and an older Wayback capture can be an
    earlier, shorter version of the article. 122 full articles were overwritten
    by their listing teasers before this was a rule, and `safe_to_write`
    provably cannot catch it - a lost tail is `edges_only`, the same signature
    as correctly dropped nav.
    """
    if len(new_body or "") < len(old_body or ""):
        return False, "krotszy niz zapisany"
    return True, ""
