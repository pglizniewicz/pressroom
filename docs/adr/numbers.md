# Where the numbers go

The rule that replaced a 259-line section of corpus tallies, and the four
standing decisions kept from it. The section used to be in `CLAUDE.md`; the rule
applies to these files just as strictly.

**Not here.** `CLAUDE.md` used to carry a 259-line "Known open state" section of
corpus tallies and dated run logs, and it went out of date exactly the way that
kind of prose does: three different figures for the same metric, a claim and its
own retraction a hundred lines apart, and an explanation that stayed after the
number it explained had moved. The counts are now asserted where they can fail:

- **corpus counts and index correctness** — `tests/corpus/`, which asserts invariants
  rather than tallies, plus `pressroom-verify-body-origin`,
  `pressroom-verify-encoding` and `pressroom-verify-names` for the per-class
  reports.
- **command and module counts** — `tests/test_entry_points.py`. It exists because
  the prose said 22 when there were 23.
- **the rendered page** — `checks.md`.

**A measurement goes in a test or a verify pass; the prose gets the rule the
measurement established.** If you catch yourself writing a row count into
`CLAUDE.md` or into one of these files, that is the mistake.

Four standing decisions that came out of measurements and still change what
someone does — they are rules, not status:

- **A `media_news` teaser is the expected end state.** Two thirds of those rows
  use a URL scheme with zero Wayback captures, ever, and a CDX spot check of the
  rest found genuine absence rather than a transient failure. An hour of
  crawling once recovered one row. **Measure the URL scheme before spending a
  crawl on a block like that.**
- **The attachment rows with no cached bytes are dead**, confirmed across all
  three mirror domains by hand. The listing teaser is their end state; a re-crawl
  recovers nothing.
- **The rows whose bytes exist only under a mirror domain have no capture of
  their own.** That was an assumption until a full crawl returned zero new files,
  and it is now established — do not run that crawl again.
- **A `soft-404 served as HTTP 200` is a recurring shape here, not a one-off.**
  Both known instances were the original server or a modern redesign answering
  200 for a file that is gone. Anything that decides a URL is dead must check
  what came back, not just the status.
