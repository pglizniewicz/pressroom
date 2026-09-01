#!/usr/bin/env python3
"""Regenerate the committed test fixtures. Not a test - run it by hand.

Two halves, and only the first one needs the corpus:

  --captures  pick one capture per source per route out of pressroom.db and
              write it gzipped under fixtures/captures/, recording the choice
              in fixtures/manifest.json so it is reproducible rather than
              whatever happened to be picked that day.
  --golden    re-run every parser over the committed captures and rewrite
              fixtures/golden/. Needs nothing but the checkout.

The workflow after an intentional parser change is `--golden`, then read the
git diff. That diff *is* the record of what the change did - the thing this repo
used to produce by hand and paste into CLAUDE.md.

Selection rule, so a refresh does not silently pick a different page: the
**smallest** capture that qualifies. Small keeps the repo light and a fixture
readable; qualifying keeps it representative (a stored body over 800 characters,
so the fixture is a real release rather than a "page moved" stub).

  python tests/refresh.py --captures     # needs pressroom.db
  python tests/refresh.py --golden
"""

import argparse
import gzip
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pressroom.attachment.control import conversion  # noqa: E402
from pressroom.capture.entity import page  # noqa: E402
from pressroom.database.control import creation  # noqa: E402
from pressroom.sources.maudio.control import (
    media_news,
    media_pr,  # noqa: E402
    news_blog,
    pressdb,
)
from pressroom.provenance.entity import origin  # noqa: E402
from pressroom.taxonomy.entity import company  # noqa: E402
from pressroom.sources.terratec.control import early, portal, presse  # noqa: E402
from tests import parsers, support  # noqa: E402

MIN_BODY = 800
# A listing fixture has to be a listing, and a rich one where the corpus has
# one. "Smallest that parses" picked a 254-byte redirect stub for two sources
# and a 3-teaser tail page for the portal's category channel, which is the
# channel portal.py calls irreplaceable - a capture that parses to almost
# nothing pins almost nothing. Where no capture reaches this, the richest one
# there is wins instead: maudio_com_news tops out at 5 entries per page and
# terratec_early's English page carries 5, and both are still the whole of what
# that channel is.
MIN_ENTRIES = 8


def extra_listings() -> dict:
    """source -> the listing captures its own scraper names.

    Five sources have a listing channel that `body_origin` cannot lead to,
    because no row's body was ever read out of one: terratec_early *is* two
    listing pages, the portal's yearly categories are a discovery channel, and
    the two live listings are cached under their own url. Every address here is
    read off the scraper's own constants rather than pasted in, so a fixture
    refresh follows the scraper instead of drifting away from it.
    """
    out = {}
    out["terratec_early"] = [p["wayback_url"] for p in early.PAGES]
    for _base, src, capture in portal.CATEGORY_PAGES:
        out.setdefault(src, []).append(capture)
    for _base, src, capture in presse.INDEX_PAGES:
        out.setdefault(src, []).append(capture)
    return out


def listing_patterns() -> dict:
    """source -> a LIKE pattern matching a capture of that source's listing.

    The four multi-domain scrapers each keep a `DOMAINS` dict mapping their tags
    to the listing url they crawl, which is exactly the question here, so the
    patterns come from there. A pattern rather than an address because the
    capture key carries a timestamp the scraper never knows.
    """
    out = {
        src: f"%id_/{url}%"
        for mod in (media_pr, pressdb, media_news)
        for src, url in mod.DOMAINS.items()
    }
    out["maudio_com_news"] = f"%id_/{news_blog.LISTING_PAGES[1]}%"
    out["soundonsound"] = "https://www.soundonsound.com/search?%"
    return out


def _ts(capture: str) -> str:
    import re

    m = re.search(r"/web/(\d{14})", capture)
    return m.group(1) if m else ""


def _detail_candidates(conn, source):
    """Captures of a row's OWN url - the ones a whole-page parser may be fed."""
    return conn.execute(
        """
        SELECT o.origin_url, length(p.content), r.url
          FROM body_origin o
          JOIN releases r ON r.url = o.url
          JOIN page_cache p ON p.url = o.origin_url
         WHERE r.source = ? AND length(COALESCE(r.body,'')) > ?
           AND o.origin_url = 'https://web.archive.org/web/'||r.detail_id||'id_/'||r.url
         ORDER BY length(p.content)""",
        (source, MIN_BODY),
    ).fetchall()


def _listing_candidates(conn, source):
    """Captures of some OTHER page - which for these sources is the listing the
    body was read out of.

    `.pdf`/`.doc` rows are excluded and it is not a tidiness rule: an attachment
    row's origin is also "not the address the row implies" (it is the *located*
    class), so without this the three media_pr sources all picked the same Word
    file as their listing fixture - a document no listing parser can read.
    """
    return conn.execute(
        """
        SELECT o.origin_url, length(p.content), r.url
          FROM body_origin o
          JOIN releases r ON r.url = o.url
          JOIN page_cache p ON p.url = o.origin_url
         WHERE r.source = ?
           AND o.origin_url <> 'https://web.archive.org/web/'||r.detail_id
                               ||'id_/'||r.url
           AND lower(r.url) NOT LIKE '%.pdf' AND lower(r.url) NOT LIKE '%.doc'
         ORDER BY length(p.content)""",
        (source,),
    ).fetchall()


def _cached(conn, addresses):
    """(address, size) for those of `addresses` page_cache actually holds,
    smallest first."""
    out = []
    for a in addresses or ():
        row = conn.execute(
            "SELECT length(content) FROM page_cache WHERE url = ?", (a,)
        ).fetchone()
        if row:
            out.append((a, row[0], ""))
    return sorted(out, key=lambda t: t[1])


def _cached_like(conn, pattern):
    """Every cached capture matching a listing pattern, smallest first. Several,
    not one: whether a capture parses to a listing is not a question its size
    answers - some are the page after the last one and hold no entries at all."""
    return conn.execute(
        "SELECT url, length(content), '' FROM page_cache WHERE url LIKE ?"
        " ORDER BY length(content) LIMIT 40",
        (pattern,),
    ).fetchall()


def _live_candidates(conn, source):
    """A live source caches under the row's own url, not a capture address."""
    return conn.execute(
        """
        SELECT p.url, length(p.content), r.url
          FROM releases r JOIN page_cache p ON p.url = r.url
         WHERE r.source = ? AND length(COALESCE(r.body,'')) > ?
         ORDER BY length(p.content)""",
        (source, MIN_BODY),
    ).fetchall()


def _attachments(conn):
    """One PDF and one .doc whose cached bytes really are the file - the soft-404
    class is exactly what this fixture must not be."""
    out = {}
    for ext, want in ((".pdf", "pdf"), (".doc", "doc")):
        rows = conn.execute(
            """
            SELECT o.origin_url, p.content, r.url
              FROM body_origin o JOIN releases r ON r.url = o.url
              JOIN page_cache p ON p.url = o.origin_url
             WHERE lower(r.url) LIKE ? ORDER BY length(p.content)""",
            (f"%{ext}",),
        ).fetchall()
        for capture, content, url in rows:
            if (
                conversion.is_attachment(content)
                and conversion.kind_of(content) == want
            ):
                out[f"attachment_{want}"] = (capture, url)
                break
    return out


def refresh_captures() -> None:
    if not support.HAVE_CORPUS:
        sys.exit(f"no corpus database at {support.CORPUS_DB}")
    conn = sqlite3.connect(f"file:{support.CORPUS_DB}?mode=ro", uri=True)
    support.CAPTURES.mkdir(parents=True, exist_ok=True)

    for stale in support.CAPTURES.glob("*.gz"):
        stale.unlink()

    extra, patterns = extra_listings(), listing_patterns()
    manifest, files = {}, {}
    tags = sorted({s for _, (_, ss) in company.COMPANIES.items() for s in ss})
    for source in tags:
        for kind in parsers.routes_for(source):
            picker = {
                "detail": _detail_candidates,
                "listing": _listing_candidates,
                "live": _live_candidates,
            }[kind]
            rows = picker(conn, source)
            want = 1
            if kind == "listing" and (source in extra or source in patterns):
                # Two for terratec_early: the German page and the English one
                # use different date markers and different anchor schemes, so
                # one of them proves nothing about the other.
                rows = _cached(conn, extra.get(source)) or _cached_like(
                    conn, patterns.get(source, "\x00")
                )
                want = 2 if source == "terratec_early" else 1
            if not rows:
                print(f"  -- {source} {kind}: nothing cached, skipped")
                continue
            picked = _usable(conn, source, kind, rows, want)
            if not picked:
                print(f"  -- {source} {kind}: no capture parses to anything, skipped")
                continue
            for n, (capture, _size, url) in enumerate(picked):
                name = f"{source}__{kind}" + (f"_{n}" if n else "")
                manifest[name] = {
                    "source": source,
                    "kind": kind,
                    "capture": capture,
                    "url": url,
                    "timestamp": _ts(capture),
                    "base_url": origin.page_of(capture) or url or capture,
                    "file": _write(conn, name, capture, files),
                }

    for name, (capture, url) in _attachments(conn).items():
        manifest[name] = {
            "source": "attachment",
            "kind": "attachment",
            "capture": capture,
            "url": url,
            "timestamp": _ts(capture),
            "base_url": "",
            "file": _write(conn, name, capture, files),
        }

    support.MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    total = sum(f.stat().st_size for f in support.CAPTURES.glob("*.gz"))
    print(
        f"{len(manifest)} fixtures over {len(files)} files, "
        f"{total / 1024:.0f} KB gzipped"
    )


def _usable(conn, source, kind, rows, want) -> list:
    """The `want` best candidates: smallest that is rich enough, and if none is,
    the richest there is.

    Size alone is the wrong rule twice over. The smallest listing capture of two
    sources is a 254-byte stub, and the smallest detail capture can be a "page
    moved" placeholder - a fixture that parses to nothing would go green forever,
    which is the one thing a regression gate must not do. And a capture that
    parses to three of the twenty-four teasers on a fatter one pins a quarter of
    the parser.
    """
    floor = MIN_ENTRIES if kind == "listing" else MIN_BODY
    rich, thin = [], []
    for capture, size, url in rows:
        content = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?", (capture,)
        ).fetchone()[0]
        seeded = _seeded(url or capture, content)
        try:
            got = parsers.parse(
                kind,
                source,
                content,
                url=url,
                timestamp=_ts(capture),
                conn=seeded,
                base_url=origin.page_of(capture) or url or capture,
            )
        except Exception:
            continue
        finally:
            seeded.close()
        got = len(got) if kind == "listing" else len((got or {}).get("body") or "")
        (rich if got >= floor else thin).append((size, capture, url, got))
    rich.sort()
    out = [(c, s, u) for s, c, u, _n in rich[:want]]
    for _s, capture, url, _n in sorted(thin, key=lambda t: -t[3])[: want - len(out)]:
        out.append((capture, _s, url))
    return out


def _write(conn, name, capture, files: dict) -> str:
    """Write the bytes once and return the file stem the fixture reads.

    Several fixtures legitimately want the same bytes - the three media_pr
    domains mirror one listing, and the two pressdb ones do too - so the
    manifest points at a file rather than owning one. Committing the same 24 KB
    capture three times would be paying for the mirror twice over.
    """
    content = conn.execute(
        "SELECT content FROM page_cache WHERE url = ?", (capture,)
    ).fetchone()[0]
    stem = files.get(capture)
    if stem:
        print(f"  {name:44} -> {stem} (shared)")
        return stem
    path = support.CAPTURES / f"{name}.gz"
    path.write_bytes(gzip.compress(content, 9, mtime=0))
    files[capture] = name
    print(f"  {name:44} {len(content):>7} -> {path.stat().st_size:>6}")
    return name


def refresh_golden() -> None:
    """Re-run every parser over the committed captures. Imported by the golden
    test too, so the two can never disagree about how a fixture is parsed."""
    support.GOLDEN.mkdir(parents=True, exist_ok=True)
    for stale in support.GOLDEN.glob("*.json"):
        stale.unlink()
    for name, spec in sorted(support.manifest().items()):
        (support.GOLDEN / f"{name}.json").write_text(
            json.dumps(
                produce(name, spec), indent=1, ensure_ascii=False, sort_keys=True
            )
            + "\n"
        )
        print(f"  {name}")


def _seeded(url: str, content: bytes):
    """An in-memory database holding these bytes under `url`.

    What the live route needs: its extraction lives inside `fetch_body`, which
    reads page_cache before it reaches for the network, so handing it a seeded
    cache exercises the parser *and* proves the cache path answers.
    """
    conn = sqlite3.connect(":memory:")
    creation.init_db(conn)
    conn.execute(
        "INSERT INTO page_cache (url, content, content_sha256) VALUES (?,?,?)",
        (url, content, page.content_hash(content)),
    )
    conn.commit()
    return conn


def produce(name: str, spec: dict):
    """The parse this fixture stands for, as a JSON-able value."""
    content = support.fixture(name)
    if spec["kind"] == "attachment":
        text, html, kind = conversion.to_richtext(content)
        plain, plain_kind = conversion.plain_text(content)
        return {
            "kind": kind,
            "richtext": text,
            "richtext_html": html,
            "plain_kind": plain_kind,
            "plain": plain,
        }
    conn = _seeded(spec["url"], content)
    try:
        return parsers.parse(
            spec["kind"],
            spec["source"],
            content,
            base_url=spec["base_url"],
            timestamp=spec["timestamp"],
            url=spec["url"],
            conn=conn,
        )
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n", 1)[0])
    p.add_argument(
        "--captures",
        action="store_true",
        help="re-pick the fixture captures out of pressroom.db",
    )
    p.add_argument(
        "--golden",
        action="store_true",
        help="re-run the parsers and rewrite the expected output",
    )
    args = p.parse_args()
    if not (args.captures or args.golden):
        p.error("nothing to do: pass --captures, --golden, or both")
    if args.captures:
        refresh_captures()
    if args.golden:
        refresh_golden()


if __name__ == "__main__":
    main()
