# pressroom — notes for the agent

The rules of this project are the repo's, not this file's. `README.md` holds
the conventions a person follows, and `pressroom/__init__.py` is the system
doc: the components' wiring, the system invariants, the vocabulary and the
decisions. Both are imported here so they are in context from the first
message:

@README.md
@pressroom/__init__.py

## Before changing anything

1. The system doc above, then the component's own spec,
   `pressroom/<bc>/__init__.py`.
2. `docs/adr/<area>.md` for the area — what was tried, what it cost, which
   measurement decided it; indexed in `docs/adr/README.md`.
3. For the browser's page, `checks.md`: one labelled line per observation a
   browser can contradict, each carrying the id of the requirement it checks.

## This machine and this session

- **Use `.venv/bin/python`, not bare `python3`,** whenever you run Python
  directly: `pressroom` is only on the venv's path.
- **The `sqlite3` CLI is not installed on this machine.** Inspect the DB with
  `.venv/bin/python -c "import sqlite3; ..."`.
- **After a rename or a new boundary, re-run `uv pip install -e ".[globenewswire]"`**
  or the console script will not exist.
- **Ask the PyCharm MCP what it says about the files you touched and their
  neighbours, as a diff against `HEAD`**: lint the `git show HEAD:` versions out
  of a scratch `dupcheck/` first, delete the directory, then lint the real files.
  `Duplicated code fragment` is the finding that justifies the round trip — ruff
  has no copy-paste rule, and a copy's other half is in a file the change did
  not touch. Not a commit gate, and skipped where no IDE is attached.
- **A run full of `?` markers is usually the archive, not the code** — archive.org
  intermittently refuses connections, so confirm with a bare `curl` before
  debugging a parser.
- **Before reporting anything done**: the suite, ruff, and `pressroom-verify-names`
  where a name moved, as README's Working here says.
