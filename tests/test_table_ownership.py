"""A table is changed only by the entity layer that owns it.

CLAUDE.md: "A component's entity layer holds its table's DDL and the statements
that change it, and nothing else creates that table." That was prose until
`page_cache` turned out to have both of its INSERTs in `fetcher/control/` and
`releases_fts` its rebuild in `release/control/` - the rule held for two tables
of four and nothing said so. So: every SQL string in the package that creates or
changes a table is in `entity/` of the component `creation.py` names as that
table's owner. Reads are unrestricted; they measure coupling, not ownership.

Docstrings are skipped, because prose about an UPDATE is not one.
"""

import ast
import pathlib
import re
import unittest

from pressroom.database.control import creation
from tests import support

PACKAGE = support.HERE.parent / "pressroom"

CREATES_RE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)")
WRITE_RE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\S+)?\s+INTO|UPDATE|DELETE\s+FROM|"
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+(\w+)",
    re.I,
)


def owners() -> dict[str, str]:
    """table -> component, read off the schema assembler's owner list."""
    out = {}
    for module in creation._OWNERS:
        component = module.__name__.split(".")[1]
        for table in CREATES_RE.findall(module.SCHEMA_SQL):
            out[table] = component
    return out


def _docstrings(tree) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


def writes(path: pathlib.Path) -> list[tuple[str, str]]:
    """(table, statement head) for every write statement in a module's strings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ):
            for m in WRITE_RE.finditer(node.value):
                out.append((m.group(1), m.group(0)))
    return out


class TableOwnershipTest(unittest.TestCase):
    def test_the_owner_list_knows_every_table(self):
        self.assertEqual(
            set(owners()),
            {"releases", "releases_fts", "page_cache", "wayback_calls", "body_origin"},
        )

    def test_a_table_is_written_only_in_its_owner_s_entity_layer(self):
        owned = owners()
        for path in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(PACKAGE)
            for table, head in writes(path):
                component = owned.get(table)
                if component is None:
                    continue  # not a table: a trigger's name, or prose
                with self.subTest(module=str(rel), statement=head):
                    self.assertEqual(
                        rel.parts[:2] if rel.parts[0] != "sources" else rel.parts[1:3],
                        (component, "entity"),
                        f"{rel} changes `{table}`; only {component}/entity/ may",
                    )
