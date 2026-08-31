"""Three static checks over this tree, next to the suite in `tests/`.

Not a business component: it makes no claim about press releases, talks to no
database and touches no network. It checks that the *code* still resolves, which
is the failure mode a repo of ~110 modules importing each other by name has and
which grep does not catch.

These are not what `tests/` covers and are not made redundant by it: the suite
exercises behaviour, and these three ask whether the tree's own names still
resolve - which a test only notices for the code paths it happens to run. Its
own blind spot, that all three are static, is `tests/test_offline_is_offline.py`.

Each check has a blind spot the next one covers, and every one of them caught a
real break during the 2026-08-26 restructuring:

  imports   every module imports. Executes module level only - a stale alias
            inside a function body passes this and raises at call time.
  unbound   `foo.bar` whose `foo` is bound nowhere in scope. Caught
            `address.capture_key` calling `address.snapshot_url` from inside its
            own module, where `address` is not a name.
  shadowed  a function that assigns to a name which is also an imported module.
            The rename that turned `bodygate.safe_to_write` into
            `gate.safe_to_write` also hit a line assigning to a local `gate`,
            which shadows the module for the whole function - so the name *is*
            bound, just too late, and `unbound` cannot see it either.

Run it after any change that renames or moves a module-level name.
"""

import argparse
import ast
import builtins
import importlib
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
BUILTINS = set(dir(builtins))

# A name in one of these positions opens a scope of its own, so the checks below
# must stop rather than descend.
SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


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


def _bound_names(node) -> set[str]:
    """Every name this scope binds, without descending into nested scopes."""
    out = set()
    args = getattr(node, "args", None)
    if args:
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            out.add(a.arg)
        for a in (args.vararg, args.kwarg):
            if a:
                out.add(a.arg)
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue
        if isinstance(n, ast.Lambda):
            continue
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _own_subtree(node):
    """Every node in this scope's body, stopping at each nested scope."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, SCOPES):
            stack.extend(ast.iter_child_nodes(n))


def _walk_unbound(node, visible, path, out) -> None:
    scope = visible | _bound_names(node)
    for child in _own_subtree(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and isinstance(child.value.ctx, ast.Load)
        ):
            base = child.value.id
            if base not in scope and base not in BUILTINS:
                out.append((path, child.lineno, f"{base}.{child.attr}"))
        if isinstance(child, SCOPES):
            _walk_unbound(child, scope, path, out)


def check_unbound() -> list[tuple[pathlib.Path, int, str]]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        top = _bound_names(tree)
        for child in ast.iter_child_nodes(tree):
            if isinstance(child, SCOPES):
                _walk_unbound(child, top, path, found)
    seen, out = set(), []
    for path, line, name in found:
        if (path, name) in seen:
            continue
        seen.add((path, name))
        out.append((path, line, name))
    return out


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
        description=__doc__.strip().split("\n\n", 1)[0]
    ).parse_args()

    names = modules()
    bad = 0

    failures = check_imports()
    print(f"1. imports: {len(names) - len(failures)}/{len(names)} modules")
    for name, tb in failures:
        bad += 1
        print(f"   FAIL {name}\n{tb}")

    unbound = check_unbound()
    print(f"2. unbound qualified names: {len(unbound)}")
    for path, line, name in unbound:
        bad += 1
        print(
            f"   {_rel(path)}:{line}: `{name}` - `{name.split('.')[0]}` is bound nowhere"
        )

    shadowed = check_shadowed()
    print(f"3. locals shadowing an imported module: {len(shadowed)}")
    for path, line, func, name in shadowed:
        bad += 1
        print(
            f"   {_rel(path)}:{line}: {func}() assigns to `{name}`, an imported module"
        )

    print("\n" + ("OK" if not bad else f"{bad} problems"))
    sys.exit(1 if bad else 0)
