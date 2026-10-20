"""The four config knobs, and the wiring that keeps them from being inert.

There are four `WorkflowsConfig` fields and four points each must pass through: dataclass +
`_meta`, `AppConfig.load()` mapping, `to_dict()`, and the PATCH allowlist. All four are wired here.

**The part that actually matters is the fifth point nobody writes down.** `materialize`,
`confirmation` and `pool` each carry their own module constant, so a field can be set, persisted,
echoed by `to_dict` and rendered in Settings while the runtime goes on using 20 / 7 days / 900s.
That is the present-and-inert control this codebase keeps finding, and it is what these tests pin:
knob is read through `workflows.settings`, and the call sites are asserted to resolve from config.

**`workflows.match_threshold` returns only with a live reader.** `WorkflowsConfig`'s own docstring
records that it was DELETED with the old SOP feature under the namespace-reuse clean break, and a
knob nothing reads would be the exact defect this file exists to prevent.
"""

import dataclasses as dc
import json
import tempfile
import unittest.mock
from pathlib import Path

import pytest
from table_rows import row

from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import AppConfig, WorkflowsConfig
from personalclaw.workflows import settings as wf_settings

FIELDS = (
    "surface_mode_default",
    "max_materialized_per_foreach",
    "confirmation_ttl_secs",
    "lease_ttl_secs",
)


def _load(workflows: dict) -> AppConfig:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        json.dump({"workflows": workflows}, handle)
        tmp = Path(handle.name)
    try:
        with unittest.mock.patch("personalclaw.config.loader.config_path", return_value=tmp):
            return AppConfig.load()
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".json.bak").unlink(missing_ok=True)


# ── point (a): the dataclass, with meta ──


@pytest.mark.parametrize("name", FIELDS)
def test_the_field_EXISTS_on_the_dataclass(name):
    assert name in {f.name for f in dc.fields(WorkflowsConfig)}


@pytest.mark.parametrize("name", FIELDS)
def test_the_field_carries_a_LABEL_and_HELP(name):
    """A field with no meta renders in Settings as a bare key name, which is how a knob ships that
    nobody can tell what it does."""
    field = next(f for f in dc.fields(WorkflowsConfig) if f.name == name)
    assert field.metadata.get("label")
    assert field.metadata.get("help")


def test_match_threshold_RETURNS_with_a_live_reader():
    """It was deleted with the old SOP feature (a knob nothing read), and it is back now
    with a real owner: the tiered matcher's T4 embedding tie-break reads it as
    the cosine floor. A live reader is what makes the field legitimate this time — a knob nothing
    consumes is the inert control this codebase keeps deleting."""
    assert "match_threshold" in {f.name for f in dc.fields(WorkflowsConfig)}
    assert WorkflowsConfig().match_threshold == 0.62


def test_match_threshold_is_config_editable_and_clamped():
    """Wired through all four config points: dataclass default, load() mapping (clamped to [0,1]),
    to_dict (asdict), and the PATCH allowlist."""
    assert "workflows.match_threshold" in _EDITABLE_CONFIG
    assert _load({"match_threshold": 0.4}).workflows.match_threshold == 0.4
    # Out of range is clamped, not stored raw — a stored value the runtime silently narrows reads
    # back wrong.
    assert _load({"match_threshold": 5.0}).workflows.match_threshold == 1.0


# ── point (b): load() maps it ──


def test_a_configured_value_SURVIVES_load():
    cfg = _load({"lease_ttl_secs": 60, "max_materialized_per_foreach": 3})
    assert cfg.workflows.lease_ttl_secs == 60
    assert cfg.workflows.max_materialized_per_foreach == 3


def test_the_new_def_default_is_OFF():
    """A def that triggers by default fires on pasted content, which holds every trigger phrase
    anyone wrote. The default is the one that surfaces nothing."""
    assert _load({}).workflows.surface_mode_default == "off"


def test_surface_mode_default_is_case_insensitive():
    assert _load({"surface_mode_default": "SUGGEST"}).workflows.surface_mode_default == "suggest"


def test_an_UNKNOWN_surface_mode_reads_as_off():
    """One tolerance rule, shared with `DefMetadata.from_dict`: a typo must not silently start
    surfacing every newly authored def, which is the direction that spends tokens."""
    assert _load({"surface_mode_default": "vibes"}).workflows.surface_mode_default == "off"


def test_a_NON_NUMERIC_int_falls_back_to_the_default():
    """Config is hand-edited JSON; a string here is a typo, not a reason to fail loading."""
    assert _load({"lease_ttl_secs": "soon"}).workflows.lease_ttl_secs == 900


def test_the_approval_lifetime_defaults_to_a_WEEK():
    """The realistic case is a user who is away; a gate expiring overnight turns travel into lost
    work."""
    assert _load({}).workflows.confirmation_ttl_secs == 7 * 24 * 3600


# ── point (c): to_dict ──


@pytest.mark.parametrize("name", FIELDS)
def test_the_field_ROUND_TRIPS_through_to_dict(name):
    """A field that serializes to nothing cannot survive a save."""
    assert name in _load({}).to_dict()["workflows"]


# ── point (d): the PATCH allowlist ──


@pytest.mark.parametrize("name", FIELDS)
def test_the_field_is_PATCHABLE(name):
    assert f"workflows.{name}" in _EDITABLE_CONFIG


def test_the_surfacing_default_is_an_ENUM_not_free_text():
    """A free-text PATCH would store `passiv` and the runtime would read `off` — a stored value that
    does not match the behaviour, which is worse than a rejection because the user reads the stored
    one."""
    spec = _EDITABLE_CONFIG["workflows.surface_mode_default"]
    assert spec["type"] == "enum"
    assert spec["values"] == ["off", "passive", "suggest"]


def test_the_lease_bound_matches_the_RECORD_s_ceiling():
    """Accepting a larger number would store a week-long lease the runtime silently shortens."""
    from personalclaw.workflows.pool import MAX_LEASE_SECS

    assert _EDITABLE_CONFIG["workflows.lease_ttl_secs"]["max"] == MAX_LEASE_SECS


def test_the_approval_lifetime_allows_ZERO():
    """`ttl: 0` means "wait for me" — `ConfirmationRequest.expires_at` reads `<= 0` as no expiry, so
    refusing 0 here would make an intent the record supports unreachable through the API."""
    assert _EDITABLE_CONFIG["workflows.confirmation_ttl_secs"]["min"] == 0


def test_the_fanout_cap_cannot_be_set_to_ZERO():
    """A cap of 0 would materialize nothing, silently turning the board off for every run. An owner
    who wants no rows sets `materialize_task: false` on the node, which says so."""
    assert _EDITABLE_CONFIG["workflows.max_materialized_per_foreach"]["min"] >= 1


# ── the fifth point: the knobs are actually READ ──


@pytest.fixture
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    return tmp_path


def _write_config(home: Path, workflows: dict) -> None:
    (home / "config.json").write_text(json.dumps({"workflows": workflows}), encoding="utf-8")


def test_the_fanout_cap_is_HONOURED_by_materialization(_home):
    """The whole point. Before this, the config was storable and the runtime used 20."""
    from personalclaw.workflows import materialize

    _write_config(_home, {"max_materialized_per_foreach": 3})
    nodes = [
        {"id": f"n{i}", "kind": "action", "config": {}, "path": f"root.body[{i}]"} for i in range(6)
    ]
    plan = materialize.plan_materialization("r-1", nodes)
    assert len(plan.create) == 3
    assert plan.capped == 3
    assert plan.cap_note


def test_the_approval_lifetime_is_HONOURED_by_build_request(_home):
    from personalclaw.workflows.confirmation import build_request

    _write_config(_home, {"confirmation_ttl_secs": 60})
    assert build_request(run_id="r", gate_id="g", now=1000.0).ttl_seconds == 60


def test_the_lease_lifetime_is_HONOURED_by_claim_task(_home):
    from personalclaw.workflows import pool

    _write_config(_home, {"lease_ttl_secs": 45})
    lease, error = pool.claim_task("t-1", holder="a", now=1000.0)
    assert error == ""
    assert lease.ttl_seconds == 45


def test_an_EXPLICIT_argument_still_wins_over_config(_home):
    """The config is the DEFAULT, not an override. A caller that names a ttl means it — a template
    declaring its own gate lifetime must not be silently rewritten by a global preference."""
    from personalclaw.workflows.confirmation import build_request

    _write_config(_home, {"confirmation_ttl_secs": 60})
    assert build_request(run_id="r", gate_id="g", now=0.0, ttl_seconds=999).ttl_seconds == 999


# ── the resolvers degrade safely ──


def test_an_UNREADABLE_config_falls_back_to_the_shipped_constants(_home):
    """A malformed `config.json` must not stop a run from materializing its tasks. Each getter
    degrades to the module constant, which is the value that shipped and is known good."""
    (_home / "config.json").write_text("{not json", encoding="utf-8")
    from personalclaw.workflows.confirmation import DEFAULT_TTL_SECS
    from personalclaw.workflows.materialize import FANOUT_TASK_CAP
    from personalclaw.workflows.pool import DEFAULT_LEASE_SECS

    assert wf_settings.fanout_task_cap() == FANOUT_TASK_CAP
    assert wf_settings.confirmation_ttl_secs() == DEFAULT_TTL_SECS
    assert wf_settings.lease_ttl_secs() == DEFAULT_LEASE_SECS
    assert wf_settings.surface_mode_default() == "off"


def test_an_over_ceiling_lease_is_CLAMPED_not_returned_raw(_home):
    """A getter returning 86400 while the lease expires in 3600 is a lie a debugger would chase."""
    from personalclaw.workflows.pool import MAX_LEASE_SECS

    _write_config(_home, {"lease_ttl_secs": 999_999})
    assert wf_settings.lease_ttl_secs() == MAX_LEASE_SECS


def test_a_zero_or_negative_fanout_cap_falls_back(_home):
    _write_config(_home, {"max_materialized_per_foreach": 0})
    from personalclaw.workflows.materialize import FANOUT_TASK_CAP

    assert wf_settings.fanout_task_cap() == FANOUT_TASK_CAP


def test_a_NEGATIVE_ttl_normalizes_to_zero(_home):
    """`expires_at` already reads `<= 0` as no expiry; letting a negative through would make two
    spellings of one intent look like different stored values."""
    _write_config(_home, {"confirmation_ttl_secs": -5})
    assert wf_settings.confirmation_ttl_secs() == 0


def test_the_resolvers_are_NOT_cached(_home):
    """These are in the live-editable PATCH set. A cached read would keep applying the old number
    until the gateway restarted, which is the difference between live-editable and restart-required.
    """
    _write_config(_home, {"lease_ttl_secs": 60})
    assert wf_settings.lease_ttl_secs() == 60
    _write_config(_home, {"lease_ttl_secs": 120})
    assert wf_settings.lease_ttl_secs() == 120


# ── the call sites go through the resolver, not the constant ──
#
# A call site that reads its module constant, or reads the stored field itself, or reads the config
# once and keeps it, is the drift `settings` exists to prevent. Each table below calls one site
# over the inputs that tell those apart: a value the owner sets and then a second one (a constant,
# or a value read once, misses one of them), a value only the resolver turns into another (a call
# site that read the stored field itself keeps it raw), no value set, a config that does not
# parse, and a value the caller names, which still wins over the config.


def _set_config(home: Path, workflows: dict | str) -> None:
    """The test home's config: the `workflows` section *workflows*, or *workflows* as the whole
    file's text when it is a string."""
    if isinstance(workflows, str):
        (home / "config.json").write_text(workflows, encoding="utf-8")
    else:
        _write_config(home, workflows)


def test_materialization_resolves_the_cap_from_SETTINGS(_home, subtests):
    """`plan_materialization`, given no cap, takes the one `settings.fanout_task_cap` answers at
    that call. Thirty nodes, so the shipped cap of 20 shows as well as a smaller one, and the cap
    note names the cap that applied. A cap of 0 is the resolver's to refuse: a call site that read
    the stored field itself would make one task there, not twenty."""
    from personalclaw.workflows import materialize

    assert materialize.FANOUT_TASK_CAP == 20
    nodes = [
        {"id": f"n{i}", "kind": "action", "config": {}, "path": f"root.body[{i}]"}
        for i in range(30)
    ]
    # case, the config's `workflows` section (or the file's text), the cap the caller names,
    # the cap that applies
    table = [
        ("a cap you set", {"max_materialized_per_foreach": 4}, None, 4),
        ("the next cap you set", {"max_materialized_per_foreach": 9}, None, 9),
        ("a cap of 0", {"max_materialized_per_foreach": 0}, None, 20),
        ("no cap set", {}, None, 20),
        ("a config that does not parse", "{not json", None, 20),
        ("a cap the caller names", {"max_materialized_per_foreach": 4}, 2, 2),
    ]
    assert len(table) == 6
    for case, workflows, named, cap in table:
        with row(subtests, case=case):
            _set_config(_home, workflows)
            plan = (
                materialize.plan_materialization("r-1", nodes)
                if named is None
                else materialize.plan_materialization("r-1", nodes, cap=named)
            )
            made = [spec.binding.node_id for spec in plan.create]
            assert made == [f"n{i}" for i in range(cap)], f"{case}: made tasks for {made}"
            assert plan.capped == 30 - cap, f"{case}: {plan.capped} capped"
            assert plan.cap_note.startswith(
                f"{cap} of 30 items materialized (cap {cap});"
            ), f"{case}: the cap note reads {plan.cap_note!r}"


def test_build_request_resolves_the_ttl_from_SETTINGS(_home, subtests):
    """`build_request`, given no lifetime, takes the one `settings.confirmation_ttl_secs` answers at
    that call, and the request lapses by it. 0 is "wait for me": a request that never lapses, from
    the config or named by the caller, and not a reason to fall back to a week. A negative
    lifetime is the resolver's to turn into 0: a call site that read the stored field itself
    would carry -5."""
    from personalclaw.workflows.confirmation import DEFAULT_TTL_SECS, build_request

    week = 7 * 24 * 3600
    assert DEFAULT_TTL_SECS == week
    # case, the config's `workflows` section (or the file's text), the lifetime the caller names,
    # the lifetime the request carries
    table = [
        ("a lifetime you set", {"confirmation_ttl_secs": 90}, None, 90),
        ("the next lifetime you set", {"confirmation_ttl_secs": 300}, None, 300),
        ("wait for me", {"confirmation_ttl_secs": 0}, None, 0),
        ("a negative lifetime", {"confirmation_ttl_secs": -5}, None, 0),
        ("no lifetime set", {}, None, week),
        ("a config that does not parse", "{not json", None, week),
        ("a lifetime the caller names", {"confirmation_ttl_secs": 90}, 600, 600),
        ("wait for me, named by the caller", {"confirmation_ttl_secs": 90}, 0, 0),
    ]
    assert len(table) == 8
    for case, workflows, named, ttl in table:
        with row(subtests, case=case):
            _set_config(_home, workflows)
            if named is None:
                request = build_request(run_id="r", gate_id="g", now=1000.0)
            else:
                request = build_request(run_id="r", gate_id="g", now=1000.0, ttl_seconds=named)
            assert request.ttl_seconds == ttl, f"{case}: the request lives {request.ttl_seconds}s"
            if ttl:
                assert request.expires_at() == 1000.0 + ttl, f"{case}: {request.expires_at()}"
                assert not request.expired(1000.0 + ttl - 1), f"{case}: lapsed early"
                assert request.expired(1000.0 + ttl), f"{case}: did not lapse"
            else:
                assert request.expires_at() == 0.0, f"{case}: lapses at {request.expires_at()}"
                assert not request.expired(1000.0 + 10 * week), f"{case}: lapsed"


def test_claim_task_resolves_the_ttl_from_SETTINGS(_home, subtests):
    """`claim_task`, given no lifetime, takes the one `settings.lease_ttl_secs` answers at that
    call. The lease it writes holds the task for exactly that long: another holder is refused one
    second before and takes it at the end. Past the ceiling and at 0 are the resolver's to clamp
    (to an hour) and refuse (to the shipped 15 minutes): a call site that read the stored field
    itself would carry 999999 while the lease ends at an hour, or end the lease after a second."""
    from personalclaw.workflows import pool

    assert (pool.DEFAULT_LEASE_SECS, pool.MAX_LEASE_SECS) == (900, 3600)
    # case, the config's `workflows` section (or the file's text), the lifetime the caller names,
    # the lifetime the lease carries
    table = [
        ("a lease you set", {"lease_ttl_secs": 300}, None, 300),
        ("the next lease you set", {"lease_ttl_secs": 600}, None, 600),
        ("a lease past the ceiling", {"lease_ttl_secs": 999_999}, None, 3600),
        ("a lease of 0", {"lease_ttl_secs": 0}, None, 900),
        ("no lease set", {}, None, 900),
        ("a config that does not parse", "{not json", None, 900),
        ("a lease the caller names", {"lease_ttl_secs": 300}, 120, 120),
    ]
    assert len(table) == 7
    for n, (case, workflows, named, ttl) in enumerate(table):
        with row(subtests, case=case):
            _set_config(_home, workflows)
            task = f"t-{n}"
            if named is None:
                lease, error = pool.claim_task(task, holder="a", now=1000.0)
            else:
                lease, error = pool.claim_task(task, holder="a", now=1000.0, ttl_seconds=named)
            assert error == "", f"{case}: {error}"
            assert lease is not None
            assert (lease.ttl_seconds, lease.expires_at()) == (ttl, 1000.0 + ttl), case
            stored = pool.read_lease(task)
            assert stored is not None, f"{case}: no lease on disk"
            assert (stored.holder, stored.ttl_seconds) == ("a", ttl), f"{case}: stored {stored}"
            refused, why = pool.claim_task(task, holder="b", now=1000.0 + ttl - 1)
            assert (refused, why) == (None, "held_by_other"), f"{case}: taken early"
            taken, why = pool.claim_task(task, holder="b", now=1000.0 + ttl)
            assert taken is not None and taken.holder == "b", f"{case}: not taken at the end: {why}"
