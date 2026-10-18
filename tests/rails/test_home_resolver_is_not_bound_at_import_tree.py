"""The source-tree checks split from
``tests/test_home_resolver_is_not_bound_at_import.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from home_resolver_is_not_bound_at_import_shared import SRC, _aliasers, _binders

#: The loader OWNS these names — it is where they are really defined.
OWNER = SRC / "config" / "loader.py"


#: The two SDK facades, exempt with a reason rather than swept.
#:
#: `sdk/channel.py` and `sdk/util.py` are pure published surfaces, and
#: `tests/test_sdk_surface_is_public.py` requires every module-level name in them to appear in
#: `__all__`. `config_dir` / `config_path` ARE declared there — the re-export IS the public
#: contract an app calls. So a delegator cannot be added without either publishing
#: `config_loader` (a core internal on the app surface, which that rail exists to prevent) or
#: widening that rail, and widening another suite's boundary rail as a side effect of this sweep is
#: not a trade worth making.
#:
#: The exemption is also SAFE, not merely convenient, and the distinction matters. The hazard is
#: capturing a *stale* resolver — which needs the module's first import to land mid-test. These two
#: are imported when an app loads, so they hold the loader's ORIGINAL object, and that is exactly
#: the shape `conftest`'s home guard finds by identity and re-points. The modules this sweep had to
#: fix are the ones the guard cannot see; these are not among them.
SDK_FACADE_EXEMPT = frozenset(
    {
        "personalclaw.sdk.channel",
        "personalclaw.sdk.util",
    }
)


def _sources() -> list[tuple[Path, ast.Module]]:
    """Every package module but the resolver's own, parsed, from the suite's one source index."""
    return [(f, tree) for f, tree in source_index.package_trees() if f != OWNER]


class TestTheScannerCanActuallySee:
    """Vacuity floors, as POSITIVE CONTROLS rather than population counts.

    The previous version asserted "more than 40 modules bind the resolver" — a meaningful floor only
    while the defect existed. At zero, a population floor has to be deleted, and a deleted floor is
    how a scanner rots into always-green. A control that feeds the detector a known-bad synthetic
    source keeps working at zero.
    """

    def test_the_walk_reaches_the_package(self):
        # The cheapest failure of all: globbing nothing.
        assert len(_sources()) > 500, "source walk found too few modules — the glob broke"


class TestNoModulePinsTheHome:
    def test_no_module_binds_the_resolver_at_import(self):
        hits = sorted(set(_binders(_sources())) - SDK_FACADE_EXEMPT)
        assert not hits, (
            "these modules bind the home resolver at IMPORT time, so a lazy import can pin a "
            "previous caller's resolver and write into the wrong home (#2443/#2442):\n  "
            + "\n  ".join(hits)
            + "\n\nDefine it locally instead:\n"
            "    from personalclaw.config import loader as config_loader\n\n"
            "    def config_dir():\n"
            "        return config_loader.config_dir()\n"
        )

    def test_no_module_aliases_the_resolver(self):
        hits = sorted(set(_aliasers(_sources())))
        assert not hits, (
            "these modules ALIAS the home resolver at module level, which captures the object "
            "exactly like an import does (#2443):\n  " + "\n  ".join(hits)
        )

    def test_the_exemption_cannot_outlive_its_reason(self):
        """An exemption for a module that no longer binds silently widens the rail.

        Two entries is a small enough list to keep honest, and the whole point of this sweep was
        that a list nobody re-measures is how 57 latent instances accumulated in the first place.
        """
        stale = sorted(SDK_FACADE_EXEMPT - set(_binders(_sources())))
        assert not stale, (
            "these modules are exempt but no longer bind the resolver — delete them from "
            "SDK_FACADE_EXEMPT:\n  " + "\n  ".join(stale)
        )
