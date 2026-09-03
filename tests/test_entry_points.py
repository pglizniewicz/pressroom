"""Every console script in pyproject.toml resolves to a callable `main`.

`pressroom-verify-names` cannot see this: it checks that every *module* imports,
and a renamed boundary leaves the entry point pointing at a module that is gone
- which shows up only as "command not found" after the next
`uv pip install -e`, if anyone happens to run that command.

The counts are asserted too. CLAUDE.md used to state them as numbers, which
nothing enforced: "22 of them", "15 scrapers".
"""

import tomllib
import unittest
from importlib import import_module

from tests import support

PYPROJECT = support.HERE.parent / "pyproject.toml"


class EntryPointTest(unittest.TestCase):
    def setUp(self):
        self.scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]

    def test_every_target_is_an_importable_module_with_a_main(self):
        for name, target in sorted(self.scripts.items()):
            with self.subTest(command=name, target=target):
                module_name, _, func = target.partition(":")
                main = getattr(import_module(module_name), func, None)
                self.assertTrue(callable(main), f"{target} is not callable")

    def test_every_command_is_named_for_what_it_is(self):
        """`pressroom-<firm>[-<generation>]` for a source, `pressroom-search` /
        `pressroom-serve` for the readers, `pressroom-verify-*` and
        `pressroom-calibrate-*` for the read-only passes."""
        for name in self.scripts:
            with self.subTest(command=name):
                self.assertTrue(name.startswith("pressroom-"), name)

    def test_a_boundary_module_is_where_a_command_points(self):
        """The boundary is what an outside actor reaches; nothing else runs a
        scraper or serves a page. `pressroom-verify-names` is the exception -
        integrity.py is not a business component and says so."""
        for name, target in sorted(self.scripts.items()):
            module_name = target.split(":")[0]
            if module_name == "pressroom.integrity":
                continue
            with self.subTest(command=name):
                self.assertIn(".boundary.", module_name)

    def test_the_four_families_and_their_sizes(self):
        """The counts CLAUDE.md and pyproject.toml used to state in prose. Both
        said 22 when there were 23 - the third `pressroom-verify-*` arrived
        without the sentence being updated, which is the whole reason a number
        in a comment is worth an assertion."""
        families = {"readers": [], "verify": [], "calibrate": [], "sources": []}
        for name in self.scripts:
            if name in ("pressroom-search", "pressroom-serve"):
                families["readers"].append(name)
            elif name.startswith("pressroom-verify-"):
                families["verify"].append(name)
            elif name.startswith("pressroom-calibrate-"):
                families["calibrate"].append(name)
            else:
                families["sources"].append(name)
        self.assertEqual(
            {k: len(v) for k, v in families.items()},
            {"readers": 2, "verify": 3, "calibrate": 2, "sources": 15},
        )
        self.assertEqual(len(self.scripts), 22)

    def test_every_source_boundary_has_a_command(self):
        """The other direction: a boundary module with a main() and no entry
        point is dead code that looks like a feature."""
        import pathlib

        import pressroom

        root = pathlib.Path(pressroom.__file__).parent
        targeted = {t.split(":")[0] for t in self.scripts.values()}
        for path in sorted(root.rglob("boundary/*.py")):
            if path.name == "__init__.py":
                continue
            dotted = ".".join(path.relative_to(root.parent).with_suffix("").parts)
            if not hasattr(import_module(dotted), "main"):
                continue
            with self.subTest(module=dotted):
                self.assertIn(dotted, targeted)


class InstalledTest(unittest.TestCase):
    def test_the_installed_metadata_matches_pyproject(self):
        """After a rename or a new boundary, `uv pip install -e ".[globenewswire]"`
        has to be re-run or the script will not exist. This is what notices -
        the editable install's entry points against the file they came from."""
        from importlib import metadata

        try:
            installed = {
                ep.name: ep.value
                for ep in metadata.distribution("pressroom").entry_points
                if ep.group == "console_scripts"
            }
        except metadata.PackageNotFoundError:
            self.skipTest("pressroom is not installed in this environment")
        declared = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
        self.assertEqual(
            installed,
            declared,
            'stale editable install - re-run `uv pip install -e ".[globenewswire]"`',
        )
