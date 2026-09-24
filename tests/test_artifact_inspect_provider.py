"""The `artifact_inspect` provider — the read half of output-offloading (WV-11).

`{{nodes.x.artifact}}` gives a downstream node a POINTER to an offloaded body; this provider is
how it pulls the CONTENT on demand. The tests that matter most are the confinement ones: the ref
arrives from a model-authored template, so a path escape must be refused rather than trusted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.action_providers.artifact_inspect_provider import (
    ArtifactInspectActionProvider,
)
from personalclaw.action_providers.base import ActionContext
from personalclaw.workflows import store
from personalclaw.workflows.journal import MAX_INLINE_OUTPUT_BYTES, Journal
from personalclaw.workflows.models import WorkflowRun


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.fixture
def provider() -> ArtifactInspectActionProvider:
    return ArtifactInspectActionProvider()


def run(coro):
    return asyncio.run(coro)


def _body(result):
    return json.loads(result.stdout)


def _offloaded_run(output) -> tuple[str, str]:
    """Persist an offloaded output and return (run_id, artifact_ref)."""
    r = store.create(WorkflowRun(id="", workflow_name="inspect"))
    journal = Journal(r.id)
    ref, _preview = journal.store_output("root.a", output)
    assert ref.startswith("artifacts/"), "fixture must produce an offloaded body"
    return r.id, ref


class TestRegistration:
    def test_registered_and_allowlisted(self) -> None:
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        _ensure_default_providers_registered()
        assert get_action_provider("artifact_inspect") is not None
        assert "artifact_inspect" in ALLOWED_HOOK_PROVIDERS

    def test_name_and_display_name(self, provider) -> None:
        assert provider.name == "artifact_inspect"
        assert provider.display_name


class TestRoundTrip:
    def test_reads_back_an_offloaded_body(self, provider) -> None:
        big = "x" * (MAX_INLINE_OUTPUT_BYTES + 500)
        run_id, ref = _offloaded_run(big)
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id, "node_id": "n"})
        result = run(provider.execute({"ref": ref}, ctx))
        assert result.success
        body = _body(result)
        assert body["content"] == big
        assert body["total"] == len(big)
        assert body["truncated"] is False

    def test_a_structured_body_is_returned_as_json_text(self, provider) -> None:
        payload = {"findings": ["a"] * (MAX_INLINE_OUTPUT_BYTES // 4)}
        run_id, ref = _offloaded_run(payload)
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": ref}, ctx))
        assert result.success
        assert json.loads(_body(result)["content"]) == payload


class TestPartialPull:
    def test_offset_and_length_slice_the_body(self, provider) -> None:
        body = "HEAD" + "m" * (MAX_INLINE_OUTPUT_BYTES + 100) + "TAIL"
        run_id, ref = _offloaded_run(body)
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": ref, "offset": 0, "length": 4}, ctx))
        assert _body(result)["content"] == "HEAD"
        assert _body(result)["truncated"] is True

    def test_omitting_length_reads_the_whole_body(self, provider) -> None:
        """`artifact_inspect` with no window is a deliberate full read — the caller asked to
        inspect the artifact, so it gets all of it (the partial pull is opt-in)."""
        body = "z" * (2 * MAX_INLINE_OUTPUT_BYTES)
        run_id, ref = _offloaded_run(body)
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": ref}, ctx))
        got = _body(result)
        assert got["content"] == body
        assert got["truncated"] is False

    def test_offset_past_the_end_returns_empty(self, provider) -> None:
        body = "abc" + "z" * (MAX_INLINE_OUTPUT_BYTES + 1)
        run_id, ref = _offloaded_run(body)
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": ref, "offset": len(body) + 50}, ctx))
        assert _body(result)["content"] == ""

    def test_a_negative_offset_is_rejected(self, provider) -> None:
        run_id, ref = _offloaded_run("x" * (MAX_INLINE_OUTPUT_BYTES + 1))
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": ref, "offset": -1}, ctx))
        assert not result.success


class TestSecurity:
    def test_a_path_escape_ref_is_refused(self, provider) -> None:
        """The core security property: a `../` escape reads nothing, even when a real file
        sits at the escaped path."""
        run_id, _ref = _offloaded_run("x" * (MAX_INLINE_OUTPUT_BYTES + 1))
        outside = store.run_dir(run_id).parent / "elsewhere.json"
        outside.write_text('{"output": "leaked"}', encoding="utf-8")
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": "../elsewhere.json"}, ctx))
        assert not result.success
        assert "leaked" not in (result.stdout or "")

    def test_an_absolute_path_ref_is_refused(self, provider) -> None:
        run_id, _ref = _offloaded_run("x" * (MAX_INLINE_OUTPUT_BYTES + 1))
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": "/etc/passwd"}, ctx))
        assert not result.success

    def test_an_outputs_ref_is_refused(self, provider) -> None:
        """`artifact_inspect` reads only the offloaded artifacts root, never inline outputs/."""
        r = store.create(WorkflowRun(id="", workflow_name="inspect"))
        out_ref = store.write_output(r.id, "root.a", {"v": 1})
        ctx = ActionContext(event="workflow_node", payload={"run_id": r.id})
        result = run(provider.execute({"ref": out_ref}, ctx))
        assert not result.success

    def test_no_run_context_is_refused(self, provider) -> None:
        """Without a run id the confinement root is unknowable, so the read is refused."""
        ctx = ActionContext(event="workflow_node", payload={})
        result = run(provider.execute({"ref": "artifacts/whatever.json"}, ctx))
        assert not result.success
        assert "run context" in result.error


class TestBadConfig:
    def test_a_missing_ref_is_an_error(self, provider) -> None:
        ctx = ActionContext(event="workflow_node", payload={"run_id": "abc"})
        result = run(provider.execute({}, ctx))
        assert not result.success
        assert "ref" in result.error

    def test_a_ref_to_an_absent_artifact_is_an_error(self, provider) -> None:
        run_id, _ref = _offloaded_run("x" * (MAX_INLINE_OUTPUT_BYTES + 1))
        ctx = ActionContext(event="workflow_node", payload={"run_id": run_id})
        result = run(provider.execute({"ref": "artifacts/deadbeef00000000.json"}, ctx))
        assert not result.success
