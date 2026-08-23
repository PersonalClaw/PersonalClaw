"""EVALUATION-SUBSTRATE ES-7 §3.2 — the model-upgrade watchdog.

The done_when clause has four parts and each gets its own rail:

* **computes a model fingerprint on ``active_models.json`` changes** — and the FILE's mtime
  is only a hint: the fingerprint is the truth, so a rewrite that changed no head model is
  not a rebind (``test_a_rewrite_that_changed_no_head_model_is_not_a_rebind``).
* **queues small-budget re-benchmarks** — capped independently of
  ``evals.default_budget_usd``, because that default may legitimately be 0 = uncapped and
  work the user never asked for must still be bounded.
* **emits exactly ONE digest notification** — the rail that matters: three rebound bindings
  and N queued items produce exactly one call, and the vacuity floor is that a no-change
  tick produces zero.
* **per-fingerprint ``results.tsv`` baselines** — a group-by over the ledger's existing
  ``model_fp`` column, with an unscored (fingerprint, scenario) reporting ``None`` rather
  than 0.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from personalclaw.evals import model_watchdog as watchdog
from personalclaw.evals import store
from personalclaw.evals.pinning import RunPin

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def watch_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # `load_active_models` PRUNES refs whose provider is not configured, so a fingerprint
    # test without these providers would read {} and pass vacuously.
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": n} for n in ("A", "B", "C", "Anthropic")]}) + "\n",
        encoding="utf-8",
    )
    return home


def _bind(home, mapping: dict[str, list[str]]) -> None:
    (home / "active_models.json").write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")


class _Notifier:
    """A DashboardState.notify-shaped callable that counts its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, kind, title, body, **kwargs):
        self.calls.append((kind, title, body))


# ── the watched vocabulary (a plan/code drift, made visible) ──────────────────


def test_the_watchdog_only_watches_bindings_that_can_exist():
    """§3.2 names four bindings; ``eval_judge`` is not one ``active_models.json`` can hold.

    ``eval_judge`` is a PROMPT use case consumed by ``eval/judge.py``'s
    ``factory("eval_judge")``, not a member of ``providers.use_cases.VALID_USE_CASES`` — so a
    watchdog "watching" it would be a control that reports no change forever.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES

    assert "eval_judge" in watchdog.PLAN_WATCHED_USE_CASES
    assert "eval_judge" not in VALID_USE_CASES
    assert watchdog.WATCHED_USE_CASES == ("chat", "reasoning", "background")
    assert all(uc in VALID_USE_CASES for uc in watchdog.WATCHED_USE_CASES)


# ── the fingerprint ───────────────────────────────────────────────────────────


def test_the_fingerprint_matches_the_ledgers_own_model_fp_column(watch_home):
    _bind(watch_home, {"chat": ["Anthropic:claude-a"], "reasoning": ["Anthropic:claude-b"]})
    fingerprint, digest = watchdog.fingerprint_now()
    assert fingerprint == {"chat": "Anthropic:claude-a", "reasoning": "Anthropic:claude-b"}
    # Computed through the pin, NOT by hashing the file here: a second digest of the same
    # facts would compare unequal to every results.tsv row it exists to match.
    expected = RunPin(
        scenario_id="s", scenario_sha256="h", model_fingerprint=fingerprint
    ).model_fp()
    assert digest == expected


def test_changed_bindings_reports_gains_losses_and_moves(watch_home):
    changes = watchdog.changed_bindings(
        {"chat": "A:1", "reasoning": "A:2", "background": "A:3"},
        {"chat": "A:1", "reasoning": "A:9", "background": ""},
    )
    assert [(c.use_case, c.before, c.after) for c in changes] == [
        ("background", "A:3", ""),
        ("reasoning", "A:2", "A:9"),
    ]
    # An unwatched use case moving is not a rebind for this purpose.
    assert watchdog.changed_bindings({"stt": "A:1"}, {"stt": "A:2"}) == []


# ── the first observation is a baseline, not an upgrade ───────────────────────


def test_the_first_observation_records_a_baseline_without_notifying(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    notifier = _Notifier()
    result = watchdog.check(now=NOW, notifier=notifier)
    assert result.changed is False and result.reason == "baseline_recorded"
    assert notifier.calls == [], "a fresh install must not be greeted with a rebind digest"
    assert watchdog.load_state()["model_fp"] == result.model_fp
    assert watchdog.load_queue() == []


# ── exactly ONE digest ────────────────────────────────────────────────────────


def test_a_rebind_of_three_bindings_emits_exactly_one_digest(watch_home):
    _bind(watch_home, {"chat": ["A:1"], "reasoning": ["A:2"], "background": ["A:3"]})
    notifier = _Notifier()
    watchdog.check(now=NOW, notifier=notifier)  # baseline
    assert notifier.calls == []

    _bind(watch_home, {"chat": ["B:1"], "reasoning": ["B:2"], "background": ["B:3"]})
    result = watchdog.check(now=NOW, notifier=notifier)

    assert result.changed is True
    assert len(result.changes) == 3, "three bindings moved"
    assert len(result.queued) >= 1, "and at least one re-benchmark was queued"
    # THE CLAUSE: one event, one notification. Never N.
    assert len(notifier.calls) == 1
    assert result.notifications == 1
    kind, title, body = notifier.calls[0]
    assert kind == watchdog.NOTIFY_KIND
    # Everything the user learns is in that ONE body, so all three moves must be in it.
    for use_case in ("chat", "reasoning", "background"):
        assert use_case in body
    assert "re-benchmark" in title


def test_a_tick_with_no_change_notifies_nothing(watch_home):
    """THE VACUITY FLOOR for "exactly one": a rail that always fires once is not a rail."""
    _bind(watch_home, {"chat": ["A:1"]})
    notifier = _Notifier()
    watchdog.check(now=NOW, notifier=notifier)  # baseline
    result = watchdog.check(now=NOW, notifier=notifier)
    assert result.changed is False and result.reason == "no_change"
    assert notifier.calls == []
    assert watchdog.load_queue() == [], "and nothing queued"


def test_a_rebind_is_reported_once_not_on_every_later_tick(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    notifier = _Notifier()
    watchdog.check(now=NOW, notifier=notifier)
    _bind(watch_home, {"chat": ["B:1"]})
    watchdog.check(now=NOW, notifier=notifier)
    watchdog.check(now=NOW, notifier=notifier)
    watchdog.check(now=NOW, notifier=notifier)
    assert len(notifier.calls) == 1, "the state must absorb the rebind, not re-announce it"


def test_a_rewrite_that_changed_no_head_model_is_not_a_rebind(watch_home):
    _bind(watch_home, {"chat": ["A:1", "A:fallback"]})
    notifier = _Notifier()
    watchdog.check(now=NOW, notifier=notifier)
    # Only the FALLBACK moved; the resolved head is unchanged, so no evidence expired.
    _bind(watch_home, {"chat": ["A:1", "A:other-fallback"]})
    result = watchdog.check(now=NOW, notifier=notifier)
    assert result.changed is False
    assert result.reason == "file_touched_no_rebind", "the mtime moved but the fingerprint did not"
    assert notifier.calls == []


def test_a_headless_tick_still_queues(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    watchdog.check(now=NOW, notifier=None)
    _bind(watch_home, {"chat": ["B:1"]})
    result = watchdog.check(now=NOW, notifier=None)
    assert result.changed is True and result.notifications == 0
    assert len(watchdog.load_queue()) == len(result.queued) >= 1


def test_a_raising_notifier_does_not_lose_the_queue(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    watchdog.check(now=NOW, notifier=None)
    _bind(watch_home, {"chat": ["B:1"]})

    def _broken(kind, title, body, **kwargs):
        raise RuntimeError("no dashboard")

    result = watchdog.check(now=NOW, notifier=_broken)
    assert result.changed is True and result.notifications == 0
    assert watchdog.load_queue(), "the queue is the durable half; the digest is best-effort"


# ── the small-budget queue ────────────────────────────────────────────────────


def test_queued_rebenchmarks_are_small_budget_and_capped_independently(watch_home, monkeypatch):
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.models import WorkflowRun

    for index in range(3):
        wf_store.create(WorkflowRun(id=f"r{index}", workflow_name="triage"))
    wf_store.create(WorkflowRun(id="r9", workflow_name="digest"))

    _bind(watch_home, {"chat": ["A:1"]})
    watchdog.check(now=NOW, notifier=None)
    _bind(watch_home, {"chat": ["B:1"]})
    result = watchdog.check(now=NOW, notifier=None, top_n=2)

    kinds = [e["kind"] for e in result.queued]
    assert kinds[0] == "judge", "the judge fixtures are queued on any watched rebind"
    assert [e["subject"] for e in result.queued[1:]] == ["triage", "digest"]
    for entry in result.queued:
        assert entry["trials"] == watchdog.SMALL_BUDGET_TRIALS == 1
        # NOT evals.default_budget_usd: that default may be 0 (uncapped), and automatic work
        # must be bounded even when the user's own runs are not.
        assert entry["budget_usd"] == watchdog.SMALL_BUDGET_USD > 0.0
        assert entry["model_fp"] == result.model_fp
        assert entry["status"] == "queued"


def test_the_queue_appends_rather_than_replacing(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    watchdog.check(now=NOW, notifier=None)
    _bind(watch_home, {"chat": ["B:1"]})
    first = watchdog.check(now=NOW, notifier=None)
    _bind(watch_home, {"chat": ["C:1"]})
    second = watchdog.check(now=NOW, notifier=None)
    assert len(watchdog.load_queue()) == len(first.queued) + len(second.queued)


# ── per-fingerprint baselines ─────────────────────────────────────────────────


def test_baselines_are_grouped_by_fingerprint(watch_home):
    old = RunPin(
        scenario_id="triage",
        scenario_sha256="h1",
        model_fingerprint={"chat": "A:1"},
        prompt_pack_sha256="p",
        config_snapshot_ref="c",
    )
    new = RunPin(
        scenario_id="triage",
        scenario_sha256="h1",
        model_fingerprint={"chat": "B:1"},
        prompt_pack_sha256="p",
        config_snapshot_ref="c",
    )
    store.append_result({"study_id": "m1", "kind": "matrix", "score_new": 0.8, "ts": "t1"}, pin=old)
    store.append_result({"study_id": "m2", "kind": "matrix", "score_new": 0.6, "ts": "t2"}, pin=old)
    store.append_result({"study_id": "m3", "kind": "matrix", "score_new": 0.9, "ts": "t3"}, pin=new)

    baselines = watchdog.baselines_by_fingerprint()
    assert set(baselines) == {old.model_fp(), new.model_fp()}
    before = baselines[old.model_fp()]["triage"]
    assert before["mean"] == pytest.approx(0.7) and before["n"] == 2
    assert before["latest_ts"] == "t2"
    assert baselines[new.model_fp()]["triage"]["mean"] == pytest.approx(0.9)


def test_an_unscored_baseline_is_none_not_zero(watch_home):
    pin = RunPin(
        scenario_id="triage",
        scenario_sha256="h1",
        model_fingerprint={"chat": "A:1"},
        prompt_pack_sha256="p",
        config_snapshot_ref="c",
    )
    store.append_result(
        {"study_id": "m1", "kind": "matrix", "verdict": "verifier_absent", "ts": "t1"}, pin=pin
    )
    bucket = watchdog.baselines_by_fingerprint()[pin.model_fp()]["triage"]
    # The §1.2 rule applied to the ledger read: an absent measurement is never a zero.
    assert bucket["mean"] is None and bucket["n"] == 1


def test_the_digest_reports_the_previous_fingerprints_baseline_count(watch_home):
    _bind(watch_home, {"chat": ["A:1"]})
    watchdog.check(now=NOW, notifier=None)
    before_fp = watchdog.load_state()["model_fp"]
    pin = RunPin(
        scenario_id="triage",
        scenario_sha256="h1",
        model_fingerprint={"chat": "A:1"},
        prompt_pack_sha256="p",
        config_snapshot_ref="c",
    )
    assert pin.model_fp() == before_fp, "the pin and the watchdog must agree on the digest"
    store.append_result({"study_id": "m1", "kind": "matrix", "score_new": 0.8, "ts": "t"}, pin=pin)

    _bind(watch_home, {"chat": ["B:1"]})
    notifier = _Notifier()
    result = watchdog.check(now=NOW, notifier=notifier)
    assert result.baseline_scenarios == 1
    assert "1 scenario baseline(s)" in notifier.calls[0][2]
