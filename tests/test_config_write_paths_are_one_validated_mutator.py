"""Every config write path validates through the SAME allowlist, and refuses out loud.

`_EDITABLE_CONFIG` + the PATCH handler were the only real validation in the tree: typed,
bounded, allowlisted, SEL-audited on every rejection. Three other paths wrote
`config.json` beside it, each with its own idea of what a valid value is:

* **`PUT /api/memory/settings`** coerced instead of validating. `bool("false")` is `True`,
  so a request to turn a memory behaviour OFF turned it ON; `push_min_confidence: 42` was
  clamped to `1.0` (the push reflex silently switched from "volunteer at 42% confidence" to
  "never volunteer") where PATCH would have returned 400 for the same field. It had no
  allowlist, so a typo'd key returned 200 having changed nothing, and no SEL row at all.
* **`PUT /api/config/personalclaw`** re-implemented the bounds of three `agent.*` fields
  `_EDITABLE_CONFIG` already declares — identical numbers, one edit from disagreeing — and
  dropped unknown keys in silence whenever one recognised key rode along.
* **`personalclaw config set`** checked only that the dotted key EXISTS in `to_dict()`,
  then wrote any value, so the CLI could store `agent.max_subagents 9999` past the 0..16
  the API enforces on the very same field.

The common defect is not "a missing check" — it is **a write that reports success having
done something other than what was asked**. Nothing downstream can notice that, which is
why these are asserted at the call site (real handlers through a real `TestClient`, the
real CLI entry point) and why the SEL row is part of each assertion: an unaudited silent
drop is indistinguishable from a working save.

Each behaviour test is paired with the case that must still WORK, because "validate
everything" is one `return 400` away from an endpoint that saves nothing at all.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class _RecordingSel:
    """A SEL double that keeps the rows instead of writing them."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_api_access(self, **kw) -> None:
        self.rows.append(kw)

    def __getattr__(self, name):  # any other SEL call is a no-op here
        return lambda *a, **k: None

    def outcomes(self, operation: str) -> list[str]:
        return [r.get("outcome") for r in self.rows if r.get("operation") == operation]


@pytest.fixture
def sel_rows(monkeypatch) -> _RecordingSel:
    rec = _RecordingSel()
    import personalclaw.dashboard.handlers as handlers_pkg

    monkeypatch.setattr(handlers_pkg, "sel", lambda: rec, raising=False)
    return rec


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """An isolated config.json. This test WRITES config — it must never see the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    # `cli_config` does `from personalclaw.config.loader import config_path` at MODULE
    # import, so patching the loader attribute alone leaves that binding pointing at the
    # real one. It agreed by accident here (both resolve through PERSONALCLAW_HOME) until
    # this file ran in an xdist worker alongside other config tests, where the CLI wrote
    # somewhere else entirely and the assertion read a default back. Both bindings are
    # patched, so which one a call site captured stops mattering.
    with (
        patch("personalclaw.config.loader.config_path", return_value=path),
        patch("personalclaw.cli_config.config_path", return_value=path),
    ):
        # Normalise ONCE up front. `AppConfig.load()` persists the full defaulted config
        # (22 KB from `{}`), and every handler here loads before it does anything else — so
        # without this the before/after snapshot would catch the LOADER's write and read as
        # "the handler changed the file" on a request the handler refused.
        from personalclaw.config.loader import AppConfig

        AppConfig.load()
        yield path


def _memory_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_memory_settings

    app = web.Application()
    app["state"] = type("_S", (), {"consolidator": None})()
    app.router.add_put("/api/memory/settings", api_memory_settings)
    return app


def _config_app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_personalclaw_config,
        api_personalclaw_config_patch,
    )

    app = web.Application()
    app.router.add_put("/api/config/personalclaw", api_personalclaw_config)
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


def _section(cfg_file, name: str) -> dict:
    return json.loads(cfg_file.read_text(encoding="utf-8")).get(name, {})


async def _unchanged(cfg_file, section: str, request_fn):
    """Run `request_fn` and return (response, whether `section` is byte-identical after).

    A SNAPSHOT, not an absence check: `AppConfig.load()` writes the whole normalised config
    back (22 KB from `{}`), so every field is present with its default the moment a handler
    reads config. "the key is not in the file" can therefore never be true, and a test that
    asserted it would be measuring the loader, not the write.
    """
    before = json.dumps(_section(cfg_file, section), sort_keys=True)
    resp = await request_fn()
    return resp, json.dumps(_section(cfg_file, section), sort_keys=True) == before


# ── PUT /api/memory/settings ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_truthy_STRING_no_longer_turns_a_memory_behaviour_on(cfg_file, sel_rows):
    """The sharpest form: the caller asked for OFF and got ON.

    `bool("false")` is `True`. A client sending the string it read out of a form field
    inverted the setting, was told 200, and the panel then rendered the new (wrong) state
    as if the user had chosen it.
    """
    async with TestClient(TestServer(_memory_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file,
            "memory",
            lambda: c.put("/api/memory/settings", json={"graph_enabled": "false"}),
        )
        assert resp.status == 400, await resp.text()
        assert "boolean" in (await resp.json())["error"]
    assert unchanged, "the coerced value was written anyway"
    assert sel_rows.outcomes("memory.settings.update") == ["denied"]


@pytest.mark.asyncio
async def test_a_real_boolean_still_writes(cfg_file, sel_rows):
    """Vacuity. Rejecting every value would satisfy the test above."""
    async with TestClient(TestServer(_memory_app())) as c:
        assert (await c.put("/api/memory/settings", json={"active_recall": False})).status == 200
    assert _section(cfg_file, "memory")["active_recall"] is False
    assert sel_rows.outcomes("memory.settings.update") == ["success"]


@pytest.mark.asyncio
async def test_the_unchanged_snapshot_can_actually_detect_a_change(cfg_file):
    """Vacuity for the helper every "nothing was written" assertion depends on.

    If `_unchanged` returned True unconditionally — a stale read, a wrong section name, a
    file the handler does not actually write — four tests above would be green for a
    codebase that writes the rejected value every time.
    """
    async with TestClient(TestServer(_memory_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file, "memory", lambda: c.put("/api/memory/settings", json={"l1_manifest": False})
        )
    assert resp.status == 200
    assert not unchanged, "_unchanged sees no difference after a write that DID land"


@pytest.mark.asyncio
async def test_a_typod_field_name_is_a_400_not_a_silent_200(cfg_file, sel_rows):
    """`actve_recall` used to be dropped without a word, and the response was 200."""
    async with TestClient(TestServer(_memory_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file, "memory", lambda: c.put("/api/memory/settings", json={"actve_recall": True})
        )
        assert resp.status == 400
        assert "actve_recall" in (await resp.json())["error"], "the reply does not name the key"
    assert unchanged
    assert sel_rows.outcomes("memory.settings.update") == ["denied"]


@pytest.mark.asyncio
async def test_a_PATCH_only_field_is_refused_here_rather_than_ignored(cfg_file):
    """`slot_size_cap` is read by this endpoint's GET and written by the PATCH.

    "One writer per field" only holds if the other writer says no out loud; a silent drop
    means the Settings panel could send it to the wrong endpoint forever.
    """
    async with TestClient(TestServer(_memory_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file, "memory", lambda: c.put("/api/memory/settings", json={"slot_size_cap": 1000})
        )
        assert resp.status == 400
        assert "slot_size_cap" in (await resp.json())["error"]
    assert unchanged


@pytest.mark.asyncio
async def test_an_out_of_range_confidence_is_refused_not_clamped(cfg_file):
    """Clamping 42 to 1.0 means "never volunteer" while the caller believes it asked for 42%."""
    async with TestClient(TestServer(_memory_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file,
            "memory",
            lambda: c.put("/api/memory/settings", json={"push_min_confidence": 42}),
        )
        assert resp.status == 400
        assert "between 0.0 and 1.0" in (await resp.json())["error"]
    assert unchanged, "the clamped 1.0 was written instead of refusing 42"


@pytest.mark.asyncio
async def test_an_empty_body_is_refused_rather_than_reported_as_saved(cfg_file):
    """`PUT {}` used to return 200 having written nothing — a save button that lies."""
    async with TestClient(TestServer(_memory_app())) as c:
        assert (await c.put("/api/memory/settings", json={})).status == 400


@pytest.mark.asyncio
async def test_writing_the_vault_mode_still_prunes_the_retired_flag(cfg_file):
    """Behaviour the rewrite had to preserve: config.json must not keep two answers."""
    cfg_file.write_text(json.dumps({"memory": {"vault_enabled": True}}), encoding="utf-8")
    async with TestClient(TestServer(_memory_app())) as c:
        assert (await c.put("/api/memory/settings", json={"vault_mode": "mirror"})).status == 200
    mem = _section(cfg_file, "memory")
    assert mem["vault_mode"] == "mirror" and "vault_enabled" not in mem


@pytest.mark.asyncio
async def test_an_empty_vault_path_still_normalises_to_the_default(cfg_file):
    """The `sanitize` half of the spec: the file must match what `load()` reads back."""
    async with TestClient(TestServer(_memory_app())) as c:
        assert (await c.put("/api/memory/settings", json={"vault_path": "  "})).status == 200
    assert _section(cfg_file, "memory")["vault_path"] == "memory-vault"


# ── PUT /api/config/personalclaw ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_recognised_key_no_longer_carries_an_unrecognised_one_through(cfg_file):
    """The silent-drop case: half the request applied, 200 returned, no mention of the rest."""
    async with TestClient(TestServer(_config_app())) as c:
        resp, unchanged = await _unchanged(
            cfg_file,
            "agent",
            lambda: c.put(
                "/api/config/personalclaw",
                json={"agent": {"max_subagents": 4, "subagent_max_tunrs": 999}},
            ),
        )
        assert resp.status == 400
        assert "subagent_max_tunrs" in (await resp.json())["error"]
    assert unchanged, "a rejected request wrote its recognised half anyway"


@pytest.mark.asyncio
async def test_a_valid_agent_put_still_writes(cfg_file):
    """Vacuity for the endpoint above."""
    async with TestClient(TestServer(_config_app())) as c:
        assert (
            await c.put("/api/config/personalclaw", json={"agent": {"max_subagents": 4}})
        ).status == 200
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["agent"]["max_subagents"] == 4


@pytest.mark.asyncio
async def test_the_two_endpoints_agree_on_the_same_field(cfg_file):
    """The point of the consolidation, asserted rather than assumed.

    `agent.max_subagents` is writable through both the PUT and the PATCH. Before, each
    carried its own copy of `0..16`; the same value must be refused by both, and the
    message must name the field on the PUT (which can carry several at once).
    """
    async with TestClient(TestServer(_config_app())) as c:
        put = await c.put("/api/config/personalclaw", json={"agent": {"max_subagents": 99}})
        patch_resp = await c.patch(
            "/api/config/personalclaw", json={"path": "agent.max_subagents", "value": 99}
        )
        put_err = (await put.json())["error"]
        patch_err = (await patch_resp.json())["error"]
    assert put.status == patch_resp.status == 400
    assert "between 0 and 16" in put_err and "between 0 and 16" in patch_err
    assert "max_subagents" in put_err, "the PUT does not say which field it refused"


# ── personalclaw config set ───────────────────────────────────────────────


def _config_set(key: str, value: str):
    """Drive the real CLI entry point, not a re-implementation of its rules."""
    import argparse

    from personalclaw.cli_config import _config_cmd

    return _config_cmd(argparse.Namespace(config_action="set", key=key, value=value, file=None))


def test_the_cli_cannot_write_past_the_bounds_the_api_enforces(cfg_file):
    """`config set agent.max_subagents 9999` used to succeed on the same field the API caps."""
    from personalclaw.config.loader import AppConfig

    default = AppConfig.load().agent.max_subagents
    with pytest.raises(SystemExit) as exc:
        _config_set("agent.max_subagents", "9999")
    assert exc.value.code == 1
    assert _section(cfg_file, "agent")["max_subagents"] == default, "9999 was written anyway"


def test_the_cli_still_writes_an_in_bounds_value(cfg_file):
    """Vacuity: the validation must not have turned `config set` into a no-op."""
    _config_set("agent.max_subagents", "8")
    assert _section(cfg_file, "agent")["max_subagents"] == 8


def test_the_cli_still_writes_a_key_the_allowlist_does_not_declare(cfg_file):
    """The allowlist is the PATCH surface, not a whole config schema.

    Refusing everything absent from it would break `config set` for most of the file, so an
    undeclared key keeps today's behaviour. Stated as a test because the alternative reading
    ("validate everything or nothing") is the tempting one.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert "session.timeout_secs" in _EDITABLE_CONFIG or True  # documented either way
    _config_set("auto_update", "true")
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["auto_update"] is True


# ── The registry is the single source ─────────────────────────────────────


def test_every_field_the_memory_put_writes_has_a_declared_spec():
    """The consolidation's structural claim: this endpoint declares WHICH fields, not what
    a valid value is. A field without a spec would `KeyError` at request time."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG
    from personalclaw.dashboard.handlers.memory import _SETTINGS_FIELDS

    assert _SETTINGS_FIELDS, "the writable set is empty — this test would pass vacuously"
    missing = [f for f in _SETTINGS_FIELDS if f"memory.{f}" not in _EDITABLE_CONFIG]
    assert not missing, f"writable memory fields with no _EDITABLE_CONFIG spec: {missing}"


def test_no_write_path_hand_rolls_a_boolean_again():
    """A grep rail on the shape that caused the inversion.

    `bool(body[...])` on a JSON value is never a validation — it is a coin flip that says
    yes. Scoped to the write handlers so an unrelated `bool()` elsewhere cannot make this
    fire, and asserted on the file contents with comments stripped so the explanation of
    the defect cannot satisfy the test that guards it.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src/personalclaw"
    offenders = {}
    for rel in ("dashboard/handlers/memory.py", "dashboard/handlers/core.py", "cli_config.py"):
        code = "\n".join(
            ln
            for ln in (root / rel).read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        hits = re.findall(r"bool\(\s*(?:body|agent_settings)\[", code)
        if hits:
            offenders[rel] = len(hits)
    assert not offenders, f"a JSON value is being coerced to bool instead of validated: {offenders}"


def test_the_shared_validator_rejects_a_bool_for_a_numeric_field():
    """`int(True)` is 1. Consolidating two validators must not adopt the looser one.

    The PATCH path allowed `true` for an int field; the PUT it now shares code with did
    not. This pins the stricter answer for both.
    """
    from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value

    with pytest.raises(ConfigValueError):
        coerce_edit_value("agent.max_subagents", True, {"type": "int", "min": 0, "max": 16})
    assert coerce_edit_value("agent.max_subagents", 4, {"type": "int", "min": 0, "max": 16}) == 4
