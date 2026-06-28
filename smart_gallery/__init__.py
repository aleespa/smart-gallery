"""smart_gallery — a DB-centric photo & video catalog.

Each managed drive carries a single SQLite database describing every media file
on it. Imports update the catalog incrementally, sync reconciles it with the
live drive, and exports copy filtered subsets elsewhere without re-analysis.
"""

__version__ = "0.1.0"
