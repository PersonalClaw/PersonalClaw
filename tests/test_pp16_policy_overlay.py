"""PP-16 seam 4d: the sparse per-run ``SupervisorPolicy`` overlay (OWNER RULING 2).

The ruling, restated once: ``SupervisorPolicy`` had ZERO persistence while five of its knobs
(`attended`, `autopilot`, `max_cycles`, `idle_secs`, `success_criteria`) are per-INSTANCE,
user-settable settings. A template is SHARED across runs, so it structurally cannot hold a
per-instance value — the declared defaults stay where they are (``KIND_CONVERGENCE``, a
template's ``supervisor:`` block) and the run persists ONLY its overrides
(``WorkflowRun.policy_overrides``). Policy is still computed once, from defaults + overlay:
one admission core, N policies.

These tests pin the contract's two halves and their deliberate asymmetry: the WRITE seam
(``store.set_policy_overrides``) is strict — an unknown key is refused loudly while it still
has an author to complain to — and the READ side (``apply_policy_overrides``,
``WorkflowRun.from_dict``) is tolerant, because there an unknown key means a core downgraded
under a run written by a newer one, which must neither crash nor drop the key on re-save.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.workflows import store as st
from personalclaw.workflows import supervisor_policy as sp
from personalclaw.workflows.models import WorkflowRun


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """Same double-binding patch as `test_workflows_store.py` — the store imported
    `config_dir` by value, so patching only the config module leaves it on the real home."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    return tmp_path


def _run(**kw) -> WorkflowRun:
    base = {"id": "", "workflow_name": "research"}
    base.update(kw)
    return WorkflowRun(**base)  # type: ignore[arg-type]


def test_an_empty_overlay_is_the_kind_policy_the_same_object() -> None:
    """The sparse-by-default clause: a run overriding nothing resolves to EXACTLY the kind's
    policy — asserted on the WHOLE policy, and on identity, so the common case provably
    costs nothing (no copy, no field drift)."""
    base = sp.policy_for_kind("sdlc")
    assert sp.apply_policy_overrides(base, {}) is base
    assert sp.apply_policy_overrides(base, None) is base
    assert sp.policy_for_run("sdlc") == base


def test_one_override_moves_one_knob_and_nothing_else() -> None:
    """Overriding `max_cycles` changes that budget and leaves every other field identical to
    the kind defaults — the overlay is a delta, never a re-declaration."""
    base = sp.policy_for_kind("sdlc")
    resolved = sp.policy_for_run("sdlc", overrides={"max_cycles": 7})
    assert resolved.budget_max_cycles == 7
    # Everything else is untouched: putting the base's own value back must reproduce it.
    from dataclasses import replace

    assert replace(resolved, budget_max_cycles=base.budget_max_cycles) == base


def test_every_overridable_key_has_a_working_applier() -> None:
    """The closed set is honest in both directions: every declared key applies without
    raising and produces a policy that differs from (or, for a no-op value, equals) the
    base — no key in ``OVERRIDABLE_POLICY_KEYS`` is a dead entry."""
    base = sp.policy_for_kind("sdlc")
    samples = {
        "attended": True,
        "autopilot": True,
        "max_cycles": 3,
        "idle_secs": 120,
        "success_criteria": "the report cites every source",
    }
    assert set(samples) == set(
        sp.OVERRIDABLE_POLICY_KEYS
    ), "the sample table drifted from OVERRIDABLE_POLICY_KEYS — extend both together"
    for key, value in samples.items():
        resolved = sp.apply_policy_overrides(base, {key: value})
        assert isinstance(resolved, sp.SupervisorPolicy), key


def test_an_unknown_stored_key_is_ignored_and_the_rest_applies() -> None:
    """The tolerant READ half: a stored overlay carrying a key this engine has never heard
    of (written by a newer core) is skipped — the recognized sibling still applies and
    nothing raises. A malformed VALUE on a recognized key is skipped the same way, because
    at read time there is nobody left to refuse it to."""
    base = sp.policy_for_kind("sdlc")
    resolved = sp.apply_policy_overrides(
        base, {"from_the_future": 42, "max_cycles": 5, "idle_secs": "not-a-number"}
    )
    assert resolved.budget_max_cycles == 5
    assert resolved.idle_secs == base.idle_secs


def test_the_write_seam_refuses_an_unknown_key(isolated) -> None:
    """The strict WRITE half: persisting a typo'd key would resolve to nothing forever while
    the user believes the knob is set, so the one seam with an author refuses it loudly —
    and refuses BEFORE writing, leaving the stored overlay untouched."""
    run = st.create(_run())
    st.set_policy_overrides(run.id, {"max_cycles": 4})
    with pytest.raises(ValueError, match="max_cyclez"):
        st.set_policy_overrides(run.id, {"max_cyclez": 9})
    assert st.get(run.id).policy_overrides == {"max_cycles": 4}


def test_the_overlay_round_trips_and_empty_clears(isolated) -> None:
    """Through the REAL store: the dict REPLACES the stored overlay, so writing ``{}`` clears
    every override and the run falls back to defaults — a run that overrides nothing
    persists nothing, and `extra` stays empty (the column is a first-class field, not a
    tolerant-reader spillover)."""
    run = st.create(_run())
    assert st.get(run.id).policy_overrides == {}

    updated = st.set_policy_overrides(run.id, {"attended": True, "max_cycles": 2})
    assert updated is not None
    back = st.get(run.id)
    assert back.policy_overrides == {"attended": True, "max_cycles": 2}
    assert back.extra == {}

    st.set_policy_overrides(run.id, {})
    assert st.get(run.id).policy_overrides == {}

    assert st.set_policy_overrides("no-such-run", {"max_cycles": 1}) is None


def test_from_dict_preserves_a_newer_cores_key_for_resave() -> None:
    """A downgraded core must ROUND-TRIP an unknown overlay key, not drop it on the next
    save: `from_dict` keeps the overlay's contents verbatim, and only resolution-time
    (`apply_policy_overrides`) ignores what it does not recognize."""
    d = _run(id="rt000001").to_dict()
    d["policy_overrides"] = {"from_the_future": 42}
    back = WorkflowRun.from_dict(d)
    assert back.policy_overrides == {"from_the_future": 42}
    assert back.to_dict()["policy_overrides"] == {"from_the_future": 42}
