"""The install the desktop app's refusal names is the route the guides give.

The tree half of ``tests/test_the_desktop_app_refuses_python_children.py``, which drives the
refusal at every door on planted homes. This one reads the getting-started and desktop guides, so
it is a rail: it runs once per CI run, in the rails job.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

from personalclaw import python_children


def test_the_install_it_names_is_the_route_the_guides_give():
    """Never an invented command: the getting-started guide's recommended install, which the
    desktop guide points at for everything the desktop app does not run."""
    for guide in ("getting-started.md", "desktop.md"):
        text = source_index.text(REPO / "docs" / "guides" / guide)
        assert python_children.INSTALL_COMMAND in text, guide
