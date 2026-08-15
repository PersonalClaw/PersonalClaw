"""MRT-4 — the heuristic route reorder, its purity contract, and its one call site.

Two things have to be true for ``route_refs`` to be safe to enable:

1. **It is a pure reorder.** Same refs in, same refs out — only the order differs. A reorder that
   dropped a candidate would silently remove a provider the user deliberately bound, and the
   resolution chain would then walk past it as though it were never configured.
2. **Something actually calls it.** A pure function nobody invokes is a dead control, so the call
   at ``resolve_provider_for_use_case`` is asserted structurally (AST), not assumed from the fact
   that the module imports cleanly.

The rest is the ordering contract itself: local-first, the pin short-circuit, the recorded-table
order, determinism/tie-break, and fail-open behavior on every input it reads.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from personalclaw.routing import policy

BRIDGE = Path(__file__).resolve().parents[1] / "src/personalclaw/providers/provider_bridge.py"

LOCAL = "ollama-models:qwen3:8b"
LOCAL_TINY = "ollama-models:tinyllm:1b"
CLOUD_A = "CloudA:big-model-1"
CLOUD_B = "CloudB:big-model-2"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home. Nothing in this module may touch the real ``~/.personalclaw``."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.config_dir", lambda: tmp_path)
    return tmp_path


def _enable(monkeypatch: pytest.MonkeyPatch, mode: str = "heuristic", pin: str = "") -> None:
    """Turn routing on: the master switch plus a per-use-case mode (both are required)."""
    monkeypatch.setattr(policy, "master_enabled", lambda: True)
    settings = {policy.MODE_KEY: mode}
    if pin:
        settings[policy.PIN_KEY] = pin
    monkeypatch.setattr(policy, "_settings_for", lambda _uc: dict(settings))


def _locals_are(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    monkeypatch.setattr(policy, "_local_provider_keys", lambda: {policy._norm(k) for k in keys})


# ── 1. the purity contract ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query_class",
    ["short_chat", "code", "summarize", "extract_structured", "long_reasoning", ""],
)
@pytest.mark.parametrize("mode", ["off", "heuristic", "learned"])
@pytest.mark.parametrize("pin", ["", "local", "cloud", CLOUD_B, "no-such-ref"])
def test_route_refs_is_a_pure_reorder(
    home: Path, monkeypatch: pytest.MonkeyPatch, query_class: str, mode: str, pin: str
) -> None:
    """Across EVERY mode/pin/class combination the result is a permutation of the input.

    Set-equality AND length are both asserted: length alone would miss a duplicated ref, and
    set-equality alone would miss a dropped duplicate. Together they pin the multiset.
    """
    _enable(monkeypatch, mode=mode, pin=pin)
    _locals_are(monkeypatch, "ollama-models")
    refs = [CLOUD_A, LOCAL, CLOUD_B, LOCAL_TINY]
    out = policy.route_refs("reasoning", query_class, refs)
    assert sorted(out) == sorted(refs), "route_refs changed the candidate SET"
    assert len(out) == len(refs), "route_refs changed the candidate COUNT"
    assert refs == [CLOUD_A, LOCAL, CLOUD_B, LOCAL_TINY], "route_refs mutated its input list"


def test_route_refs_preserves_duplicates(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated ref survives as a repeat — the invariant is on the multiset, not the set."""
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    refs = [CLOUD_A, LOCAL, CLOUD_A]
    out = policy.route_refs("reasoning", "code", refs)
    assert sorted(out) == sorted(refs)
    assert out.count(CLOUD_A) == 2


def test_route_refs_is_deterministic_and_stable(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equal-ranked refs keep their bound order (the documented tie-break), every time."""
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    refs = [CLOUD_B, CLOUD_A, LOCAL]
    first = policy.route_refs("reasoning", "short_chat", refs)
    for _ in range(5):
        assert policy.route_refs("reasoning", "short_chat", refs) == first
    # Two cloud refs are equal-ranked → their input order survives verbatim.
    assert first == [LOCAL, CLOUD_B, CLOUD_A]


# ── 2. the ordering contract ────────────────────────────────────────────────────


def test_off_is_identity(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default. With routing off the bound order is returned byte-for-byte."""
    _enable(monkeypatch, mode="off")
    _locals_are(monkeypatch, "ollama-models")
    refs = [CLOUD_A, LOCAL]
    assert policy.route_refs("reasoning", "summarize", refs) == refs


def test_master_switch_off_beats_a_use_case_mode(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``routing.enabled`` is genuinely the master: mode=heuristic cannot route without it."""
    monkeypatch.setattr(policy, "master_enabled", lambda: False)
    monkeypatch.setattr(policy, "_settings_for", lambda _uc: {policy.MODE_KEY: "heuristic"})
    assert policy.routing_active("reasoning") is False


def test_heuristic_orders_local_first(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    assert policy.route_refs("reasoning", "summarize", [CLOUD_A, LOCAL]) == [LOCAL, CLOUD_A]


def test_learned_mode_falls_back_to_the_heuristic(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MRT-5 owns learned scoring; until then ``learned`` behaves as the heuristic floor —
    it must not error and must not silently mean 'off'."""
    _enable(monkeypatch, mode="learned")
    _locals_are(monkeypatch, "ollama-models")
    assert policy.route_refs("reasoning", "summarize", [CLOUD_A, LOCAL]) == [LOCAL, CLOUD_A]


def test_long_reasoning_demotes_a_small_local_model(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 1B local model is the wrong tool for long reasoning; the 8B one still leads."""
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    out = policy.route_refs("reasoning", "long_reasoning", [CLOUD_A, LOCAL_TINY, LOCAL])
    assert out[0] == LOCAL
    assert out.index(LOCAL_TINY) > out.index(CLOUD_A)


def test_no_size_hint_is_not_treated_as_small(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An id that exposes no size is UNKNOWN, not tiny — it keeps its local-first slot."""
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    unsized = "ollama-models:some-model"
    assert policy.size_hint_b(unsized) == 0.0
    assert policy.route_refs("reasoning", "long_reasoning", [CLOUD_A, unsized])[0] == unsized


def test_structured_class_prefers_a_declaring_provider(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    monkeypatch.setattr(policy, "_structured_providers", lambda: {policy._norm("CloudB")})
    out = policy.route_refs("reasoning", "extract_structured", [CLOUD_A, LOCAL, CLOUD_B])
    assert out[0] == CLOUD_B


def test_pin_short_circuits_the_heuristic(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user pin is mightier than the policy: pin=cloud beats local-first outright."""
    _enable(monkeypatch, pin="cloud")
    _locals_are(monkeypatch, "ollama-models")
    assert policy.route_refs("reasoning", "summarize", [LOCAL, CLOUD_A]) == [CLOUD_A, LOCAL]


def test_explicit_ref_pin_hoists_that_ref(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, pin=CLOUD_B)
    _locals_are(monkeypatch, "ollama-models")
    assert policy.route_refs("reasoning", "code", [LOCAL, CLOUD_A, CLOUD_B])[0] == CLOUD_B


def test_unmatchable_pin_leaves_the_order_alone(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pin=local with nothing local bound must not drop or scramble anything."""
    _enable(monkeypatch, pin="local")
    _locals_are(monkeypatch)  # no local providers registered
    refs = [CLOUD_A, CLOUD_B]
    assert policy.route_refs("reasoning", "code", refs) == refs


def test_recorded_table_order_wins_and_keeps_unlisted_refs(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded order applies, and a ref bound AFTER the table was written is moved to the
    end — never lost (that would be the drop this whole atom guards against)."""
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    policy.save_policy(
        home,
        {
            "version": policy.POLICY_VERSION,
            "use_cases": {
                "reasoning": {
                    "mode": "heuristic",
                    "pin": None,
                    "classes": {
                        "summarize": {
                            "order": [CLOUD_A, LOCAL],
                            "basis": {"source": "user"},
                        }
                    },
                }
            },
        },
    )
    out = policy.route_refs("reasoning", "summarize", [LOCAL, CLOUD_B, CLOUD_A], home=home)
    assert out == [CLOUD_A, LOCAL, CLOUD_B]
    assert policy.order_basis("reasoning", "summarize", home=home) == {"source": "user"}


# ── 3. fail-open ────────────────────────────────────────────────────────────────


def test_corrupt_policy_file_reads_as_an_empty_table(home: Path) -> None:
    (home / "routing_policy.json").write_text("{not json", encoding="utf-8")
    assert policy.load_policy(home) == {
        "version": policy.POLICY_VERSION,
        "classifier_version": 1,
        "use_cases": {},
    }


def test_missing_policy_file_is_not_fatal(home: Path) -> None:
    assert policy.load_policy(home)["use_cases"] == {}


def test_a_raising_ranking_input_returns_the_original_order(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-open rail: a routing decision must never fail because a read did."""
    _enable(monkeypatch)

    def _boom() -> set[str]:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(policy, "_local_provider_keys", _boom)
    refs = [CLOUD_A, LOCAL]
    assert policy.route_refs("reasoning", "summarize", refs) == refs


def test_unknown_mode_reads_as_off(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized mode must not silently ENABLE routing."""
    monkeypatch.setattr(policy, "master_enabled", lambda: True)
    monkeypatch.setattr(policy, "_settings_for", lambda _uc: {policy.MODE_KEY: "aggressive"})
    assert policy.mode_for("reasoning") == "off"
    assert policy.routing_active("reasoning") is False


def test_single_candidate_is_returned_unchanged(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    assert policy.route_refs("reasoning", "code", [LOCAL]) == [LOCAL]
    assert policy.route_refs("reasoning", "code", []) == []


def test_unknown_provider_is_treated_as_cloud(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conservative direction: mislabeling cloud as local would order a paid, off-machine
    provider ahead of a free on-machine one."""
    _locals_are(monkeypatch, "ollama-models")
    assert policy.is_local_ref(CLOUD_A) is False
    assert policy.is_local_ref(LOCAL) is True
    # The APP-name/config-name spelling difference is absorbed (§7 gotcha).
    assert policy.is_local_ref("Ollama:qwen3:8b") is True


# ── 4. the call site is real (not merely defined) ───────────────────────────────


def _bridge_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in provider_bridge.py")


def test_route_refs_is_called_inside_resolve_provider_for_use_case() -> None:
    """The anti-dead-control rail: ``route_refs`` must be INVOKED at the resolution seam.

    Asserted on the AST of ``resolve_provider_for_use_case`` itself, so moving the call to a
    helper that nothing runs, or reducing it to a bare import, fails here.
    """
    fn = _bridge_function("resolve_provider_for_use_case")
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "route_refs" in called, "resolve_provider_for_use_case does not CALL route_refs"
    assert "routing_active" in called, "the routing seam does not check per-use-case enablement"


def test_the_seam_sits_after_the_override_and_native_bypasses() -> None:
    """Step ordering, structurally: the routing call must come AFTER the native-agent branch
    (step 0) and the model_override resolution (step 1) — both of which RETURN — and before the
    active-ref loop it reorders. If it moved earlier, an explicit model choice would be routed."""
    src = BRIDGE.read_text(encoding="utf-8")
    fn_start = src.index("def resolve_provider_for_use_case(")
    body = src[fn_start:]
    native = body.index("_build_native_runtime(")
    override = body.index('if model_override and (":" in model_override')
    seam = body.index("route_refs(")
    loop = body.index("for i, ref in enumerate(_refs)")
    assert native < override < seam < loop


def test_routed_provenance_fields_exist_and_are_distinct_from_degraded() -> None:
    """``routed_fallback`` is a SEPARATE column from ``degraded`` — collapsing them would make a
    router's local-first bet indistinguishable from a user's own chain falling through."""
    from personalclaw.guardrails.audit import AttemptRecord

    rec = AttemptRecord(
        audit_id="a", ts=0.0, use_case="reasoning", provider="p", model="m", attempt=1
    )
    assert rec.routed is False and rec.routed_fallback is False and rec.degraded is False
    row = json.loads(
        AttemptRecord(
            audit_id="a",
            ts=0.0,
            use_case="reasoning",
            provider="p",
            model="m",
            attempt=2,
            routed=True,
            routed_fallback=True,
        ).to_json_line()
    )
    assert row["routed"] is True
    assert row["routed_fallback"] is True
    assert row["degraded"] is False


def test_routing_config_round_trips_through_the_loader(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wiring points (a)-(c): a written value survives load() and reappears in to_dict()."""
    from personalclaw.config.loader import AppConfig

    (home / "config.json").write_text(
        json.dumps(
            {
                "routing": {
                    "enabled": True,
                    "local_timeout_secs": 7.5,
                    "min_samples": 9,
                    "weights": {"success": 0.7, "feedback": 0.3},
                    "hysteresis": 0.02,
                    "cloud_quality_margin": 0.2,
                    "energy_sampling": True,
                    "reproposal_cooldown_days": 3,
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = AppConfig.load()
    assert cfg.routing.enabled is True
    assert cfg.routing.local_timeout_secs == 7.5
    assert cfg.routing.min_samples == 9
    assert cfg.routing.weights.success == 0.7
    assert cfg.routing.reproposal_cooldown_days == 3
    assert cfg.to_dict()["routing"]["local_timeout_secs"] == 7.5


def test_routing_config_survives_a_malformed_section(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo degrades to the shipped default instead of making config.json unloadable."""
    from personalclaw.config.loader import AppConfig

    (home / "config.json").write_text(
        json.dumps({"routing": {"local_timeout_secs": "soon", "min_samples": 0, "weights": 3}}),
        encoding="utf-8",
    )
    cfg = AppConfig.load()
    assert cfg.routing.local_timeout_secs == 20.0
    assert cfg.routing.min_samples == 1  # floored, never zero
    assert cfg.routing.weights.success == 0.60
    assert cfg.routing.enabled is False  # master stays OFF


def test_editable_config_exposes_the_runtime_subset() -> None:
    """Wiring point (d): the PATCH allowlist carries the routing knobs a user tunes live."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    for key in (
        "routing.enabled",
        "routing.local_timeout_secs",
        "routing.min_samples",
        "routing.hysteresis",
        "routing.cloud_quality_margin",
        "routing.energy_sampling",
        "routing.reproposal_cooldown_days",
    ):
        assert key in _EDITABLE_CONFIG, f"{key} is not runtime-editable"
    # Per-use-case mode/pin deliberately do NOT live in config.json (§7).
    assert not [k for k in _EDITABLE_CONFIG if k.startswith("routing.use_cases")]


# ── 5. the seam behaves: provenance, bypass, and the pinned-ref-raises rule ──────


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch):
    """Drive ``resolve_provider_for_use_case`` with a recording stand-in for the registry
    resolver, so the assertions are about what the SEAM does: which refs it walks, in which
    order, and what routing provenance it hands each attempt."""
    from personalclaw.providers import provider_bridge as pb

    calls: list[dict] = []

    def _fake_resolve(capability: str, **kwargs):
        calls.append(
            {
                "model": kwargs.get("model_override"),
                "hint": kwargs.get("provider_hint"),
                "routed": kwargs.get("_guard_routed", False),
                "routed_fallback": kwargs.get("_guard_routed_fallback", False),
            }
        )
        return object() if kwargs.get("_resolvable", True) else None

    monkeypatch.setattr(pb, "_resolve_from_config_registry", _fake_resolve)
    return pb, calls


def _bind(monkeypatch: pytest.MonkeyPatch, refs: list[str]) -> None:
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.active_model_refs", lambda _uc: list(refs)
    )


def test_seam_walks_the_routed_order_and_stamps_routed(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """SC #3, first half: with local+cloud bound on ``reasoning`` and the heuristic enabled, the
    LOCAL ref is attempted first and the attempt carries ``routed`` provenance."""
    pb, calls = seam
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])
    pb.resolve_provider_for_use_case("reasoning")
    assert [c["model"] for c in calls] == ["qwen3:8b"], "local-first order was not walked"
    assert calls[0]["routed"] is True
    assert calls[0]["routed_fallback"] is False, "the FIRST routed candidate is not a fallback"


def test_a_cloud_rescue_of_the_routed_local_bet_stamps_routed_fallback(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """SC #3, second half: when the routed-first local ref can't serve (breaker OPEN — the same
    state a killed local server produces), the chain reaches the cloud ref and THAT attempt is
    stamped ``routed_fallback``. One pass over the chain: no extra attempt, no stacked timeout."""
    pb, calls = seam
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])

    class _OpenBreaker:
        def is_open(self) -> bool:
            return True

    monkeypatch.setattr(
        "personalclaw.guardrails.breaker.get_breaker",
        lambda name, **_kw: _OpenBreaker() if "ollama" in name else _ClosedBreaker(),
    )
    pb.resolve_provider_for_use_case("reasoning")
    # The local ref was skipped without being built (no timeout burned rediscovering it), and the
    # cloud ref served the call carrying the routing-outcome stamp.
    assert [c["hint"] for c in calls] == ["CloudA"]
    assert calls[0]["routed"] is True
    assert calls[0]["routed_fallback"] is True


class _ClosedBreaker:
    def is_open(self) -> bool:
        return False


def test_routing_off_stamps_no_provenance(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """With routing off, resolution is indistinguishable from a machine that never had it: bound
    order, and no ``routed`` stamp to make an unrouted call look routed."""
    pb, calls = seam
    _enable(monkeypatch, mode="off")
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])
    pb.resolve_provider_for_use_case("reasoning")
    assert [c["hint"] for c in calls] == ["CloudA"]
    assert calls[0]["routed"] is False and calls[0]["routed_fallback"] is False


def test_model_override_bypasses_routing_entirely(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """SC #4: an explicit model choice never reaches the routing seam, and is never stamped as
    routed — the provenance says truthfully that the CALLER chose, not the router."""
    pb, calls = seam
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])
    pb.resolve_provider_for_use_case("reasoning", model_override="CloudB:big-model-2")
    assert len(calls) == 1
    assert calls[0]["routed"] is False, "an explicit override was stamped as a routing decision"


def test_pin_short_circuit_is_visible_in_what_the_seam_walks(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """SC #4, the pin half: a cloud pin beats local-first, and the attempt is still ``routed``
    (routing decided the order — the user's pin is what it decided WITH)."""
    pb, calls = seam
    _enable(monkeypatch, pin="cloud")
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [LOCAL, CLOUD_A])
    pb.resolve_provider_for_use_case("reasoning")
    assert [c["hint"] for c in calls] == ["CloudA"]
    assert calls[0]["routed"] is True


def test_unresolvable_routed_first_ref_still_raises(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC #6: routing changes ORDER, not resolution semantics. A chain whose every entry is
    unbuildable still raises ProviderResolutionError — routing must never convert a stale pin into
    a silent fall-through to some other provider."""
    from personalclaw.providers import provider_bridge as pb

    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])
    monkeypatch.setattr(pb, "_resolve_from_config_registry", lambda *_a, **_k: None)
    with pytest.raises(pb.ProviderResolutionError) as exc:
        pb.resolve_provider_for_use_case("reasoning")
    assert exc.value.agent_error is not None
    assert exc.value.agent_error.code == "ERR_MODEL_UNRESOLVED"
    assert "cannot be built" in str(exc.value)


def test_a_ref_removed_from_active_models_drops_from_candidates(
    home: Path, monkeypatch: pytest.MonkeyPatch, seam
) -> None:
    """SC #6, second half: the candidate pool is READ from active_models.json every resolution, so
    unbinding a ref removes it on the next load. Nothing in routing caches a pool."""
    pb, calls = seam
    _enable(monkeypatch)
    _locals_are(monkeypatch, "ollama-models")
    _bind(monkeypatch, [CLOUD_A, LOCAL])
    pb.resolve_provider_for_use_case("reasoning")
    assert calls[0]["hint"] == "ollama-models"
    calls.clear()
    _bind(monkeypatch, [CLOUD_A])  # the local ref is unbound
    pb.resolve_provider_for_use_case("reasoning")
    assert [c["hint"] for c in calls] == ["CloudA"]


def test_routing_policy_travels_in_a_snapshot() -> None:
    """A user decision (mode/pin/manual order) must survive a restore, like autonomy_rungs.json.
    The derived stats fold deliberately does NOT travel — it is rebuildable."""
    from personalclaw.snapshot import CORE_FILES

    assert "routing_policy.json" in CORE_FILES["config"]
    assert "routing_stats.json" not in {f for files in CORE_FILES.values() for f in files}
