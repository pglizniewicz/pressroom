"""Does a parse reproduce a stored body? The verdicts `pressroom-verify-body-origin`
hands out, and how an origin's class is read off its address.

How an origin's address was established is not stored - it is derivable, which
is why `body_origin` has two columns and no score:

  computed    origin_url is exactly `web/<detail_id>id_/<url>`, i.e. the
              address the row implies. Nothing was checked when it was written.
  located     the row's url is a .pdf/.doc and its bytes were found under that
              path, possibly on a sibling domain of the same scraper.
  inferred    neither - the page had to be found by looking for the body's text
              among the captures sharing that timestamp.

What counts as reproduced, and why not stricter:

  exact              the parser's text equals the stored body
  typography only    equal once whitespace and bullets are dropped - to_text()
                     numbers an <ol> `1.` where the PDF wrote `1)`, and the two
                     routes place a superscript differently
  body inside parse  the stored body is a prefix/substring of the parse: a
                     teaser waiting for the recovery pass, not a wrong pairing

Not a score - the fraction of text probes that hit is neither necessary nor
sufficient, since a body can probe low and be right while an attachment row
probes perfectly against the original server's error page.
"""

from pressroom.converter.control import conversion


def squash(text: str) -> str:
    """Every non-whitespace character, bullets dropped."""
    return "".join((text or "").replace("•", "").split())


def verdict(body: str, candidates: list | None) -> str:
    if candidates is None:
        return "no parser for this source"
    if not candidates:
        return "PARSER FOUND NOTHING"
    if any(c == body for c in candidates):
        return "exact"
    if any(squash(c) == squash(body) for c in candidates):
        return "typography only"
    if any(squash(body) and squash(body) in squash(c) for c in candidates):
        return "body inside parse (teaser)"
    if any(squash(c) and squash(c) in squash(body) for c in candidates):
        return "PARSE INSIDE BODY"
    return "MISMATCH"


def attachment_verdict(body: str, body_html, content: bytes) -> str:
    """Attachments have no HTML parser - the extractor is the parser."""
    if body_html:
        text, _html, _kind = conversion.to_richtext(content)
    else:
        text, _kind = conversion.plain_text(content)
    if not text:
        return "EXTRACTOR FOUND NOTHING"
    if text == body:
        return "exact"
    if squash(text) == squash(body):
        return "typography only"
    if squash(body) and squash(body) in squash(text):
        return "body inside parse (teaser)"
    return "MISMATCH"


def origin_class(url: str, detail_id, origin_url: str) -> str:
    """'computed' | 'located' | 'inferred' - how this address was established.

    Derived rather than stored, which is why body_origin has two columns and no
    third saying how each entry was arrived at: the three cases are
    distinguishable from the address itself.
    """
    if origin_url == f"https://web.archive.org/web/{detail_id}id_/{url}":
        return "computed"
    if url.lower().endswith((".pdf", ".doc")):
        return "located"
    return "inferred"
