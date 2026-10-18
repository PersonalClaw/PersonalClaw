"""The source-tree checks split from
``tests/test_surface_overlay.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

from personalclaw import surface_overlay as so


def test_every_overlayable_surface_has_a_frontend_call_site():
    """🪤 A surface id nothing renders would be an invisible failure.

    The closed `OVERLAYABLE_SURFACES` set is only honest if each id has a
    ``<SurfaceOverlay surface="…" />`` on a real page. Adding an id without the call site
    reddens here rather than accepting overlays that render nowhere.
    """
    web = REPO / "web" / "src"
    sources = "\n".join(
        source_index.text(p)
        for p in source_index.web_files()
        if p.suffix == ".tsx" and p.is_relative_to(web) and not p.name.endswith(".test.tsx")
    )
    assert "<SurfaceOverlay" in sources, "the band has no call site at all"
    for surface in sorted(so.OVERLAYABLE_SURFACES):
        assert (
            f'<SurfaceOverlay surface="{surface}"' in sources
        ), f"OVERLAYABLE_SURFACES declares {surface!r} but no page renders a band for it"
