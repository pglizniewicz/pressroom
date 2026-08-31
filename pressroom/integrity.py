"""Two static checks over this tree, next to the suite in `tests/`.

Not a business component: it makes no claim about press releases, talks to no
database and touches no network. It checks that the *code* still resolves, which
is the failure mode a repo of ~110 modules importing each other by name has and
which grep does not catch.

These are not what `tests/` covers and are not made redundant by it: the suite
exercises behaviour, and these two ask whether the tree's own names still
resolve - which a test only notices for the code paths it happens to run. Its
own blind spot, that both are static, is `tests/test_offline_is_offline.py`.

Each caught a real break during the 2026-08-26 restructuring:

  imports   every module imports. The half a linter cannot do at all: it runs
            the import, so `from x import y` with no `y` in `x` fails here and
            nowhere else. Executes module level only - a stale alias inside a
            function body passes this and raises at call time.
  shadowed  a function that assigns to a name which is also an imported module.
            The rename that turned `bodygate.safe_to_write` into
            `gate.safe_to_write` also hit a line assigning to a local `gate`,
            which shadows the module for the whole function - so the name *is*
            bound, just too late.

There was a third, `unbound`: `foo.bar` whose `foo` is bound nowhere in scope.
ruff's F821 replaced it, being strictly stronger - it flags the same lines and
also the bare `foo(...)` form, which this one structurally could not see because
it only inspected `ast.Attribute` nodes. That gap was not hypothetical; it is
why this pass printed OK over three live NameErrors in
`capture/control/archive.py`. Do not add bare-name detection back here: that is
pyflakes, and `docs/adr/working-here.md` records the split.

Run it after any change that renames or moves a module-level name.
"""

import argparse
import ast
import importlib
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent


def modules() -> list[str]:
    """Every module in the package, as dotted names."""
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT.parent).with_suffix("")
        out.append(".".join(rel.parts).removesuffix(".__init__"))
    return out


def check_imports() -> list[tuple[str, str]]:
    failures = []
    for name in modules():
        try:
            importlib.import_module(name)
        except Exception:
            failures.append((name, traceback.format_exc(limit=3)))
    return failures


def check_shadowed() -> list[tuple[pathlib.Path, int, str, str]]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    imported.add((al.asname or al.name).split(".")[0])
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(func):
                if (
                    isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Store)
                    and n.id in imported
                ):
                    out.append((path, n.lineno, func.name, n.id))
    return out


def _rel(path) -> str:
    return str(pathlib.Path(path).relative_to(ROOT.parent))


def main() -> None:
    argparse.ArgumentParser(
        description=(__doc__ or "").strip().split("\n\n", 1)[0]
    ).parse_args()

    names = modules()
    bad = 0

    failures = check_imports()
    print(f"1. imports: {len(names) - len(failures)}/{len(names)} modules")
    for name, tb in failures:
        bad += 1
        print(f"   FAIL {name}\n{tb}")

    shadowed = check_shadowed()
    print(f"2. locals shadowing an imported module: {len(shadowed)}")
    for path, line, func, name in shadowed:
        bad += 1
        print(
            f"   {_rel(path)}:{line}: {func}() assigns to `{name}`, an imported module"
        )

    print("\n" + ("OK" if not bad else f"{bad} problems"))
    sys.exit(1 if bad else 0)
