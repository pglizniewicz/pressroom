"""`docs/layout.md`'s table still describes the tree that exists.

The table is a lookup - one row per business component, naming the file inside
it that owns each job - and it used to sit in `CLAUDE.md`, which is read into
every session. Moving it into `docs/layout.md` made it accurate in one way and
fragile in another: nothing loads it any more, so a rename that misses it is
silent, and a stale map is read as current rather than as a leftover. It is the
most rename-sensitive prose in the repo, so it gets the check.

Three claims about the table, because it can be wrong in three directions: a component
with no row (the map has a hole), a row naming a component that is gone (the map
points nowhere), and a row naming a file that has moved inside its component
(the map is subtly wrong, which is worse than either).

The last assertion is the convention itself, which `CLAUDE.md` states and
nothing enforced: a component holds only the three layers.
`tests/test_no_dead_module_references.py` catches a dead `<name>.py` in this file
too, but only by basename - `release/entity/schema.py` passes there as long as
some `schema.py` exists anywhere, which is exactly the subtly-wrong case.
"""

import re
import unittest

from tests import support

ROOT = support.HERE.parent
PACKAGE = ROOT / "pressroom"
MAP = ROOT / "docs" / "layout.md"

LAYERS = {"boundary", "control", "entity"}

# A row is `| `name`[, `name`...] | prose |`; the prose names paths relative to
# the component, e.g. `control/connection.py`.
ROW_RE = re.compile(r"^\|\s*(`[^|]+?`)\s*\|\s*(.+?)\s*\|$")
NAME_RE = re.compile(r"`([a-z_0-9]+)`")
PATH_RE = re.compile(r"`(boundary|control|entity)/([a-z_0-9]+\.py)`")


def _dirs(parent) -> list:
    return [p for p in parent.iterdir() if p.is_dir() and p.name != "__pycache__"]


def _components() -> dict:
    """Component name -> its directory: every directory under `pressroom/`."""
    return {path.name: path for path in _dirs(PACKAGE)}


def _rows() -> list:
    out = []
    for line in MAP.read_text().splitlines():
        m = ROW_RE.match(line)
        if not m or m.group(1) == "`component`":
            continue
        names = NAME_RE.findall(m.group(1))
        if names:
            out.append((names, m.group(2)))
    return out


class LayoutMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = _components()
        cls.rows = _rows()

    def test_the_map_has_rows(self):
        """A regex that matches nothing would make every other test here pass."""
        self.assertGreaterEqual(len(self.rows), 16, "no rows parsed from the map")

    def test_every_component_has_a_row(self):
        mapped = {name for names, _ in self.rows for name in names}
        for component in sorted(self.components):
            with self.subTest(component=component):
                self.assertIn(
                    component,
                    mapped,
                    f"pressroom/{component}/ has no row in docs/layout.md",
                )

    def test_every_row_names_a_component_that_exists(self):
        for names, _ in self.rows:
            for name in names:
                with self.subTest(component=name):
                    self.assertIn(
                        name,
                        self.components,
                        f"docs/layout.md has a row for {name}, which "
                        f"is not a component",
                    )

    def test_every_path_in_a_row_resolves_inside_that_row_s_component(self):
        for names, prose in self.rows:
            for layer, filename in PATH_RE.findall(prose):
                rel = f"{layer}/{filename}"
                with self.subTest(component="/".join(names), path=rel):
                    self.assertTrue(
                        any(
                            (self.components[name] / layer / filename).exists()
                            for name in names
                        ),
                        f"docs/layout.md's {'/'.join(names)} row names {rel}, "
                        f"which is not there",
                    )

    def test_a_component_holds_only_the_three_layers(self):
        """The convention CLAUDE.md states: `<component>/<layer>/`."""
        for component, path in sorted(self.components.items()):
            for child in sorted(_dirs(path)):
                with self.subTest(component=component, directory=child.name):
                    self.assertIn(child.name, LAYERS)


if __name__ == "__main__":
    unittest.main()
