"""Issue #3490 — a Settings control that writes a config leaf NOBODY reads.

Seven controls (six switches plus one behind a confirmation dialog) PATCHed **200**, persisted
under their declared path, and survived a reload and a gateway restart, while nothing in the
tree consumed the value. The round-trip contract was fully satisfied — which is exactly why
``test_config_roundtrip.py`` and ``test_settings_control_coverage.py`` are both green with all
seven inert: the first proves a field SURVIVES a save/load, the second proves a WRITE PATH
reaches it, and neither asks whether a reader exists. The inert-surface census did record
all seven, but it was a shrink-only ratchet pinned at the measured population, so they were
blessed rather than reported.

``RoutingPanel.tsx`` states the rule this file enforces, and acted on it for
``routing.energy_sampling``: *"an inert knob needs its reader wired or its allowlist row
dropped; a control would only make a promise the code ignores more convincing."* Both halves
are exercised here — three leaves got the reader, four lost the leaf.

WIRED (the feature existed and the key was simply never connected):

* ``agent.self_qa.fix_branch_enabled`` — the sharpest case, because the product spends a
  confirmation dialog on it. ``selfqa/fix_branch.py`` implements the branch, the evidence
  provider gates on it, and the template declares an input of the SAME NAME and routes a
  branch on it. Nothing joined the two: ``selfqa/watch.py`` supplied ``{"repo", "commits"}``,
  so ``with_declared_defaults`` filled the template's own ``false`` forever.
* ``ambient.tiles_enabled`` — the composable home ships, and the two bounds beside this switch
  (``max_tiles``, ``default_refresh_ttl_secs``) were already read; only the master switch was
  inert. Now ``views_store`` composes an empty overlay and refuses a pin when it is off.
* ``ambient.genui_enabled`` — ``visualize()`` is documented as the ONE funnel every genui
  producer goes through, so one gate keeps the promise for all of them.

DELETED (no reader was intended, so the leaf, its allowlist row and its control are gone):

* ``ambient.surfaces_max_layer`` — ``surface_layers.py`` argues the layer ceiling is a PROCESS
  latch and not config ("persisting it would mean a user who recovered once boots into safe
  mode forever") and ships two levers for it; the control's own hint also misdescribed the
  layers it claimed to set ("1 = + tiles" vs L1 = app surfaces).
* ``ambient.tray_enabled`` — menu-bar presence is the Electron shell's. ``desktop/main.js``
  starts the tray unconditionally and reports it truthfully as the ``tray`` desktop
  capability, so this leaf's default (off) contradicted shipped behaviour and honouring it
  would have removed the menu-bar item from every desktop user.
* ``sources.daily_request_budget`` — a rolling-day cap needs a per-source request tally, and
  ``SourceEngine._emit_poll_completed`` records the measurement that no shipped provider
  reports ``requests_used`` at all. The allowance that IS enforced is per-source and per-poll
  (``budget.max_requests``, counted by ``web_source._Budget``).
* ``packs.connector_catalog_url`` — the refresh it existed for does not exist.
  ``packs/connectors.py`` deferred it to "a later change" and did not read the URL either.

🪤 THE FRONTEND SCAN STRIPS COMMENTS FIRST, AND SAYS SO HERE BECAUSE THE FIX IS WHAT WOULD
TRIP IT. Each deletion leaves a comment in its panel naming the leaf it removed and why — so
a raw text scan would read the explanation of the fix as the defect, the failure mode this
repo has now had at least five times. :func:`_control_sites` therefore strips comments, and
:func:`test_the_frontend_scan_is_not_vacuous` proves the stripped scan still finds the
controls that DO exist, because a zero from a matcher that stopped resolving is
indistinguishable from a clean tree.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from typing import Any
from unittest.mock import MagicMock

import pytest
from inert_settings_controls_shared import DELETED, REPO

#: The three leaves that got a reader instead. Kept beside the deletions on purpose: the two
#: halves of the rule are one decision per key, and a rail that only checked one half would
#: pass on "delete everything".
WIRED: tuple[str, ...] = (
    "agent.self_qa.fix_branch_enabled",
    "ambient.tiles_enabled",
    "ambient.genui_enabled",
)


# ── the scan's own controls ───────────────────────────────────────────────────────────────


# ── the deletions: gone from every surface the round-trip contract names ───────────────────


@pytest.mark.parametrize("path_key,cls_name,leaf", DELETED, ids=[d[0] for d in DELETED])
def test_a_deleted_leaf_is_gone_from_the_dataclass_and_its_meta(
    path_key: str, cls_name: str, leaf: str
) -> None:
    """Contract points 1-2: no dataclass field, so no ``_meta`` help to promise anything."""
    from personalclaw.config import loader as loader_mod

    cls = getattr(loader_mod, cls_name)
    names = {f.name for f in fields(cls)}
    assert leaf not in names, f"{path_key} is still declared on {cls_name}"


@pytest.mark.parametrize("path_key,cls_name,leaf", DELETED, ids=[d[0] for d in DELETED])
def test_a_deleted_leaf_is_not_resurrected_by_load_or_carried_by_to_dict(
    path_key: str, cls_name: str, leaf: str, tmp_path, monkeypatch
) -> None:
    """Contract points 3-4, driven through the real file.

    A hand-written ``config.json`` (or one left behind by an install that HAD the key) carries
    the leaf; ``load()`` must not attach it to the section, and ``to_dict()`` must not write it
    back. Both directions matter: a section that silently round-tripped an unknown key would
    keep the dead value alive in every future save.
    """
    from personalclaw.config import loader as loader_mod

    section, _, _ = path_key.partition(".")
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({section: {leaf: 7}}), encoding="utf-8")
    monkeypatch.setattr(loader_mod, "config_path", lambda: cfg_file)
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)

    loaded = loader_mod.AppConfig.load()
    assert not hasattr(getattr(loaded, section), leaf), f"load() resurrected {path_key}"
    assert leaf not in loaded.to_dict()[section], f"to_dict() still carries {path_key}"


@pytest.mark.parametrize("path_key,cls_name,leaf", DELETED, ids=[d[0] for d in DELETED])
def test_a_deleted_leaf_has_no_write_path(path_key: str, cls_name: str, leaf: str) -> None:
    """Contract point 5: no ``_EDITABLE_CONFIG`` row, which is the exact predicate the PATCH
    handler evaluates (``spec = _EDITABLE_CONFIG.get(path_key)``; a falsy spec is refused with
    "field not editable"). So the write that used to return 200 now returns 400."""
    from personalclaw.config.editable import _EDITABLE_CONFIG

    assert not _EDITABLE_CONFIG.get(path_key), f"{path_key} is still allowlisted for PATCH"


@pytest.mark.parametrize("path_key,cls_name,leaf", DELETED, ids=[d[0] for d in DELETED])
def test_a_deleted_leaf_is_gone_from_the_generated_schema_census(
    path_key: str, cls_name: str, leaf: str
) -> None:
    """And from ``config-baseline.json``, the generated schema census. Asserted against the
    COMMITTED file rather than a fresh render: ``test_config_baseline.py`` already proves the
    two match, so reading the committed copy here also proves it was regenerated in this
    commit instead of left stale for CI to catch."""
    committed = json.loads((REPO / "config-baseline.json").read_text(encoding="utf-8"))
    assert path_key not in {e["path"] for e in committed}


def test_the_inert_population_shrank_by_exactly_these_seven() -> None:
    """The census's committed site list, read for DIRECTION.

    The list only shrinks, and deleting a line is legitimate only when the census stopped
    finding the surface — a surface that is NEW means a reader is missing, and listing it reads
    green while guarding nothing. Seven ``config_reader`` surfaces left the census here (four
    leaves deleted, three given readers), so the list holds exactly these, by name: the
    frontend-read ``voice.*`` leaves the Python census cannot see (five since
    ``voice.session_map_audio_enabled``, which the chat page reads for the Session Map),
    ``routing.energy_sampling`` (deliberately control-less), and ``learning.min_evidence``.
    """
    from scripts.generate_inert_surface_baseline import listed

    surfaces = {site.split("::", 1)[1] for site in listed()}
    assert {s for s in surfaces if s.startswith("config_reader:")} == {
        "config_reader:learning.min_evidence",
        "config_reader:routing.energy_sampling",
        "config_reader:voice.confirmation_phrases",
        "config_reader:voice.duplex_mute_enabled",
        "config_reader:voice.exit_phrases",
        "config_reader:voice.push_to_talk_chord",
        "config_reader:voice.session_map_audio_enabled",
    }
    for path_key, _cls, _leaf in DELETED:
        assert f"config_reader:{path_key}" not in surfaces
    for path_key in WIRED:
        assert f"config_reader:{path_key}" not in surfaces


# ── wire 1: agent.self_qa.fix_branch_enabled reaches the gate ─────────────────────────────


@pytest.fixture
def watch_state(tmp_path, monkeypatch):
    """Isolate the commit-watch state file. ``state_path()`` derives from ``config_dir()``,
    which conftest redirects once per SESSION, so without this the "first sight" verdict
    depends on test ordering."""
    from personalclaw.selfqa import watch as watch_mod

    path = tmp_path / "selfqa" / "commit_watch.state.json"
    monkeypatch.setattr(watch_mod, "state_path", lambda: path)
    return path


@pytest.fixture
def stub_git(monkeypatch):
    """Replace the watcher's read-only git with a fixed HEAD.

    No repo on disk: this test is about the INPUTS the fire carries, and a real repo would add
    a git dependency to an assertion that has nothing to do with git.
    """
    from personalclaw.selfqa import watch as watch_mod

    head = "a" * 40

    def fake_git(repo, *args):
        return head if args[:1] == ("rev-parse",) else ""

    monkeypatch.setattr(watch_mod, "_git", fake_git)
    return head


def _set_fix_branch(monkeypatch, value: bool) -> None:
    from personalclaw.config import loader as loader_mod

    cfg = loader_mod.AppConfig()
    cfg.agent.self_qa.fix_branch_enabled = value
    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))


@pytest.mark.parametrize("configured", [True, False])
def test_the_fire_carries_the_configured_fix_branch_decision(
    configured: bool, watch_state, stub_git, monkeypatch
) -> None:
    """🔑 THE DEFECT. The template declares ``fix_branch_enabled`` and routes its ``fix-route``
    branch on it; the fire supplied only ``{"repo", "commits"}``, so the user's switch could
    not reach the gate by any route. Both values are asserted in one parametrization because a
    producer hardcoding either one would pass a single-value test.
    """
    from personalclaw.selfqa import watch as watch_mod

    _set_fix_branch(monkeypatch, configured)
    watch_mod.check("/tmp/watched")  # first sight records HEAD and stays quiet
    watch_state.write_text(json.dumps({"repo": "/tmp/watched", "last_sha": "b" * 40}), "utf-8")

    fire = watch_mod.check("/tmp/watched")
    assert fire.inputs is not None
    assert fire.inputs["fix_branch_enabled"] is configured
    # The pre-existing inputs are untouched: this is an added join, not a replacement.
    assert fire.inputs["repo"] == "/tmp/watched"
    assert fire.inputs["commits"] == ["a" * 40]


def test_the_supplied_name_is_the_one_the_template_declares() -> None:
    """The join itself. The config leaf and the workflow input already shared a NAME and
    nothing connected them, so a producer supplying ``fixBranch`` or ``fix_branch`` would look
    wired and route the same dead branch. Read from the shipped template, not a literal.
    """
    spec = json.loads(
        (REPO / "src/personalclaw/workflows/bundled/self-qa/workflow.json").read_text(
            encoding="utf-8"
        )
    )
    assert "fix_branch_enabled" in spec["inputs"]
    assert spec["inputs"]["fix_branch_enabled"]["type"] == "boolean"


def test_the_provider_hands_the_decision_to_run_workflow(
    watch_state, stub_git, monkeypatch
) -> None:
    """End of the seam: the value survives the delegation to the one starter that owns dedupe
    and supervisor registration. Asserting only on ``watch.check`` would leave the provider
    free to rebuild the inputs dict and drop the key again."""
    from personalclaw.action_providers import registry as reg
    from personalclaw.action_providers.base import ActionResult
    from personalclaw.action_providers.selfqa_watch_provider import (
        SelfQaCommitWatchActionProvider,
    )
    from personalclaw.selfqa import watch as watch_mod

    _set_fix_branch(monkeypatch, True)
    watch_mod.check("/tmp/watched")
    watch_state.write_text(json.dumps({"repo": "/tmp/watched", "last_sha": "b" * 40}), "utf-8")

    seen: list[dict[str, Any]] = []

    async def fake_execute(action_config, ctx, timeout=30):
        seen.append(action_config)
        return ActionResult(success=True, stdout="run started")

    runner = MagicMock()
    runner.execute = fake_execute
    monkeypatch.setattr(
        reg, "get_action_provider", lambda name: runner if name == "run-workflow" else None
    )

    result = asyncio.run(
        SelfQaCommitWatchActionProvider().execute({"repo": "/tmp/watched"}, MagicMock())
    )
    assert result.success
    assert seen and seen[0]["inputs"]["fix_branch_enabled"] is True


def test_an_unreadable_switch_is_off(monkeypatch) -> None:
    """Fails CLOSED, the one direction that matters here: this decides whether an unattended
    run writes a git ref, so a config read that blows up must not license one."""
    from personalclaw.config import loader as loader_mod
    from personalclaw.selfqa import watch as watch_mod

    def boom():
        raise OSError("config is unreadable")

    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(boom))
    assert watch_mod.fix_branch_enabled() is False


# ── wire 2: ambient.tiles_enabled gates the composable home ───────────────────────────────


@pytest.fixture
def views_home(tmp_path, monkeypatch):
    """A views store in ``tmp_path``. Never the real home — this fixture writes tiles."""
    from personalclaw.dashboard import views_store

    monkeypatch.setattr(views_store, "config_dir", lambda: tmp_path)
    return views_store


def _set_tiles(monkeypatch, value: bool) -> None:
    from personalclaw.config import loader as loader_mod

    cfg = loader_mod.AppConfig()
    cfg.ambient.tiles_enabled = value
    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))


def test_the_composable_home_switch_decides_what_the_overview_composes(
    views_home, monkeypatch
) -> None:
    """ON composes the pinned tile, OFF composes none — asserted as an exact 1-vs-0 in ONE
    test, so it carries its own vacuity floor: a gate that returned an empty overlay in both
    arms, or in neither, fails here rather than reading as a pass in one direction.

    And the row STAYS ON DISK while off. The switch gates composition, not the user's data:
    turning the home off must not destroy pins, and turning it back on must restore exactly
    the view they had.
    """
    store = views_home
    overview = store.PRESET_OVERVIEW_ID

    _set_tiles(monkeypatch, True)
    store.add_tile(overview, "artifact:report", size="m")
    on_view = store.get_view(overview)
    assert on_view is not None
    assert len([t for t in on_view.tiles if t.ref.startswith("artifact:")]) == 1

    _set_tiles(monkeypatch, False)
    off_view = store.get_view(overview)
    assert off_view is not None
    assert [t for t in off_view.tiles if t.ref.startswith("artifact:")] == []
    # The core band is untouched: OFF is the shipped fixed layout, not an empty dashboard.
    assert off_view.tiles, "the preset's code-defined core refs must still render"
    raw = json.loads((store.views_path()).read_text(encoding="utf-8"))
    assert raw["overlay"][overview][0]["ref"] == "artifact:report", "the pin was destroyed"

    _set_tiles(monkeypatch, True)
    back = store.get_view(overview)
    assert back is not None
    assert [t.ref for t in back.tiles if t.ref.startswith("artifact:")] == ["artifact:report"]


def test_a_pin_is_refused_while_the_composable_home_is_off(views_home, monkeypatch) -> None:
    """A write into a surface that renders nothing is a swallowed write: it would report
    success and never appear. Both call sites (the pin endpoint, the agent's propose tool)
    already map ``ValueError`` to a caller-facing refusal, so saying no here is what lets
    them say why."""
    store = views_home
    _set_tiles(monkeypatch, False)
    with pytest.raises(ValueError, match="composable home is off"):
        store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:report")


def test_an_unreadable_composable_home_switch_composes(views_home, monkeypatch) -> None:
    """Fails OPEN, deliberately the opposite polarity from the fix-branch switch: this gates a
    read-only composition and spends nothing, so a config blip must not blank a dashboard the
    user populated."""
    from personalclaw.config import loader as loader_mod

    def boom():
        raise OSError("config is unreadable")

    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(boom))
    assert views_home.tiles_enabled() is True


# ── wire 3: ambient.genui_enabled gates the one generative-UI primitive ───────────────────


def _set_genui(monkeypatch, value: bool) -> None:
    from personalclaw.config import loader as loader_mod

    cfg = loader_mod.AppConfig()
    cfg.ambient.genui_enabled = value
    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))


def test_generative_ui_off_refuses_the_primitive_before_spending_a_call(monkeypatch) -> None:
    """ON authors a widget, OFF refuses — and the injected completion is NEVER reached in the
    OFF arm, so the switch also costs no tokens. One test, both arms, with the call count as
    the floor: a gate placed after ``_build_prompt`` would still pay for the refusal."""
    from personalclaw import visualize as viz

    calls: list[str] = []

    async def completion(prompt: str, use_case: str = "reasoning") -> str:
        calls.append(use_case)
        return 'a = StatTile("Runs", "12")'

    _set_genui(monkeypatch, True)
    result = asyncio.run(viz.visualize({"runs": 12}, "show it", completion=completion))
    assert "StatTile" in result.widget
    assert len(calls) == 1

    _set_genui(monkeypatch, False)
    with pytest.raises(viz.GenUiDisabled):
        asyncio.run(viz.visualize({"runs": 12}, "show it", completion=completion))
    assert len(calls) == 1, "the refusal must not reach the model"


def test_the_visualize_tool_names_the_switch_and_not_the_model_stack(monkeypatch) -> None:
    """The refusal a user actually reads. The generic arm advises binding a reasoning model —
    the wrong Settings page for a switch they set themselves, which is why the disabled case
    is caught by type before it."""
    from personalclaw import mcp_artifacts

    _set_genui(monkeypatch, False)
    out = mcp_artifacts._visualize({"data": {"runs": 12}}, lambda *a, **k: None)
    assert "Generative UI" in out
    assert "reasoning model" not in out


def test_the_workflow_node_fails_user_with_the_switch_named(monkeypatch) -> None:
    """A run refused because the operator turned generative UI off is a USER failure carrying
    its fix, not the transport class ``classify_exception`` would assign to an unknown
    exception — a provider fault invites a retry that can never succeed."""
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import FailureClass, InstanceState, Node, NodeKind

    _set_genui(monkeypatch, False)
    node = Node(kind=NodeKind.VISUALIZE, id="viz", config={"data": {"runs": 12}})
    ctx = BindingContext(inputs={}, node_outputs={})
    result = asyncio.run(engine.dispatch_visualize(node, ctx, completion=None))
    assert result.state is InstanceState.FAILED
    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.USER
    assert not result.failure.retryable, "a retry can never clear an operator's switch"
    assert "Generative UI" in result.failure.cause_plain
    assert result.failure.remediation, "a USER failure owes the user a next step"
