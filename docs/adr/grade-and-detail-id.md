# `grade` and `detail_id`

**`releases.grade` records how good a row is; `detail_id` records where its text
came from. They were one field and that was a mistake** — seven places
re-derived which kind of value a `detail_id` held, six by counting digits, and
the digit rule had started constraining what a *new* source was allowed to store.

- `grade` is `full` | `teaser` | `stub`, a verdict about the body. Use
  `stored_grade()`, not `already_stored()`, whenever a row might deserve an
  upgrade later — `already_stored()` alone wedges teaser rows permanently. It is
  right only when the loop needs "have I seen this url".
- **An upgrade that replaces a teaser body with the real article must pass
  `grade="full"`**, or the row keeps a verdict that stopped being true and the
  next run offers it again as upgradable. This one stood in the rulebook *and*
  in `upgrade_release`'s own docstring, and four of nine call sites still did
  not do it — a rule two copies of prose could not hold, because what breaks it
  is a third place that reads neither. `GradeVerdictTest` in
  `tests/test_mirrored_rules.py` walks the tree for the shape instead: a call
  passing `body=` and no `grade=` fails, unless it is one of the two writes
  named there, each with its reason next to the code.
- `detail_id` is an **opaque reference** — a Wayback timestamp, or the platform's
  own id on a live source. Nothing parses it; asking is `address.is_timestamp()`'s
  job, and only the archive-facing code asks.
- `full` is only as good as what the scraper knew: `media_pr` and `pressdb` store
  a timestamp on rows whose body is a listing blurb, so `length(body)` stays the
  honest check. That is what the `short` flag is for.
