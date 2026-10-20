"""The node-inspection endpoint — `GET /api/workflows/runs/{id}/nodes/{node_id}/inspect`.

The load-bearing claims, each a property this module locks:

* the **reconstructability set** comes back for a terminal node — `resolved_prompt` (or a
  ref), `resolved_inputs`, `output` (or an `artifact_ref`), `attempts`, `ledger_events`,
  `cached`;
* **secrets are absent** — the resolved prompt is stored UN-redacted on disk (the controller's
  `node_bindings.store_prompt` writes through the raw `store.write_output`, not the redacting
  journal path), so the endpoint's redaction is the ONLY thing standing between a credential on disk
  and a credential in a browser. This is the security contract and the reason this test exists;
* an offloaded output returns `{"artifact_ref": ...}` rather than the raw blob;
* a cache-served node reports `cached: true`;
* an unknown run / unknown node is a 404, and a node that has not finished answers with its live
  state — running with what it has seen and no output yet, or pending when the run has not reached
  it — so a step can be looked at while the run is live.

State is built with the engine's OWN writers (`store.write_state`, `store.write_output`, the
real `Journal`) rather than hand-built dicts, so this module cannot drift from the shapes the
controller actually persists — the same discipline `test_workflows_introspection.py` uses.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.workflows import handlers as H
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.models import InstanceState, NodeInstance, WorkflowRun

pytestmark = pytest.mark.anyio

# A real AWS access-key ID (the canonical AWS docs example) — matches the credential redactor's
# `AKIA[A-Z0-9]{16}` pattern, so if it survives into a response the redaction genuinely failed.
SECRET = "AKIAIOSFODNN7EXAMPLE"

# A `stage` node (one subagent execution — it produces an LLM prompt) depends on `upstream`;
# the binding is what makes `resolved_inputs` non-empty.
SPEC = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {"kind": "transform", "id": "upstream", "config": {"expr": {"v": 1}}},
        {"kind": "stage", "id": "target", "config": {"prompt": "use {{nodes.upstream.output.v}}"}},
    ],
}
UP_PATH = "root.children[0]"
TGT_PATH = "root.children[1]"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _req(run_id: str, node_id: str):
    """A mocked GET with the two path params the handler reads. No app state: inspect is a
    read, so it takes no guard and needs none."""
    req = make_mocked_request(
        "GET", f"/api/workflows/runs/{run_id}/nodes/{node_id}/inspect", app=web.Application()
    )
    req.match_info["run_id"] = run_id
    req.match_info["node_id"] = node_id
    return req


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _build_run(
    *,
    target_state: InstanceState = InstanceState.DONE,
    prompt: str = "resolved prompt text",
    upstream_output=None,
    target_output=None,
    target_output_ref: str | None = None,
    cached: bool = False,
    attempts: int = 0,
) -> str:
    """Persist a two-node run to disk exactly as the controller would, then return its id.

    `target_output_ref` overrides the instance's `output_ref` — a value NOT under `outputs/`
    is how a future offloaded artifact points elsewhere, which is the `artifact_ref` path.
    """
    run = store.create(WorkflowRun(id="", workflow_name="inspect-wf"))
    store.write_spec(run.id, {"name": "inspect-wf", "root": SPEC})

    up_out = {"v": 1} if upstream_output is None else upstream_output
    up_ref = store.write_output(run.id, UP_PATH, up_out)

    tgt_out = {"answer": "ok"} if target_output is None else target_output
    tgt_ref = store.write_output(run.id, TGT_PATH, tgt_out)
    # The prompt is written RAW at `<path>::prompt`, mirroring the controller's
    # `node_bindings.store_prompt` (which does NOT redact) — this is what makes the secrets-absent
    # test meaningful.
    store.write_output(run.id, f"{TGT_PATH}::prompt", prompt)

    store.write_state(
        run.id,
        {
            UP_PATH: NodeInstance(path=UP_PATH, state=InstanceState.DONE, output_ref=up_ref),
            TGT_PATH: NodeInstance(
                path=TGT_PATH,
                state=target_state,
                attempt=attempts,
                output_ref=(target_output_ref if target_output_ref is not None else tgt_ref),
            ),
        },
    )

    j = J.Journal(run.id)
    for n in range(attempts):
        j.write(
            J.STEP_ATTEMPT,
            instance_path=TGT_PATH,
            node_id="target",
            epoch=0,
            attempt=n + 1,
            failure_class="transient",
            error="flaked",
        )
    if cached:
        j.step_cached(
            TGT_PATH, "target", epoch=0, cache_key="k", state=InstanceState.DONE, output_ref=tgt_ref
        )
    else:
        j.step_completed(
            TGT_PATH,
            "target",
            epoch=0,
            cache_key="k",
            state=target_state,
            resolved_prompt_ref=f"{TGT_PATH}::prompt",
            output_ref=tgt_ref,
        )
    return run.id


class TestReconstructabilitySet:
    async def test_all_six_fields_are_present_for_a_terminal_node(self) -> None:
        run_id = _build_run()
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        for key in ("resolved_prompt", "resolved_inputs", "output", "attempts", "ledger_events"):
            assert key in body, key
        assert "cached" in body and isinstance(body["cached"], bool)
        assert body["node_id"] == "target"
        assert body["state"] == "done"

    async def test_resolved_inputs_carry_the_upstream_output(self) -> None:
        """`resolved_inputs` is the declared-dependency closure the node actually saw — the
        `{{nodes.upstream.output.v}}` binding means `upstream` is the one input."""
        run_id = _build_run()
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["resolved_inputs"] == {"upstream": {"v": 1}}

    async def test_resolved_prompt_is_the_stored_text(self) -> None:
        run_id = _build_run(prompt="hello world")
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["resolved_prompt"] == "hello world"

    async def test_a_large_prompt_returns_a_ref_not_the_body(self) -> None:
        big = "x" * (J.MAX_INLINE_OUTPUT_BYTES + 10)
        run_id = _build_run(prompt=big)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert isinstance(body["resolved_prompt"], dict) and "ref" in body["resolved_prompt"]
        assert big not in json.dumps(body)

    async def test_the_ledger_slice_is_scoped_to_this_instance(self) -> None:
        run_id = _build_run()
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["ledger_events"]
        assert all(e["instance_path"] == TGT_PATH for e in body["ledger_events"])


class TestSecretsAbsent:
    async def test_a_secret_in_the_prompt_is_redacted(self) -> None:
        """THE load-bearing assertion. The prompt is stored raw on disk; the endpoint MUST
        strip the credential before it leaves the process."""
        run_id = _build_run(prompt=f"here is the key {SECRET} do not leak it")
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert SECRET not in json.dumps(body)
        assert "[REDACTED" in json.dumps(body["resolved_prompt"])

    async def test_a_secret_in_a_resolved_input_is_redacted(self) -> None:
        run_id = _build_run(upstream_output={"v": 1, "token": SECRET})
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert SECRET not in json.dumps(body)

    async def test_a_secret_in_the_output_is_redacted(self) -> None:
        run_id = _build_run(target_output={"answer": f"leaked {SECRET}"})
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert SECRET not in json.dumps(body)


class TestArtifactOffload:
    async def test_an_offloaded_output_returns_an_artifact_ref(self) -> None:
        """An `output_ref` that is not under `outputs/` is a pointer elsewhere (an offloaded
        artifact); the endpoint hands back the pointer, never the raw blob."""
        run_id = _build_run(target_output_ref="artifacts/big-report.json")
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["output"] == {"artifact_ref": "artifacts/big-report.json"}

    async def test_an_oversize_output_is_not_inlined(self) -> None:
        huge = {"blob": "y" * (J.MAX_INLINE_OUTPUT_BYTES + 10)}
        run_id = _build_run(target_output=huge)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert isinstance(body["output"], dict) and "artifact_ref" in body["output"]
        assert huge["blob"] not in json.dumps(body)


class TestCached:
    async def test_a_cache_served_node_reports_cached_true(self) -> None:
        run_id = _build_run(cached=True)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["cached"] is True

    async def test_a_freshly_run_node_reports_cached_false(self) -> None:
        run_id = _build_run(cached=False)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["cached"] is False


class TestAttempts:
    async def test_retry_records_are_returned(self) -> None:
        run_id = _build_run(attempts=2)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert len(body["attempts"]) == 2
        assert all(e["kind"] == J.STEP_ATTEMPT for e in body["attempts"])

    async def test_a_first_try_success_has_no_attempt_records(self) -> None:
        run_id = _build_run(attempts=0)
        body = _body(await H.api_run_node_inspect(_req(run_id, "target")))
        assert body["attempts"] == []


class TestNotFoundAndLive:
    async def test_an_unknown_run_is_a_404(self) -> None:
        resp = await H.api_run_node_inspect(_req("deadbeef", "target"))
        assert resp.status == 404 and _body(resp)["error"]["code"] == "not_found"

    async def test_an_unknown_node_is_a_404(self) -> None:
        run_id = _build_run()
        resp = await H.api_run_node_inspect(_req(run_id, "ghost"))
        assert resp.status == 404 and _body(resp)["error"]["code"] == "not_found"

    async def test_a_running_node_answers_with_its_live_state(self) -> None:
        """🔴 Before: 409 `not_terminal`, three times on one live run, for the step the run was
        working on. It answers what it has: its state, what it was given, and no output yet —
        the stored value is an earlier attempt's, never this one's — with the same redaction."""
        run_id = _build_run(
            target_state=InstanceState.RUNNING,
            prompt=f"use key {SECRET} now",
            target_output={"answer": "from an earlier attempt"},
        )
        resp = await H.api_run_node_inspect(_req(run_id, "target"))
        assert resp.status == 200, _body(resp)
        body = _body(resp)
        assert body["state"] == "running"
        assert body["output"] is None
        assert "use key" in body["resolved_prompt"] and SECRET not in json.dumps(body)
        assert body["resolved_inputs"] == {"upstream": {"v": 1}}

    async def test_a_node_the_run_has_not_reached_is_pending(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="inspect-wf"))
        store.write_spec(run.id, {"name": "inspect-wf", "root": SPEC})
        store.write_state(
            run.id,
            {UP_PATH: NodeInstance(path=UP_PATH, state=InstanceState.RUNNING)},
        )
        resp = await H.api_run_node_inspect(_req(run.id, "target"))
        assert resp.status == 200, _body(resp)
        body = _body(resp)
        assert (body["state"], body["output"], body["ledger_events"]) == ("pending", None, [])


class TestRouteRegistration:
    def test_the_inspect_route_is_registered(self) -> None:
        app = web.Application()
        H.register_workflow_routes(app)
        paths = [r.resource.canonical for r in app.router.routes()]
        assert "/api/workflows/runs/{run_id}/nodes/{node_id}/inspect" in paths


class TestClientMethodExists:
    def test_api_ts_exposes_the_inspect_method(self) -> None:
        """The route needs a client, or the node inspector has nothing to call. The api.ts
        method is that client (the inspector drawer renders what it returns)."""
        from pathlib import Path

        api_ts = (
            Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "api.ts"
        ).read_text(encoding="utf-8")
        assert "workflowRunNodeInspect" in api_ts
        assert "/nodes/${encodeURIComponent(nodeId)}/inspect" in api_ts
