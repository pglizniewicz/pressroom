# `grade` and `detail_id`

**`releases.grade` records how good a row is; `detail_id` records where its text
came from. They were one field and that was a mistake** — seven places
re-derived which kind of value a `detail_id` held, six by counting digits, and
the digit rule had started constraining what a *new* scraper was allowed to store.

- `grade` is `full` | `teaser` | `stub`, a statement of how complete the body
  is. Use `stored_grade()`, not `already_stored()`, whenever a row might be
  upgraded later — with `already_stored()` alone a teaser row is never upgraded.
  It is right only when the loop needs "have I seen this url".
- **An upgrade that replaces a teaser body with the real article must pass
  `grade="full"`**, or the row keeps a grade that stopped being true and the
  next run offers it again as upgradable. This one stood in the rulebook *and*
  in `upgrade_release`'s own docstring, and four of nine call sites still did
  not do it — a rule two copies of prose failed to enforce, because what breaks
  it is a third place that reads neither. `GradeVerdictTest` in
  `tests/test_mirrored_rules.py` walks the tree for the shape instead: a call
  passing `body=` and no `grade=` fails, unless it is one of the two writes
  named there, each with its reason next to the code.
- `detail_id` is an **opaque reference** — a Wayback timestamp, or the platform's
  own id on a live source. Nothing parses it; asking is `address.is_timestamp()`'s
  job, and only the archive-facing code asks.
- `full` states only what the scraper could tell: `media_pr` and `pressdb` store
  a timestamp on rows whose body is a listing blurb, so `length(body)` stays the
  check — the `short` flag.
