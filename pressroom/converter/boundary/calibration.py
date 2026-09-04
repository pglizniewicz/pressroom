"""Review the attachment converters against the whole cached corpus.

Same job as `text/boundary/container_calibration.py` does for DOM containers,
and for the same reason: a threshold picked from one document is a converter
that works on the document you looked at. Read-only - its only output file is
the review page.

It answers two questions, because one of them cannot be answered by numbers:

  1. **Metrics**, printed. How many documents convert, how much of the text
     is kept, and how often each known defect fires.
  2. **Full texts**, written to an HTML page. Four versions of each document
     side by side: what the database holds now, the text-only extraction, the
     structured conversion rendered, and its `body_html` source. The metric
     report once said "100% of words kept" about a conversion that had put the
     headline *after* the footer.

Bytes come from `page_cache` only, mirror domains included: most rows with no
cached capture of their own can still be read from a sibling's bytes. The
measures are `control/quality.py`'s; this walks, prints and writes the page.

Usage:
  pressroom-calibrate-converters                     # metrics + 12-document page
  pressroom-calibrate-converters --preview all       # every document on the page
  pressroom-calibrate-converters --preview 0         # metrics only
  pressroom-calibrate-converters --out ~/review.html
"""

import argparse
import collections
import html as html_mod
import statistics

from pressroom.converter.control import conversion, quality
from pressroom.database.control import connection


def documents(conn) -> dict[str, tuple[str, list[tuple[int, str, str, str]]]]:
    """filename -> (page_cache key, [(row id, source, url, body)]).

    One entry per distinct attachment file, so a document mirrored on three
    domains is reviewed once and its rows listed together.
    """
    cached = {}
    for key, head in conn.execute(
        "SELECT url, substr(content, 1, 8) FROM page_cache "
        "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc'"
    ):
        if "id_/" not in key:
            continue
        name = conversion.attachment_name(key)
        # Prefer bytes that actually are the attachment: several keys can share
        # a filename - the same document under two directory schemes, plus the
        # original server's soft-404 - and taking whichever came first reviewed
        # an error page and reported 0% retention against the real PDF.
        if name not in cached or conversion.is_attachment(bytes(head)):
            cached[name] = key
    rows = collections.defaultdict(list)
    for rid, source, url, body in conn.execute(
        "SELECT id, source, url, COALESCE(body, '') FROM releases "
        "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc' ORDER BY id"
    ):
        rows[conversion.attachment_name(url)].append((rid, source, url, body))
    return {name: (cached[name], rs) for name, rs in rows.items() if name in cached}


def review(limit_preview, out_path: str) -> None:
    conn = connection.connect_ro()
    docs = documents(conn)
    print(
        f"[calibrate] {len(docs)} distinct attachment files with cached bytes",
        flush=True,
    )

    verdicts = collections.Counter()
    retention, defects, cards, decisions = [], collections.Counter(), [], []
    for name in sorted(docs):
        key, rows = docs[name]
        content = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?", (key,)
        ).fetchone()[0]
        text, kind = conversion.plain_text(content)
        if not kind:
            verdicts["not an attachment (soft-404)"] += 1
            continue
        body, body_html, _ = conversion.to_richtext(content)
        if not body_html:
            # .doc has no structured route by policy, and a soft-404 is not an
            # attachment at all - both are expected. A *PDF* arriving here is
            # not: it means the converter failed, and that is a decision for a
            # person, not a silent fall back to the text route.
            if kind == "pdf":
                decisions.append(
                    (
                        name,
                        [r[0] for r in rows],
                        "no structured output",
                        "look at the document: use its text, or fix the converter",
                    )
                )
            verdicts[f"{kind}: text route"] += 1
            cards.append(
                (
                    name,
                    kind,
                    key,
                    rows,
                    text,
                    body,
                    body_html,
                    0.0,
                    quality.artefacts(text),
                    (0, 0, 0),
                    False,
                )
            )
            continue
        tw, sw = len(text.split()), len(body.split())
        ratio = sw / tw if tw else 0.0
        at, ab = quality.artefacts(text), quality.artefacts(body)
        cleaner = sum(ab) < sum(at)
        ok = sw > 50 and (ratio >= quality.MIN_RETENTION or cleaner)
        verdicts[f"{kind}: {'convert' if ok else 'keep text'}"] += 1
        retention.append((ratio, name))
        if (
            quality.top_level_blocks(body_html) <= 1
            and len(text.split("\n")) >= quality.DEGENERATE_MIN_LINES
        ):
            decisions.append(
                (
                    name,
                    [r[0] for r in rows],
                    "one block for the whole document",
                    "paragraph breaks were lost - check the gap rule",
                )
            )
        elif ratio < quality.REVIEW_RETENTION:
            decisions.append(
                (
                    name,
                    [r[0] for r in rows],
                    f"retention {ratio:.1%}",
                    "words missing against the text route",
                )
            )

        h3_at = quality.first_heading_index(body_html)
        if kind == "pdf":
            rotated = quality.rotated_blocks(content)
            defects["pdf: rotated blocks dropped"] += rotated
            defects["pdf: docs with a rotated block"] += 1 if rotated else 0
        if h3_at > 3:
            defects["first h3 later than element 3"] += 1
        elif h3_at == -1:
            defects["no h3 at all"] += 1
        defects["furniture repeats left in body"] += quality.furniture(body_html)
        defects["rejoined hyphenations"] += len(quality.HYPHEN_RE.findall(body))
        cards.append((name, kind, key, rows, text, body, body_html, ratio, at, ab, ok))

    print(
        "\n=== verdict under the writing gate "
        f"(retention >= {quality.MIN_RETENTION:.0%}, or fewer interleaving artefacts)"
    )
    for k, v in sorted(verdicts.items()):
        print(f"  {v:5}  {k}")

    retention.sort()
    print("\n=== word retention of the structured route")
    edges = [(0, 0.5), (0.5, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.0), (1.0, 99)]
    for lo, hi in edges:
        n = sum(1 for r, _ in retention if lo <= r < hi)
        label = f"{lo:.2f}-{hi:.2f}" if hi < 9 else f">= {lo:.2f}"
        print(f"  {label:>12}: {n}")
    if retention:
        print(
            f"  median {statistics.median(r for r, _ in retention):.3f}, "
            f"worst {retention[0][0]:.3f} ({retention[0][1]})"
        )

    print("\n=== decisions needed (a PDF must never fall back silently)")
    if decisions:
        for name, ids, what, suggestion in decisions:
            print(f"  {str(ids):26} {name:44} {what}")
            print(f"  {'':26} -> {suggestion}")
    else:
        print(
            "  none - every cached PDF converted, none degenerate, "
            f"none below {quality.REVIEW_RETENTION:.0%} retention"
        )

    print(
        "\n=== known defects, measured (thresholds live in conversion.py and quality.py)"
    )
    for k in sorted(defects):
        print(f"  {defects[k]:5}  {k}")

    if limit_preview:
        chosen = cards if limit_preview == "all" else cards[: int(limit_preview)]
        write_preview(chosen, out_path)
        print(f"\nreview page: {out_path} ({len(chosen)} documents)")
        print("  open it with file:// - a flatpak browser cannot read /tmp on the host")
    conn.close()


_PAGE_HEAD = """<!doctype html><meta charset=utf-8>
<title>pressroom: attachment extraction review</title>
<style>
 :root{--bg:#fbfaf7;--fg:#1c1a17;--line:#d8d4cb;--muted:#5d5a53;--card:#fff;--warn:#8c1d18}
 @media(prefers-color-scheme:dark){:root{--bg:#17161a;--fg:#ecebe6;--line:#3a3941;
   --muted:#aaa69e;--card:#202025;--warn:#ff9d94}}
 *{box-sizing:border-box}
 body{margin:0;padding:1.5rem;background:var(--bg);color:var(--fg);
      font:16px/1.5 system-ui,-apple-system,sans-serif}
 h1{font-size:1.2rem} h2{font-size:1.05rem;margin:2.5rem 0 .2rem;
    border-bottom:2px solid var(--line);padding-bottom:.3rem}
 h3.v{font:600 .75rem/1.4 system-ui,sans-serif;text-transform:uppercase;
      letter-spacing:.06em;color:var(--muted);margin:0 0 .5rem}
 .grid{display:grid;gap:1rem;align-items:start;
       grid-template-columns:repeat(auto-fit,minmax(27rem,1fr))}
 .card{background:var(--card);border:1px solid var(--line);border-radius:6px;
       padding:.75rem;overflow:auto;max-height:34rem}
 pre{margin:0;white-space:pre-wrap;font:12px/1.45 ui-monospace,monospace}
 .rendered p{margin:0 0 .7rem} .rendered h3{font-size:1rem;margin:.9rem 0 .4rem}
 .rendered ul{margin:0 0 .7rem 1.2rem} .rendered li{margin-bottom:.3rem}
 .src{font:11px/1.45 ui-monospace,monospace;color:var(--muted);white-space:pre-wrap;
      word-break:break-word}
 .meta{font-size:.8rem;color:var(--muted);margin:.25rem 0 .9rem}
 .warn{color:var(--warn);font-weight:600}
</style>
<h1>Attachment extraction, four versions of each document</h1>
<p class=meta><b>A</b> what <code>releases.body</code> holds now &middot;
<b>B</b> the text route (<code>pdftotext -layout</code> / <code>antiword -t</code>) &middot;
<b>C</b> the structured route rendered &middot; <b>D</b> its <code>body_html</code>.
Bytes are read from <code>page_cache</code>; nothing here is written to the database.</p>
"""


def write_preview(cards, out_path: str) -> None:
    esc = html_mod.escape
    parts = [_PAGE_HEAD]
    for name, kind, key, rows, text, body, body_html, ratio, at, ab, ok in cards:
        ids = ", ".join(f"#{rid} ({src})" for rid, src, _u, _b in rows)
        stored = rows[0][3]
        tags = {t: body_html.count(f"<{t}>") for t in ("p", "h3", "ul", "li", "strong")}
        flag = (
            ""
            if ok
            else " <span class=warn>&mdash; gate would keep the text version</span>"
        )
        parts.append(f"""
<h2>{esc(name)} <span class=meta>&mdash; {kind}{flag}</span></h2>
<p class=meta>rows: {esc(ids)}<br>bytes from: {esc(key.split("id_/")[1])}<br>
A {len(stored)} chars / {stored.count(chr(10))} newlines &middot;
B {len(text)} chars / {text.count(chr(10))} newlines &middot;
C {len(body)} chars, retention {ratio:.1%} &middot;
D {len(body_html)} chars {tags} &middot;
interleaving artefacts text {at} vs structured {ab}</p>
<div class=grid>
 <div class=card><h3 class=v>A &mdash; in the database now</h3><pre>{esc(stored)}</pre></div>
 <div class=card><h3 class=v>B &mdash; {"pdftotext -layout" if kind == "pdf" else "antiword -t"}</h3><pre>{esc(text)}</pre></div>
 <div class=card><h3 class=v>C &mdash; structured, rendered</h3><div class=rendered>{body_html}</div></div>
 <div class=card><h3 class=v>D &mdash; body_html source</h3><div class=src>{esc(body_html)}</div></div>
</div>""")
    with open(out_path, "w") as fh:
        fh.write("\n".join(parts))


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n", 1)[0])
    p.add_argument(
        "--preview",
        default="12",
        help="documents on the review page: a number, 'all', or 0 for none",
    )
    p.add_argument(
        "--out",
        default="attachments-review.html",
        help="where to write the review page",
    )
    args = p.parse_args()
    preview = args.preview if args.preview == "all" else int(args.preview)
    review(preview, args.out)
