"""Which model the PAIRED evals score against (#2680) — resolution, refusal, and the delta.

The defect: the loop-2 :mod:`~personalclaw.evals.gate`, the
:mod:`~personalclaw.evals.ablation` report and :mod:`~personalclaw.evals.skills_bench` each
ran a matrix over two arms that are SUPPOSED to differ, and none of them declared a
``provider_binding``. A cell is spawned with no ambient credentials, so every one of them fell
through to the offline ``scripted`` replay — byte-identical output for the same scenario no
matter what the arm staged. Two arms that differ could only ever tie, and the gate published a
``0.0`` delta it had not measured.

Three claims are tested here, and the third is the one that actually closes the issue:

1. **a bound run records what answered** — the PERSISTED artifact carries a real
   ``cell_model_fp``, not the ``no_model`` sentinel. Read off the file, never off a log line;
2. **an unbound run REFUSES** — it names the unmet precondition and emits no score at all;
3. **two arms whose staged artifacts differ produce DIFFERENT scores.**

**How claim 3 is made real rather than assumed.** ``tests/test_evals_gate.py`` proves the
before/after *arithmetic* against ``_ScoringMatrix``, a stand-in that models the agent as a
perfect-recall reader of the staged text. That is the right tool for the arithmetic and the
wrong one for this claim: a stand-in cannot show that a staged file reaches a real model's
prompt, which is precisely what was broken. So
:func:`test_two_arms_whose_staged_artifacts_differ_score_differently` runs the REAL
``run_matrix`` — real spawn, real ``build_child_env`` allowlist, real ``cell_provider``
staging, real ``EvalRunner``, real ``openai`` SDK client over a real socket — against a
loopback endpoint that speaks the OpenAI streaming dialect and ECHOES the prompt it was
given. The echo is not a stand-in for the model's *judgement*; it is what makes "did the
staged bytes reach the prompt" a deterministic question instead of a probabilistic one. The
same gate was also driven end to end against a real local model (Ollama ``gemma4:12b``), which
measured ``before 0.5`` / ``after 1.0`` through this identical path.

The seam that carries the arm into the prompt is worth naming, because it is not the obvious
one: ``eval/runner.py`` sends ``provider.stream(turn_def.user)`` — the raw user text — so
``ContextBuilder.build_message`` (which owns per-turn skill *surfacing*) is NOT on the eval
path. What IS on it is ``build_session_context``, called for every session after the first and
PREPENDED to that session's first turn; it injects ``SkillsLoader.get_context()``, which
carries an ``always: true`` skill's full body. Hence a two-session scenario, and hence a
staged skill that declares ``always``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from personalclaw.evals import benchmark_binding as bb
from personalclaw.evals import gate as gate_mod
from personalclaw.evals import provenance
from personalclaw.evals import scenarios as scenario_lib

# ── homes ─────────────────────────────────────────────────────────────────────


def _home(
    tmp_path: Path,
    monkeypatch,
    *,
    providers: list[dict] | None = None,
    chain: list[str] | None = None,
    declared: str | None = None,
    budget: float = 1.0,
) -> Path:
    """An isolated home, written as FILES so the real resolution paths are the ones under test.

    ``PERSONALCLAW_HOME`` rather than patching ``config_dir``: the loader reads it per call, so
    it is undoable, and a consumer module's ``from ... import config_dir`` cannot freeze it.

    ``chain`` is load-bearing for every gate-level test, in a way worth stating: ``RunPin``'s
    ``model_fingerprint`` comes from ``active_models.json``, and ``is_complete()`` requires it —
    so a home with an EMPTY chain refuses at ``UNGATED_NO_PIN`` and never reaches the provider
    check at all. A chain that NAMES a model pins fine whether or not the ref resolves, which is
    what separates "this home has no models" from "this home's models do not resolve".
    """
    evals: dict = {"enabled": True, "default_budget_usd": budget}
    if declared is not None:
        evals["benchmark_model_ref"] = declared
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": providers if providers is not None else [], "evals": evals}),
        encoding="utf-8",
    )
    (tmp_path / "active_models.json").write_text(
        json.dumps({"chat": list(chain or [])}), encoding="utf-8"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ══ the three outcomes ════════════════════════════════════════════════════════


def test_a_declared_ref_that_resolves_is_recorded_as_declared(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], declared="Acme:m1")
    bound = bb.resolve_benchmark_binding()
    assert bound.is_bound
    assert bound.source == bb.SOURCE_DECLARED
    assert bound.ref == "Acme:m1"
    assert bound.chain_position is None


def test_an_undeclared_ref_falls_back_to_the_default_chain_and_says_which_position(
    tmp_path, monkeypatch
):
    """The (c) fallback, and the record that makes it provable rather than invisible."""
    _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], chain=["Acme:m1"], declared="")
    bound = bb.resolve_benchmark_binding()
    assert bound.is_bound
    assert bound.source == bb.SOURCE_DEFAULT_CHAIN
    assert bound.chain_position == 0


def test_a_fallen_back_run_is_distinguishable_from_a_directly_bound_one(tmp_path, monkeypatch):
    """Same ref, two paths — and the artifact tells them apart.

    The whole hazard of a fallback is that it looks like a choice. These two bindings resolve
    the IDENTICAL model, so ``ref`` cannot separate them; ``source`` and ``chain_position``
    have to, or "we scored against your model" and "we scored against whatever was lying
    around" read the same in the report.
    """
    _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], chain=["Acme:m1"])
    fell_back = bb.resolve_benchmark_binding(declared="")
    chosen = bb.resolve_benchmark_binding(declared="Acme:m1")
    assert fell_back.ref == chosen.ref
    assert fell_back.to_dict() != chosen.to_dict()
    assert (fell_back.source, fell_back.chain_position) == (bb.SOURCE_DEFAULT_CHAIN, 0)
    assert (chosen.source, chosen.chain_position) == (bb.SOURCE_DECLARED, None)


def test_a_declared_ref_that_does_not_resolve_never_falls_back(tmp_path, monkeypatch):
    """Substituting a model the operator did NOT pick, after being told which one to use.

    Worse than doing it by omission, so the chain is not consulted at all here: the declared
    ref is reported ``unresolved`` with the binding's own sentence, even though ``Acme:m1``
    sitting in the chain would have resolved fine.
    """
    _home(
        tmp_path,
        monkeypatch,
        providers=[{"name": "Acme"}],
        chain=["Acme:m1"],
        declared="Ghost:m9",
    )
    bound = bb.resolve_benchmark_binding()
    assert not bound.is_bound
    assert bound.source == bb.SOURCE_UNRESOLVED
    assert bound.ref == "Ghost:m9"
    assert "Ghost" in bound.detail
    # 🪤 And it names the FIELD the ref came from, not only the missing provider. Driving this
    # refusal through the dashboard produced a sentence whose sole instruction was "Settings →
    # Models" — the panel that holds providers, not the one holding the ref the operator
    # mistyped. Both panels have to appear, or the message sends the reader to the wrong one.
    assert bb.CONFIG_FIELD in bound.detail
    assert "Settings → Evaluations" in bound.detail
    assert "Settings → Models" in bound.detail, "cell_provider's own diagnosis is kept verbatim"


def test_the_chain_is_walked_in_order_so_a_broken_default_does_not_refuse_the_home(
    tmp_path, monkeypatch
):
    """The chain IS the fallback order; honouring only position 0 would refuse a fine home.

    Position 0 is UNQUALIFIED — no ``Provider:`` prefix — because that is the one chain entry
    ``resolve_binding`` can actually reject. A ref naming a provider the home does not have
    never reaches here at all: ``load_active_models`` prunes it (and, since the pruned list is
    also what ``RunPin.model_fingerprint`` reads, prunes it out of the pin too). An unqualified
    ref survives both, so it is the real shape of a chain entry that cannot bind.
    """
    _home(
        tmp_path,
        monkeypatch,
        providers=[{"name": "Acme"}],
        chain=["unqualified-model-id", "Acme:m1"],
        declared="",
    )
    bound = bb.resolve_benchmark_binding()
    assert bound.is_bound
    assert (bound.ref, bound.chain_position) == ("Acme:m1", 1)


def test_nothing_declared_and_an_empty_chain_names_the_unmet_precondition(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, providers=[], chain=[], declared="")
    bound = bb.resolve_benchmark_binding()
    assert not bound.is_bound
    assert bb.CONFIG_FIELD in bound.detail
    assert "Settings" in bound.detail


def test_a_chain_where_nothing_resolves_names_every_candidate_it_tried(tmp_path, monkeypatch):
    """Exhausting the chain is a distinct outcome from having no chain, and says which refs.

    Naming them matters because this is the case an operator cannot diagnose by looking at
    Settings → Models: the refs ARE listed there and they DO look bound.
    """
    _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], chain=["bare-a", "bare-b"])
    bound = bb.resolve_benchmark_binding(declared="")
    assert not bound.is_bound
    assert "bare-a" in bound.detail and "bare-b" in bound.detail


def test_every_outcome_carries_the_same_artifact_keys(tmp_path, monkeypatch):
    """A reader switches on ``source``; it must never have to probe for a field's presence."""
    keys = {"source", "use_case", "ref", "detail", "chain_position"}
    _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], chain=["Acme:m1"])
    for declared in ("Acme:m1", "", "Ghost:m9"):
        assert set(bb.resolve_benchmark_binding(declared=declared).to_dict()) == keys


# ══ the gate: the positive and negative controls ══════════════════════════════


def _gate_scenario(name: str, *, token: str) -> dict:
    """Two sessions, because only session 2+ gets ``build_session_context``.

    Session 1's assertion is satisfied by the user text itself, so it passes on both arms and
    the delta comes from session 2 alone — which keeps ``0.5`` vs ``1.0`` legible instead of
    collapsing two effects into one number.
    """
    return {
        "name": name,
        "version": 1,
        "fixture_home": "empty",
        "tiers": ["gate"],
        "sessions": [
            {
                "name": "warmup",
                "turns": [
                    {
                        "user": "Reply with the single word: acknowledged",
                        "assertions": [
                            {"type": "contains", "value": "acknowledged", "case_sensitive": False}
                        ],
                    }
                ],
            },
            {
                "name": "probe",
                "turns": [
                    {
                        "user": "What is the deployment codeword? Reply with the codeword only.",
                        "assertions": [{"type": "contains", "value": token}],
                    }
                ],
            },
        ],
    }


def _install(home: Path, data: dict) -> gate_mod.GateSubset:
    """Install one scenario and return a subset holding ONLY it.

    Built by hand rather than through ``gate_subset()`` so the shipped dozen cannot join the
    run: every member is a spawned child, and this suite pays for each one.
    """
    d = home / "evals" / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{data['name']}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    scenario_lib.install_library()
    member = gate_mod.SubsetMember(
        name=data["name"],
        sha256=scenario_lib.sha256_of_scenario_data(data),
        turns=gate_mod.turn_count(data),
        hard_assertions=gate_mod.hard_assertion_count(data),
    )
    return gate_mod.GateSubset(members=(member,))


class _RecordingMatrix:
    """Records the ``provider_binding`` each cell was handed, and scores nothing."""

    def __init__(self) -> None:
        self.bindings: list[object] = []

    def __call__(self, spec, *, matrix_id, artifact_arm=None, provider_binding=None, **_kw):
        from personalclaw.evals.matrix import MatrixResult

        self.bindings.append(provider_binding)
        return MatrixResult(spec=spec, cells=[], aggregates={})


def test_a_bound_gate_hands_every_cell_the_resolved_binding(tmp_path, monkeypatch):
    """The positive control at the caller boundary: the binding actually reaches ``run_matrix``.

    Cheap and separate from the end-to-end test on purpose — this one would still red if the
    resolution worked and the argument were dropped, which is the exact shape of the original
    defect one layer up.
    """
    home = _home(
        tmp_path,
        monkeypatch,
        providers=[{"name": "Acme"}],
        chain=["Acme:m1"],
        declared="Acme:m1",
    )
    subset = _install(home, _gate_scenario("bound_probe", token="TOKEN-1"))
    fake = _RecordingMatrix()
    report = gate_mod.run_gate(
        run_id="r1",
        arms=(
            gate_mod.ArtifactArm(label=gate_mod.ARM_BEFORE, files={}),
            gate_mod.ArtifactArm(label=gate_mod.ARM_AFTER, files={"skills/x/SKILL.md": "x"}),
        ),
        subset=subset,
        run_matrix=fake,
    )
    assert report.state == gate_mod.GATE_GATED
    assert fake.bindings and all(b is not None for b in fake.bindings)
    assert {b.model_ref() for b in fake.bindings} == {"Acme:m1"}
    assert report.provider["source"] == bb.SOURCE_DECLARED


def test_an_unbound_gate_refuses_to_score_and_emits_no_score(tmp_path, monkeypatch):
    """The negative control: nothing resolvable ⇒ REFUSE, name the precondition, score nothing.

    A silent zero is the failure #2680 exists to close, so all three halves are asserted —
    the state, the sentence, and the absence of any scored cell. ``run_matrix`` is handed in
    and must never be called: a refusal that still spawned children would have spent real
    money to produce a number it then discarded.

    The home's chain is non-empty but UNQUALIFIED, which is the only shape that reaches this
    check: it pins fine (``model_fingerprint`` is non-empty) and binds to nothing. A home with
    an empty chain, or one naming an absent provider, is a DIFFERENT refusal — see
    :func:`test_the_refusal_is_reported_separately_from_the_unpinnable_one`.
    """
    home = _home(tmp_path, monkeypatch, providers=[{"name": "Acme"}], chain=["bare-model"])
    subset = _install(home, _gate_scenario("unbound_probe", token="TOKEN-2"))
    fake = _RecordingMatrix()
    report = gate_mod.run_gate(
        run_id="r2",
        arms=(
            gate_mod.ArtifactArm(label=gate_mod.ARM_BEFORE, files={}),
            gate_mod.ArtifactArm(label=gate_mod.ARM_AFTER, files={"skills/x/SKILL.md": "x"}),
        ),
        subset=subset,
        run_matrix=fake,
    )
    assert report.state == gate_mod.GATE_UNGATED
    assert bb.CONFIG_FIELD in report.reason
    assert report.provider["source"] == bb.SOURCE_UNRESOLVED
    assert fake.bindings == []
    for block in (report.before, report.after):
        assert block["scored"] == 0
        assert block["mean_score"] is None
    assert report.delta is None


def test_the_refusal_is_reported_separately_from_the_unpinnable_one(tmp_path, monkeypatch):
    """An unpinnable home has a MORE specific unmet precondition, and must keep it.

    The provider check sits after ``pin.is_complete()`` for exactly this reason. Both facts are
    true of a home that binds nothing — it cannot pin AND it cannot resolve a model — so the
    order decides which one the operator is told, and "your pin is missing model_fingerprint"
    names a narrower thing to go fix than "nothing resolved".
    """
    home = _home(tmp_path, monkeypatch, providers=[], chain=[])
    subset = _install(home, _gate_scenario("unpinned_probe", token="TOKEN-3"))
    report = gate_mod.run_gate(run_id="r3", arms=_two_arms(), subset=subset, run_matrix=_fail)
    assert report.state == gate_mod.GATE_UNGATED
    assert "model_fingerprint" in report.reason
    assert bb.CONFIG_FIELD not in report.reason


def _two_arms():
    return (
        gate_mod.ArtifactArm(label=gate_mod.ARM_BEFORE, files={}),
        gate_mod.ArtifactArm(label=gate_mod.ARM_AFTER, files={"skills/x/SKILL.md": "x"}),
    )


def _fail(*_a, **_k):  # pragma: no cover - a refusal must not reach the matrix
    raise AssertionError("run_matrix was called on a refusal path")


# ══ claim 1: the PERSISTED artifact records what answered ═════════════════════


def test_the_persisted_gate_artifact_records_a_real_cell_model_fp(tmp_path, monkeypatch):
    """Asserted off the proposal FILE, because a log line is not evidence.

    The gate computes its own pin (``compute_pin_for_subject``) separately from the per-cell
    pin ``run_matrix`` computes, so the cell-model half has to be stamped onto it explicitly —
    without that, a fully bound run persists ``cell_model_fp: no_model`` and the artifact
    contradicts the run.
    """
    from personalclaw.learning import proposals as queue

    home = _home(
        tmp_path,
        monkeypatch,
        providers=[{"name": "Acme"}],
        chain=["Acme:m1"],
        declared="Acme:m1",
    )
    subset = _install(home, _gate_scenario("persist_probe", token="TOKEN-4"))
    report = gate_mod.run_gate(
        run_id="r4", arms=_two_arms(), subset=subset, run_matrix=_RecordingMatrix()
    )

    # `attach_gate` is the real persistence seam — the inbox card and the detail view both
    # project the gate block off the proposal FILE, so what this reads back is what a user sees.
    _verdict, prop = queue.enqueue(
        kind="skill",
        title="deploy-codeword",
        body="The deployment codeword is PINEAPPLE-7742.",
        target="deploy-codeword",
        provenance="human",
    )
    assert prop is not None
    assert queue.attach_gate(prop.id, report.to_dict())

    on_disk = json.loads(
        (home / "learning" / "proposals" / f"{prop.id}.json").read_text(encoding="utf-8")
    )
    pin = on_disk["gate"]["pin"]
    assert pin["cell_model_fp"] not in (provenance.NO_MODEL, provenance.UNRECORDED)
    assert len(pin["cell_model_fp"]) == 12
    assert pin["cell_model_fingerprint"] == {"chat": "Acme:m1"}
    assert on_disk["gate"]["provider"]["ref"] == "Acme:m1"


# ══ claim 3: THE DISCRIMINATING TEST ═════════════════════════════════════════


class _EchoHandler(BaseHTTPRequestHandler):
    """An OpenAI-compatible streaming endpoint that echoes the prompt it was given.

    Real wire protocol on a real socket: the cell's ``OpenAIProvider`` is the shipped client,
    ``stream=True`` with ``stream_options.include_usage`` is the shipped request, and the SSE
    frames below are the shipped dialect. What is NOT real is the model's judgement — and
    that is the point. The question this test answers is "do the staged bytes reach the
    prompt", which an echo makes deterministic and a language model makes a coin flip.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # noqa: A003 - silence the stderr access log
        return None

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        prompt = "".join(str(m.get("content") or "") for m in body.get("messages") or [])
        self.server.prompts.append(prompt)  # type: ignore[attr-defined]

        def frame(payload: dict) -> bytes:
            return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"

        chunks = [
            frame(
                {
                    "id": "echo",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model") or "echo",
                    "choices": [{"index": 0, "delta": {"content": prompt}, "finish_reason": None}],
                }
            ),
            frame(
                {
                    "id": "echo",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model") or "echo",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            ),
            frame(
                {
                    "id": "echo",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model") or "echo",
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
            b"data: [DONE]\n\n",
        ]
        payload = b"".join(chunks)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@dataclass
class _Endpoint:
    """The loopback endpoint's address and the prompts it was actually sent."""

    base_url: str
    prompts: list[str]


@pytest.fixture()
def echo_endpoint():
    """A loopback OpenAI-compatible endpoint the SPAWNED cell can reach over TCP.

    Threading matters: each cell is a separate process and the scenario has two sessions, so a
    single-shot handler would deadlock the run rather than fail it.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    server.prompts = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Endpoint(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            prompts=server.prompts,  # type: ignore[attr-defined]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


#: The staged candidate. ``always: true`` is what puts the BODY (rather than a one-line index
#: entry) into ``SkillsLoader.get_context()``, which is the only skill text
#: ``build_session_context`` injects — and ``build_session_context`` is the only ContextBuilder
#: call on the eval path.
_CANDIDATE_SKILL = """---
name: auto/deploy-codeword
description: The project's deployment codeword.
always: true
source: auto
---

# deploy-codeword (auto-generated)

The deployment codeword is {token}. When asked for it, answer exactly {token}.
"""


@pytest.mark.timeout(300)
def test_two_arms_whose_staged_artifacts_differ_score_differently(
    tmp_path, monkeypatch, echo_endpoint
):
    """The clause #2680 exists to satisfy — and the one an unbound gate could never satisfy.

    Everything below the ``run_gate`` call is shipped code: the real ``run_matrix``, the real
    ``build_child_env`` name allowlist, a real spawned child per cell, the real
    ``cell_provider`` staging that registers the wire type inside that child, the real
    ``gate.apply_in_child`` staging of the arm, the real ``EvalRunner``, the real
    ``Assertion.check``. Only the endpoint is local — see :class:`_EchoHandler`.

    Before the binding existed this asserted a tie: both arms resolved ``llm/scripted.py``,
    whose replay is byte-identical for a given scenario regardless of what the arm staged.
    """
    token = "PINEAPPLE-7742"
    home = _home(
        tmp_path,
        monkeypatch,
        providers=[
            {
                "name": "Echo",
                "type": "eval_echo",
                "model": "echo-1",
                "options": {"base_url": echo_endpoint.base_url},
            }
        ],
        chain=["Echo:echo-1"],
        declared="Echo:echo-1",
        budget=100.0,
    )
    subset = _install(home, _gate_scenario("discriminating_probe", token=token))

    report = gate_mod.run_gate(
        run_id="discriminating",
        arms=(
            # An empty ``before`` is the honest baseline for a candidate that does not exist
            # yet — which is exactly what ``arms_for_proposal`` builds for a new auto skill.
            gate_mod.ArtifactArm(label=gate_mod.ARM_BEFORE, files={}),
            gate_mod.ArtifactArm(
                label=gate_mod.ARM_AFTER,
                files={
                    "skills/auto/deploy-codeword/SKILL.md": _CANDIDATE_SKILL.format(token=token)
                },
            ),
        ),
        subset=subset,
        trials=1,
    )

    assert report.state == gate_mod.GATE_GATED, report.reason
    assert report.before["scored"] == 1 and report.after["scored"] == 1
    # THE assertion. Not "the arms ran" and not "a score exists" — the two scores DIFFER.
    assert report.before["mean_score"] != report.after["mean_score"], report.to_dict()
    # Pinned to the exact pair rather than an inequality, because the arithmetic is knowable
    # and a bare `!=` would also be satisfied by noise: the scenario has two hard assertions,
    # session 1's passes on both arms (the user text answers it), session 2's passes only where
    # the staged skill reached the prompt — so 1/2 against 2/2. The same numbers came back from
    # the real-model drive (Ollama gemma4:12b) through this identical path.
    assert (report.before["mean_score"], report.after["mean_score"]) == (0.5, 1.0)
    assert report.delta == 0.5
    assert report.regressed is False
    # …and the difference is the staged token reaching the PROMPT, not noise elsewhere. Without
    # this, a passing delta could come from anything that happens to differ between two child
    # processes; with it, the causal chain arm → staged file → session context → prompt is
    # observed at the wire.
    sent = "\n".join(echo_endpoint.prompts)
    assert token in sent, "the staged skill never reached any prompt"
    # Claim 1 again, on a run that really spawned cells rather than a recorded fake: the pin
    # names the model that answered.
    assert report.pin["cell_model_fingerprint"] == {"chat": "Echo:echo-1"}
    assert report.pin["cell_model_fp"] not in (provenance.NO_MODEL, provenance.UNRECORDED)
    assert report.provider["source"] == bb.SOURCE_DECLARED
