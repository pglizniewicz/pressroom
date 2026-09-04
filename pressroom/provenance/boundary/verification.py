"""Prove that every recorded origin really is where a row's body came from.

`body_origin` says which archive.org capture each body was read out of. The
proof is reproduction: run the source's own parser over the recorded capture
and compare the result to what the row stores. The verdicts and the origin
classes are `control/reproduction.py`'s; what is this module's is the walk over
`body_origin` and the one thing a library cannot hold - which parser each source
tag means, since that dispatch imports the sources themselves.

Anything not reproduced is reported. Read-only: this writes nothing, ever.

Usage:
  pressroom-verify-body-origin
  pressroom-verify-body-origin --source terratec_new_de
  pressroom-verify-body-origin --inferred-only     # just the searched-for ones
"""

import argparse
import collections


from pressroom.converter.control import conversion
from pressroom.database.control import connection
from pressroom.provenance.control import reproduction
from pressroom.provenance.entity import origin
from pressroom.maudio.control import (
    golive,
    media_news,
    media_pr,
    news_blog,
    presse_de,
    pressdb,
)
from pressroom.terratec.control import cms, portal, pressemit

# source -> parser, for the sources whose reproduction is a plain whole-page
# parse. The re-extraction library takes its parser from the caller, so this is
# the one place left that has to dispatch by tag without being a scraper - which
# is the right place for it: a verifier that reproduces bodies must know each
# source's parser. Declared next to candidate_bodies, which handles the sources
# whose reproduction needs more than one call.
CACHED_PARSERS = {
    "terratec": pressemit.parse_snapshot,
    "midiman_com": golive.parse_snapshot,
    "midiman_net": golive.parse_snapshot,
    "maudio_com": golive.parse_snapshot,
    "maudio_com_news": news_blog.parse_detail,
}


def candidate_bodies(
    source: str, content: bytes, page_url: str, ts: str, url: str
) -> list[str] | None:
    """Every body the source's parser can see in these bytes, this row's first.

    A listing capture holds many releases, so the entry keyed to this row's url
    is the one that must match; the others are returned as a fallback only so a
    parser that cannot expose urls still gets compared against something.
    """
    if source.startswith("terratec_new"):
        entries = cms.extract_entries(content)
        mine = [e["body"] for e in entries if e.get("url") == url]
        return mine or [e["body"] for e in entries]
    if source == "midiman_de":
        entries = presse_de.parse_page(content, page_url, ts)
        mine = [
            e["body"]
            for e in entries
            if presse_de.release_url(e, e["title"], e["date"]) == url
        ]
        return mine or [e["body"] for e in entries]
    if source.endswith("_pressdb"):
        entries = pressdb.extract_entries(content, page_url, ts)
        mine = [e.get("body") or "" for e in entries if e.get("url") == url]
        return (
            mine
            or [e.get("body") or "" for e in entries]
            or [pressdb.parse_detail(content).get("body") or ""]
        )
    if source.endswith("_media_pr"):
        entries = media_pr.extract_entries(content, page_url, ts)
        mine = [e.get("body") or "" for e in entries if e.get("url") == url]
        return mine or [e.get("body") or "" for e in entries]
    # Explicit tags, not a suffix: `maudio_com_news` also ends in `_news` and
    # belongs to a different scraper entirely (news_blog.py), which the
    # suffix rule silently mis-parsed into 25 reported mismatches whose bodies
    # were in fact identical.
    if source in (
        "midiman_net_media_news",
        "midiman_com_media_news",
        "maudio_com_media_news",
        "midiman_couk_news",
    ):
        out = [media_news.parse_detail(content).get("body") or ""]
        try:
            entries = media_news.extract_entries(content, page_url, ts)
            out += [e.get("body") or "" for e in entries if e.get("url") == url] or [
                e.get("body") or "" for e in entries
            ]
        except Exception:
            pass
        return out
    if source.startswith("terratec_press"):
        return [portal.parse_snapshot(content).get("body") or ""]
    parser = CACHED_PARSERS.get(source)
    return [parser(content).get("body") or ""] if parser else None


def run(source: str | None = None, inferred_only: bool = False) -> None:
    conn = connection.connect_ro()
    sql = """SELECT r.id, r.source, r.url, r.detail_id, COALESCE(r.body, ''), r.body_html,
                    c.origin_url
               FROM body_origin c JOIN releases r ON r.url = c.url
              WHERE 1=1"""
    params = []
    if source:
        sql += " AND r.source = ?"
        params.append(source)
    if inferred_only:
        # The inferred class, spelled out: an address that is neither the one
        # the row implies nor an attachment found by path.
        sql += (
            " AND c.origin_url <> 'https://web.archive.org/web/' || r.detail_id"
            " || 'id_/' || r.url"
            " AND lower(r.url) NOT LIKE '%.pdf' AND lower(r.url) NOT LIKE '%.doc'"
        )
    rows = conn.execute(sql + " ORDER BY r.id", params).fetchall()
    print(f"[verify] {len(rows)} recorded origins to reproduce", flush=True)

    tally = collections.Counter()
    problems = []
    for rid, src, url, ts, body, body_html, capture in rows:
        got = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?", (capture,)
        ).fetchone()
        kind = reproduction.origin_class(url, ts, capture)
        if got is None:
            tally[(kind, "bytes not cached")] += 1
            continue
        content = got[0]
        if not conversion.is_attachment(content) and not conversion.looks_like_html(
            content
        ):
            tally[(kind, "bytes are neither html nor an attachment")] += 1
            problems.append((rid, src, "bytes are neither", capture))
            continue
        if conversion.is_attachment(content):
            v = reproduction.attachment_verdict(body, body_html, content)
        else:
            page = origin.page_of(capture) or capture
            try:
                v = reproduction.verdict(
                    body, candidate_bodies(src, content, page, ts, url)
                )
            except Exception as e:
                v = f"PARSER RAISED ({type(e).__name__})"
        tally[(kind, v)] += 1
        if v.isupper() or v.split()[0].isupper():
            problems.append((rid, src, v, capture))

    for kind in ("inferred", "located", "computed"):
        total = sum(n for (k, _), n in tally.items() if k == kind)
        if not total:
            continue
        label = {
            "inferred": "found by searching for the body's text",
            "located": "an attachment's bytes found under that path",
            "computed": "the address the row implies, unchecked when written",
        }[kind]
        print(f"\n=== {total} origins {label}")
        for (k, v), n in sorted(tally.items(), key=lambda kv: -kv[1]):
            if k == kind:
                print(f"  {n:5}  {v}")
    if problems:
        print(f"\nNOT REPRODUCED: {len(problems)}")
        for rid, src, why, capture in problems[:20]:
            print(f"  #{rid} {src:22} {why:34} {capture[:70]}")
    else:
        print("\nevery recorded origin reproduces the body it claims")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n", 1)[0])
    p.add_argument("--source", help="limit to one source tag")
    p.add_argument(
        "--inferred-only",
        action="store_true",
        help="only the origins whose page had to be searched for",
    )
    args = p.parse_args()
    run(args.source, args.inferred_only)
