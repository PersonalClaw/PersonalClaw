"""Tests for the legacy loop-kind alias layer and cockpit key equivalence.

Two properties carry the weight. Every alias must point at a template that actually
ships — an alias to a missing template is a dead reference that only fails when a user
clicks it. And the backend and frontend tables must agree, because the picker offering
something the backend cannot resolve is the same dead reference wearing a UI.
"""

import json
import re
from pathlib import Path

import pytest

from personalclaw.workflows.bundled_defs import template_names
from personalclaw.workflows.loop_aliases import (
    KIND_TO_TEMPLATE,
    TOOL_TO_TEMPLATE,
    VARIANT_HINTS,
    alias_manifest,
    aliased_kinds,
    base_container,
    keys_equivalent,
    resolve_kind,
    resolve_tool,
)

FE_MODULE = Path(__file__).resolve().parents[1] / "web/src/pages/workflows/containerKey.ts"


# ── every alias points somewhere real ──


@pytest.mark.parametrize("kind", sorted(KIND_TO_TEMPLATE))
def test_every_aliased_kind_resolves_to_a_shipped_template(kind):
    """An alias to a template that does not exist is a dead reference that only fails when
    a user clicks it."""
    assert KIND_TO_TEMPLATE[kind] in set(template_names())


@pytest.mark.parametrize("tool", sorted(TOOL_TO_TEMPLATE))
def test_every_aliased_tool_resolves_to_a_shipped_template(tool):
    assert TOOL_TO_TEMPLATE[tool] in set(template_names())


@pytest.mark.parametrize("hint", sorted(VARIANT_HINTS))
def test_every_variant_hint_resolves_to_a_shipped_template(hint):
    assert VARIANT_HINTS[hint] in set(template_names())


def test_every_loop_kind_that_exists_has_an_alias():
    """A kind with no alias is a legacy reference that silently stops working."""
    from personalclaw.loop.loop import KINDS

    assert set(KINDS) <= set(KIND_TO_TEMPLATE), set(KINDS) - set(KIND_TO_TEMPLATE)


# ── resolution ──


def test_a_bare_goal_resolves_to_the_open_ended_variant():
    """A goal loop with no verify command is open-ended by definition; the verifiable
    variant would demand an input the legacy reference never had."""
    assert resolve_kind("goal") == "goal-pursuit-open-ended"


def test_a_verify_command_reads_as_the_verifiable_variant():
    """A goal loop with a command that proves it WAS the verifiable variant in all but
    name. Ignoring that drops the user into a template that discards their input."""
    assert resolve_kind("goal", has_verify_command=True) == "goal-pursuit-verifiable"


def test_an_explicit_variant_wins_over_the_inferred_one():
    assert (
        resolve_kind("goal", variant="open-ended", has_verify_command=True)
        == "goal-pursuit-open-ended"
    )


def test_only_goal_is_variant_sensitive():
    """A verify command on a research loop does not make it a verifiable goal."""
    assert resolve_kind("research", has_verify_command=True) == "deep-research"


def test_an_unknown_kind_resolves_to_nothing():
    """No default. Running the wrong workflow is harder to debug than running none,
    because "it ran something" hides the mistake."""
    for junk in ("", "   ", "nonsense", "loop"):
        assert resolve_kind(junk) == ""


def test_stored_references_survive_case_and_whitespace():
    """These come out of months-old transcripts and saved crons."""
    assert resolve_kind("GOAL") == "goal-pursuit-open-ended"
    assert resolve_kind(" code ") == "code-project"


def test_legacy_tool_names_resolve():
    assert resolve_tool("loop_create_research") == "deep-research"
    assert resolve_tool("LOOP_START") == "general-project"


def test_an_unknown_tool_resolves_to_nothing():
    assert resolve_tool("not_a_tool") == ""
    assert resolve_tool("") == ""


def test_the_alias_layer_is_one_way():
    """A template must not resolve BACK to a loop kind: reverse lookup would invite
    writing new references in the legacy vocabulary, and an alias layer that accepts new
    writes is a second API rather than a bridge."""
    import personalclaw.workflows.loop_aliases as aliases

    exported = {name for name in dir(aliases) if not name.startswith("_")}
    assert not any("template_to" in name or "to_kind" in name for name in exported)
    assert alias_manifest()["one_way"] is True


def test_the_manifest_lists_what_still_needs_retiring():
    """A deprecation nobody can measure is a deprecation that never happens."""
    manifest = alias_manifest()
    assert set(manifest["kinds"]) == set(KIND_TO_TEMPLATE)
    assert manifest["tools"]
    assert aliased_kinds() == sorted(KIND_TO_TEMPLATE)


# ── cockpit key equivalence ──


@pytest.mark.parametrize(
    "left,right",
    [
        ("loop:abc", "run:abc"),
        ("loop:abc", "workflow:run:abc"),
        ("workflow:abc", "abc"),
        ("run:abc", "workflow:run:abc"),
    ],
)
def test_keys_for_the_same_container_are_equivalent(left, right):
    """The regression this closes is silent: the stream connects, the cockpit renders, no
    error appears, and nothing ever updates."""
    assert keys_equivalent(left, right)


@pytest.mark.parametrize(
    "left,right",
    [("loop:abc", "loop:xyz"), ("workflow:run:abc", "run:xyz"), ("", ""), ("loop:", "run:")],
)
def test_different_or_empty_keys_are_not_equivalent(left, right):
    """Two blanks matching would route every unkeyed event to every open cockpit."""
    assert not keys_equivalent(left, right)


def test_the_longest_prefix_is_stripped_first():
    """Shortest-first would leave `run:abc` behind and fail a comparison that should
    match."""
    assert base_container("workflow:run:abc") == "abc"
    assert base_container("workflow:abc") == "abc"


def test_an_unprefixed_key_is_already_a_base():
    assert base_container("abc") == "abc"
    assert base_container("  loop:abc  ") == "abc"
    assert base_container("") == ""


def test_equivalence_is_symmetric():
    assert keys_equivalent("loop:abc", "run:abc") == keys_equivalent("run:abc", "loop:abc")


# ── the backend↔frontend coupling ──


def _fe_table() -> dict[str, str]:
    """Parse the FE alias table out of the TypeScript module."""
    text = FE_MODULE.read_text(encoding="utf-8")
    block = text.split("KIND_TO_TEMPLATE: Readonly<Record<string, string>> = {", 1)[1]
    block = block.split("}", 1)[0]
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block))


def test_the_frontend_alias_table_matches_the_backend():
    """The picker offering a template the backend cannot resolve is a dead menu entry, and
    the drift would only show when a user clicked it."""
    assert _fe_table() == KIND_TO_TEMPLATE


def test_the_frontend_module_exists_where_the_test_expects_it():
    """A moved file would make the drift test silently vacuous."""
    assert FE_MODULE.is_file()


def test_the_frontend_key_prefixes_match_the_backend():
    """A divergence here reintroduces exactly the silent event-drop the equivalence
    function closes."""
    from personalclaw.workflows.loop_aliases import _KEY_PREFIXES

    text = FE_MODULE.read_text(encoding="utf-8")
    block = text.split("const KEY_PREFIXES = [", 1)[1].split("]", 1)[0]
    fe_prefixes = tuple(re.findall(r"'([^']+)'", block))
    assert fe_prefixes == _KEY_PREFIXES


def test_the_frontend_ships_its_own_tests():
    """A shared contract with tests on only one side drifts on the untested side."""
    assert (FE_MODULE.parent / "containerKey.test.ts").is_file()


def test_the_alias_manifest_is_json_serializable():
    """It goes over the API."""
    assert json.loads(json.dumps(alias_manifest()))


# ── mid-run steering (R14) ──


@pytest.fixture
def run_store(tmp_path, monkeypatch):
    """A real run in an isolated store — never the developer's own home.

    `store.py` does `from personalclaw.config.loader import config_dir`, a MODULE-LEVEL
    bind, so patching the loader attribute does not reach it: the name in `store` still
    points at the original function. Patching `store.config_dir` directly is the only
    thing that isolates it.

    This mattered concretely — the first version of this fixture leaked an `r-steer` run
    into the real home, and the leak surfaced three test files away as
    `test_custom_agent_gets_hook_transform` failing, because a live run makes
    `build_message` prepend an `[ACTIVE WORKFLOWS]` block to EVERY message.
    """
    from personalclaw.workflows import service, store

    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return service, store


def _make_run(service, store):
    """A RUNNING run. Field names read from the dataclass rather than guessed: `id` and
    `workflow_name`, not `run_id`/`def_name`."""
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = WorkflowRun(id="r-steer", workflow_name="general-project", status=RunStatus.RUNNING)
    store.save(run)
    return run


def test_a_steering_instruction_queues_on_the_run(run_store):
    service, store = run_store
    _make_run(service, store)
    result = service.steer_run("r-steer", "focus on the parser")
    assert result.get("ok") and result["queued"] == 1


def test_steering_instructions_keep_their_order(run_store):
    """The user's second thought usually refines the first, so reversing them would apply
    the refinement before the thing it refines."""
    service, store = run_store
    _make_run(service, store)
    service.steer_run("r-steer", "first")
    service.steer_run("r-steer", "second")
    pending = service.pending_steering("r-steer")["pending"]
    assert [p["text"] for p in pending] == ["first", "second"]


def test_an_empty_instruction_is_refused(run_store):
    service, store = run_store
    _make_run(service, store)
    for blank in ("", "   ", "\n"):
        assert service.steer_run("r-steer", blank)["code"] == "WF_STEER_EMPTY"


def test_steering_an_unknown_run_is_coded(run_store):
    service, _ = run_store
    assert service.steer_run("nope", "x")["code"] == "WF_RUN_NOT_FOUND"


def test_steering_a_terminal_run_is_refused(run_store):
    """A terminal run cannot act on an instruction, and silently accepting one would leave
    the user believing they had changed something."""
    service, store = run_store
    from personalclaw.workflows.models import RunStatus

    run = _make_run(service, store)
    run.status = RunStatus.COMPLETE
    store.save(run)
    assert service.steer_run("r-steer", "too late")["code"] == "WF_RUN_ALREADY_TERMINAL"


def test_pending_steering_on_an_unknown_run_is_coded(run_store):
    service, _ = run_store
    assert service.pending_steering("nope")["code"] == "WF_RUN_NOT_FOUND"


def test_pending_steering_is_empty_before_anything_is_queued(run_store):
    service, store = run_store
    _make_run(service, store)
    assert service.pending_steering("r-steer")["count"] == 0


def test_a_long_instruction_is_bounded(run_store):
    """An unbounded instruction becomes an unbounded prompt injection into every
    subsequent iteration."""
    service, store = run_store
    _make_run(service, store)
    service.steer_run("r-steer", "x" * 9000)
    assert len(service.pending_steering("r-steer")["pending"][0]["text"]) <= 4000


def test_the_steering_routes_are_registered():
    """A service function with no route is a feature the UI cannot reach."""
    import personalclaw.workflows.handlers as handlers

    assert hasattr(handlers, "api_run_steer")
    assert hasattr(handlers, "api_run_steering")


def test_the_steering_routes_are_documented():
    """The routes reference is CI-gated; an undocumented route is one nobody discovers."""
    doc = (Path(__file__).resolve().parents[1] / "src/personalclaw/reference/routes.md").read_text()
    assert "/steer`" in doc
    assert "/steering`" in doc


def test_the_frontend_can_reach_the_steering_endpoints():
    """A backend endpoint with no client method is unreachable from the UI."""
    api = (Path(__file__).resolve().parents[1] / "web/src/lib/api.ts").read_text()
    assert "steerWorkflowRun" in api
    assert "workflowSteering" in api


def test_the_manifest_advertises_the_loop_aliases():
    """The picker and any authoring model both need to know `kind: goal` still resolves."""
    from personalclaw.workflows.service import manifest

    aliases = manifest().get("loop_aliases") or {}
    assert set(aliases.get("kinds") or {}) == set(KIND_TO_TEMPLATE)
