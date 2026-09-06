"""A lazily-imported module must not bind a home resolver at import (#2443, #2442).

Both of those issues were the same defect, reached from different directions.

`tasks/native.py` did `from personalclaw.config.loader import config_dir` at module level, and
`registry._ensure_native()` imports that module LAZILY — on first use. Under test, first use can
land inside a test that has patched `loader.config_dir` to return its own tmp home, so the module
captures THAT LAMBDA. `monkeypatch` then restores the attribute on `loader` at teardown, but
nothing can restore a copy another module already took, so every later test in the process resolves
the FIRST test's home.

Measured, in the failing pair (trace of the real resolver):

    GET id=t-201ca58d
        path = .../test_withheld_granted_the0/home/tasks/...   <- the FIRST test's home
        env  = .../test_a_provider_that_refuses_l0/home        <- the current test's home

**The env var does not save you**, which is what made this hard to find: `PERSONALCLAW_HOME` is
read by the REAL `config_dir`, and the captured lambda never consults it. So the second test filed
its task into a directory it never looks at, which surfaced as two unrelated-looking assertions:
`assert 0 == 1` (its own tasks dir is empty) and `assert 200 == 400` (its unlink loop found nothing,
so the undo it expected to refuse succeeded).

`agent_metadata.py` was the same shape (#2442), lazily imported from `agents/runners.py`.

## What this rail does, and what it does not

The hazard needs BOTH halves: an import-time binding AND a lazy import of that module. Scanning for
the intersection found **59** modules, so this is systemic and latent rather than two accidents —
the two above are simply the two that were reached in an order that bit.

Fixing 59 modules is not this change. So this is a **ratchet**: the intersection must stay a subset
of the baseline recorded below. A NEW module joining it reds by name; every module fixed should be
deleted from the baseline, which can only shrink. It does not claim the baseline is safe — each
entry is a latent instance of a defect that has now cost two issues.
"""

from __future__ import annotations

import ast
from pathlib import Path

import personalclaw

SRC = Path(personalclaw.__file__).parent
HOME_RESOLVERS = {"config_dir", "config_path"}

#: Modules that bind a home resolver at import AND are imported lazily somewhere. A RATCHET:
#: shrink it by fixing a module (resolve through the live `config_loader.config_dir` attribute),
#: never grow it. Empty is the goal.
BASELINE: frozenset[str] = frozenset(
    {
        "personalclaw.apps.catalog",
        "personalclaw.apps.manager",
        "personalclaw.apps.mcp_bridge",
        "personalclaw.artifacts.native",
        "personalclaw.auth.credentials",
        "personalclaw.auth.enrollment",
        "personalclaw.auth.pairing",
        "personalclaw.cli_server",
        "personalclaw.concurrency",
        "personalclaw.config",
        "personalclaw.config.migrations",
        "personalclaw.context_management",
        "personalclaw.dashboard.chat",
        "personalclaw.dashboard.chat_handlers",
        "personalclaw.dashboard.chat_plan",
        "personalclaw.dashboard.chat_runner",
        "personalclaw.dashboard.handlers",
        "personalclaw.dashboard.handlers.agents",
        "personalclaw.dashboard.handlers.autonudge",
        "personalclaw.dashboard.handlers.triggers",
        "personalclaw.dashboard.handlers.updates",
        "personalclaw.dashboard.session_store",
        "personalclaw.dashboard.views_store",
        "personalclaw.engagement_signals",
        "personalclaw.evals.store",
        "personalclaw.history",
        "personalclaw.inbox",
        "personalclaw.inbox_providers.filesystem_source",
        "personalclaw.knowledge.research_reports",
        "personalclaw.loop.files",
        "personalclaw.mcp_core",
        "personalclaw.mcp_shared",
        "personalclaw.memory",
        "personalclaw.notification_rules",
        "personalclaw.packs.build",
        "personalclaw.packs.connectors",
        "personalclaw.packs.fingerprint",
        "personalclaw.packs.import_",
        "personalclaw.packs.installed",
        "personalclaw.portability",
        "personalclaw.providers.entity_routes",
        "personalclaw.push",
        "personalclaw.resilience.crashes",
        "personalclaw.resilience.doctor",
        "personalclaw.resilience.remediation",
        "personalclaw.schedule_script",
        "personalclaw.session_map",
        "personalclaw.session_pid",
        "personalclaw.session_workspace",
        "personalclaw.skills.loader",
        "personalclaw.tasks.hierarchy",
        "personalclaw.tool_providers.savings",
        "personalclaw.tool_providers.tool_prefs",
        "personalclaw.triggers.boot_migrate",
        "personalclaw.vector_memory",
        "personalclaw.voice.bindings",
        "personalclaw.voice.profiles",
        "personalclaw.workflows.leases",
        "personalclaw.workflows.store",
    }
)


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return ("personalclaw." + str(rel).replace("/", ".")).replace(".__init__", "")


def _scan() -> tuple[set[str], set[str]]:
    """(modules binding a home resolver at import, every module imported lazily)."""
    binders: set[str] = set()
    lazy: set[str] = set()
    for f in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - the lint gate owns syntax
            continue
        mod = _module_name(f)
        for node in tree.body:  # module level ONLY - a nested import is the safe shape
            if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.config.loader":
                if {a.name for a in node.names} & HOME_RESOLVERS:
                    binders.add(mod)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):  # an import inside a function body = a lazy import
                if isinstance(inner, ast.ImportFrom) and inner.module:
                    lazy.add(inner.module)
                    lazy.update(f"{inner.module}.{a.name}" for a in inner.names)
                elif isinstance(inner, ast.Import):
                    lazy.update(a.name for a in inner.names)
    return binders, lazy


def _risky() -> set[str]:
    binders, lazy = _scan()
    return {m for m in binders if m in lazy}


class TestTheScanIsReal:
    """Both floors, because either half breaking alone would make the ratchet vacuous."""

    def test_the_binder_scan_finds_modules(self):
        # FLOOR 1: if the import shape changes, `binders` empties and the ratchet passes over
        # nothing at all.
        binders, _ = _scan()
        assert len(binders) > 40, f"binder scan found only {len(binders)} - scanner broke"

    def test_the_lazy_import_scan_finds_targets(self):
        # FLOOR 2: and if the lazy-import walk breaks, the INTERSECTION empties, which also
        # passes. One floor cannot see the other's failure.
        _, lazy = _scan()
        assert len(lazy) > 1000, f"lazy-import scan found only {len(lazy)} - scanner broke"


class TestTheRatchetHolds:
    def test_no_new_module_joins_the_risky_set(self):
        new = sorted(_risky() - BASELINE)
        assert not new, (
            "these modules bind a home resolver at import AND are imported lazily, so under test "
            "they can capture a previous test's patched resolver and write into the wrong home "
            "(#2443/#2442):\n  " + "\n  ".join(new) + "\n\nResolve through the live attribute: "
            "`from personalclaw.config import loader as config_loader`, then call "
            "`config_loader.config_dir()` at each use."
        )

    def test_the_baseline_does_not_name_a_module_that_is_already_clean(self):
        """A stale baseline entry silently widens the ratchet, so it must shrink as fixes land."""
        stale = sorted(BASELINE - _risky())
        assert (
            not stale
        ), "already clean - delete from BASELINE so the ratchet keeps its grip:\n  " + "\n  ".join(
            stale
        )


class TestTheTwoFixedModulesStayFixed:
    """The regression guard for the two modules the issues were actually about.

    Each still EXPOSES ``config_dir`` — deliberately, so ``conftest``'s home guard and the nine
    existing per-module patch sites keep working — but it is DEFINED here and delegates to the
    loader per call, rather than being a name imported (and therefore captured) at import time.
    Both halves are asserted: the name is present, and it is this module's own function.
    """

    def _assert_local_and_delegating(self, module, issue: str) -> None:
        from personalclaw.config import loader

        assert hasattr(module, "config_dir"), (
            f"{module.__name__} must KEEP a module-level `config_dir` — conftest's home guard "
            f"and the existing patch sites replace it by name ({issue})"
        )
        assert module.config_dir is not loader.config_dir, (
            f"{module.__name__}.config_dir must be its OWN function, not the loader's object "
            f"bound at import: this module is imported lazily, so an imported binding captures "
            f"whatever the name pointed at on first use — a previous test's lambda ({issue})"
        )
        assert module.config_dir.__module__ == module.__name__, (
            f"{module.__name__}.config_dir must be DEFINED in that module, not aliased from "
            f"elsewhere — an alias captures the object exactly like the import did ({issue})"
        )

    def test_the_task_store_resolves_the_home_live(self):
        from personalclaw.tasks import native

        self._assert_local_and_delegating(native, "#2443")

    def test_the_agent_metadata_store_resolves_the_home_live(self):
        from personalclaw import agent_metadata

        self._assert_local_and_delegating(agent_metadata, "#2442")

    def test_the_delegation_follows_a_patched_loader(self):
        """The behavioural half: patching the LOADER must reach the store, which is the thing an
        import-time binding broke. Without this the two structural checks above could pass over
        a function that had captured the loader's object internally anyway."""
        from unittest.mock import patch

        from personalclaw.tasks import native

        with patch("personalclaw.config.loader.config_dir", return_value=Path("/tmp/pclaw-probe")):
            assert native.config_dir() == Path("/tmp/pclaw-probe")
            assert native._tasks_dir() == Path("/tmp/pclaw-probe") / "tasks"
