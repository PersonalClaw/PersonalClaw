"""best-of-n against a model provider that goes DOWN and comes back (day-5/6 power-user validation).

Every test here drives the SHIPPED path end to end: the bundled `best-of-n` template started
through `service.start_run` and driven by the real `WorkflowWatchdog`, the real `best-of-n`
action provider and sampling core, `one_shot_completion`, the provider bridge, the
`ModelCallGuard`, and the bundled Ollama app's OWN provider module — which talks real HTTP to a
fake Ollama on 127.0.0.1. Nothing between the template and the socket is stubbed, because every
defect below lived in a seam a stub would have skipped:

* **blame** — the run failed at `sample` (all N sampling calls refused) and the UI said
  "failed at `select` … user error … check the referenced node id". `select` only binds
  `{{nodes.sample.output.winner}}`; it ran against an output that could never exist.
* **retry** — Fork → Start "failed at +0.0 s with ZERO provider calls": the child inherited
  the parent's FAILED instances, so its frontier was complete before it began.
* **temperature** — a proxy saw every candidate request carry `model`, `messages`, `stream`
  and nothing else: the ladder was dropped at the Ollama factory, so N paid calls produced one
  answer N times. Asserted here on the OUTGOING request bodies, never on the node config.
* **usage** — completed runs reported `tokens: 0, tokens_recorded: true` and "Nothing is costing
  money", while the step had just made N+judge model calls.
* **cancel** — the header said 10s and Introspect said 0s, and four in-flight generations
  read as "nothing costing money".
* **liveness** — a model streaming its answer slower than the stall window was killed as
  "no progress", because nothing a best-of-n step does reached the stall clock.
* **failed usage** — a step that failed or was stall-killed wrote no usage at all: a run that made
  five calls said "no model recorded", and a measured row's `provider` was always empty.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.models import InstanceState, RunStatus

pytestmark = pytest.mark.anyio

PROMPT = "Name one primary color in one word."
ENTRY = "flaky-ollama"
MODEL = "gemma3:4b"
REF = f"{ENTRY}:{MODEL}"
#: What the fake reports per completed call, so a token total is checkable arithmetic.
PROMPT_TOKENS = 11
EVAL_TOKENS = 3


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeOllama:
    """Ollama's `/api/chat` + `/api/ps` on an ephemeral 127.0.0.1 port.

    DOWN drops every connection before answering a byte — the validator's proxy did exactly
    this (`reject-until`), and httpx surfaces it as the same transport error. UP answers
    `/api/chat` with a real NDJSON stream whose `done` chunk carries usage (or not, when
    `report_usage` is off). `hold` parks every chat request until released, which is how a test
    gets generations genuinely IN FLIGHT at the moment it cancels.

    A candidate's answer is `sample_text`, streamed in `sample_chunks` pieces `chunk_delay` seconds
    apart: a model that is generating, slowly. The judge's answer always arrives at once.

    `chats` is every `/api/chat` body a live server received: the outgoing request, parsed.
    """

    def __init__(self) -> None:
        self.up = False
        #: What an UP server answers `/api/chat` with: 200, or a refusal carrying `error`, the
        #: `{"error": …}` body Ollama itself sends.
        self.status = 200
        self.error = ""
        self.report_usage = True
        self.sample_text = "Blue"
        self.sample_chunks = 1
        self.chunk_delay = 0.0
        self.hold: asyncio.Event | None = None
        self.chats: list[dict[str, Any]] = []
        self.refused = 0
        self.held = 0
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def sample_requests(self) -> list[dict[str, Any]]:
        """The candidate requests — the ones whose last user turn IS the sampling prompt."""
        return [c for c in self.chats if _last_user(c) == PROMPT]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.hold is not None:
            self.hold.set()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        if not self.up:
            self.refused += 1
            writer.transport.abort()
            return
        lines = head.decode("latin-1").split("\r\n")
        _method, path, _version = lines[0].split(" ", 2)
        headers = {
            k.strip().lower(): v.strip()
            for k, v in (h.split(":", 1) for h in lines[1:] if ":" in h)
        }
        raw = await reader.readexactly(int(headers.get("content-length", "0") or 0))
        delay = 0.0
        if path == "/api/chat" and self.status != 200:
            self.chats.append(json.loads(raw or b"{}"))
            parts = [json.dumps({"error": self.error}).encode()]
            status, ctype = f"{self.status} Refused", "application/json"
        elif path == "/api/chat":
            body = json.loads(raw or b"{}")
            self.chats.append(body)
            if self.hold is not None:
                self.held += 1
                await self.hold.wait()
            sample = _last_user(body) == PROMPT
            text = self.sample_text if sample else '{"score": 0.5, "reason": "fine"}'
            pieces = _pieces(text, self.sample_chunks) if sample else [text]
            delay = self.chunk_delay if sample else 0.0
            done: dict[str, Any] = {"done": True}
            if self.report_usage:
                done.update(prompt_eval_count=PROMPT_TOKENS, eval_count=EVAL_TOKENS)
            parts = [
                (
                    json.dumps({"message": {"role": "assistant", "content": p}, "done": False})
                    + "\n"
                ).encode()
                for p in pieces
            ] + [(json.dumps(done) + "\n").encode()]
            status, ctype = "200 OK", "application/x-ndjson"
        elif path == "/api/ps":
            parts, status, ctype = [b'{"models": []}'], "200 OK", "application/json"
        else:
            parts, status, ctype = [b""], "404 Not Found", "text/plain"
        try:
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {sum(len(p) for p in parts)}"
                "\r\nConnection: close\r\n\r\n".encode()
            )
            for i, part in enumerate(parts):
                if i and delay:
                    await asyncio.sleep(delay)
                writer.write(part)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


def _last_user(body: dict[str, Any]) -> str:
    users = [m.get("content") or "" for m in body.get("messages") or [] if m.get("role") == "user"]
    return str(users[-1]).strip() if users else ""


def _pieces(text: str, n: int) -> list[str]:
    """`text` in `n` consecutive pieces (fewer when it is shorter than `n`)."""
    size = max(1, -(-len(text) // max(1, n)))
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


@contextlib.asynccontextmanager
async def _wired(monkeypatch, tmp_path, **services: Any) -> AsyncIterator[tuple[FakeOllama, Any]]:
    """A fake Ollama, the bundled Ollama app pointed at it, and a real supervisor.

    The bundle's provider module is loaded by path (it is what the gateway loads — a core copy
    would prove nothing about the app), registering the `ollama` type into the default registry
    that `conftest._restore_provider_registry` puts back afterwards. The resolution chain and the
    active refs are pinned to the one entry, so no developer binding can leak in; the output
    budget is pinned so a budget lookup cannot reach a real local-model server.

    `services` are the supervisor's `EngineServices` fields, as the gateway sets them from config.
    """
    from personalclaw.apps.native_contract import (
        NATIVE_DIR,
        load_bundle_module,
        namespaced_module_name,
    )
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import ProviderEntry, get_default_registry
    from personalclaw.workflows import bundled_defs
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.providers.use_cases.resolution_chain", lambda uc: [REF])
    monkeypatch.setattr("personalclaw.providers.use_cases.active_model_refs", lambda uc: [REF])

    async def _budget(_ref: str) -> int:
        return 256

    monkeypatch.setattr("personalclaw.local_models.budgets.output_budget", _budget)

    fake = FakeOllama()
    await fake.start()
    module_name = namespaced_module_name("ollama-models", "provider")
    load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    get_default_registry().register_entry(
        ProviderEntry(
            name=ENTRY,
            type="ollama",
            model=MODEL,
            # `default_model` rides along because the "Add instance" form writes it — the
            # captured request carried it as a top-level key, so the entry here does too.
            options={"endpoint": fake.endpoint, "default_model": MODEL},
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )
    bundled_defs.register_bundled_provider()
    supervisor = WorkflowWatchdog(state=None, services=EngineServices(**services))
    try:
        yield fake, supervisor
    finally:
        await supervisor.stop()
        await fake.stop()
        sys.modules.pop(module_name, None)


async def _start(supervisor: Any, n: int) -> str:
    body = await service.start_run(
        name="best-of-n", inputs={"prompt": PROMPT, "n": n}, supervisor=supervisor
    )
    assert body.get("ok"), body
    return str(body["run_id"])


async def _terminal(supervisor: Any, run_id: str, timeout: float = 30.0) -> RunStatus:
    controller = supervisor.controller(run_id)
    assert controller is not None, f"run {run_id} has no live controller"
    return await controller.wait_for_terminal(timeout=timeout)


def _states(run_id: str) -> dict[str, str]:
    return {n["node_id"]: n["state"] for n in service.status(run_id)["nodes"] if n["node_id"]}


# ── blame ────────────────────────────────────────────────────────────────────


async def test_an_outage_is_blamed_on_the_step_that_failed_with_its_real_class(
    monkeypatch, tmp_path
):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        assert fake.refused >= 2, "the provider was never actually called while it was down"

        status = service.status(run_id)
        attention = status["attention"]
        assert attention["kind"] == "escalation"
        assert (
            attention["node_id"] == "sample"
        ), f"the failure is attributed to {attention['node_id']!r}, not to the step that failed"
        # The provider dropped every connection: a network failure, which a Retry can clear once
        # it is back, and so the class the run page offers Retry for.
        classes = [a["failure_class"] for a in attention["attempts"]]
        assert classes == ["network"], classes
        # One attempt and no retry budget: "every retry was spent" would be false.
        assert attention["reason"] == "not_retried"

        states = _states(run_id)
        assert states["sample"] == InstanceState.FAILED.value
        assert states["select"] == InstanceState.SKIPPED.value, (
            "`select` binds sample's output, which a failed step never produces — running it can "
            f"only fail with a binding error that blames the user (got {states['select']!r})"
        )
        failures = [n["failure"] for n in status["nodes"] if n.get("failure")]
        assert all(f["class"] != "user" for f in failures), failures


# ── whether a Retry can help ─────────────────────────────────────────────────


def _failure(run_id: str, node_id: str) -> dict[str, Any]:
    (node,) = [n for n in service.status(run_id)["nodes"] if n["node_id"] == node_id]
    return node["failure"]


@pytest.mark.parametrize(
    ("status", "error", "failure_class", "where"),
    [
        (401, "unauthorized", "permission", "fix its key in Settings → Providers"),
        (404, f"model '{MODEL}' not found", "user", "the Background use case in Settings → Models"),
    ],
)
async def test_a_refusal_a_retry_cannot_clear_offers_none_and_says_what_to_change(
    monkeypatch, tmp_path, status, error, failure_class, where
):
    """Measured on main: both were filed `transient`, so the run page offered Retry for a key the
    provider rejects and a model it does not have, and the fix read "check the action's
    configuration and the gateway log"."""
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        fake.status, fake.error = status, error
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        assert len(fake.chats) == 2, "the control: the provider really was asked, and refused"

        failure = _failure(run_id, "sample")
        assert failure["class"] == failure_class
        assert failure["retryable"] is False, "a Retry sends the same request and is refused again"
        assert where in failure["remediation"], failure["remediation"]
        attempts = service.status(run_id)["escalations"][0]["attempts"]
        assert [a["failure_class"] for a in attempts] == [failure_class]


@pytest.mark.parametrize("status", [429, 500])
async def test_a_refusal_a_retry_can_clear_still_offers_one(monkeypatch, tmp_path, status):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        fake.status, fake.error = status, "try again later"
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        failure = _failure(run_id, "sample")
        assert (failure["class"], failure["retryable"]) == ("transient", True)


async def test_the_failures_that_open_the_breaker_say_when_a_retry_can_run(monkeypatch, tmp_path):
    """Five samples against a provider that is down: the fifth failure opens its breaker, and a
    Retry before it lapses is refused without a call. The run says when it can run instead."""
    from personalclaw.guardrails.breaker import get_breaker

    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        run_id = await _start(supervisor, n=5)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        breaker = get_breaker(ENTRY)
        assert breaker.is_open(), "the control: five failures in a row open the breaker"

        failure = _failure(run_id, "sample")
        assert (failure["class"], failure["retryable"]) == ("network", True)
        assert failure["providers"] == [ENTRY]
        assert failure["retry_at"] == pytest.approx(time.time() + breaker.retry_after(), abs=3)

        # A Retry pressed anyway: the child is refused without a single call, and says when.
        refused_before = fake.refused
        forked = service.fork_run(run_id, note="retry inside the window")
        child = str(forked["child_run_id"])
        assert (await service.start_draft_run(child, supervisor=supervisor)).get("ok")
        assert await _terminal(supervisor, child) is RunStatus.FAILED
        assert fake.refused == refused_before, "the breaker let a call through"
        child_failure = _failure(child, "sample")
        assert child_failure["retryable"] is True
        assert child_failure["retry_at"] > time.time() + 10, child_failure


# ── retry ────────────────────────────────────────────────────────────────────


async def test_a_fork_of_the_failed_run_retries_once_the_provider_is_back(monkeypatch, tmp_path):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        parent = await _start(supervisor, n=2)
        assert await _terminal(supervisor, parent) is RunStatus.FAILED

        fake.up = True
        forked = service.fork_run(parent, note="retry after the provider came back")
        assert forked.get("ok"), forked
        child = str(forked["child_run_id"])
        # The child inherits the parent's SUCCESSES; what failed or never ran starts fresh.
        child_state = store.read_state(child)
        assert {i.state for i in child_state.values()} <= {InstanceState.PENDING}, {
            p: i.state for p, i in child_state.items()
        }

        started = await service.start_draft_run(child, supervisor=supervisor)
        assert started.get("ok"), started
        assert await _terminal(supervisor, child) is RunStatus.COMPLETE
        assert len(fake.sample_requests()) == 2, "the retry did not call the provider"
        assert _states(child) == {"sample": "done", "select": "done"}
        # The parent is untouched: a fork is a new run, not a rewrite of the old one.
        assert store.get(parent).status is RunStatus.FAILED


# ── temperature ──────────────────────────────────────────────────────────────


async def test_every_candidate_request_carries_its_own_temperature_on_the_wire(
    monkeypatch, tmp_path
):
    from personalclaw.sampling import _TEMPERATURE_LADDER

    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        run_id = await _start(supervisor, n=3)
        assert await _terminal(supervisor, run_id) is RunStatus.COMPLETE

        sent = fake.sample_requests()
        assert len(sent) == 3
        temperatures = sorted(
            float((body.get("options") or {}).get("temperature", -1)) for body in sent
        )
        assert temperatures == sorted(_TEMPERATURE_LADDER[:3]), (
            f"the outgoing candidate requests carried {[sorted(b) for b in sent]} — the ladder "
            "never reached the model"
        )
        # A routing field is not a wire parameter: the request body is the model's contract.
        assert all("default_model" not in body for body in fake.chats)

        slate = service.inspect_node(run_id, "sample")["output"]
        assert all(c["sampled_at"] == c["temperature"] for c in slate["candidates"]), slate


async def test_every_candidate_request_carries_the_output_budget_core_derived(
    monkeypatch, tmp_path
):
    """The budget `one_shot_completion` derives (`_wired` pins it at 256) is `options.num_predict`
    on the wire — the one cap ollama reads. The factory dropped the `max_tokens` build kwarg, so
    every candidate generated until the model stopped."""
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.COMPLETE

        caps = [(body.get("options") or {}).get("num_predict") for body in fake.sample_requests()]
        assert caps == [256, 256], caps


# ── usage ────────────────────────────────────────────────────────────────────


async def test_a_completed_run_reports_what_its_model_calls_used(monkeypatch, tmp_path):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.COMPLETE

        calls = len(fake.chats)  # two samples + one judge pass per survivor
        assert calls == 4, calls
        rows = {r["node_id"]: r for r in J.ledger(run_id, kinds={J.STEP_COMPLETED})}
        assert rows["sample"]["tokens"] == calls * (PROMPT_TOKENS + EVAL_TOKENS)
        assert rows["sample"]["model"] == MODEL
        # The provider that served them, which the guard saw and the row used to leave empty.
        assert rows["sample"]["provider"] == ENTRY
        # A local model: measured, and free — not "no model recorded".
        assert rows["sample"]["cost_usd"] == 0.0

        stats = service.introspect(run_id)["stats"]
        assert stats["tokens_recorded"] is True
        assert stats["tokens"] == calls * (PROMPT_TOKENS + EVAL_TOKENS)
        assert stats["models"] == [MODEL]
        assert service.status(run_id)["tokens"] == stats["tokens"]


async def test_a_provider_that_reports_no_usage_is_not_read_as_zero(monkeypatch, tmp_path):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        fake.report_usage = False
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.COMPLETE

        rows = {r["node_id"]: r for r in J.ledger(run_id, kinds={J.STEP_COMPLETED})}
        assert rows["sample"]["tokens"] is None, rows["sample"]
        assert rows["sample"]["model"] == MODEL
        stats = service.introspect(run_id)["stats"]
        assert stats["tokens_recorded"] is False
        # The transform made no model call, so ITS zero is a measurement and stays one.
        assert rows["select"]["tokens"] == 0


# ── cancel ───────────────────────────────────────────────────────────────────


async def test_a_cancel_counts_the_generations_it_cut_off_and_keeps_one_duration(
    monkeypatch, tmp_path
):
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        fake.hold = asyncio.Event()
        run_id = await _start(supervisor, n=4)
        for _ in range(200):
            if fake.held >= 4:
                break
            await asyncio.sleep(0.05)
        assert fake.held == 4, f"only {fake.held} generations were in flight"

        assert service.cancel_run(run_id, supervisor=supervisor).get("ok")
        assert await _terminal(supervisor, run_id) is RunStatus.CANCELLED

        run = store.get(run_id)
        body = service.introspect(run_id)
        stats = body["stats"]
        # ONE duration for the run: the number the run page's header renders.
        assert run.elapsed_seconds > 0
        assert service.status(run_id)["elapsed_secs"] == run.elapsed_seconds
        assert stats["duration_secs"] == pytest.approx(
            run.elapsed_seconds, abs=0.01
        ), f"the header says {run.elapsed_seconds:.1f}s and Introspect {stats['duration_secs']}s"
        # Four generations were running when the cancel landed: that is spend nobody measured,
        # not "nothing costing money".
        assert stats.get("calls_cut_off") == 4, stats
        assert stats["tokens_recorded"] is False and stats["priced"] is False
        assert stats["models"] == [MODEL]

        cut = J.ledger(run_id, kinds={"step_cancelled"})
        assert [(r["node_id"], r["model_calls_open"]) for r in cut] == [("sample", 4)], cut
        assert (cut[0]["model"], cut[0]["provider"]) == (MODEL, ENTRY)
        # None of the four finished, so no provider reported anything: `null`, not a zero that
        # reads as four calls measured at nothing.
        assert (cut[0]["tokens"], cut[0]["cost_usd"]) == (None, None)
        assert any(row["kind"] == "step_cancelled" for row in body["answers"]["changed"])


# ── liveness ─────────────────────────────────────────────────────────────────

#: The stall window these tests run under, and the controller's tick, both shrunk so a stall takes
#: seconds rather than the shipped 300s. The tick is how often the window is checked.
STALL_SECS = 1
TICK_SECS = 0.2


async def test_a_model_still_generating_is_not_killed_as_stalled(monkeypatch, tmp_path):
    """Measured on a dev gateway with the stall window at 4s: a best-of-n whose model was streaming
    its answer over 8s failed "no progress for 4s (timeout_stall)" 5s in, mid-generation."""
    monkeypatch.setattr("personalclaw.workflows.controller.TICK_WAKE_SECS", TICK_SECS)
    async with _wired(monkeypatch, tmp_path, node_timeout_stall=STALL_SECS) as (fake, supervisor):
        fake.up = True
        fake.sample_text = "Blue is one of the three primary colors."
        # About 2.5s of steady output per candidate, never silent for more than 0.25s.
        fake.sample_chunks, fake.chunk_delay = 10, 0.25
        run_id = await _start(supervisor, n=2)
        status = await _terminal(supervisor, run_id)
        assert status is RunStatus.COMPLETE, service.status(run_id)["nodes"]

        (row,) = [r for r in J.ledger(run_id, kinds={J.STEP_COMPLETED}) if r["node_id"] == "sample"]
        # The control: the step really did outlast the window it was checked against.
        assert row["duration_secs"] > 2 * STALL_SECS, row


async def test_a_model_that_sends_nothing_is_still_stopped_and_its_calls_are_counted(
    monkeypatch, tmp_path
):
    """Silence is still a stall. And the step it stops had called the model twice, which its row
    has to say: the row carried no usage, and Introspect read "Models: none recorded"."""
    monkeypatch.setattr("personalclaw.workflows.controller.TICK_WAKE_SECS", TICK_SECS)
    async with _wired(monkeypatch, tmp_path, node_timeout_stall=STALL_SECS) as (fake, supervisor):
        fake.up = True
        fake.hold = asyncio.Event()  # every request is accepted and never answered
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        assert fake.held == 2

        (row,) = [r for r in J.ledger(run_id, kinds={J.STEP_FAILED}) if r["node_id"] == "sample"]
        assert row["failure"]["class"] == "timeout" and "timeout_stall" in row["error"], row
        assert row["model_calls_open"] == 2, row
        assert (row["model"], row["provider"]) == (MODEL, ENTRY)
        # Neither call finished, so neither provider reported usage: unknown, never zero.
        assert (row["tokens"], row["cost_usd"]) == (None, None)

        stats = service.introspect(run_id)["stats"]
        assert stats["calls_cut_off"] == 2
        assert stats["models"] == [MODEL]
        assert stats["tokens_recorded"] is False and stats["priced"] is False


# ── failed usage ─────────────────────────────────────────────────────────────


async def test_a_step_that_failed_after_its_calls_answered_records_what_they_used(
    monkeypatch, tmp_path
):
    """Every candidate came back empty, so the step failed, after two calls that each reported
    their usage. The row that ends the step is the only record of them."""
    async with _wired(monkeypatch, tmp_path) as (fake, supervisor):
        fake.up = True
        fake.sample_text = ""
        run_id = await _start(supervisor, n=2)
        assert await _terminal(supervisor, run_id) is RunStatus.FAILED
        assert len(fake.sample_requests()) == 2

        (row,) = [r for r in J.ledger(run_id, kinds={J.STEP_FAILED}) if r["node_id"] == "sample"]
        spent = 2 * (PROMPT_TOKENS + EVAL_TOKENS)
        assert row["tokens"] == spent, row
        assert (row["model"], row["provider"], row["cost_usd"]) == (MODEL, ENTRY, 0.0)
        assert row["model_calls_open"] == 0

        stats = service.introspect(run_id)["stats"]
        assert (stats["tokens"], stats["tokens_recorded"]) == (spent, True)
        assert stats["models"] == [MODEL]
        # The run row is charged the same number, so the header and Introspect agree, and a token
        # budget sees spend that ended in a failure.
        assert service.status(run_id)["tokens"] == spent
