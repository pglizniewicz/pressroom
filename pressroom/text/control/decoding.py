#!/usr/bin/env python3
"""Turning archived bytes into text when the page's declared charset is a lie.

The specific failure this exists for: a CMS serves `charset=utf-8` and is
genuinely UTF-8 almost everywhere, but a few bytes were pasted out of Word and
never converted, so the document is not valid UTF-8. A strict decode then fails,
BeautifulSoup falls back to chardet, and every *correct* UTF-8 sequence in the
file comes out as mojibake - a whole listing's titles corrupted by one
apostrophe. Decoding UTF-8 first and falling back per byte fixes both halves at
once, and guesses nothing, which is the point: chardet's verdict on these files
is on record as unreliable.

This is NOT a universal decoder. Use it for pages that claim UTF-8, and keep
`from_encoding="cp1252"` where a source is wholly pre-UTF-8 (the 2001-era GoLive
pages, midiman.de): there, two adjacent high bytes can coincidentally form a
valid UTF-8 sequence and would be honoured as one.

→ docs/adr/encoding.md
"""

import codecs
import collections
import re

# What the write path had to undo, by method. Read by the run summary and by the
# read-only encoding check: a fact about decoding, not about storage.
REPAIRS = collections.Counter()

# What a byte that isn't valid UTF-8 is assumed to be instead. cp1252 because
# these are Word-pasted smart quotes (0x91-0x94), dashes (0x96) and (tm) (0x99),
# which is exactly what cp1252 adds over ISO-8859-1.
FALLBACK = "cp1252"


def _utf8_cp1252(err: UnicodeDecodeError):
    """codecs error handler: re-read the offending bytes as cp1252.

    `errors="replace"` covers the five bytes cp1252 leaves undefined
    (0x81/0x8d/0x8f/0x90/0x9d) - one U+FFFD is a better outcome than an
    exception that loses the whole page.
    """
    return err.object[err.start : err.end].decode(FALLBACK, errors="replace"), err.end


codecs.register_error("utf8_cp1252", _utf8_cp1252)


def decode_html(content: bytes) -> str:
    """Decode `content` as UTF-8, reading any byte that isn't valid UTF-8 as
    cp1252 instead.

    Pass the result to BeautifulSoup as a str: given text rather than bytes it
    skips UnicodeDammit entirely, so this decision is final and no sniffing can
    override it.
    """
    return content.decode("utf-8", errors="utf8_cp1252")


# --- Undoing a wrong decode that is already in the database ----------------
#
# The other direction of the same concern: not "these bytes need a charset" but
# "this text was read with the wrong one and stored". Two shapes occur in this
# corpus, and both are reversible from the text alone - no refetch, which
# matters because the captures that produced them predate page_cache.

# A C1 control character means cp1252 bytes were read as ISO-8859-1: cp1252
# fills 0x80-0x9F with punctuation, ISO-8859-1 leaves it as controls. Evidence
# that this is what happened and not something else: every such character in
# the corpus maps back to punctuation a press release would actually contain -
# bullet (174x), (tm) (103x), German quotes (100x), en dash (33x), apostrophe.
C1_RE = re.compile("[" + chr(0x80) + "-" + chr(0x9F) + "]")

# Mojibake: UTF-8 bytes read as some 8-bit charset. The lead byte of a UTF-8
# sequence lands on one of these, and the continuation bytes on another high
# character - 0xC3/0xC2 read as cp1252 give A-tilde/A-circumflex, the same byte
# read as cp1258 gives A-breve, and 0xE2 starts the a-EUR-something family that
# punctuation turns into.
_MOJIBAKE_LEADS = "ÃÂĂâ"
MOJIBAKE_RE = re.compile(
    "[" + _MOJIBAKE_LEADS + "][" + chr(0x80) + "-" + chr(0x17F) + chr(0x20AC) + "]"
)

# Which 8-bit charset the text may have been read as, in the order worth trying.
# Two actually occurred on terratec.net's German pages: cp1252 and - not a typo -
# **cp1258**, Vietnamese, which is what tells 'FĂ¼r' apart from cp1250's
# otherwise identical A-breve, since the same rows carry U+00BC and cp1250 has no
# byte for it. The rest are cheap to test.
#
# mac-roman is deliberately absent: it re-encodes this text happily, its output
# carries no damage markers, and the text it produces is simply wrong. The
# codepages that remain are the Windows/ISO family, which coincide exactly on the
# byte ranges UTF-8 uses, so where several qualify they agree character for
# character - which is what the agreement check below relies on.
_MOJIBAKE_CODECS = ("cp1252", "cp1258", "cp1250", "cp1254", "cp1257", "latin-1")

# Built from the codec, not typed out. The five bytes cp1252 leaves undefined
# (0x81/0x8D/0x8F/0x90/0x9D) are left ALONE rather than mapped to U+FFFD: one
# midiman row carries three 0x81 bytes next to already-correct typography, so
# replacing them would destroy a character in a row that has no wrong decode to
# undo. Left in place they keep the row detectable and repairable later.
_C1_TRANSLATION = {}
for _code in range(0x80, 0xA0):
    try:
        _C1_TRANSLATION[_code] = bytes([_code]).decode(FALLBACK)
    except UnicodeDecodeError:
        pass


def undo_c1(text: str) -> str:
    """Re-read every C1 control character as the cp1252 byte it really was,
    leaving the five cp1252 does not define untouched.

    Per character, deliberately. The obvious whole-string version -
    text.encode("latin-1").decode("cp1252") - raises on any character above
    U+00FF, and the rows that need this are exactly the mixed ones: 21 of
    `creative`'s 23 damaged rows hold a stray 0x95 bullet *and* correctly
    decoded typography above U+00FF, so a whole-string encode fails on the
    whole file it is supposed to save.
    """
    return text.translate(_C1_TRANSLATION)


def undo_mojibake(text: str):
    """Re-encode `text` with the charset it was wrongly read as, and decode
    that as the UTF-8 it always was. Returns (fixed, codec) or None.

    A candidate counts only if the round trip succeeds *and* leaves no damage
    markers behind, so a codec that merely happens to encode the string cannot
    win. When several candidates qualify they must agree character for
    character - they usually do, because they differ only outside the byte
    ranges UTF-8 uses - and a disagreement returns None rather than a guess.
    """
    results = {}
    for codec in _MOJIBAKE_CODECS:
        try:
            out = text.encode(codec).decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            continue
        if MOJIBAKE_RE.search(out) or C1_RE.search(out) or "�" in out:
            continue
        results[codec] = out

    if not results:
        return None
    distinct = set(results.values())
    if len(distinct) > 1:
        return None
    codec = next(iter(results))
    return results[codec], codec


def repair_text(text: str):
    """Undo whatever wrong decode this text carries. Returns (fixed, method)
    or None when there is nothing to do - or nothing safe to do.

    Idempotent: run it on repaired text and it reports None, which is what lets
    this sit in the write path at all - every store and every upgrade calls it,
    and a row that was already repaired costs a regex match. Mojibake is
    undone before C1 characters because a mojibake round trip needs the string
    exactly as stored; in this corpus the two shapes never co-occur in one row
    anyway.
    """
    methods = []
    out = text

    if MOJIBAKE_RE.search(out):
        fixed = undo_mojibake(out)
        if fixed is None:
            return None
        out, codec = fixed
        methods.append(f"utf8-read-as-{codec}")

    if C1_RE.search(out):
        out = undo_c1(out)
        methods.append("cp1252-read-as-latin1")

    if not methods or out == text:
        return None
    # Never trade damage for a lost character: a U+FFFD the input did not have
    # means the repair itself dropped something.
    if out.count("�") > text.count("�"):
        return None
    return out, "+".join(methods)


def repaired(text):
    """A value with a wrong decode undone, or the value unchanged.

    Applied at the write rather than by a pass afterwards, because part of this
    damage is *upstream* and recurs on every refetch: a server serving valid
    UTF-8 for a C1 control where a (tm) belongs is still a control character
    after a correct decode. A repair pass would need somebody to remember to
    re-run it.

    `repair_text` returns None unless the inversion is unambiguous, so "leave it
    alone" is the default, and repairs are counted in REPAIRS so a run that
    fixes something says so.

    Only ever rewrites characters in 0x80-0x9F and the mojibake lead set, so a
    tag cannot be touched. A body that has markup is repaired one level up, in
    richtext.extract(), where the repair happens *before* to_text() and the
    invariant `body == to_text(body_html)` therefore holds by construction.
    """
    if not text:
        return text
    fixed = repair_text(text)
    if not fixed:
        return text
    REPAIRS[fixed[1]] += 1
    return fixed[0]
