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

- **`uvx ruff format .` and `uvx ruff check .` before a commit.** The tree went
  years without either and did not drift much — the style it grew into *is*
  ruff's default, measured rather than guessed: 88 columns, double quotes, four
  spaces, and `target-version` it reads out of `requires-python` by itself. So
  the config is a single pinned `select` and nothing else. Pinned rather than
  inherited only so that a change to ruff's defaults is not silently a change to
  this repo.

  Three deliberate exclusions, each with a measurement behind it:

  - **`E501` is out.** After formatting, the lines still over 88 are HTML
    templates, SQL, a User-Agent and messages written for a human — none of them
    things a formatter will split or should. Enabling it buys a `noqa` per line
    and nothing else.
  - **`docstring-code-format` stays off.** The archaeology in this repo's
    docstrings carries markup samples, and they are not code to be reformatted.
  - **No `dev` extra.** `uvx` keeps the property `pyproject.toml` already argues
    for: a fresh checkout verifies itself with nothing installed beyond the three
    real dependencies.

  The adoption itself was gated on a proof rather than a promise: a digest over
  `ast.dump()` of every module, before and after, came back identical. That is
  what makes the reformat safe to read past — no literal moved, so no docstring
  moved, so `--help` did not move, and `command.run()` hands a boundary's
  docstring to argparse whole. Do the same before any future bulk rewrite of this
  tree; a diff that size is not reviewable by eye.

  What the linter is actually worth here is not tidiness. It is that this tree
  gets *moved* — the BCE restructuring, the retired `backfill_`/`repair_` family,
  the CMS-generation renames — and every move leaves the same two residues: a
  name that moved without its import, and an import that stayed after its use
  left. See the `F821` story in the next entry for what the first one cost.

- **`pressroom-verify-names` after any change that renames or moves a
  module-level name.** Two checks now, both of which caught a real break during
  the restructuring: *imports* (every module imports — executes module level only,
  which is exactly its blind spot) and *a local shadowing an imported module* (a
  line assigning to a local `gate` shadows the module for the whole function, so
  the name *is* bound, just too late).

  There was a third, *unbound qualified names* — `foo.bar` whose `foo` is bound
  nowhere — and ruff's `F821` replaced it. Not as a duplicate: it is strictly
  stronger. `_walk_unbound` only inspected `ast.Attribute` nodes, so it saw
  `foo.bar` and walked straight past a bare `foo(...)`, which is an `ast.Call` on
  a plain `ast.Name`. That is not a hypothetical gap. `capture/control/archive.py`
  called `snapshot_url()` in three places and imported the module it lives in
  nowhere; this pass printed `unbound qualified names: 0` and `OK` over three
  certain `NameError`s, in the network phase of two scrapers and the whole
  `--attachments` crawl, where neither the hermetic suite nor `--offline` reaches.
  `F821` found all three on its first run. Checked before deleting, not assumed:
  an injected `foo.bar` into a real module is flagged by both, on the same line.

  So the division is not "two overlapping name checkers". Ruff reasons inside one
  file and does it better; this pass keeps the half a static linter cannot do at
  all — it *executes* the import, so `from x import y` with no `y` in `x` fails
  here and nowhere else (ruff sees only an unused import), and it is
  dependency-free, which `uvx` is not. **Do not add bare-name detection back** —
  that is re-implementing pyflakes.

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
