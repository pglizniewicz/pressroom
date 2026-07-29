#!/usr/bin/env python3
"""Turning archived bytes into text when the page's declared charset is a lie.

The specific failure this exists for: a CMS serves `charset=utf-8` and is
genuinely UTF-8 almost everywhere, but a handful of bytes were pasted straight
out of Word and never converted, so the document is *not* valid UTF-8. Three
stray 0x92 bytes in one boilerplate footer sentence were enough to do this to
midiman.co.uk's 40 KB news listing:

  - a strict UTF-8 decode fails outright;
  - BeautifulSoup then falls back to chardet, which guessed windows-1250;
  - so every *correct* UTF-8 sequence in the file decoded as mojibake -
    e.g. e2 80 9c ("u201c) came out as "â€ś", corrupting 14 stored titles
    over one wrongly-pasted apostrophe.

Decoding UTF-8 first and falling back per-byte fixes both halves at once: the
valid sequences stay valid, and the strays are read as the cp1252 they are.
Nothing in the process is a guess, which is the point - chardet's verdict on
these files is unreliable (elsewhere in wayback_cache it landed on
windows-1258 for two pages, which is Vietnamese).

This is NOT a universal decoder. Use it for pages that claim UTF-8; keep
`from_encoding="cp1252"` where a source is known to be wholly pre-UTF-8 (the
2001-era GoLive pages, midiman.de). On such a page two adjacent high bytes can
coincidentally form a valid UTF-8 sequence and would be honoured as one -
harmless for a stray byte among ASCII, wrong for prose full of accents.
"""

import codecs

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
    return err.object[err.start:err.end].decode(FALLBACK, errors="replace"), err.end


codecs.register_error("utf8_cp1252", _utf8_cp1252)


def decode_html(content: bytes) -> str:
    """Decode `content` as UTF-8, reading any byte that isn't valid UTF-8 as
    cp1252 instead.

    Pass the result to BeautifulSoup as a str: given text rather than bytes it
    skips UnicodeDammit entirely, so this decision is final and no sniffing can
    override it.
    """
    return content.decode("utf-8", errors="utf8_cp1252")
