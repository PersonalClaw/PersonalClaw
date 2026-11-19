"""The documents package's tests, a package so that their conftest is ``documents.conftest``.

No other folder under ``tests/`` is one, so a conftest here that is not in a package is imported
as ``conftest``. pytest removes that name from ``sys.modules`` first, and every root test that
reads ``import conftest`` then gets this one instead of the suite's own, but only in a run that
collects this folder."""
