"""Where an article is in a page: the smallest element whose text covers a
stored body, and a name for that element a selector could key on.

A parser needs a selector, and picking one from a single capture is how you get
a parser that works on the page you looked at and silently stores navigation
for the rest. `pressroom-calibrate-containers` reports this measure over every
cached capture of a source; the measure is here, so the boundary walks the cache
and prints, and decides nothing.
"""

from bs4 import BeautifulSoup


def norm(text: str) -> str:
    return " ".join((text or "").split())


def describe(tag) -> str:
    """A stable, greppable shape for one element: tag plus the attributes that
    a selector could actually key on. Keeps `width`/`valign` -
    on 1998-era table layouts those are the only distinguishing marks there
    are, and dropping them would collapse every <td> into one bucket."""
    bits = []
    for a in ("id", "class", "valign", "width", "align", "border"):
        v = tag.get(a)
        if v:
            bits.append(f'{a}="{" ".join(v) if isinstance(v, list) else v}"')
    return f"{tag.name}[{' '.join(bits)}]" if bits else tag.name


def ancestry(tag, depth: int = 3) -> str:
    return " < ".join(describe(a) for a in list(tag.parents)[:depth])


def best_container(soup, body: str):
    """Smallest element whose text contains the head of the stored body.

    Anchored on a slice from the MIDDLE of the stored body, not its start. The
    stored body is the *old* extraction, and for the whole-page parsers it
    opens with the site navigation - so a prefix anchor matches only <html>
    and every source calibrates to "the whole document", which is the answer
    we already have and the one we are trying to replace. The middle of a
    press release is article prose on every template here.
    """
    flat = norm(body)
    mid = len(flat) // 2
    key = flat[max(0, mid - 40) : mid + 40]
    if len(key) < 40:
        return None, 0
    # Smallest element that both contains the anchor AND holds most of the
    # body. Without the coverage floor the winner is whichever <p> the anchor
    # happens to sit in - true, useless, and not a container.
    floor = 0.75 * len(flat)
    best = fallback = None
    for tag in soup.find_all(True):
        txt = norm(tag.get_text(" ", strip=True))
        if key not in txt:
            continue
        if fallback is None or len(txt) > fallback[1]:
            fallback = (tag, len(txt))
        if len(txt) >= floor and (best is None or len(txt) < best[1]):
            best = (tag, len(txt))
    return best or fallback or (None, 0)


def locate(content: bytes, body: str):
    """`best_container` over a capture's bytes. cp1252 stated, never sniffed:
    every source this runs on is pre-UTF-8 and declares no charset (README's
    Encoding rule)."""
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    return best_container(soup, body)
