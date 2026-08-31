"""The corpus: what a recovered press release is, and how it is written, graded
and searched.

`releases.url` is the dedup key and inserts are INSERT OR IGNORE, so a rerun of
any crawl is free. Two fields are deliberately separate and were one until
2026-08-22: `grade` is a verdict about the body, `detail_id` is an opaque
reference to where the body came from - a capture timestamp, a platform id -
and nothing parses it.

A release is stored twice, as plain text and as an allowlisted HTML subset,
both out of one extraction, so the indexed text and the displayed markup cannot
drift apart. Nothing reaches `body` without passing the gate in control/gate.py.

Not an ORM: plain sqlite3, plain SQL strings, one function per statement shape.
The point is that each statement exists exactly once.
"""
