"""Editing a workflow definition from the dashboard must not lose what the definition has.

The editor has exactly one read to start from — `GET /api/workflows/{name}`, which strips every
value held under a credential-shaped key down to a `_has_<key>` presence flag — and one
write to finish with, `POST /api/workflows`. Measured on `main` before this change, a save of that
read lost three kinds of thing:

* `runtime_hints` (and `defaults`, `on_overlap`): the save route never read them, `author_def`
  never took them and the native store never wrote `runtime_hints`. Nine bundled templates carry
  `runtime_hints` — the judge rubric and the loop invariants the engine enforces — so a copy of
  `code-project` saved through the API was a template with its judge contract gone;
* every value the read hides: the store wrote the `_has_<key>` flag itself. `paper-ingest`'s
  extraction schema declares an `authors` field, `authors` contains `auth`, so its copy saved a
  schema with `_has_authors: true` in place of the field;
* nothing refused either — the save answered 201.

`secrets.reinject_secrets` was written for exactly this ("re-injection on write, keyed by node
id") and had no production caller; it also only looked at a node's top-level `config` keys, which
`authors` (under `config.schema`) is not.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import secrets, service
from personalclaw.workflows.bundled_defs import BundledWorkflowDefProvider
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider, defs_root


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.fixture(autouse=True)
def _providers():
    """The two providers the dashboard sees: the shipped library and the user's own defs."""
    registered = []
    for provider in (BundledWorkflowDefProvider(), NativeWorkflowDefProvider()):
        if defs_mod.get_provider(provider.name) is None:
            defs_mod.register_provider(provider)
            registered.append(provider.name)
    yield
    for name in registered:
        defs_mod.unregister_provider(name)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _req(method: str, path: str, *, match_info=None, body=None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = None
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _body(resp) -> dict[str, Any]:
    return json.loads(resp.body.decode())


async def _read(name: str) -> dict[str, Any]:
    """What the editor opens: the detail route's (stripped) definition."""
    resp = await H.api_def_detail(_req("GET", f"/api/workflows/{name}", match_info={"name": name}))
    assert resp.status == 200, _body(resp)
    return _body(resp)["definition"]


def _editable(definition: dict[str, Any]) -> dict[str, Any]:
    """The save body the editor sends: everything the definition has except its bookkeeping."""
    skip = {"name", "version", "spec_semver", "source", "provenance", "created_at", "updated_at"}
    return {k: v for k, v in definition.items() if k not in skip}


async def _save(name: str, doc: dict[str, Any], **extra: Any):
    return await H.api_def_save(_req("POST", "/api/workflows", body={"name": name, **doc, **extra}))


def _stored(name: str) -> dict[str, Any]:
    """The definition as it sits on disk — what a run executes."""
    return json.loads((defs_root() / name / "workflow.json").read_text(encoding="utf-8"))


def _find(node: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if node.get("id") == node_id:
        return node
    kids = list(node.get("children") or [])
    kids += [n for n in (node.get("body"), node.get("default")) if isinstance(n, dict)]
    kids += list((node.get("cases") or {}).values())
    for kid in kids:
        hit = _find(kid, node_id)
        if hit is not None:
            return hit
    return None


def _flags(value: Any) -> list[str]:
    if isinstance(value, dict):
        own = [k for k in value if str(k).startswith("_has_")]
        return own + [f for v in value.values() for f in _flags(v)]
    if isinstance(value, list):
        return [f for v in value for f in _flags(v)]
    return []


# ── a copy of a shipped template keeps everything it had ────────────────────────


@pytest.mark.anyio
async def test_a_copy_of_a_bundled_template_keeps_its_runtime_hints() -> None:
    original = await _read("code-project")
    assert original.get("runtime_hints"), "the control: code-project ships runtime_hints"

    resp = await _save("my-code-project", _editable(original), based_on="code-project")

    assert resp.status == 201, _body(resp)
    saved = _stored("my-code-project")
    assert saved["runtime_hints"] == original["runtime_hints"]
    assert saved["on_overlap"] == original["on_overlap"]
    # EVERY definition's read hides `defaults.budget.max_tokens` (`RunDefaults` always writes it,
    # and it matches the `token` hint), so this is the value every editor save has to restore.
    assert original["defaults"]["budget"].get("_has_max_tokens") is True
    assert saved["defaults"]["budget"]["max_tokens"] == 0
    assert secrets.strip_secrets(saved["defaults"]) == original["defaults"]
    assert _flags(saved) == []


@pytest.mark.anyio
async def test_a_copy_keeps_the_value_its_read_hides() -> None:
    original = await _read("paper-ingest")
    identify = _find(original["root"], "identify")
    assert identify is not None
    # The control: the read really does hide it — `authors` matches the `auth` hint.
    assert identify["config"]["schema"].get("_has_authors") is True
    assert "authors" not in identify["config"]["schema"]

    resp = await _save("my-paper-ingest", _editable(original), based_on="paper-ingest")

    assert resp.status == 201, _body(resp)
    saved = _find(_stored("my-paper-ingest")["root"], "identify")
    assert saved is not None
    assert saved["config"]["schema"]["authors"] == ["string"]
    assert _flags(_stored("my-paper-ingest")) == [], "a presence flag must never be persisted"


@pytest.mark.anyio
async def test_editing_in_place_keeps_a_hidden_value_and_takes_the_edit() -> None:
    root = {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "infer", "id": "draft", "config": {"prompt": "Draft it", "max_tokens": 900}}
        ],
    }
    first = await _save("drafter", {"description": "Drafts things", "root": root})
    assert first.status == 201, _body(first)

    opened = await _read("drafter")
    draft = _find(opened["root"], "draft")
    assert draft is not None and draft["config"].get("_has_max_tokens") is True
    draft["config"]["prompt"] = "Draft it twice"

    resp = await _save("drafter", _editable(opened))

    assert resp.status == 201, _body(resp)
    saved = _find(_stored("drafter")["root"], "draft")
    assert saved is not None
    assert saved["config"] == {"prompt": "Draft it twice", "max_tokens": 900}


@pytest.mark.anyio
async def test_a_hidden_value_nothing_can_restore_is_refused_at_its_step() -> None:
    original = await _read("paper-ingest")
    identify = _find(original["root"], "identify")
    assert identify is not None
    identify["id"] = "identify-renamed"  # the saved definition has no step by this id

    resp = await _save("my-paper-ingest", _editable(original), based_on="paper-ingest")

    assert resp.status == 422
    issues = _body(resp)["error"]["detail"]["issues"]
    hidden = [i for i in issues if i["code"] == "WF_HIDDEN_VALUE_UNMATCHED"]
    assert len(hidden) == 1, issues
    # Addressed to the step, in the engine's own path grammar, so the editor can pin it there.
    assert hidden[0]["path"] == "root.children[1].children[2]"
    assert "authors" in hidden[0]["message"]
    assert not (defs_root() / "my-paper-ingest").exists(), "a refused save writes nothing"


@pytest.mark.anyio
async def test_a_dry_run_reports_the_same_refusal_and_writes_nothing() -> None:
    original = await _read("paper-ingest")
    identify = _find(original["root"], "identify")
    assert identify is not None
    identify["id"] = "identify-renamed"

    resp = await _save("my-paper-ingest", _editable(original), based_on="paper-ingest", save=False)

    assert resp.status == 422
    codes = [i["code"] for i in _body(resp)["error"]["detail"]["issues"]]
    assert "WF_HIDDEN_VALUE_UNMATCHED" in codes
    assert not (defs_root() / "my-paper-ingest").exists()


# ── the round trip: open, edit, save, reload, run ───────────────────────────────


class _RecordingSupervisor:
    """Stands in for the watchdog: records the spec a run was launched with."""

    def __init__(self) -> None:
        self.launched: list[dict[str, Any]] = []

    async def launch(self, run: Any, spec: dict[str, Any]) -> Any:
        self.launched.append(spec)
        return None


@pytest.mark.anyio
async def test_open_edit_save_reload_run_on_a_copy_of_a_bundled_template() -> None:
    opened = await _read("code-project")
    init = _find(opened["root"], "init")
    assert init is not None
    init["config"]["prompt"] = init["config"]["prompt"] + "\n\nWork in small commits."

    saved = await _save("my-code-project", _editable(opened), based_on="code-project")
    assert saved.status == 201, _body(saved)

    reloaded = await _read("my-code-project")
    reloaded_init = _find(reloaded["root"], "init")
    assert reloaded_init is not None
    assert reloaded_init["config"]["prompt"].endswith("Work in small commits.")
    assert reloaded["source"] == "user"
    assert reloaded["metadata"] == opened["metadata"]
    assert reloaded["runtime_hints"] == opened["runtime_hints"]

    supervisor = _RecordingSupervisor()
    started = await service.start_run(
        name="my-code-project",
        inputs={"task": "Fix the login bug", "verify_command": "make test"},
        supervisor=supervisor,
        skip_preflight=True,
    )
    assert started.get("ok"), started
    assert len(supervisor.launched) == 1
    ran = supervisor.launched[0]
    ran_init = _find(ran["root"], "init")
    assert ran_init is not None
    assert ran_init["config"]["prompt"].endswith(
        "Work in small commits."
    ), "the run executes the edit"
    assert ran["runtime_hints"] == opened["runtime_hints"], "and the judge contract it shipped with"


# ── version history: one recorded version, readable on its own ─────────────────


@pytest.mark.anyio
async def test_one_recorded_version_can_be_read_back_stripped() -> None:
    root_v1 = {"kind": "infer", "id": "only", "config": {"prompt": "one", "max_tokens": 50}}
    root_v2 = {"kind": "infer", "id": "only", "config": {"prompt": "two", "max_tokens": 50}}
    assert (await _save("history", {"root": root_v1})).status == 201
    assert (await _save("history", {"root": root_v2})).status == 201

    resp = await H.api_def_version_detail(
        _req(
            "GET",
            "/api/workflows/history/versions/1",
            match_info={"name": "history", "version": "1"},
        )
    )

    assert resp.status == 200, _body(resp)
    body = _body(resp)
    assert body["version"] == 1
    config = body["definition"]["root"]["config"]
    assert config["prompt"] == "one"
    # The version read is a READ like any other: stripped the same way.
    assert config.get("_has_max_tokens") is True and "max_tokens" not in config


@pytest.mark.anyio
async def test_a_version_read_refuses_what_does_not_exist() -> None:
    assert (
        await _save("history", {"root": {"kind": "infer", "id": "a", "config": {"prompt": "x"}}})
    ).status == 201

    missing_version = await H.api_def_version_detail(
        _req("GET", "/", match_info={"name": "history", "version": "7"})
    )
    missing_def = await H.api_def_version_detail(
        _req("GET", "/", match_info={"name": "no-such-def", "version": "1"})
    )
    not_a_number = await H.api_def_version_detail(
        _req("GET", "/", match_info={"name": "history", "version": "latest"})
    )

    assert missing_version.status == 404
    assert missing_def.status == 404
    assert not_a_number.status == 400


@pytest.mark.anyio
async def test_restoring_an_old_version_keeps_the_values_that_version_hid() -> None:
    v1 = {"kind": "infer", "id": "only", "config": {"prompt": "one", "max_tokens": 50}}
    v2 = {"kind": "infer", "id": "other", "config": {"prompt": "two"}}
    assert (await _save("history", {"root": v1})).status == 201
    assert (await _save("history", {"root": v2})).status == 201

    old = _body(
        await H.api_def_version_detail(
            _req("GET", "/", match_info={"name": "history", "version": "1"})
        )
    )["definition"]

    resp = await _save("history", _editable(old), based_on_version=1)

    assert resp.status == 201, _body(resp)
    assert _stored("history")["root"]["config"] == {"prompt": "one", "max_tokens": 50}
    assert (
        _stored("history")["version"] == 3
    ), "a restore is a NEW version; history is not rewritten"


def test_the_version_read_route_is_registered_after_the_diff_route() -> None:
    """`/versions/{version}` would swallow `/versions/diff` if it were matched first."""
    from aiohttp import web

    app = web.Application()
    H.register_workflow_routes(app)
    gets = [
        r.resource.canonical
        for r in app.router.routes()
        if r.method == "GET" and r.resource is not None
    ]
    assert "/api/workflows/{name}/versions/{version}" in gets
    assert gets.index("/api/workflows/{name}/versions/diff") < gets.index(
        "/api/workflows/{name}/versions/{version}"
    )


# ── the restore walk itself ─────────────────────────────────────────────────────


def test_a_nested_hidden_value_and_a_hidden_input_are_both_restored() -> None:
    stored = {
        "inputs": {"auth_header": {"type": "string", "help": "sent as-is"}},
        "root": {
            "kind": "sequence",
            "children": [
                {"kind": "action", "id": "call", "config": {"with": {"token_budget": 12}}}
            ],
        },
    }
    incoming = secrets.strip_secrets(stored)
    assert incoming["inputs"] == {"_has_auth_header": True}, "the control: the read hides both"

    restored = secrets.reinject_secrets(incoming, stored)

    assert restored == stored
    assert secrets.unmatched_flags(restored) == []


def test_a_config_key_named_like_a_child_position_is_not_mistaken_for_one() -> None:
    """An action's `with.body` is request data, not a child node: re-anchoring there would look
    the hidden value up on a node that does not exist."""
    stored = {
        "root": {
            "kind": "action",
            "id": "post",
            "config": {"with": {"body": {"refresh": 30}}},
        }
    }
    restored = secrets.reinject_secrets(secrets.strip_secrets(stored), stored)
    assert restored == stored


def test_an_unmatched_flag_is_located_at_its_step() -> None:
    incoming = {
        "root": {
            "kind": "sequence",
            "children": [{"kind": "infer", "id": "new", "config": {"_has_max_tokens": True}}],
        }
    }
    restored = secrets.reinject_secrets(incoming, {"root": {"kind": "sequence"}})
    assert secrets.unmatched_flags(restored) == [("root.children[0]", "new", "config.max_tokens")]
