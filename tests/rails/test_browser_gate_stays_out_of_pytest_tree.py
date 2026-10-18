"""The source-tree checks split from
``tests/test_browser_gate_stays_out_of_pytest.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pathlib

from browser_gate_stays_out_of_pytest_shared import (
    _ROOT,
    _TESTS,
    BROWSER_TARGET,
    spawns_the_browser_gate,
)


def _collectible_modules() -> list[pathlib.Path]:
    return sorted(_TESTS.rglob("test_*.py"))


class TestNoPytestModuleShellsTheBrowserGate:
    def test_no_collected_module_launches_the_browser(self):
        modules = _collectible_modules()
        assert len(modules) > 100, (
            f"only {len(modules)} test modules found under {_TESTS} — the sweep below would be "
            f"nearly vacuous, so the glob is wrong, not the repo"
        )
        offenders: dict[str, list[str]] = {}
        for path in modules:
            hits = spawns_the_browser_gate(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.name] = hits
        assert not offenders, (
            "these pytest modules shell the browser gate, so a bare `pytest` now runs it:\n"
            + "\n".join(f"  {name}: {', '.join(hits)}" for name, hits in offenders.items())
            + f"\n\nMove the browser leg to `make {BROWSER_TARGET}` (a Playwright spec under "
            f"web/e2e/, wired into a CI job — see tests/test_e2e_specs_are_executed.py)."
        )

    def test_no_python_test_module_hides_in_the_spec_directory(self):
        """`web/e2e/` is the browser gate's own directory. A `test_*.py` there is collected by a
        bare root-level `pytest` while looking like it belongs to the Playwright suite."""
        stowaways = sorted(p.name for p in (_ROOT / "web" / "e2e").rglob("test_*.py"))
        assert not stowaways, (
            f"python test modules inside the Playwright spec directory: {stowaways} — a bare "
            f"pytest from the repo root collects these."
        )
