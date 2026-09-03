# Working here

What the suite is for, what each `verify` pass sees and the blind spot each one
leaves for the next to cover, plus the standing don'ts. `CLAUDE.md` carries
these as instructions; here they keep the reason attached.

- **`.venv/bin/python -m unittest discover -s tests` first, after anything.**
  stdlib `unittest`, no new dependency, seconds with no database and ~25 with
  one. It is the answer to "did the logic change", which this repo used to
  re-derive by hand after every refactor. Two tiers:

  - **hermetic**, on archived captures committed under `tests/fixtures/`
    (gzipped, covering all 24 source tags and all three parse routes). Runs on a
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

  Three exclusions, each with a measurement behind it:

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

  What the linter is worth here: this tree gets *moved* — the BCE restructuring,
  the retired `backfill_`/`repair_` family, the CMS-generation renames — and
  every move leaves the same two residues: a
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
  a plain `ast.Name`. `fetcher/control/archive.py`
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
  `tests/test_offline_is_offline.py`, which invokes all 15 scrapers under
  `--offline`, `--retext` and `--seed-cache` with `requests` and
  `socket.connect` monkeypatched to raise a **BaseException**. Not an Exception:
  several call sites wrap their fetch in `except Exception` and degrade
  gracefully, which silently swallowed the first version of that probe and
  reported a network-crawling scraper clean. It found five scrapers where
  `--offline` was not offline. The rule those fixes encode: **the guard goes on
  the candidate list, not on the fetch** — an empty candidate list leaves the
  loop body untouched, which is what keeps it a one-line change per scraper.

  Its other blind spot is prose: it resolves Python names, and a docstring that
  names a deleted file resolves nothing. That is
  `tests/test_no_dead_module_references.py`.

  Why any of this: grep is not enough — renaming `DETAIL_URL_TMPL` in one scraper
  silently broke a follow-up script that imported it, and it was committed that
  way.

- **The PyCharm inspection report, through the MCP, read against `HEAD`.** The
  one check here that is not a command: it goes through whatever IDE session
  happens to be attached, so it gates nothing and a fresh checkout does without
  it.

  **It is a diff, not a verdict.** This tree's report is never empty and never
  will be, so a run that asks "is it clean" learns nothing; the only answer worth
  having is which entries were not there before. That makes the baseline part of
  the check rather than an optional extra: put the `git show HEAD:` versions of
  the same files in a scratch `dupcheck/` *inside the project* — nothing outside
  it is analysed — lint those, then delete the directory and lint the real files,
  and compare on the description and the source line rather than the line number,
  which the change has moved. Delete before the real run: while
  those copies exist, every real file duplicates wholesale against its own twin
  and the report is unreadable. `dupcheck/` is gitignored, because a scratch
  directory that survives one distracted `git add -A` is in the next commit.

  **The baseline run doubles as proof the inspection can still speak**, which it
  needs, because a file with nothing to say gets no entry at all: "clean" and
  "never analysed" arrive as the same empty answer, and a batch can come back
  short of what was asked for. Watching the duplicate you came to remove be
  reported, by length and line, is what makes its later absence mean something.

  **`Duplicated code fragment` is what makes the round trip worth taking.** Ruff
  has no copy-paste rule, in any
  ruleset: it reasons about one construct inside one file, and a paragraph
  transcribed into a second file is, to it, two correct paragraphs. The last two
  copies removed from this tree were both invisible to it and both had survived a
  linted commit — a parser body shared by two CMS generations of one firm,
  comments and all, and a `{where}`/`limit` builder shared by two offline passes
  over different queries. Run it on the files touched *and their neighbours*: a
  copy is a pair, and the other half is in a file the change never opened.

  **The standing noise is not a finding.** bs4's stubs hand back
  `Tag | NavigableString | None` everywhere, so every access the code has already
  proved safe is a weak
  warning. `{where}` is a `.format()` placeholder inside a string the IDE parses
  as SQL, so both attachment cursors raise a dialect error on something that is
  not a query yet. And the requirements inspection calls `requests`, `bs4` and
  `dateutil` undeclared against a `pyproject.toml` that declares all three. None
  of this is silenced — silencing it is how the one line that mattered would go
  with it.

  **Two things it cannot see.** `min_severity` takes only `warning` or `error`,
  so everything below that — the spellchecker included — is out of reach this
  way; a docstring's typos are still nobody's job but the reader's. And it is the
  IDE's analysis, not the project's: it does not know a rule from `CLAUDE.md`, so
  a change can be clean here and still break something only the suite asserts.

  **Not headless `bin/inspect.sh`, though it is installed.** It wants an
  inspection-profile XML and this project has none: `.idea/` is
  gitignored and `USE_PROJECT_PROFILE` is false, so what actually ran is the
  IDE-wide Default profile. Wiring the headless path means committing IDE config
  into a tree that excludes it, and spinning up a second IDE to re-index a
  project the running one has indexed already. A hook cannot do it either —
  hooks are shell, and no MCP is reachable from one.

- **`tests/` is outside the package**, so `pressroom-verify-names`
  sees only the package's own modules.
- **`.venv/bin/python`, not bare `python3`.** `pressroom` is only on the venv's
  path, so bare `python3` cannot import it — which is loud, and used to be
  silent: everything ran on Fedora's system RPMs until the first dependency
  Fedora does not package.
- `pressroom.db` is gitignored, along with `pressroom.db.bak`. `'rebuild'` and
  bulk updates aren't reversible — **copy the DB before one.** It is how 122
  overwritten articles were recovered. `PRESSROOM_DB` is how a whole-corpus
  `--force` gets rehearsed against that copy, without moving the file in and
  out of place.
- archive.org intermittently refuses connections. A run full of `?` markers is
  usually the archive, not the code — confirm with a bare `curl` before debugging
  a parser.
- Not wanted here: an ORM, a query builder, a row dataclass, schema churn on
  `releases`. Six plain functions over plain SQL is the chosen design. One
  exception: `grade`, because the alternative was leaving a verdict
  inside a reference field and seven places guessing which was which. Provenance
  still goes in its own table — that is what `body_origin` is.
