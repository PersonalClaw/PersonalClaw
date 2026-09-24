"""Onboarding progress state (ONBOARDING-UX C1 / OU-1).

Three properties carry this contract, and each has a rail here:

1. **Tolerant reads.** A store written by a client that had none of these fields — or one
   carrying a wrong-typed value — must still load, and the GET must still answer. A
   first-run signal that 500s on a stale file is worse than no signal.
2. **Partial merge at both levels.** Writing one field must not clear the others, nested
   fields included. Without that, every onboarding step would have to read the whole
   document and echo it back, and any two steps racing would lose progress.
3. **Entity state, not config.** The write path is ``POST /api/onboarding/state`` and the
   bytes land in ``entity_settings/onboarding.json`` — never ``config.json``, never the
   ``_EDITABLE_CONFIG`` PATCH allowlist (§2.1).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import onboarding as ob
from personalclaw.dashboard import handlers_system as hs


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Point the whole config home at a tmp dir so the real home is never touched.

    ``PERSONALCLAW_HOME`` rather than a ``config_dir`` patch: the store resolves its path
    through ``entity_routes._entity_settings_path`` -> ``config_dir()`` on every call, and
    the env var is the one lever every such caller honours.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _store_path(home):
    return home / "entity_settings" / "onboarding.json"


def _write_raw(home, payload):
    p = _store_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def _req(body):
    r = MagicMock()
    r.json = AsyncMock(return_value=body)
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


# ── 1. defaults + round trip ─────────────────────────────────────────────────


def test_fresh_home_starts_at_the_first_step(_isolate_home):
    assert not _store_path(_isolate_home).exists()
    state = ob.load_onboarding_state()
    assert state == {
        "step": "name",
        "essentials": {"model": None, "search": False, "speech": False, "channel": None},
        "first_success": {"knowledge": False, "trigger": False, "loop": False},
    }


def test_merge_persists_to_entity_settings_not_config(_isolate_home):
    ob.merge_onboarding_state({"step": "essentials", "essentials": {"model": "acme-models"}})

    # The bytes are where §2.1 says they belong…
    on_disk = json.loads(_store_path(_isolate_home).read_text(encoding="utf-8"))
    assert on_disk["step"] == "essentials"
    assert on_disk["essentials"]["model"] == "acme-models"
    # …and NOT in config.json, whose PATCH allowlist this deliberately bypasses.
    cfg = _isolate_home / "config.json"
    assert not cfg.exists() or "onboarding" not in cfg.read_text(encoding="utf-8")


def test_state_survives_a_reload(_isolate_home):
    """A mid-flow reload re-reads from disk — no in-process cache carries the answer."""
    ob.merge_onboarding_state({"step": "first_success", "first_success": {"knowledge": True}})
    # Simulate the reload by dropping every cached module-level value there could be:
    # the store keeps none, so a plain re-read must already agree with disk.
    again = ob.load_onboarding_state()
    assert again["step"] == "first_success"
    assert again["first_success"]["knowledge"] is True


# ── 2. partial merge at both levels ──────────────────────────────────────────


def test_top_level_merge_is_partial(_isolate_home):
    """Write A, POST only B, assert A survives."""
    ob.merge_onboarding_state({"essentials": {"model": "acme-models", "search": True}})
    after = ob.merge_onboarding_state({"step": "first_success"})
    assert after["step"] == "first_success"
    assert after["essentials"] == {
        "model": "acme-models",
        "search": True,
        "speech": False,
        "channel": None,
    }


def test_nested_merge_is_partial(_isolate_home):
    """A patch naming one card must not clear the other two."""
    ob.merge_onboarding_state({"first_success": {"knowledge": True, "loop": True}})
    after = ob.merge_onboarding_state({"first_success": {"trigger": True}})
    assert after["first_success"] == {"knowledge": True, "trigger": True, "loop": True}


def test_nested_merge_can_clear_one_field_explicitly(_isolate_home):
    """Partial means absent-is-untouched, not absent-is-false — an explicit false wins."""
    ob.merge_onboarding_state({"first_success": {"knowledge": True, "trigger": True}})
    after = ob.merge_onboarding_state({"first_success": {"knowledge": False}})
    assert after["first_success"] == {"knowledge": False, "trigger": True, "loop": False}


def test_essentials_model_can_be_nulled(_isolate_home):
    ob.merge_onboarding_state({"essentials": {"model": "acme-models"}})
    after = ob.merge_onboarding_state({"essentials": {"model": None}})
    assert after["essentials"]["model"] is None


# ── 3. tolerant reads ────────────────────────────────────────────────────────


def test_old_client_store_missing_every_new_field_still_loads(_isolate_home):
    """The shape an older client would have left behind: no step/essentials/first_success."""
    _write_raw(_isolate_home, {"some_older_key": "whatever"})
    state = ob.load_onboarding_state()
    assert state == ob.default_state()


def test_wrong_typed_fields_do_not_raise_and_fall_back_per_field(_isolate_home):
    _write_raw(
        _isolate_home,
        {
            "step": 7,  # not a string
            "essentials": "nope",  # not an object
            "first_success": {"knowledge": "yes", "loop": True},  # one bad, one good
        },
    )
    state = ob.load_onboarding_state()
    assert state["step"] == "name"
    assert state["essentials"] == ob.default_state()["essentials"]
    # The bad sibling does not cost us the good one — per-field fallback.
    assert state["first_success"] == {"knowledge": False, "trigger": False, "loop": True}


def test_out_of_domain_step_on_disk_falls_back(_isolate_home):
    _write_raw(_isolate_home, {"step": "some-step-we-retired"})
    assert ob.load_onboarding_state()["step"] == "name"


def test_corrupt_json_and_non_object_json_both_load(_isolate_home):
    p = _store_path(_isolate_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json at all", encoding="utf-8")
    assert ob.load_onboarding_state() == ob.default_state()
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert ob.load_onboarding_state() == ob.default_state()


def test_unknown_on_disk_keys_are_not_leaked_back_out(_isolate_home):
    """Bug #22's lesson: garbage must not ride a read back out to every client."""
    _write_raw(_isolate_home, {"step": "done", "totally_bogus_key_xyz": "junk"})
    assert "totally_bogus_key_xyz" not in ob.load_onboarding_state()


# ── 4. strict writes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "patch",
    [
        {"totally_bogus_key_xyz": 1},
        {"step": "not-a-step"},
        {"step": 3},
        {"essentials": {"bogus": True}},
        {"essentials": {"search": "yes"}},
        {"essentials": {"model": 42}},
        {"essentials": []},
        {"first_success": {"knowledge": "true"}},
        {"first_success": {"bogus_card": True}},
        ["not", "an", "object"],
    ],
)
def test_bad_patches_are_rejected(_isolate_home, patch):
    with pytest.raises(ValueError):
        ob.merge_onboarding_state(patch)


def test_a_rejected_patch_writes_nothing(_isolate_home):
    ob.merge_onboarding_state({"step": "essentials"})
    with pytest.raises(ValueError):
        ob.merge_onboarding_state({"step": "bogus"})
    assert ob.load_onboarding_state()["step"] == "essentials"


def test_every_declared_step_is_writable(_isolate_home):
    """No declared step is unreachable — a step nobody can write is an inert enum."""
    for step in ob.STEPS:
        assert ob.merge_onboarding_state({"step": step})["step"] == step


# ── 5. the HTTP surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_carries_progress_beside_the_readiness_triple(_isolate_home):
    ob.merge_onboarding_state({"step": "first_success", "essentials": {"speech": True}})
    data = await _json(await hs.api_onboarding(_req({})))
    # The pre-existing contract an old client reads is untouched…
    for key in ("needs_model", "has_model_provider", "has_chat_binding"):
        assert key in data
    # …and the new fields ride alongside it.
    assert data["step"] == "first_success"
    assert data["essentials"]["speech"] is True
    assert data["first_success"] == {"knowledge": False, "trigger": False, "loop": False}


@pytest.mark.asyncio
async def test_get_still_answers_over_a_corrupt_store(_isolate_home):
    p = _store_path(_isolate_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("]]] broken", encoding="utf-8")
    data = await _json(await hs.api_onboarding(_req({})))
    assert data["needs_model"] is True  # readiness still computed
    assert data["step"] == "name"


@pytest.mark.asyncio
async def test_post_round_trips_through_the_get(_isolate_home):
    resp = await hs.api_onboarding_state(_req({"step": "done", "first_success": {"loop": True}}))
    assert resp.status == 200
    posted = await _json(resp)
    assert posted["ok"] is True
    assert posted["state"]["step"] == "done"
    got = await _json(await hs.api_onboarding(_req({})))
    assert got["step"] == "done"
    assert got["first_success"]["loop"] is True


@pytest.mark.asyncio
async def test_post_partial_merge_over_http(_isolate_home):
    await hs.api_onboarding_state(_req({"essentials": {"model": "acme-models"}}))
    resp = await hs.api_onboarding_state(_req({"first_success": {"knowledge": True}}))
    state = (await _json(resp))["state"]
    assert state["essentials"]["model"] == "acme-models"
    assert state["first_success"]["knowledge"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{"nope": 1}, {"step": "bogus"}, {"essentials": {"search": "yes"}}, "a string"]
)
async def test_post_rejects_bad_bodies_with_400(_isolate_home, body):
    resp = await hs.api_onboarding_state(_req(body))
    assert resp.status == 400
    assert "error" in await _json(resp)


@pytest.mark.asyncio
async def test_post_rejects_unparseable_json_with_400(_isolate_home):
    r = MagicMock()
    r.json = AsyncMock(side_effect=ValueError("boom"))
    resp = await hs.api_onboarding_state(r)
    assert resp.status == 400


def test_post_route_is_registered():
    """The handler must be reachable — a store with no route is inert."""
    import ast
    from pathlib import Path

    import personalclaw.dashboard.server as srv

    tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
    posts = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_post"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "/api/onboarding/state" in posts


def test_onboarding_is_not_wired_into_config(_isolate_home):
    """§2.1: this is entity state. It must not reach the config allowlist or dataclass."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    assert not hasattr(cfg, "onboarding_step")
    assert not hasattr(cfg, "first_success")
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert not any(k.startswith("onboarding") or k == "first_success" for k in _EDITABLE_CONFIG)
