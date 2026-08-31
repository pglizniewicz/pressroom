# Working here

What the suite is for, what each `verify` pass sees and the blind spot each one
leaves for the next to cover, plus the standing don'ts. `CLAUDE.md` carries
these as instructions; here they keep the reason attached.

- **`.venv/bin/python -m unittest discover -s tests` first, after anything.**
  stdlib `unittest`, no new dependency, seconds with no database and ~25 with
  one. It is the answer to "did the logic change", which this repo used to
  re-derive by hand after every refactor. Two tiers:

  - **hermetic**, on archived captures committed under `tests/fixtures/`
    (gzipped, covering all 25 source tags and all three parse routes). Runs on a
    fresh checkout with no `pressroom.db` and no network.
  - **corpus** (`tests/corpus/`), skipped unless a database is present. It
    asserts the headline each `pressroom-verify-*` pass reports in prose, as an
    *invariant* rather than a tally — a token probe compares FTS against a scan
    of the same text, not against a number someone wrote down.

  **`tests/refresh.py --golden` after an intentional parser change**, then read
  the git diff: the golden files hold the full text of every parse, so that diff
  is the record of what the change did. `--captures` re-picks the fixtures out of
  `pressroom.db` and needs the corpus.

  The tests do not replace the `verify` and `calibrate` passes: those print
  per-class reports, which is what you read when a test goes red. Nor do they
  replace `checks.md`, which owns the rendered page.

  Two rules for adding to it: **every case is a measurement this corpus already
  paid for** — `gate.py`'s allowances, the cp1258 mojibake, the 122 articles
  overwritten by listing teasers — and the docstrings carry that archaeology,
  same rule as everywhere else here. And **a test that cannot fail is worse than
  no test**; `'integrity-check'` passing on a corrupt index is this repo's own
  example, so deliberate breaks are checked against the tests meant to catch
  them.

- **`pressroom-verify-names` after any change that renames or moves a
  module-level name.** Three checks, each with a blind spot the next one covers,
  and every one caught a real break during the restructuring: *imports* (every
  module imports — executes module level only, which is exactly its blind spot),
  *unbound qualified names* (`foo.bar` whose `foo` is bound nowhere, which found
  a stale module alias that imported clean and raised at call time), and *a local
  shadowing an imported module* (a line assigning to a local `gate` shadows the
  module for the whole function, so the name *is* bound, just too late).

  Its own blind spot — it is static, and a scraper's phase 1 has to be *run* — is
  `tests/test_offline_is_offline.py`, which invokes all 16 scrapers under
  `--offline`, `--retext` and `--seed-cache` with `requests` and
  `socket.connect` monkeypatched to raise a **BaseException**. Not an Exception:
  several call sites wrap their fetch in `except Exception` and degrade
  gracefully, which silently swallowed the first version of that probe and
  reported a network-crawling source clean. It found five sources where
  `--offline` was not offline. The rule those fixes encode: **the guard goes on
  the candidate list, not on the fetch** — an empty candidate list leaves the
  loop body untouched, which is what keeps it a one-line change per source.

  Its other blind spot is prose: it resolves Python names, and a docstring that
  names a deleted file resolves nothing. That is
  `tests/test_no_dead_module_references.py`.

  Why any of this: grep is not enough — renaming `DETAIL_URL_TMPL` in one scraper
  silently broke a follow-up script that imported it, and it was committed that
  way. The same class of break has happened more than once.

- **`tests/` is outside the package on purpose**, so `pressroom-verify-names`
  sees only the package's own modules.
- `pressroom.db` is gitignored, along with `pressroom.db.bak`. `'rebuild'` and
  bulk updates aren't reversible — **copy the DB before one.** That is not
  advice: it is how 122 overwritten articles were recovered.
- archive.org intermittently refuses connections. A run full of `?` markers is
  usually the archive, not the code — confirm with a bare `curl` before debugging
  a parser.
- Not wanted here: an ORM, a query builder, a row dataclass, schema churn on
  `releases`. Six plain functions over plain SQL is the chosen design. One
  deliberate exception: `grade`, because the alternative was leaving a verdict
  inside a reference field and seven places guessing which was which. Provenance
  still goes in its own table — that is what `body_origin` is.
