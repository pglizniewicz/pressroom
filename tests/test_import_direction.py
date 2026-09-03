"""Who imports whom, checked against the rules the rulebook already states.

`pressroom-verify-names` cannot see any of this: its import pass *runs*
`importlib.import_module` on every module, so an `import requests` added to
`release/control/query.py` is reported as a success - `requests` is installed in
this venv. Its second pass does parse the AST, but keeps only the first segment
of each imported name in a flat set, so it never holds an edge.

Without this, a violation passes the whole suite on a development machine and
surfaces only where someone runs `pressroom-serve` without `requests` and `bs4`
installed - which is exactly the audience the two readers are kept
dependency-free for.

So this builds the import graph out of source text, executing nothing, and each
class below names the rule it asserts. `ImportGraphTest` is the precondition for
the rest: a graph that silently lost its edges would make every other class pass
by matching nothing.

Two things it does not check. `entity/` importing `control/` is not
forbidden by the rulebook, and inventing that rule here is not this file's job.
Import cycles are not checked because `pressroom-verify-names` really does
import every module, so a cycle already takes it down.
"""

import ast
import collections
import functools
import pathlib
import re
import sys
import unittest

from tests import support

ROOT = support.HERE.parent
PACKAGE = ROOT / "pressroom"

# The dependency-direction paragraph lives in both files, word for word.
RULEBOOK = (ROOT / "CLAUDE.md", ROOT / "docs" / "adr" / "layout-and-naming.md")
DIRECTION_RE = re.compile(
    r"A source component\s+imports\s+(.+?);\s*none of those", re.S
)
NAME_RE = re.compile(r"`([a-z_0-9]+)`")

# Invariant 4's own list, one pattern per clause, matched against the path
# relative to `pressroom/`. The values are what a failure quotes back.
READER_PATH = {
    "database/control/*.py": "database/control/",
    "**/entity/*.py": "every entity layer",
    "release/control/query.py": "release/control/query.py",
    "taxonomy/**/*.py": "taxonomy/",
    "text/control/decoding.py": "text/control/decoding.py",
}

# The two readers Invariant 4 keeps dependency-free, and the command each is.
READERS = {
    "pressroom.release.boundary.search": "pressroom-search",
    "pressroom.browser.boundary.http": "pressroom-serve",
}

POLITENESS = "pressroom.fetcher.control.politeness"

# The grouping directory the source components live in, and the components
# themselves - read off the tree, not typed out. This list was literal here and
# again in CLAUDE.md's roll-call, and a seventh firm had to be added to both
# before the dependency-direction rule would cover it. It is a fact about a
# path now: what is under `pressroom/sources/` is a source component.
GROUP = "sources"

SOURCE_COMPONENTS = frozenset(
    p.name
    for p in (PACKAGE / GROUP).iterdir()
    if p.is_dir() and p.name != "__pycache__"
)

# What a source component is allowed to reach, and what for. The rulebook
# states this set in prose; test_the_allowlist_and_the_rulebook_name_the_same
# _components keeps the two from drifting apart, which they had already done.
SOURCE_MAY_IMPORT = {
    "scraper": "the command line, the catch-up strategies and the parse types",
    "release": "the gates, the write path and the FTS index",
    "fetcher": "archive.org and the polite live fetch",
    "text": "the richtext extractor and the date parser",
    "database": "connect(), which every crawl opens",
    "reporting": "outcome.Stats and the seven fixed markers",
    "q4": "the IR-platform parser intel and amd both delegate to",
}

# The one documented exception to the direction, and why it is safe.
IMPORTS_A_SOURCE = {
    "pressroom.provenance.boundary.verification": (
        "reproduces a stored body by re-running the parser that wrote it, so it "
        "needs the parsers themselves; nothing imports it back"
    ),
}

# Every non-stdlib name the package may mention, and what it is there for. A
# fifth library becomes loud once, here, rather than quietly reaching a reader.
THIRD_PARTY = {
    "requests": "the live and archive fetches",
    "bs4": "every parser's DOM",
    "dateutil": "the loose date formats terratec and the text component meet",
    "curl_cffi": "the impersonating session globenewswire needs, an extra",
}

Graph = collections.namedtuple("Graph", "edges third mentions relative unresolved")


@functools.cache
def _modules() -> dict:
    """Dotted name -> path, with a package named for its directory."""
    out = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = path
    return out


def _statements(node):
    """Import nodes that run at import time - never a function body.

    `ast.walk` would report the one deliberate exception, `curl_cffi` inside
    `make_session()`, as a module-level import, and LazyImportTest is the only
    thing in the tree that would notice.
    """
    for field in ("body", "orelse", "finalbody", "handlers"):
        for child in getattr(node, field, None) or []:
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                yield child
            elif isinstance(
                child, (ast.If, ast.Try, ast.With, ast.ClassDef, ast.ExceptHandler)
            ):
                yield from _statements(child)


@functools.cache
def _graph() -> Graph:
    known = _modules()
    edges = {name: set() for name in known}
    third = {name: set() for name in known}
    mentions, relative, unresolved = set(), [], []
    for name, path in known.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # The inventory counts a library wherever it is named; the rules below
        # want module level only, which is why the two walks are separate.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not (
                isinstance(node, ast.ImportFrom) and node.level
            ):
                for imported in _imported(node):
                    top = imported.split(".")[0]
                    if top != "pressroom" and top not in sys.stdlib_module_names:
                        mentions.add(top)
        for node in _statements(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                relative.append((name, node.lineno))
                continue
            for imported in _imported(node):
                top = imported.split(".")[0]
                if top != "pressroom":
                    if top not in sys.stdlib_module_names:
                        third[name].add(top)
                    continue
                # `from x.y import z` names a symbol, not a module, in more
                # than a third of the package's `from pressroom.` imports.
                # Sticking the two together unconditionally invents a node that
                # is not there and drops the edge that is - silently, which is
                # what the unresolved list below exists to make loud.
                target = imported if imported in known else imported.rsplit(".", 1)[0]
                if target in known:
                    edges[name].add(target)
                else:
                    unresolved.append((name, node.lineno, imported))
    return Graph(edges, third, mentions, relative, unresolved)


def _imported(node) -> list:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [f"{node.module}.{alias.name}" for alias in node.names]


def _relative_path(module: str) -> pathlib.PurePosixPath | None:
    """The module's path under `pressroom/`, or None for a loose module.

    `integrity.py` sits at the root - it walks the tree rather than
    belonging to it - so it has neither a component nor a layer.
    """
    path = _modules().get(module)
    if path is None:
        return None
    parts = path.relative_to(PACKAGE).parts
    return pathlib.PurePosixPath(*parts) if len(parts) > 1 else None


def _inside_component(module: str) -> tuple:
    """The module's path from its component down: `(component, layer, file)`.

    `sources/` is a grouping directory, not a component, so it is dropped here
    rather than in `_relative_path` - the reader-path patterns and every failure
    message still want the whole path under `pressroom/`. Doing it the other way
    round is how the move under `sources/` could have passed silently: every
    source module would have reported the component `sources` with the layer
    `terratec`, and half the classes below would have matched nothing.
    """
    rel = _relative_path(module)
    if rel is None:
        return ()
    parts = rel.parts[1:] if rel.parts[0] == GROUP else rel.parts
    return parts if len(parts) > 1 else ()


def _component(module: str) -> str | None:
    parts = _inside_component(module)
    return parts[0] if parts else None


def _layer(module: str) -> str | None:
    parts = _inside_component(module)
    return parts[1] if len(parts) > 2 else None


def _rel(label: str) -> str:
    """A module as a path a reader can click; anything else unchanged."""
    path = _modules().get(label)
    return str(path.relative_to(ROOT)) if path else label


def _forbidden_to_a_reader(module: str) -> str | None:
    """The module itself, if Invariant 4 puts it out of a reader's reach."""
    return module if _out_of_bounds(module) else None


def _out_of_bounds(module: str) -> str | None:
    """The three things Invariant 4 names beyond "no requests, no bs4"."""
    if module == POLITENESS:
        return "fetcher/control/politeness.py"
    component = _component(module)
    if component == "q4":
        return "q4"
    if component in SOURCE_COMPONENTS:
        return f"the {component} source component"
    return None


def _path_to(start: str, offends) -> list | None:
    """Shortest import path from `start` to something `offends` names.

    Breadth-first, so the path in the failure message is the short one. A
    message that says only "a reader reached bs4" leaves the reader to rebuild
    the chain by hand, which is the work the test was supposed to do.
    """
    edges = _graph().edges
    came_from = {start: None}
    queue = collections.deque([start])
    while queue:
        module = queue.popleft()
        bad = offends(module)
        if bad is not None:
            path, node = [], module
            while node is not None:
                path.append(node)
                node = came_from[node]
            path.reverse()
            if bad != module:
                path.append(bad)
            return path
        for target in sorted(edges[module]):
            if target not in came_from:
                came_from[target] = module
                queue.append(target)
    return None


def _trace(path: list) -> str:
    return " -> ".join(_rel(step) for step in path)


def _on_reader_path() -> dict:
    """Module -> the clause of Invariant 4 that claims it."""
    out = {}
    for module in _modules():
        rel = _relative_path(module)
        if rel is None:
            continue
        for pattern, clause in READER_PATH.items():
            if rel.full_match(pattern):
                out[module] = clause
                break
    return out


class ImportGraphTest(unittest.TestCase):
    """The measurement holds together. Everything below assumes it."""

    def test_no_module_reaches_for_a_relative_import(self):
        """The graph resolves `pressroom.x.y` by name; `.` would vanish."""
        for module, lineno in _graph().relative:
            with self.subTest(module=module, line=lineno):
                self.fail(
                    f"{_rel(module)}:{lineno} imports relatively; the package "
                    f"is absolute-only, and this test resolves edges by name"
                )

    def test_every_import_of_the_package_names_a_module_in_the_tree(self):
        """An edge pointing nowhere is a hole in the graph, not in the code."""
        for module, lineno, imported in _graph().unresolved:
            with self.subTest(module=module, line=lineno):
                self.fail(
                    f"{_rel(module)}:{lineno} imports {imported}, which this "
                    f"test could not resolve to a module - teach _graph() the "
                    f"form, or the rules below stop seeing that edge"
                )

    def test_the_graph_covers_the_whole_package(self):
        """A glob that matched nothing would make every rule below pass."""
        graph = _graph()
        self.assertGreaterEqual(len(_modules()), 100, "barely any modules parsed")
        self.assertGreaterEqual(
            sum(len(targets) for targets in graph.edges.values()),
            100,
            "barely any edges parsed",
        )

    def test_the_grouping_directory_still_names_the_sources(self):
        """`SOURCE_COMPONENTS` is read off `pressroom/sources/`, so an empty or
        renamed directory would leave every isolation rule below matching
        nothing and passing."""
        self.assertTrue(SOURCE_COMPONENTS, f"pressroom/{GROUP}/ holds no component")
        for component in sorted(SOURCE_COMPONENTS):
            with self.subTest(component=component):
                self.assertTrue(
                    (PACKAGE / GROUP / component / "boundary").is_dir(),
                    f"{GROUP}/{component}/ has no boundary; a source's command "
                    f"is the only thing anyone runs",
                )

    def test_both_readers_have_a_closure_worth_walking(self):
        """ReaderClosureTest proves nothing about a reader with no imports."""
        for reader, command in READERS.items():
            with self.subTest(reader=command):
                walked = {reader}
                queue = collections.deque([reader])
                while queue:
                    module = queue.popleft()
                    for target in _graph().edges[module]:
                        if target not in walked:
                            walked.add(target)
                            queue.append(target)
                self.assertGreaterEqual(
                    len(walked), 5, f"{command} imports almost nothing"
                )


class ThirdPartyInventoryTest(unittest.TestCase):
    """Every non-stdlib name in the package is one of the declared few."""

    def test_the_package_mentions_no_library_this_file_has_not_heard_of(self):
        """A new dependency is named here once, or it arrives unnoticed."""
        self.assertEqual(
            _graph().mentions,
            set(THIRD_PARTY),
            "a library entered or left the tree; add it to THIRD_PARTY with "
            "what it is for, or drop the entry that is no longer earned",
        )


class ReaderPathTest(unittest.TestCase):
    """Invariant 4: the path a reader walks imports stdlib only.

    Checked as the prose states it - `never import requests or bs4 into any of
    those` - so the blame lands on the module that did it. The transitive half
    is ReaderClosureTest's, where a chain is what the reader actually pays for.
    """

    @classmethod
    def setUpClass(cls):
        cls.claimed = _on_reader_path()

    def test_every_clause_of_the_invariant_names_a_module(self):
        """A pattern matching nothing is a rule that cannot fail."""
        for pattern, clause in READER_PATH.items():
            with self.subTest(clause=clause):
                self.assertTrue(
                    any(
                        _relative_path(module)
                        and _relative_path(module).full_match(pattern)
                        for module in _modules()
                    ),
                    f"{pattern} matches no module; Invariant 4 names {clause}",
                )

    def test_every_module_on_the_reader_path_imports_stdlib_only(self):
        for module, clause in sorted(self.claimed.items()):
            libraries = _graph().third[module]
            with self.subTest(module=module, clause=clause):
                self.assertFalse(
                    libraries,
                    f"{_rel(module)} imports {', '.join(sorted(libraries))}, and "
                    f"Invariant 4 puts {clause} on the reader path; the two "
                    f"readers are deliberately dependency-free",
                )


class ReaderClosureTest(unittest.TestCase):
    """Neither reader reaches a library, politeness, q4 or a source."""

    def test_neither_reader_reaches_a_third_party_library(self):
        for reader, command in READERS.items():
            path = _path_to(
                reader,
                lambda module: min(_graph().third[module], default=None),
            )
            with self.subTest(reader=command):
                if path:
                    self.fail(
                        f"{_trace(path)}\n    {command} is deliberately "
                        f"dependency-free (CLAUDE.md, Invariant 4); it must run "
                        f"where nothing is installed"
                    )

    def test_neither_reader_reaches_politeness_q4_or_a_source_component(self):
        for reader, command in READERS.items():
            path = _path_to(reader, _forbidden_to_a_reader)
            with self.subTest(reader=command):
                if path:
                    self.fail(
                        f"{_trace(path)}\n    Invariant 4 names "
                        f"{_out_of_bounds(path[-1])} as out of bounds for "
                        f"{command}"
                    )


class SourceComponentTest(unittest.TestCase):
    """A source component imports only the components the rulebook names."""

    def test_a_source_component_imports_only_what_the_rulebook_allows(self):
        for module, targets in sorted(_graph().edges.items()):
            component = _component(module)
            if component not in SOURCE_COMPONENTS:
                continue
            for target in sorted(targets):
                reached = _component(target)
                if reached in (component, None):
                    continue
                with self.subTest(module=module, target=target):
                    self.assertIn(
                        reached,
                        SOURCE_MAY_IMPORT,
                        f"{_rel(module)} imports {_rel(target)}; a source "
                        f"component reaches only {', '.join(SOURCE_MAY_IMPORT)}"
                        f" - and never another source",
                    )

    def test_the_allowlist_and_the_rulebook_name_the_same_components(self):
        """The prose said four when the tree had seven. It cannot again."""
        for prose in RULEBOOK:
            text = prose.read_text(encoding="utf-8")
            match = DIRECTION_RE.search(text)
            rel = prose.relative_to(ROOT)
            with self.subTest(prose=str(rel)):
                self.assertIsNotNone(
                    match, f"{rel} no longer states the dependency direction"
                )
                self.assertEqual(
                    set(NAME_RE.findall(match.group(1))),
                    set(SOURCE_MAY_IMPORT),
                    f"{rel} and SOURCE_MAY_IMPORT name different components; "
                    f"the paragraph is in both files of RULEBOOK, word for word",
                )


class SourceIsolationTest(unittest.TestCase):
    """None of the shared components imports a source. One file may."""

    def test_only_the_documented_exception_imports_a_source_component(self):
        for module, targets in sorted(_graph().edges.items()):
            if module in IMPORTS_A_SOURCE:
                continue
            component = _component(module)
            for target in sorted(targets):
                reached = _component(target)
                if reached == component or reached not in SOURCE_COMPONENTS:
                    continue
                with self.subTest(module=module, target=target):
                    self.fail(
                        f"{_rel(module)} imports {_rel(target)}; the direction "
                        f"runs the other way, and the only file allowed to "
                        f"reverse it is {_rel(next(iter(IMPORTS_A_SOURCE)))}"
                    )

    def test_nothing_imports_the_documented_exception_back(self):
        for module, targets in sorted(_graph().edges.items()):
            for exception, why in IMPORTS_A_SOURCE.items():
                if exception in targets:
                    with self.subTest(module=module):
                        self.fail(
                            f"{_rel(module)} imports {_rel(exception)}, which "
                            f"is safe only because nothing does: {why}"
                        )

    def test_the_documented_exception_is_still_the_exception(self):
        """An allowlist entry that stopped being earned is a lie, not a spare."""
        for exception in IMPORTS_A_SOURCE:
            with self.subTest(module=exception):
                self.assertIn(exception, _graph().edges, f"{exception} is gone")
                self.assertTrue(
                    any(
                        _component(target) in SOURCE_COMPONENTS
                        for target in _graph().edges[exception]
                    ),
                    f"{_rel(exception)} imports no source component any more; "
                    f"drop it from IMPORTS_A_SOURCE",
                )


class LayerDirectionTest(unittest.TestCase):
    """The boundary is what an outside actor reaches - nothing below it.

    Only the outward half is asserted. `entity/` importing `control/` happens
    twice, both out of `reporting/entity/outcome.py` and both in the summary
    line it renders; the rulebook says an entity owns a table, never that it may
    not call control, so that stays recorded rather than enforced - the way
    `docs/adr/layout-and-naming.md` records its two BCE deviations.
    """

    def test_no_control_or_entity_module_imports_a_boundary(self):
        for module, targets in sorted(_graph().edges.items()):
            if _layer(module) not in ("control", "entity"):
                continue
            for target in sorted(targets):
                if _layer(target) != "boundary":
                    continue
                with self.subTest(module=module, target=target):
                    self.fail(
                        f"{_rel(module)} imports {_rel(target)}; a boundary is "
                        f"what an outside actor reaches, so nothing under one "
                        f"reaches back into it"
                    )


class LazyImportTest(unittest.TestCase):
    """`curl_cffi` is an extra, so exactly one function may ask for it."""

    def test_curl_cffi_appears_in_no_module_level_import(self):
        for module, libraries in sorted(_graph().third.items()):
            with self.subTest(module=module):
                self.assertNotIn(
                    "curl_cffi",
                    libraries,
                    f"{_rel(module)} imports curl_cffi at module level; it is "
                    f"an extra, so a missing install must break one scraper "
                    f"rather than every module in the tree",
                )

    def test_curl_cffi_is_still_imported_inside_make_session(self):
        """Otherwise the assertion above passes because nothing uses it."""
        path = _modules()["pressroom.sources.creative.control.globenewswire"]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "make_session"
            for imported in ast.walk(node)
            if isinstance(imported, (ast.Import, ast.ImportFrom))
            for name in _imported(imported)
            if name.split(".")[0] == "curl_cffi"
        ]
        self.assertTrue(
            found,
            f"{_rel('pressroom.sources.creative.control.globenewswire')} no longer "
            f"imports curl_cffi inside make_session()",
        )


if __name__ == "__main__":
    unittest.main()
