"""A workflow automation's Allow survives a release that adds or removes a defaulted field, and a
release that changes what a default does asks again, saying so.

🔴 The defect (Backlog: "Workflow version digests hash defaulted fields, so a product change voids
every Allow"). An automation's Allow records the version of the workflow it runs and a digest of
what that version runs (`versions.digest`). The digest was taken over the definition read through
the model with every default filled in (`runnable`), so a release that added or removed a
defaulted field anywhere in the definition model changed every workflow's digest, and every
recorded version's too. Every workflow automation then stopped with "“X” has changed since this
automation was allowed to run it, and the version it was allowed (v3) is no longer kept here",
and offered "Use v3" to replace v3, though the workflow had not changed.

Now the digest is of what the definition declares (`versions.declared`: every field holding its
model default left out), under a definition-model revision (`versions.DEFINITION_REVISION`) bumped
by hand when a default's VALUE changes what a workflow does, which the maintainer settled re-asks.
An Allow recorded before is carried over, once, at start (`versions.remember_digests`), without
rewriting the append-only history. A bump says a PersonalClaw default changed, and the row offers
"Allow again", never "Use v<the same version>".

The end-to-end tests drive the doors themselves, as
`test_an_automation_runs_the_workflow_version_its_owner_allowed` does: the editor's save, the
owner's Allow, the automation's fire, its Triggers row and its Use route.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_an_automation_runs_the_workflow_version_its_owner_allowed import (  # noqa: F401
    NAME,
    TRIGGER_ID,
    H,
    _agents_tool_save,
    _allowed_automation,
    _editor_save,
    _native_store,
    _one_home,
    _reply,
    _request,
    _root,
    _stored,
    _use,
    anyio_backend,
    engine,
)

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.config import loader as config_loader
from personalclaw.triggers import grants
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import automation_version, versions
from personalclaw.workflows.models import InputParam, Node, NodeKind, RunDefaults, WorkflowDef

#: The defaults rail's snapshot: the definition model's default values, and the revision they were
#: taken under (`versions.model_defaults`, `versions.DEFINITION_REVISION`).
SNAPSHOT = Path(__file__).parent / "fixtures" / "workflow_definition_defaults.json"


def _spec(**fields: Any) -> dict[str, Any]:
    return {"name": "weekly-report", "root": _root("v1"), **fields}


def _a_release_adds_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """A release that adds a field with its default to the definition, one to its run defaults
    and one to each input: every definition read through the model now writes all three."""
    top, nested, param = WorkflowDef.to_dict, RunDefaults.to_dict, InputParam.to_dict
    monkeypatch.setattr(WorkflowDef, "to_dict", lambda self: {**top(self), "max_retries": 0})
    monkeypatch.setattr(RunDefaults, "to_dict", lambda self: {**nested(self), "retry_secs": 0})
    monkeypatch.setattr(InputParam, "to_dict", lambda self: {**param(self), "secret": False})


# ── the digest is of what the definition declares ──


def test_a_field_added_with_its_default_leaves_the_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 Before: the round-trip wrote the new fields into every definition, and every digest
    changed: at the top, in the run defaults, and in each input."""
    plain, with_input = _spec(), _spec(inputs={"topic": {"required": True}})
    before = versions.digest(plain), versions.digest(with_input)
    _a_release_adds_fields(monkeypatch)
    assert (versions.digest(plain), versions.digest(with_input)) == before


def test_a_defaulted_field_removed_leaves_the_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 Before: removing a field every definition held at its default (as the run-cost cap's
    change removed `max_retries`) changed every digest."""
    spec = _spec()
    before = versions.digest(spec)
    top = WorkflowDef.to_dict
    monkeypatch.setattr(
        WorkflowDef, "to_dict", lambda self: {k: v for k, v in top(self).items() if k != "tags"}
    )
    assert versions.digest(spec) == before


def test_a_field_set_to_its_default_is_the_same_declaration() -> None:
    """A field written at its default and the same field left unset are one declaration: at the
    top, in the run defaults and their budget, in the metadata, in an input, and in a step."""
    plain = _spec(inputs={"topic": {}})
    spelled = _spec(
        on_overlap="skip",
        tags=[],
        defaults={"model_tier": "standard", "budget": {"max_tokens": 0, "max_cost": 0.0}},
        metadata={"risk": "low", "guided": False},
        inputs={"topic": {"type": "string", "required": False, "help": ""}},
    )
    spelled["root"]["children"][0]["label"] = ""
    assert versions.digest(spelled) == versions.digest(plain)


def test_what_a_workflow_declares_still_changes_its_digest() -> None:
    """The control: a step, a run default or an input it sets is what it runs, so each still
    changes the digest."""
    base = versions.digest(_spec())
    assert versions.digest({**_spec(), "root": _root("v2")}) != base
    assert versions.digest(_spec(defaults={"model_tier": "deep"})) != base
    assert versions.digest(_spec(inputs={"topic": {"required": True}})) != base


def test_a_revision_bump_changes_the_digest_of_an_unchanged_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that changes what a default does bumps the revision, and every Allow it covers is
    asked again: the same definition has another digest, said as revised, not changed."""
    spec = _spec()
    before = versions.digest(spec)
    monkeypatch.setattr(versions, "DEFINITION_REVISION", versions.DEFINITION_REVISION + 1)
    assert versions.digest(spec) != before
    assert versions.revised_since(before, spec)
    assert not versions.revised_since(before, {**spec, "root": _root("v2")})


# ── an Allow given before is carried over ──


async def _fire() -> Any:
    return await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="manual", trigger_id=TRIGGER_ID)
    )


def _as_allowed_before(version: int) -> None:
    """Make the stored automation's Allow what a build before this one recorded: the round-trip
    digest of the version it was given for (`versions.round_trip_digest`)."""
    record = versions.get_version(NAME, version)
    assert record is not None
    store = TriggerStore(base_dir=config_loader.config_dir())
    trigger = _stored()
    block = dict(trigger.capabilities or {})
    block["workflow"] = {**block["workflow"], "digest": versions.round_trip_digest(record.spec)}
    trigger.capabilities = block
    store.upsert(trigger)


@pytest.mark.anyio
async def test_an_allow_given_before_still_runs_after_a_model_change(
    engine: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811 - the imported fixture
) -> None:
    """🔴 Before: the release's new defaulted fields changed every digest, the Allow matched no
    recorded version, and the fire refused with "has changed since…, Use v1"."""
    await _editor_save("v1")
    _allowed_automation()
    _as_allowed_before(1)
    assert versions.remember_digests() == 1, "the start notes v1's earlier digest"
    _a_release_adds_fields(monkeypatch)
    result = await _fire()
    assert result.success, result.error
    run, spec = engine.launched[-1]
    assert run.spec_version == 1
    assert spec["root"]["children"][-1]["config"]["expr"] == "v1"


@pytest.mark.anyio
async def test_an_allow_given_before_follows_a_switch_that_changes_no_step(
    engine: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811 - the imported fixture
) -> None:
    """The earlier digest is read as its version's digest now before it is held to the definition
    as it is, so a later version that only flips the publish switch runs as the one allowed, and
    is what runs: v2, not the recorded v1."""
    await _editor_save("v1")
    _allowed_automation()
    _as_allowed_before(1)
    versions.remember_digests()
    _a_release_adds_fields(monkeypatch)
    published = await H.api_def_a2a_publish(
        _request(
            "POST", f"/api/workflows/{NAME}/a2a-publish", {"published": True}, match={"name": NAME}
        )
    )
    assert _reply(published)["a2a_published"] is True
    result = await _fire()
    assert result.success, result.error
    assert engine.launched[-1][0].spec_version == 2


@pytest.mark.anyio
async def test_an_allow_given_before_runs_its_own_version_not_a_later_copy(
    engine: Any,  # noqa: F811 - the imported fixture
) -> None:
    """Read through its version's digest now, an earlier Allow finds the version it was given
    for, though a later one an agent saved runs alike: v1 runs, not its copy v3."""
    await _editor_save("v1")
    _allowed_automation()
    _as_allowed_before(1)
    versions.remember_digests()
    for said in ("v2", "v1", "v4"):  # v3 runs as v1 does; v4 is the definition as it is
        _agents_tool_save(said)
    result = await _fire()
    assert result.success, result.error
    run, spec = engine.launched[-1]
    assert run.spec_version == 1
    assert spec["root"]["children"][-1]["config"]["expr"] == "v1"


@pytest.mark.anyio
async def test_the_start_notes_the_earlier_digests_once(
    engine: Any,  # noqa: F811 - the imported fixture
) -> None:
    """Noted once, while the model is the one those digests were taken under: a second start
    writes nothing, and the history is never rewritten."""
    await _editor_save("v1")
    record = versions.get_version(NAME, 1)
    assert record is not None
    history = (versions._version_path(NAME, 1)).read_bytes()
    assert versions.remember_digests() == 1
    noted = versions._renamed_path()
    at = noted.stat().st_mtime_ns
    assert versions.remember_digests() == 0
    assert noted.stat().st_mtime_ns == at
    assert versions._version_path(NAME, 1).read_bytes() == history
    assert versions.current_form(versions.round_trip_digest(record.spec)) == versions.digest(
        record.spec
    )


@pytest.mark.anyio
async def test_a_steps_pin_recorded_before_still_starts_its_version(
    engine: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811 - the imported fixture
) -> None:
    """A run's pinned step versions and a saved version's steps (`VersionRecord.calls`) recorded
    the round-trip digest too; carried over the same way, without rewriting them."""
    await _editor_save("v1")
    record = versions.get_version(NAME, 1)
    assert record is not None
    pin = automation_version.Allowed(NAME, 1, versions.round_trip_digest(record.spec))
    versions.remember_digests()
    _a_release_adds_fields(monkeypatch)
    now = automation_version.current_now(NAME)
    assert now is not None
    ran = automation_version.step_runs({NAME: pin}, now)
    assert ran.spec is not None and ran.version == 1, ran.problem


# ── a release that changes what a default does asks again, in its own words ──


@pytest.mark.anyio
async def test_a_revision_bump_asks_again_and_says_a_default_changed(
    engine: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811 - the imported fixture
) -> None:
    """A bump re-asks every Allow it covers: the fire runs nothing, and says the workflow has not
    changed but a PersonalClaw default it runs with has; the row offers "Allow again" for the same
    version, its question says so, and the owner's yes lets it run again."""
    await _editor_save("v1")
    _allowed_automation()
    monkeypatch.setattr(versions, "DEFINITION_REVISION", versions.DEFINITION_REVISION + 1)

    refused = await _fire()
    assert not refused.success
    said = refused.error or ""
    assert "has not changed" in said and "a PersonalClaw update changed a default" in said, said
    assert "Allow again" in said and "Use v1" not in said, said

    row = grants.workflow_version(_stored())
    assert row is not None
    assert (row["revised"], row["allowed"], row["use"]) == (True, 1, {"version": 1, "steps": {}})
    asked = grants.use_version_question(_stored())
    assert asked is not None
    assert asked.title == f"Allow version 1 of “{NAME}” again?"
    assert "has not changed since" in asked.sentence and "no longer kept" not in asked.sentence
    assert grants.use_version_change(_stored()) == "v1, with PersonalClaw's changed default"

    allowed = _reply(await _use(1, confirm=True))
    assert allowed.get("ok"), allowed
    result = await _fire()
    assert result.success, result.error
    assert engine.launched[-1][0].spec_version == 1


# ── the defaults rail ──


def _drift(snapshot: dict[str, Any], now: dict[str, Any], at: str = "") -> list[str]:
    """Each default *now* holds at another value than *snapshot* does, by its path; fields only one
    of them holds are not drift (adding or removing a defaulted field needs no bump)."""
    out: list[str] = []
    for key in sorted(set(snapshot) & set(now)):
        was, held = snapshot[key], now[key]
        path = f"{at}.{key}" if at else key
        if isinstance(was, dict) and isinstance(held, dict):
            out.extend(_drift(was, held, path))
        elif was != held:
            out.append(f"{path}: {was!r} -> {held!r}")
    return out


def _shape(value: Any) -> Any:
    return {k: _shape(v) for k, v in value.items()} if isinstance(value, dict) else None


def test_a_default_whose_value_changed_bumps_the_revision() -> None:
    """The rail: a default whose VALUE changed is a change to what every workflow that leaves it
    unset does, so it ships with a bump of `versions.DEFINITION_REVISION`, which asks every Allow
    again. A field added or removed is not (its digest is the same); the snapshot is retaken."""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    now = versions.model_defaults()
    drift = _drift(snapshot["defaults"], now)
    if drift and versions.DEFINITION_REVISION <= snapshot["revision"]:
        pytest.fail(
            "A definition-model default changed its value, which changes what every workflow that "
            "leaves it unset does: bump versions.DEFINITION_REVISION (every Allow it covers is "
            f"asked again), then retake {SNAPSHOT.name}. Changed: " + "; ".join(drift)
        )
    assert _shape(now) == _shape(snapshot["defaults"]), (
        f"A field was added to or removed from the definition model: retake {SNAPSHOT.name} "
        "(no revision bump is needed for that)."
    )
    assert snapshot["revision"] == versions.DEFINITION_REVISION, f"retake {SNAPSHOT.name}"


@pytest.mark.parametrize("kind", list(NodeKind), ids=lambda k: k.value)
def test_a_bare_step_writes_its_kind_alone(kind: NodeKind) -> None:
    """The digest keeps a definition's steps as the model writes them (`versions.declared`), so it
    leaves out a step's defaulted fields only while the model writes a step sparse: a field written
    whatever it holds would change every digest, and ask every Allow again, the defect this fixes
    one level down. A step field that must be written at its default needs a revision bump."""
    assert Node(kind=kind).to_dict() == {"kind": kind.value}


def test_the_rail_catches_a_changed_default() -> None:
    """The rail's own check: a test copy of the model whose run defaults' model tier changed its
    value drifts; one that only gained a field does not."""
    now = versions.model_defaults()
    changed = json.loads(json.dumps(now))
    changed["definition"]["defaults"]["model_tier"] = "fast"
    assert _drift(now, changed) == ["definition.defaults.model_tier: 'standard' -> 'fast'"]
    grown = json.loads(json.dumps(now))
    grown["definition"]["defaults"]["retry_secs"] = 0
    assert _drift(now, grown) == []
