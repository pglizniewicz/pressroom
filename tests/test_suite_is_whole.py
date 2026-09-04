"""Every `test_*.py` in this tree is actually in the run.

Discovery skips a directory that is not a package, and does it silently:
`unittest`'s `_find_test_path` returns `None` for a directory with no
`__init__.py` rather than raising, so the run still ends in `OK`. The namespace
branch does not help either - `discover -s tests` with no `-t` leaves
`start_dir == top_level_dir`, so `is_namespace` stays False and every
subdirectory has to be a package for real.

The subpackages under `tests/` are correct today and nothing checked that they
stay that way. A new `tests/<area>/` whose `__init__.py` was never written takes
its tests out of the suite and reports nothing: the failure is a green run
rather than a red one, which is the shape every invariant in `CLAUDE.md` is
written against.

The first assertion is the claim. The second is the one way it goes wrong in
practice, kept because it is the one whose message says what to do.
"""

import pathlib
import sys
import unittest

from tests import support


def _test_files() -> set:
    return {
        p.resolve()
        for p in support.HERE.rglob("test_*.py")
        if "__pycache__" not in p.parts
    }


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _discovered_files() -> set:
    """The files behind the tests `discover -s tests` actually collects.

    Called with no `top_level_dir`, because that is the documented command and
    the argument is what decides whether subdirectories may be namespaces.
    Loading is all this does - `discover()` imports the modules, which this
    process has anyway, and builds the cases without running any - so calling it
    from inside the suite cannot recurse.
    """
    out = set()
    for test in _flatten(unittest.TestLoader().discover(str(support.HERE))):
        module = sys.modules.get(type(test).__module__)
        path = getattr(module, "__file__", None)
        if path:
            out.add(pathlib.Path(path).resolve())
    return out


def _shown(path) -> str:
    return str(path.relative_to(support.HERE.parent))


class SuiteIsWholeTest(unittest.TestCase):
    def test_every_test_module_is_discovered(self):
        missing = sorted(_shown(p) for p in _test_files() - _discovered_files())
        self.assertEqual(missing, [], f"committed but never run: {missing}")

    def test_every_directory_holding_tests_is_a_package(self):
        for path in sorted(_test_files()):
            directory = path.parent
            with self.subTest(directory=_shown(directory)):
                self.assertTrue(
                    (directory / "__init__.py").is_file(),
                    f"{_shown(directory)} holds tests and is not a package, so "
                    f"unittest walks past it without saying so",
                )


if __name__ == "__main__":
    unittest.main()
