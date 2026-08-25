#!/usr/bin/env python3
"""One-time pass: fill `page_cache.content_sha256` for the rows that predate it,
then report which cached addresses hold byte-identical content.

The hash is an identity fact, not a storage trick. Nothing here deletes a blob:
deduplicating them is exactly what would make sharding this table awkward later,
and the few MB are not worth it. What the column buys is the ability to ask "do
we already have these bytes, under another name" without guessing.

That guess is what this replaces. The same press-release attachment was served
from midiman.com, midiman.net and m-audio.com, and the crawl stores whichever
copy archive.org answered for - so a row's own url routinely has no cached
capture while a sibling's does. Matching those by **file name** paired 12 rows
with a *different release* that happened to share one (#5343: the row holds
"M-Audio Ships ProSessions Sound and Loop Libraries", the same-named file in
another directory is "M-Audio Announces ProSessions Sound Library"). Matching by
the full path with the domain swapped - `backfill_midiman_attachments.
domain_variants`, which the crawl already used - got all 119 right. A hash
settles it without either heuristic.

New entries get their hash at insert (fetch.fetch_cached, wayback.fetch_snapshot,
both through db.content_hash). This exists for the 6344 rows written before that.

Usage:
  python repair_cache_hashes.py --dry-run
  python repair_cache_hashes.py
"""

import argparse
import collections

import db

# Rows are hashed in batches: the cache is ~370 MB and loading every blob at
# once would hold all of it in memory for no reason.
BATCH = 200


def fill(conn, dry_run: bool = False) -> int:
    todo = conn.execute("SELECT count(*) FROM page_cache "
                        "WHERE content_sha256 IS NULL").fetchone()[0]
    print(f"[hashes] {todo} cached entries without a hash", flush=True)
    if dry_run or not todo:
        return 0
    done = 0
    while True:
        rows = conn.execute("SELECT url, content FROM page_cache "
                            "WHERE content_sha256 IS NULL LIMIT ?", (BATCH,)).fetchall()
        if not rows:
            break
        for url, content in rows:
            conn.execute("UPDATE page_cache SET content_sha256 = ? WHERE url = ?",
                         (db.content_hash(content), url))
        conn.commit()
        done += len(rows)
        print(f"  {done}/{todo}", flush=True)
    return done


def report(conn) -> None:
    groups = collections.defaultdict(list)
    for sha, url, size in conn.execute(
            "SELECT content_sha256, url, length(content) FROM page_cache "
            "WHERE content_sha256 IS NOT NULL"):
        groups[sha].append((url, size))
    shared = {sha: us for sha, us in groups.items() if len(us) > 1}
    dupes = sum(len(us) - 1 for us in shared.values())
    wasted = sum(us[0][1] * (len(us) - 1) for us in shared.values())
    print(f"\n{len(groups)} distinct byte sequences in {sum(len(u) for u in groups.values())} "
          f"entries")
    print(f"  {len(shared)} of them stored under more than one address, "
          f"{dupes} surplus copies, {wasted / 1e6:.0f} MB")
    print("  (left in place on purpose - see this file's docstring)")

    by_kind = collections.Counter()
    for us in shared.values():
        hosts = {u.split("id_/", 1)[-1].split("/")[2] if "id_/" in u else u.split("/")[2]
                 for u, _ in us}
        paths = {u.split("id_/", 1)[-1].split("/", 3)[-1] if "id_/" in u else u
                 for u, _ in us}
        if len(hosts) > 1 and len(paths) == 1:
            by_kind["same path, different host (a mirror)"] += 1
        elif len(hosts) == 1:
            by_kind["same host, different path or capture"] += 1
        else:
            by_kind["different host and path"] += 1
    print("\nwhat the shared groups are:")
    for k, v in by_kind.most_common():
        print(f"  {v:5}  {k}")

    print("\nlargest groups:")
    for sha, us in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:5]:
        print(f"  {len(us)} copies, {us[0][1] / 1000:.0f} kB  {sha[:12]}")
        for u, _ in us[:3]:
            print(f"      {u[:110]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = p.parse_args()
    conn = db.connect()
    fill(conn, args.dry_run)
    report(conn)
    conn.close()
