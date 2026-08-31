"""The one SQLite file: how it is opened, and in what order it is brought up to
date.

Owns no table. Every table's DDL and every statement over it belongs to the
component whose responsibility that table is, and migration.py calls those
schema modules in a fixed order; connection.py knows nothing about any of them.
That split is what lets a reader open the database without importing a single
domain component.

Two ways in, and the difference is load-bearing: connect() runs the migrations
and installs the FTS triggers, connect_ro() does neither and opens `?mode=ro`,
so an accidental write from a reader is an OperationalError instead of a
silently damaged index.
"""
