"""The source-tree checks split from
``tests/test_terminal_persist_capability.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import source_index
import terminal_persist_capability_shared

from personalclaw import tmux_substrate
from tests.test_terminal_handler import _make_request  # noqa: F401  (shared request builder)

_enabled = terminal_persist_capability_shared._enabled


def test_the_tmux_probe_has_exactly_one_owner():
    """No module outside `tmux_substrate` may re-derive "is tmux installed".

    `dashboard/handlers/terminal.py` carried its own `shutil.which("tmux")` — same docstring,
    same body — beside `tmux_substrate.tmux_available()`. Nothing was wrong at the time, which
    is the point: the gate, the boot recovery sweep and now a dashboard label all read this
    fact, and `tmux_substrate`'s own module docstring says that two copies of a tmux fact is how
    a reaper, a sweep and a label come to disagree. The copy is deleted; this keeps it deleted.

    Derived from the tree rather than from a list of known offenders, so a NEW copy in a module
    nobody thought of reds too.
    """
    import pathlib

    owner = pathlib.Path(tmux_substrate.__file__)
    root = owner.parent
    pattern = 'which("tmux")'
    # Not vacuous: the owner really does contain the probe this scan looks for, so an empty
    # `strays` below means "one owner" and not "the pattern stopped matching anything".
    assert pattern in owner.read_text(encoding="utf-8")
    strays = sorted(
        str(p.relative_to(root))
        for p in source_index.package_files()
        if p != owner.resolve() and pattern in source_index.text(p)
    )
    assert strays == [], (
        "tmux availability must come from tmux_substrate.tmux_available(); a second "
        f"shutil.which('tmux') lives in {strays}"
    )
