#!/usr/bin/env python3
"""Prove that no stored text carries a wrong decode any more - and that the two
places which detect one still agree.

The repair itself moved into the write path on 2026-08-25:
`richtext.extract()` undoes the damage on the markup *before* rendering the text
from it (so `body == to_text(body_html)` holds by construction), and
`db.store_release`/`upgrade_release` do the same for a title and for a body with
no markup twin. What used to be `repair_encoding.py` - a pass CLAUDE.md told you
to re-run after anything that refetched - therefore has nothing left to do, and
what remains of it is a *report*, which is this file.

Three claims, all read-only:

1. **Nothing repairable is stored.** A row whose text `encoding.repair_text()`
   can still fix means something wrote a body around `db.py`, or a new damage
   shape appeared. The second is the interesting one.
2. **The two detectors agree.** `encoding.C1_RE`/`MOJIBAKE_RE` drive the repair;
   `db._MOJIBAKE_SQL`/`_C1_SQL` drive the browser's audit flag, because SQLite
   has no regex. They have drifted before - the SQL named five C1 codepoints by
   hand while the regex matched the whole 0x80-0x9F range, so the audit view
   reported 12 damaged rows where the repair found 35.
3. **The known residue is exactly one row.** `midiman_net_pressdb` #4978 carries
   three 0x81 bytes, and cp1252 does not define that byte - there is nothing to
   decode it *to*, so `repair_text` refuses it by design and the audit view
   shows it forever.

Bytes are the ground truth for claim 1, so `--bytes` cross-checks any damaged
field against the original capture in `page_cache`: every word carrying a
non-ASCII character is looked up in the capture with tags stripped, which says
whether the database or the page is wrong. That distinction is not academic -
ir.amd.com really does serve `\\xc2\\x99` where a trademark sign belongs, and for
those rows the database is *better* than the page.

Usage:
  python verify_encoding.py
  python verify_encoding.py --source midiman_net_pressdb
  python verify_encoding.py --bytes          # cross-check against page_cache
"""

import argparse
import collections
import re

import db
import encoding

_TAG_RE = re.compile(r"<[^>]+>")
_NON_ASCII_WORD = re.compile(r"\S*[^\x00-\x7f]\S*")

# The expected residue: a row nothing can repair, named so a report that grows
# past it is obviously a change and not the status quo.
KNOWN_UNFIXABLE = {4978}


def damaged(text: str) -> bool:
    return bool(text) and bool(encoding.C1_RE.search(text)
                               or encoding.MOJIBAKE_RE.search(text))


def undefined_bytes(text: str) -> list:
    """The C1 codepoints in `text` that cp1252 does not define - the reason a
    repair is refused rather than guessed."""
    return sorted({hex(ord(c)) for c in text
                   if encoding.C1_RE.match(c)
                   and ord(c) not in encoding._C1_TRANSLATION})


def capture_words(conn, url: str) -> str:
    """The row's capture bytes as text, tags stripped - or "" if not cached.
    Read through body_origin, which is where the address a body came from lives."""
    row = conn.execute(
        "SELECT p.content FROM body_origin o JOIN page_cache p ON p.url = o.origin_url "
        "WHERE o.url = ?", (url,)).fetchone()
    if not row:
        return ""
    return _TAG_RE.sub(" ", encoding.decode_html(row[0]))


def run(source: str = None, check_bytes: bool = False) -> None:
    conn = db.connect_ro()
    sql = ("SELECT id, source, url, title, body, body_html FROM releases "
           + ("WHERE source = ? " if source else "") + "ORDER BY source, id")
    rows = conn.execute(sql, (source,) if source else ()).fetchall()

    per_source = collections.Counter()
    repairable, refused = [], []
    for rid, src, url, title, body, body_html in rows:
        hit = False
        for field, text in (("title", title), ("body", body), ("body_html", body_html)):
            if not damaged(text):
                continue
            hit = True
            got = encoding.repair_text(text)
            if got is None:
                refused.append((rid, src, field, undefined_bytes(text) or ["ambiguous"]))
            else:
                repairable.append((rid, src, field, got[1], text, got[0]))
        if hit:
            per_source[src] += 1

    print(f"[encoding] {len(rows)} wierszy przejrzanych, {sum(per_source.values())} "
          f"z uszkodzonym polem")
    for src, n in per_source.most_common():
        print(f"  {src:26} {n}")

    print(f"\n1. do naprawy: {len(repairable)} (musi byc 0 - sciezka zapisu "
          f"naprawia w locie)")
    for rid, src, field, method, before, after in repairable[:10]:
        print(f"  #{rid} {src} {field} [{method}]")
        print(f"      {before[:90]!r}")
        print(f"   -> {after[:90]!r}")

    print(f"\n2. odrzucone przez repair_text: {len(refused)}")
    for rid, src, field, why in refused:
        flag = "" if rid in KNOWN_UNFIXABLE else "  <-- NOWE"
        print(f"  #{rid} {src} {field}: {', '.join(why)}{flag}")

    # The browser's audit flag is the same claim in SQL. Comparing the counts is
    # what catches the two detectors drifting apart.
    audit = db.quality_counts(conn)["mojibake"]
    regex_rows = sum(per_source.values())
    verdict = "zgodne" if audit == regex_rows else "ROZJECHANE"
    print(f"\n3. detektory: regex {regex_rows} wierszy, SQL audytu {audit} -> {verdict}")

    if check_bytes:
        print("\n4. kontrola z bajtami capture'a:")
        checked = agree = 0
        for rid, src, field, _method, before, _after in repairable + [
                (r, s, f, "", "", "") for r, s, f, _ in refused]:
            url = conn.execute("SELECT url FROM releases WHERE id = ?", (rid,)).fetchone()[0]
            page = capture_words(conn, url)
            if not page:
                continue
            checked += 1
            words = _NON_ASCII_WORD.findall(before or "")
            if words and any(w in page for w in words):
                agree += 1
                print(f"  #{rid} {field}: strona nosi ten sam znak - uszkodzenie "
                      f"jest po stronie zrodla")
        print(f"  {checked} sprawdzonych, {agree} gdzie strona zgadza sie z baza")

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", help="limit to one source tag")
    p.add_argument("--bytes", dest="check_bytes", action="store_true",
                   help="cross-check damaged fields against the cached capture")
    args = p.parse_args()
    run(args.source, args.check_bytes)
