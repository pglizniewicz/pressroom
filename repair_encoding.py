#!/usr/bin/env python3
"""Repair: undo wrong decodes already stored in `releases`.

Written as a one-off and no longer one. A refetch reintroduces the damage,
because some of it is upstream: ir.amd.com serves `\xc2\xc2` - sorry, `\xc2\x99`,
i.e. valid UTF-8 for the C1 control U+0099 - where it means (tm). Decoding
that correctly still yields a control character, so **re-run this after any
pass that rewrites bodies from the network**; `backfill_body_html.py` brought
10 amd rows and 1 creative row back on 2026-08-21.

157 rows across 8 sources were stored through a wrong charset decision, in two
shapes that need two different reversals (both in encoding.py):

  cp1252 bytes read as ISO-8859-1 -> C1 control characters, 132 rows in
    terratec_de (30), terratec_pressde (26), creative (23), terratec_pressen
    (22), terratec (20), amd (10), midiman_net_pressdb (1). The characters map
    straight back to press-release punctuation: bullet 174x, (tm) 103x, German
    quotes 100x, en dash 33x.
  UTF-8 read as an 8-bit charset -> mojibake, 25 rows, all terratec_new_de,
    in two variants: cp1252 ('FÃ¼hrungsduo') and cp1258 ('FĂ¼r') - Vietnamese,
    which is a guess chardet is on record for on this corpus.

Runs offline. Unlike repair_couk_news_encoding.py this cannot rebuild from
page_cache, because the captures that produced these rows predate the cache -
so the repair is text-level, which is sound here only because both transforms
are byte-exact inversions and encoding.repair_text refuses anything ambiguous.

Every run cross-checks against page_cache where a row's capture happens to
be there, decoding those bytes the way the fixed scrapers now do.
--sample-refetch does the same against freshly fetched captures, one per damage
shape, which also seeds the cache for next time.

Usage:
  python repair_encoding.py --dry-run            # report, write nothing
  python repair_encoding.py --dry-run --source creative
  python repair_encoding.py --dry-run --sample-refetch 8   # network spot-check
  python repair_encoding.py
"""

import argparse
import collections
import html
import re

import db
import encoding

# Only a 14-digit detail_id is a Wayback timestamp. The Q4 sources (amd, intel)
# and the live Creative scrape store that platform's own numeric detail id, and
# pasting one into a /web/<ts>id_/ URL does not fail - archive.org helpfully
# serves the nearest capture of *something*, which is how this check first
# "compared" a row against an unrelated page.
_TS_LEN = 14

# Crude on purpose: this only has to make a capture's prose greppable, not
# parse it - bs4 stays out of here so the offline path needs no third-party
# import (and db.py's stdlib-only rule keeps company).
_TAG_RE = re.compile(r"<[^>]+>")
_NON_ASCII_WORD = re.compile(r"\S*[^\x00-\x7f]\S*")

# Rows are read once, up front: the whole corpus is ~30 MB of text and the
# damaged fraction is not known without looking at every body.
_ROWS_SQL = """
    SELECT id, source, url, detail_id, title, body, body_html
      FROM releases
     {where}
     ORDER BY source, id
"""


def damaged(text: str) -> bool:
    return bool(text) and bool(encoding.C1_RE.search(text) or encoding.MOJIBAKE_RE.search(text))


def _is_timestamp(detail_id) -> bool:
    return bool(detail_id) and str(detail_id).isdigit() and len(str(detail_id)) == _TS_LEN


def cached_bytes(conn, detail_id, url):
    """The original capture's bytes, if this row's snapshot happens to be in
    page_cache. Keyed the way wayback.fetch_snapshot stores it: the id_
    (raw, un-rewritten) form of the Wayback URL."""
    if not _is_timestamp(detail_id):
        return None
    for scheme in ("https", "http"):
        row = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?",
            (f"{scheme}://web.archive.org/web/{detail_id}id_/{url}",),
        ).fetchone()
        if row:
            return row[0]
    return None


def plan_row(rid, source, url, detail_id, title, body, body_html=None):
    """What this row needs, or None. Each field is judged separately - plenty
    of rows carry the damage in only one of them.

    body_html is repaired alongside body rather than left to be re-derived,
    because the same character-level inversion applies to both and the two
    must agree: the browser reads body_html, FTS reads body."""
    fixes = {}
    skipped = []
    for field, text in (("title", title), ("body", body), ("body_html", body_html)):
        if not damaged(text):
            continue
        got = encoding.repair_text(text)
        if got is None:
            # Ambiguous or not invertible: report it, never guess. Nothing is
            # written for this field, so a later rerun can still fix it.
            undefined = sorted({hex(ord(c)) for c in text
                                if encoding.C1_RE.match(c)
                                and ord(c) not in encoding._C1_TRANSLATION})
            why = (f"cp1252 nie definiuje {', '.join(undefined)}" if undefined
                   else "brak jednoznacznego odwrócenia")
            skipped.append(f"{field}: {why}")
            continue
        fixes[field] = got
    if not fixes and not skipped:
        return None
    return {"id": rid, "source": source, "url": url, "detail_id": detail_id,
            "fixes": fixes, "skipped": skipped}


def show(item, before) -> None:
    for field, (fixed, method) in item["fixes"].items():
        print(f"  #{item['id']} {item['source']} {field} [{method}]")
        print(f"      {before[field][:100]!r}")
        print(f"   -> {fixed[:100]!r}")
    for reason in item["skipped"]:
        print(f"  #{item['id']} {item['source']} POMINIĘTE {reason}")


def cache_check(conn, items, refetched=None) -> None:
    """Cross-check the text repair against original capture bytes.

    Bytes are ground truth. What gets compared is not whole strings - the
    stored text went through get_text(), so tags, entities and whitespace are
    long gone - but the *repaired characters themselves*: every word carrying a
    non-ASCII character is looked up in the capture, tags stripped and entities
    unescaped. That is exactly the claim under test ("this row really said
    Fuehrungsduo with an umlaut"), and a disagreement prints the missing words
    instead of a verdict.
    """
    checked = agree = disagree = 0
    for item in items:
        content = (refetched or {}).get(item["id"]) or cached_bytes(
            conn, item["detail_id"], item["url"])
        if content is None:
            continue

        # How the fixed scrapers read these sources: terratec.net claims UTF-8,
        # the older pages declare nothing and are cp1252.
        if item["source"] == "terratec_new_de":
            text = encoding.decode_html(content)
        else:
            text = content.decode("cp1252", errors="replace")
        haystack = " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())

        words = []
        for fixed, _ in item["fixes"].values():
            for word in _NON_ASCII_WORD.findall(fixed):
                word = word.strip(".,;:()[]\"'")
                if len(word) > 3 and word not in words:
                    words.append(word)
        words = words[:6]
        if not words:
            continue

        checked += 1
        missing = [w for w in words if w not in haystack]
        if not missing:
            agree += 1
        else:
            disagree += 1
            print(f"  ROZBIEŻNOŚĆ #{item['id']} {item['source']}: "
                  f"brak w bajtach capture: {missing}")
    print(f"\nkontrola z bajtami: {checked} wierszy sprawdzonych, "
          f"{agree} zgodnych, {disagree} rozbieżnych")


def refetch_sample(conn, items, n: int) -> dict:
    """Fetch up to `n` captures from archive.org, one per (source, method), so
    the check covers every distinct damage shape rather than n rows of one.

    Imported here, not at module top: the offline path must not need requests.
    """
    import requests

    import wayback

    by_shape = collections.OrderedDict()
    for item in items:
        methods = {m for _, m in item["fixes"].values()}
        shape = (item["source"], tuple(sorted(methods)))
        if shape not in by_shape and _is_timestamp(item["detail_id"]):
            by_shape[shape] = item
    picked = list(by_shape.values())[:n]
    skipped_shapes = sorted({i["source"] for i in items
                             if not _is_timestamp(i["detail_id"])})
    print(f"\nrefetch {len(picked)} captures (po jednym na kształt uszkodzenia)")
    if skipped_shapes:
        # Said out loud rather than left as a silent gap in the evidence.
        print(f"  bez kontroli z bajtami (detail_id nie jest timestampem "
              f"Wayback, więc capture nie istnieje): {', '.join(skipped_shapes)}")

    out = {}
    session = requests.Session()
    for item in picked:
        snap = f"https://web.archive.org/web/{item['detail_id']}id_/{item['url']}"
        content = wayback.fetch_snapshot(conn, session, snap, timeout=20)
        state = f"{len(content)} B" if content else "nie udało się"
        print(f"  #{item['id']} {item['source']} {state}")
        if content:
            out[item["id"]] = content
    return out


def repair(dry_run: bool, source: str = None, sample: int = 0) -> None:
    conn = db.connect()
    where = "WHERE source = ?" if source else ""
    rows = conn.execute(_ROWS_SQL.format(where=where),
                        (source,) if source else ()).fetchall()

    originals = {r[0]: {"title": r[4] or "", "body": r[5] or "",
                        "body_html": r[6] or ""} for r in rows}
    items = [i for i in (plan_row(*r) for r in rows) if i]
    print(f"przejrzano {len(rows)} wierszy, uszkodzonych {len(items)}\n")
    for item in items:
        show(item, originals[item["id"]])

    per_source = collections.Counter()
    per_method = collections.Counter()
    fields = collections.Counter()
    for item in items:
        per_source[item["source"]] += 1
        for field, (_, method) in item["fixes"].items():
            per_method[method] += 1
            fields[field] += 1

    print("\nwedług źródła:")
    for src, n in per_source.most_common():
        print(f"  {src:22} {n}")
    print("według metody:")
    for method, n in per_method.most_common():
        print(f"  {method:28} {n} pól")

    refetched = refetch_sample(conn, items, sample) if sample else None
    cache_check(conn, items, refetched)

    skipped = sum(len(i["skipped"]) for i in items)
    if not dry_run:
        written = 0
        for item in items:
            title = item["fixes"].get("title", (None,))[0]
            body = item["fixes"].get("body", (None,))[0]
            body_html = item["fixes"].get("body_html", (None,))[0]
            if title is None and body is None and body_html is None:
                continue
            # upgrade_release goes through releases_au, so the FTS index follows
            # every write. No bulk UPDATE, no 'rebuild'.
            if db.upgrade_release(conn, item["url"], title=title, body=body,
                                  body_html=body_html):
                written += 1
        print(f"\nnaprawiono {written} wierszy ({fields['title']} tytułów, "
              f"{fields['body']} treści, {fields['body_html']} HTML), "
              f"pominięto {skipped} pól")
    else:
        print(f"\n[dry-run] naprawiłbym {len(items)} wierszy "
              f"({fields['title']} tytułów, {fields['body']} treści, "
              f"{fields['body_html']} HTML), pominiętych pól: {skipped}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--source", help="Limit to one source tag")
    parser.add_argument("--sample-refetch", type=int, default=0, metavar="N",
                        help="Also fetch N captures from archive.org and check against them")
    args = parser.parse_args()
    repair(dry_run=args.dry_run, source=args.source, sample=args.sample_refetch)
