"""Every `<name>.py` a docstring, a comment or a `.md` file names still exists.

`pressroom-verify-names` cannot see this. It resolves Python *names* - imports,
qualified attributes, shadowed locals - and prose is none of those. So the
2026-08-26 restructuring, which moved 37 flat modules into
`<component>/<layer>/`, left 66 references to 19 files that had ceased to
exist: `richtext.py` still told a reader it must never be imported by `db.py`
or `serve.py`, `attachment_crawl.py` still contrasted itself with "every other
`backfill_*.py` in this repo" - a family this repo now calls the smell - and
four boundary modules still printed `python verify_encoding.py` in the usage
their `--help` shows.

Same argument as tests/test_mirrored_rules.py: a comment cannot fail, and one
that names a deleted file is worse than no comment, because it reads as a
pointer. This one can fail.

The prose files are in scope for the same reason and a stronger one: `CLAUDE.md`
and `docs/adr/` are where someone goes to look the tree *up*, so a stale name
there is read as current rather than as a leftover.

The allowlist below is for the opposite case - a *deliberate* mention of
something gone, where the disappearance is the point. Each entry says why.
"""

import pathlib
import re
import unittest

from tests import support

ROOT = support.HERE.parent

# Prose is not only docstrings. `CLAUDE.md` states the rules and `docs/adr/`
# holds the reasoning behind them, and a pointer to a deleted file is worse
# there than in a comment: those are the files someone opens *to look up* how
# the tree is arranged. Added when the rules and the records were split into
# separate files, which multiplied the places a stale module name can sit.
SEARCHED = ("pressroom", "tests", "docs")
PROSE = ("CLAUDE.md", "checks.md")

# A bare module-style filename in prose. Not matching a path with
# directories in it: `text/control/decoding.py` is resolved by the same check
# below, through the basename, and a stricter pattern would miss the flat names
# this exists to catch.
NAME_RE = re.compile(r"\b([a-z_][a-z0-9_]*\.py)\b")

# Mentions of a file that is *supposed* to be gone. The reason is the entry.
RETIRED = {
    # Named to explain why a module is shaped the way it is, in the module that
    # replaced it. Deleting the name would delete the explanation.
    "common.py": "the module of unrelated helpers politeness.py and q4/control/platform.py were split out of",
    # The second such module, named in the rule that banned both. Same argument as
    # common.py: without the name the rule loses its example.
    "db.py": "the other module of unrelated helpers CLAUDE.md's Layout rule was written against",
    # Counterexamples, not pointers: CLAUDE.md names them to say what a source
    # module is *not* called, because the tag is a CMS generation not a domain.
    "terratec.py": "the name terratec/control/pressemit.py deliberately does not have",
    "midiman.py": "the name maudio/control/golive.py deliberately does not have",
    # Folded into the module whose parser it already imported: one CMS
    # generation on two hosts is one crawler. docs/adr/catch-up.md names it as
    # one of the two loops that graded a bodyless row correctly before the
    # library did, which is the record the merge must not erase.
    "presse.py": "the terratec .de loop now merged into terratec/control/pressemit.py",
    # The retired backfill_/repair_/migrate_ family. These two are the evidence
    # for the rule in docs/adr/layout-and-naming.md - one for the docstring that
    # outlived its own moment, one for the finished fix that got deleted.
    "repair_encoding.py": "the docstring that ended up reading 'written as a one-off and no longer one'",
    "repair_cache_hashes.py": "the worked example of a finished fix that is in git history and not in the prose",
}


# This file, which names the dead modules as the examples of what
# it is for. Every other file has the allowlist.
SELF = pathlib.Path(__file__).resolve()


def _sources() -> list:
    out = []
    for top in SEARCHED:
        for pattern in ("*.py", "*.md"):
            for path in sorted((ROOT / top).rglob(pattern)):
                if "__pycache__" in path.parts or path.resolve() == SELF:
                    continue
                out.append(path)
    for name in PROSE:
        out.append(ROOT / name)
    return out


class DeadModuleReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.existing = {
            p.name for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts
        }

    def test_every_filename_in_prose_names_a_file_that_exists(self):
        for path in _sources():
            rel = path.relative_to(ROOT)
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for name in NAME_RE.findall(line):
                    if name in RETIRED or name in self.existing:
                        continue
                    with self.subTest(file=str(rel), line=lineno, name=name):
                        self.fail(
                            f"{rel}:{lineno} names {name}, which no longer "
                            f"exists.\n    {line.strip()}"
                        )

    def test_the_allowlist_has_no_entry_for_a_file_that_exists(self):
        """A retired name that comes back is a stale allowlist, and a stale
        allowlist is how the next rename is missed."""
        for name in RETIRED:
            with self.subTest(name=name):
                self.assertNotIn(
                    name, self.existing, f"{name} exists again; drop it from RETIRED"
                )


if __name__ == "__main__":
    unittest.main()
