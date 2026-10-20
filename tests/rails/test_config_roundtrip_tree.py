"""A removed config field is gone from every surface, the shipped tree included.

The tree half of ``tests/test_config_roundtrip.py``, which drives every config field through a
real save and load on a planted home. These two read the web client, the configuration reference
and the shipped source, as well as the config classes, so they are a rail: they run once per CI
run, in the rails job.
"""

from __future__ import annotations

from dataclasses import fields

import source_index
from rails_repo import REPO

from personalclaw.config.loader import AppConfig

_CONFIG_DOC = REPO / "docs" / "reference" / "configuration.md"


def test_no_agent_sandbox_config_field_on_any_surface():
    """#364 — the Settings → Agent defaults → "Sandbox" control was removed, and the
    removal has to be complete on EVERY surface, not just the dataclass.

    The control wrote `agent.sandbox` ("auto"/"off") and nothing read it: sandboxing is
    decided by the `mode` argument threaded into `sandbox.wrap_argv`, which arrives from
    per-provider options or a literal at each of its call sites — `AppConfig.agent.sandbox`
    never entered that chain. A settings switch labelled "Sandbox" that makes no sandbox
    decision is a false promise about a security control, so the field, its PATCH allowlist
    entry, its frontend control, its docs row and the docstring that claimed the wiring
    existed all go together. Wiring it instead would have required choosing which of
    `wrap_argv`'s five call sites a global toggle overrides — including two deliberately
    hardcoded boundaries (`knowledge_providers/pack_parse.py`'s "strict" for untrusted
    pack parsing, `schedule_script.py`'s "standard") — which is a policy this codebase has
    not decided and a test may not mint.

    Asserted across all five surfaces in one rail on purpose: a per-surface check passes
    while any single surface still carries the field, which is the exact shape that leaves
    a "removed" control still reachable. Its positive half, that `wrap_argv`'s `mode` is the
    sandbox decision's one input, is `test_the_sandbox_decision_has_exactly_one_input` in
    ``tests/test_config_roundtrip.py``.
    """
    from personalclaw.config.editable import _EDITABLE_CONFIG
    from personalclaw.config.loader import AgentConfig

    assert "sandbox" not in {
        f.name for f in fields(AgentConfig)
    }, "AgentConfig.sandbox is back: a config field nothing reads, under a security label"
    assert "agent.sandbox" not in _EDITABLE_CONFIG, "agent.sandbox is PATCH-writable again"
    assert "sandbox" not in AppConfig().to_dict()["agent"], "agent.sandbox is serialized again"

    panel = source_index.text(REPO / "web/src/pages/settings/AgentDefaultsPanel.tsx")
    assert 'field="sandbox"' not in panel, "the Agent defaults Sandbox control is back"

    docs = source_index.text(_CONFIG_DOC)
    assert "agent.sandbox" not in docs, "docs still document agent.sandbox as a real setting"

    sandbox_src = source_index.text(REPO / "src/personalclaw/sandbox.py")
    assert "agent.sandbox" not in sandbox_src, (
        "sandbox.py still claims agent.sandbox governs the backend — the docstring that "
        "made this field look wired in the first place"
    )


def test_no_dashboard_layout_config_field_on_any_surface():
    """#529 — the second half of a clean break that only removed one side.

    The customizable-bento dashboard was retired in the v2 launcher-forward redesign and
    the frontend half was deleted with it. The backend half outlived it by 46 days: a
    10-id widget registry, a 45-line validator clamping to a "12-col grid" that no longer
    existed, a PUT allowlist entry, a write branch, a GET echo, the config field, and the
    typed client member. Measured before deleting, `dashboard_layout` had **nine** live
    sites across `src/` and `web/src` and **zero readers** — no code positioned, sized or
    hid anything from it. So `PUT {"dashboard_layout": {"widgets": [{"id": "tasks", …,
    "hidden": true}], "v": 1}}` returned 200, persisted, round-tripped — and the Tasks
    widget stayed visible. That is worse than an absent field: the stored data looks
    authoritative, so an app or MCP tool reading it would trust a layout the dashboard
    demonstrably ignores.

    One rail across every surface on purpose, following
    :func:`test_no_agent_sandbox_config_field_on_any_surface`: a per-surface check passes
    while any single surface still carries the field, which is the exact shape that left
    a "retired" mechanism fully writable for 46 days. Its positive half, that the
    dashboard-config PUT still refuses an unknown field, is
    `test_the_dashboard_config_put_still_rejects_an_unknown_field` in
    ``tests/test_config_roundtrip.py``.
    """
    from personalclaw.config.editable import _EDITABLE_CONFIG
    from personalclaw.config.loader import DashboardConfig
    from personalclaw.dashboard.handlers import files as F

    assert "dashboard_layout" not in {
        f.name for f in fields(DashboardConfig)
    }, "DashboardConfig.dashboard_layout is back: a persisted grid layout with no renderer"
    assert (
        "dashboard_layout" not in AppConfig().to_dict()["dashboard"]
    ), "dashboard.dashboard_layout is serialized into config.json again"
    assert (
        "dashboard.dashboard_layout" not in _EDITABLE_CONFIG
    ), "dashboard.dashboard_layout is PATCH-writable again"
    for gone in ("_sanitize_dashboard_layout", "_DASHBOARD_WIDGET_IDS"):
        assert not hasattr(F, gone), f"{gone} is back — the validator without a consumer"

    handlers = source_index.text(REPO / "src/personalclaw/dashboard/handlers/files.py")
    assert (
        "dashboard_layout" not in handlers
    ), "the dashboard-config PUT allowlist or its GET echo names dashboard_layout again"
    client = source_index.text(REPO / "web/src/lib/api.ts")
    assert "dashboard_layout?" not in client, "the typed client field is back"
    docs = source_index.text(_CONFIG_DOC)
    assert (
        "dashboard.dashboard_layout" not in docs
    ), "docs still document dashboard.dashboard_layout as a real setting"
