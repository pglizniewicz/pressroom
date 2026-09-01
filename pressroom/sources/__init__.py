"""The source components, grouped: one directory per firm this recovers from.

Not a business component itself, the way `pressroom/integrity.py` is not one.
It owns no responsibility, has no layers and holds nothing but the components
below it - `pressroom/sources/<firm>/<boundary|control|entity>/` is the same
convention one level down.

What the grouping buys is the dependency direction. "A source component" used to
be a list of names kept by hand in `CLAUDE.md` and again in
`tests/test_import_direction.py`; it is a fact about a path now, so nothing
outside this directory may import anything inside it, and a firm that is in the
tree is in the rule - `docs/adr/layout-and-naming.md`.
"""
