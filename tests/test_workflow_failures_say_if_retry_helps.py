"""A failed workflow step says whether a Retry can help, decided where the cause is known.

Each defect below was measured on `main` before this change, and each test here fails there:

* every failed ACTION was filed `transient` with "check the action's configuration and the gateway
  log" (`engine.dispatch_action`), so the run page offered Retry for a missing config field, a
  rejected key and a model the provider does not have, each of which fails again the same way;
* an exception whose cause is typed (an HTTP status, an open circuit breaker, the provider
  bridge's "no model is set up") was read by substring, and fell through to "check the gateway
  log" or to a class that disagreed with it;
* a secret the credential store does not hold resolved to "" and the step ran with it;
* only the LAST escalation of a run could be read, because `run.attention` is one slot;
* a Retry re-sent a failed effect step under a key minted from the CHILD's run id, so a receiver
  could not recognise it as the attempt it retries, and an action never received the key at all;
* a Retry inside a provider's circuit-breaker window started a run the breaker refused without a
  call, and nothing said when a Retry could run;
* "To first output" was `0.0` for a run that produced no output at all.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from personalclaw.workflows import checkpoints as CP
from personalclaw.workflows import service, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.effects import EffectStatus, effect_history, idempotency_key
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.failure_taxonomy import classify_exception
from personalclaw.workflows.models import FailureClass, InstanceState, Node, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    # The production secret resolver reads the credential store under `config_dir()`.
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _status_error(code: int, text: str) -> httpx.HTTPStatusError:
    """What the bundled Ollama app raises for a refused `/api/chat`: the status on the response."""
    request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
    response = httpx.Response(code, request=request, text=text)
    return httpx.HTTPStatusError(
        f"Ollama answered {code}: {text}", request=request, response=response
    )


def _make_run(spec: dict) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    return run


def _action_spec(name: str, config: dict[str, Any]) -> dict:
    return {
        "name": name,
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "send", "config": config}],
        },
    }


class _Result:
    """An `ActionResult` as a provider returns it."""

    def __init__(self, success=True, stdout="", error="", failure_class="", agent_error=None):
        self.success = success
        self.stdout = stdout
        self.outcome = ""
        self.error = error
        self.exit_code = 0 if success else 1
        self.stderr = ""
        self.agent_error = agent_error
        self.failure_class = failure_class
        self.retry_after = 0.0


# ── an exception, classified by what it IS ───────────────────────────────────


class TestAnExceptionIsClassifiedByItsType:
    def test_a_rejected_key_offers_no_retry_and_says_where_the_key_is(self) -> None:
        """No keyword in the message: OpenAI's own 401 text. On main it read as an unexplained
        INTERNAL failure whose fix was the gateway log."""
        failure = classify_exception(_status_error(401, "Incorrect API key provided: sk-...a1b2"))
        assert failure.failure_class is FailureClass.PERMISSION
        assert not failure.retryable
        assert "Settings → Providers" in failure.remediation

    def test_a_model_the_provider_does_not_have_is_the_users_to_change(self) -> None:
        failure = classify_exception(_status_error(404, "model 'gemma3:4b' not found"))
        assert failure.failure_class is FailureClass.USER
        assert not failure.retryable
        assert "Settings → Models" in failure.remediation

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_a_rate_limit_or_a_server_fault_offers_a_retry(self, code: int) -> None:
        failure = classify_exception(_status_error(code, "try again later"))
        assert failure.failure_class is FailureClass.TRANSIENT
        assert failure.retryable

    def test_a_provider_that_is_down_is_a_network_failure_a_retry_clears(self) -> None:
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        failure = classify_exception(
            httpx.ConnectError("All connection attempts failed", request=request)
        )
        assert failure.failure_class is FailureClass.NETWORK
        assert failure.retryable
        assert "127.0.0.1:11434" in failure.remediation, "the fix names the address it tried"

    def test_a_wrapped_transport_error_is_classified_by_its_cause(self) -> None:
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        try:
            try:
                raise httpx.ReadError("peer closed the connection", request=request)
            except httpx.ReadError as inner:
                raise RuntimeError("sampling failed") from inner
        except RuntimeError as wrapped:
            failure = classify_exception(wrapped)
        assert failure.failure_class is FailureClass.NETWORK
        assert failure.cause_plain.startswith("RuntimeError: sampling failed")

    def test_no_model_set_up_carries_the_bridges_own_fix(self) -> None:
        from personalclaw.errors import AgentError
        from personalclaw.providers.provider_bridge import ProviderResolutionError

        fix = "add a model provider in Settings → Providers, then bind 'background' to it"
        exc = ProviderResolutionError(
            "No provider configured for use case 'background'.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what="no model provider resolves for use case 'background'",
                why="no provider in config.json declares the capability this use case needs",
                fix=fix,
            ),
        )
        failure = classify_exception(exc)
        assert failure.failure_class is FailureClass.USER
        assert not failure.retryable
        assert failure.remediation.startswith(fix)

    def test_an_open_breaker_says_when_a_retry_can_run(self) -> None:
        from personalclaw.guardrails.failure import CircuitOpenError

        failure = classify_exception(CircuitOpenError("flaky-ollama", 21.0))
        assert failure.failure_class is FailureClass.TRANSIENT
        assert failure.retryable
        assert failure.to_dict()["retry_at"] == pytest.approx(time.time() + 21.0, abs=2)
        assert "21s" in failure.remediation


# ── an action's failure, classified by the provider that saw it ─────────────


async def _dispatch(provider: Any, config: dict[str, Any]) -> Any:
    node = Node.from_dict({"kind": "action", "id": "a", "config": config})
    return await dispatch_action(node, BindingContext(), get_provider=lambda name: provider)


class TestAnActionSaysWhetherARetryHelps:
    async def test_a_missing_title_is_a_config_fault_that_names_the_field(self) -> None:
        from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

        result = await _dispatch(
            CreateTaskActionProvider(), {"provider": "create-task", "with": {"title_template": ""}}
        )
        assert result.state is InstanceState.FAILED
        assert result.failure.failure_class is FailureClass.USER
        assert not result.failure.retryable, "a Retry sends the same config and fails the same way"
        assert "`title_template`" in result.failure.remediation

    async def test_best_of_n_without_a_prompt_names_the_field(self) -> None:
        from personalclaw.action_providers.best_of_n_provider import BestOfNActionProvider

        result = await _dispatch(BestOfNActionProvider(), {"provider": "best-of-n", "with": {}})
        assert result.failure.failure_class is FailureClass.USER
        assert not result.failure.retryable
        assert "`prompt`" in result.failure.remediation

    async def test_a_provider_that_names_a_transient_cause_still_gets_its_retry(self) -> None:
        class Flaky:
            async def execute(self, cfg, ctx, timeout=30):
                return _Result(success=False, error="upstream 503", failure_class="transient")

        result = await _dispatch(Flaky(), {"provider": "flaky"})
        assert result.failure.failure_class is FailureClass.TRANSIENT
        assert result.failure.retryable

    async def test_a_provider_that_says_nothing_is_not_assumed_retryable(self) -> None:
        class Mute:
            async def execute(self, cfg, ctx, timeout=30):
                return _Result(success=False, error="exit status 2")

        result = await _dispatch(Mute(), {"provider": "mute"})
        assert result.failure.failure_class is FailureClass.INTERNAL
        assert not result.failure.retryable


# ── a secret that is not set ─────────────────────────────────────────────────


class TestASecretThatIsNotSet:
    async def test_the_step_fails_instead_of_sending_an_empty_one(self) -> None:
        """The store answers "" for a key it does not hold, and the engine substituted it: the
        action ran with `Bearer `, which fails at its receiver with nothing naming the key."""
        sent: list[dict] = []

        class Recorder:
            async def execute(self, cfg, ctx, timeout=30):
                sent.append(dict(cfg))
                return _Result(stdout='{"ok": true}')

        spec = _action_spec(
            "uses-a-secret",
            {"provider": "notify", "with": {"token": "Bearer {{secret:NOT_ADDED_YET}}"}},
        )
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(get_provider=lambda n: Recorder()))
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert sent == [], f"the action ran with the secret substituted as empty: {sent}"
        failure = c.instances["root.children[0]"].failure
        assert failure.failure_class is FailureClass.USER
        assert "'NOT_ADDED_YET'" in failure.remediation

    def test_the_run_start_refusal_sends_no_one_to_a_page_that_cannot_clear_it(self) -> None:
        """Run start refuses a missing secret before the step can, and said "add it in Settings →
        Providers". This check reads `credentials.json`, which that page never writes: a
        provider's key is stored under a name of its own, so the save cannot clear the refusal."""
        from personalclaw.workflows import preflight as PF

        spec = _action_spec("uses-a-secret", {"provider": "notify", "with": {"t": "{{secret:K1}}"}})
        result = PF.preflight(spec, credential_resolver=lambda k: False)
        (finding,) = [f for f in result.errors if f.code == "WF_PRE_CREDENTIAL_MISSING"]
        assert "'K1'" in finding.remediation
        assert "Settings" not in finding.remediation, finding.remediation


class TestABindingSaysWhatToChange:
    def test_a_field_read_from_a_value_that_has_none_says_so(self) -> None:
        """ "check the referenced node id and field exist" was the whole fix, for a read whose node
        and value both exist and whose value is simply not an object."""
        from personalclaw.workflows.engine_support import resolve_config

        node = Node.from_dict({"kind": "stage", "id": "w", "config": {"prompt": "{{item.v.w}}"}})
        _, failure = resolve_config(node, BindingContext(item={"v": "flat"}, has_item=True))
        assert failure is not None
        assert failure.failure_class is FailureClass.INTERNAL
        assert "is a str, which has no fields" in failure.remediation, failure.remediation
        assert "node id" not in failure.remediation


# ── every escalation, not the last ───────────────────────────────────────────


class TestEveryEscalationIsKept:
    async def test_a_run_whose_two_items_gave_up_explains_both(self) -> None:
        spec = {
            "name": "two-give-up",
            "root": {
                "kind": "foreach",
                "id": "fan",
                "config": {
                    # Two items fail, for two different reasons.
                    "items": [{"v": {"w": 1}}, {"name": "no v"}, {"v": "flat"}],
                    "on_item_error": "skip",
                    "max_concurrency": 1,
                },
                "body": {"kind": "transform", "id": "body", "config": {"expr": "{{item.v.w}}"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        await c.run_to_completion(timeout=20)
        body = service.status(run.id)
        escalations = body["escalations"]
        assert [e["instance_path"] for e in escalations] == ["root.body#1", "root.body#2"]
        first, second = (e["detail"] for e in escalations)
        assert "unresolved reference at 'v'" in first and "cannot read 'w'" in second
        # The control: the one `attention` slot holds only the second, which is all main showed.
        assert body["attention"]["detail"] == second

        # A step that gave up and later succeeded (a rewind re-ran it) no longer explains the run.
        instances = store.read_state(run.id)
        instances["root.body#1"].state = InstanceState.DONE
        store.write_state(run.id, instances)
        assert [e["instance_path"] for e in service.status(run.id)["escalations"]] == [
            "root.body#2"
        ]


# ── a Retry keeps the attempt's idempotency key ─────────────────────────────


class TestARetryKeepsItsAttemptsKey:
    async def test_the_forked_retry_resends_the_effect_under_the_parents_key(self) -> None:
        received: list[str] = []

        class Receiver:
            def __init__(self, fail: bool) -> None:
                self.fail = fail

            async def execute(self, cfg, ctx, timeout=30):
                received.append(str(ctx.payload.get("idempotency_key", "")))
                if self.fail:
                    return _Result(
                        success=False, error="connection refused", failure_class="network"
                    )
                return _Result(stdout='{"id": "msg-1"}')

        spec = _action_spec("send-once", {"provider": "notify"})
        run = _make_run(spec)
        parent = RunController(
            run, spec, services=EngineServices(get_provider=lambda n: Receiver(True))
        )
        assert await parent.run_to_completion(timeout=20) == RunStatus.FAILED
        path = "root.children[0]"
        parent_key = idempotency_key(run.id, path, parent.instances[path].epoch)
        assert [r.idempotency_key for r in effect_history(run.id)[path]] == [parent_key]

        child_id = CP.fork_run(parent.run, spec, parent.instances).child.id
        child = RunController(
            store.get(child_id),
            spec,
            services=EngineServices(get_provider=lambda n: Receiver(False)),
        )
        assert await child.run_to_completion(timeout=20) == RunStatus.COMPLETE
        own = [
            r for r in effect_history(child_id)[path] if r.effect_status is EffectStatus.COMMITTED
        ]
        assert [r.idempotency_key for r in own] == [parent_key], "the child minted its own key"
        # The receiver saw ONE key across both attempts, which is what lets it dedupe.
        assert received == [parent_key, parent_key]


# ── a Retry inside a breaker's window ────────────────────────────────────────


class TestARetryInsideTheBreakerWindow:
    SPEC = {
        "name": "calls-a-model",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "infer", "id": "ask", "config": {"prompt": "go"}}],
        },
    }

    @staticmethod
    async def _down(prompt, *, use_case="background", output_type=None):
        """A guarded call that reached the provider and failed: the guard records it."""
        from personalclaw.guardrails.breaker import get_breaker
        from personalclaw.guardrails.calls import FAILED, open_call

        call = open_call("flaky-ollama", "gemma3:4b", temperature=None)
        if call is not None:
            call.state = FAILED
        get_breaker("flaky-ollama").record_failure()
        raise ConnectionError("provider refused the connection")

    async def test_the_run_page_says_when_a_retry_can_run(self) -> None:
        from personalclaw.guardrails.breaker import get_breaker

        for _ in range(4):  # four earlier calls to the same provider already failed
            get_breaker("flaky-ollama").record_failure()
        run = _make_run(self.SPEC)
        c = RunController(run, self.SPEC, services=EngineServices(completion=self._down))
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        breaker = get_breaker("flaky-ollama")
        assert breaker.is_open(), "the control: this step's failure opened the breaker"
        (node,) = service.status(run.id)["nodes"]
        failure = node["failure"]
        assert failure["retryable"] is True
        assert failure["retry_at"] == pytest.approx(time.time() + breaker.retry_after(), abs=2)

    async def test_a_breaker_that_opens_after_the_step_failed_still_holds_the_retry(self) -> None:
        """Every call to the provider counts toward its breaker, so a background pass can open it
        after the run has ended. Measured by #3597: three samples, then two background calls."""
        from personalclaw.guardrails.breaker import get_breaker

        run = _make_run(self.SPEC)
        c = RunController(run, self.SPEC, services=EngineServices(completion=self._down))
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        before = service.status(run.id)["nodes"][0]["failure"]
        assert before["retryable"] is True and "retry_at" not in before  # closed at failure time
        for _ in range(4):
            get_breaker("flaky-ollama").record_failure()
        after = service.status(run.id)["nodes"][0]["failure"]
        assert after.get("retry_at", 0) > time.time() + 20, after


# ── "To first output" when there was none ────────────────────────────────────


class TestFirstOutput:
    async def test_a_run_that_produced_no_output_has_no_first_output_time(self) -> None:
        run = _make_run(TestARetryInsideTheBreakerWindow.SPEC)

        async def down(prompt, *, use_case="background", output_type=None):
            raise ConnectionError("provider refused the connection")

        c = RunController(
            run, TestARetryInsideTheBreakerWindow.SPEC, services=EngineServices(completion=down)
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        stats = service.introspect(run.id)["stats"]
        assert stats["steps_completed"] == 0  # the control: nothing produced output
        assert stats["first_byte_ms"] is None, "0 ms claimed output arrived instantly"
