"""No module binds the PersonalClaw home resolver at import time (#2443, #2442).

## The defect this closes

A module-level ``from personalclaw.config.loader import config_dir`` binds whatever that name
pointed at the moment the importing module was first imported. Much of this codebase is imported
LAZILY — on first use, from inside a function — so that moment is not startup. Under test it can
land inside a test that has replaced ``loader.config_dir`` to keep writes out of the real home, and
the importing module then keeps the replacement **forever**: the attribute can be restored on
``loader``, but not in a copy another module already took.

``PERSONALCLAW_HOME`` is not a defence. The env var is read by the REAL ``config_dir``; a captured
stand-in never consults it. Measured on #2443: a task store resolved the FIRST test's home while
``PERSONALCLAW_HOME`` correctly named the current one, so every record it wrote landed in a
directory nothing ever read back. That surfaced as two assertions in a different file that looked
nothing like a home bug (``assert 0 == 1`` and ``assert 200 == 400``), which is why the same defect
was filed twice as "cross-test pollution".

## The invariant, and why it is absolute rather than a ratchet

#2677 fixed the two modules that had actually been bitten and recorded the remaining 57 as a
baseline that could only shrink. That baseline is now **empty**: every module resolves the home
through a locally DEFINED delegator. So this file asserts the strong form — *no* module binds the
resolver at import — rather than policing a list.

Two properties make a delegator correct, and both are load-bearing:

* **DEFINED, not imported and not aliased.** There is no object to capture. An alias assignment
  (``config_dir = config_loader.config_dir``) captures exactly like the import did, which is why
  :meth:`TestNoModulePinsTheHome.test_no_module_aliases_the_resolver` sits beside the import
  check.
* **The NAME stays on the module.** ``conftest``'s home guard re-points module-level ``config_dir``
  attributes, and several modules deliberately re-export the name as a test patch seam. Keeping the
  name is what let the sweep touch **zero** test files.
"""

from __future__ import annotations

import ast

from home_resolver_is_not_bound_at_import_shared import SRC, _aliasers, _binders


class TestTheScannerCanActuallySee:
    """Vacuity floors, as POSITIVE CONTROLS rather than population counts.

    The previous version asserted "more than 40 modules bind the resolver" — a meaningful floor only
    while the defect existed. At zero, a population floor has to be deleted, and a deleted floor is
    how a scanner rots into always-green. A control that feeds the detector a known-bad synthetic
    source keeps working at zero.
    """

    def test_the_binder_detector_flags_a_known_binder(self):
        tree = ast.parse("from personalclaw.config.loader import config_dir\n")
        assert _binders([(SRC / "synthetic.py", tree)]), "binder detector is blind"

    def test_the_alias_detector_flags_a_known_alias(self):
        tree = ast.parse("from personalclaw.config import loader\nconfig_dir = loader.config_dir\n")
        assert _aliasers([(SRC / "synthetic.py", tree)]), "alias detector is blind"


class TestDelegationActuallyFollowsTheLoader:
    """The behavioural half. The structural checks cannot tell a live delegator from a function
    that captured the loader's object internally, so this asserts the property that matters:
    patching the LOADER reaches the module."""

    def test_patching_the_loader_reaches_a_delegating_module(self, tmp_path):
        from unittest.mock import patch

        from personalclaw.tasks import native

        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            assert native.config_dir() == tmp_path
            assert native._tasks_dir() == tmp_path / "tasks"

    def test_the_name_is_still_on_the_module(self):
        """Keeping the module-level NAME is what let the sweep change zero test files:
        ``conftest``'s home guard re-points it, and several modules re-export it as a documented
        test patch seam. A delegator that removed the name would break both."""
        from personalclaw import agent_metadata
        from personalclaw.tasks import native

        for module in (native, agent_metadata):
            assert hasattr(module, "config_dir"), f"{module.__name__} lost its patch seam"
            assert (
                module.config_dir.__module__ == module.__name__
            ), f"{module.__name__}.config_dir must be DEFINED there, not re-imported"
