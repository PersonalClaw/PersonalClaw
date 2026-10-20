"""The real-tree checks split from ``tests/test_harness_validate.py``.

The harness job runs ``python -m harness validate``, which fails on an error but not on a warning;
this holds that the shipped specs have no scanner warning either. They run once per CI run, in the
rails job. The module named above says what they hold
to, and its own tests prove each check works on trees they plant.
"""

from __future__ import annotations

from harness import cli


def test_cli_validate_has_no_scanner_warnings_now() -> None:
    # The scanner check-ids are resolvable, so validate should have zero
    # warnings on the shipped set (every rule's scanner: ref resolves).
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["validate", "--fast"])
    assert rc == 0
    assert "0 warning(s)" in buf.getvalue()
