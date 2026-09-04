"""The browser's spec and its two oracles agree, id for id.

`pressroom/browser/__init__.py` is the spec: EARS statements under stable
`Rn.m` ids. Its D1 splits them between two oracles - R1–R5 are held by
`tests/browser/test_http.py`, R6–R11 by `checks.md` - and its D2 says where the
id is written: the first word of a test's docstring, or right after the check's
label. This file reads all three and holds the trace in both directions: every
statement has at least one test or check, and every id a test or check carries
is a statement. A test without an id, or a check without one, is the third way
the trace goes stale and is refused too.

What it cannot hold is whether a check passes - that is a browser's to say, in
the `web-static` run over `checks.md`.
"""

import ast
import re
import unittest

import pressroom.browser
from tests import support

TEST_FILE = support.HERE / "browser" / "test_http.py"
CHECKS = support.HERE.parent / "checks.md"

ID = r"R\d+\.\d+"
STATEMENT_RE = re.compile(rf"^- ({ID}) — ", re.M)
GROUP_RE = re.compile(r"^### R(\d+):", re.M)
CHECK_RE = re.compile(rf"^- \[([a-z0-9-]+)]((?:\s+{ID},?)*)", re.M)
ID_RE = re.compile(ID)

# D1: which oracle holds which groups.
HTTP_GROUPS = range(1, 6)
PAGE_GROUPS = range(6, 12)


def statements() -> list[str]:
    return STATEMENT_RE.findall(pressroom.browser.__doc__)


def test_ids() -> dict[str, str | None]:
    """Test name -> the id its docstring opens with, or None."""
    out = {}
    tree = ast.parse(TEST_FILE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            doc = ast.get_docstring(node) or ""
            first = doc.split(None, 1)[0] if doc else ""
            out[node.name] = first if ID_RE.fullmatch(first) else None
    return out


def check_ids() -> dict[str, list[str]]:
    """Check label -> the ids after it. The `## Deviations` section holds
    decisions, not checks, and is skipped."""
    out = {}
    for section in re.split(r"^## ", CHECKS.read_text(), flags=re.M):
        if section.startswith("Deviations"):
            continue
        for label, ids in CHECK_RE.findall(section):
            out[label] = ID_RE.findall(ids)
    return out


class SpecTraceTest(unittest.TestCase):
    def setUp(self):
        self.spec = statements()
        self.tests = test_ids()
        self.checks = check_ids()

    def test_the_spec_has_statements_under_every_group(self):
        groups = {int(g) for g in GROUP_RE.findall(pressroom.browser.__doc__)}
        self.assertEqual(groups, set(HTTP_GROUPS) | set(PAGE_GROUPS))
        self.assertEqual(len(self.spec), len(set(self.spec)), "an id is repeated")
        for rid in self.spec:
            self.assertIn(int(rid[1:].split(".")[0]), groups, rid)

    def test_every_http_statement_has_a_test_carrying_its_id(self):
        held = set(self.tests.values())
        for rid in self.spec:
            if int(rid[1:].split(".")[0]) in HTTP_GROUPS:
                with self.subTest(statement=rid):
                    self.assertIn(rid, held, f"{rid} has no test in {TEST_FILE.name}")

    def test_every_page_statement_has_a_check_carrying_its_id(self):
        held = {rid for ids in self.checks.values() for rid in ids}
        for rid in self.spec:
            if int(rid[1:].split(".")[0]) in PAGE_GROUPS:
                with self.subTest(statement=rid):
                    self.assertIn(rid, held, f"{rid} has no check in checks.md")

    def test_every_test_carries_the_id_of_a_statement(self):
        self.assertTrue(self.tests)
        for name, rid in self.tests.items():
            with self.subTest(test=name):
                self.assertIsNotNone(rid, f"{name}: docstring must open with an id")
                self.assertIn(rid, self.spec, f"{name} traces to no statement")

    def test_every_check_carries_the_id_of_a_statement(self):
        self.assertTrue(self.checks)
        for label, ids in self.checks.items():
            with self.subTest(check=label):
                self.assertTrue(ids, f"[{label}] names no requirement")
                for rid in ids:
                    self.assertIn(rid, self.spec, f"[{label}] traces to no statement")
