"""An agent write validates its SHAPES before it validates nothing (#349).

`POST /api/agents` / `PUT /api/agents/{name}` validated the NAME well (regex, length,
traversal, duplicate → 409) and the three LIST fields with `isinstance(..., list)` — and
handed every other value straight to the dataclass. Three consequences, all reproduced by
executing the real handlers against an isolated home:

======  ==========================================  ================================================
#349 A  a ~1200-deep JSON value                     raw 500 (`RecursionError` inside `cfg.save()`)
#349 B  a wrong-typed scalar on any of 13 fields    200, and the wrong type PERSISTS in config.json
#349 C  a retired/reserved agent name               200 `ok:true` for an agent that then isn't there
======  ==========================================  ================================================

**What the whole-class sweep added to the report.** Three findings the issue does not name:

* the same 500 reproduces through a LIST field (`tools: [<2000-deep>]`), so a scalars-only
  guard would not have closed A — the depth bound has to sit where every field passes;
* `PATCH /api/agents/detail/{name}` has the identical gap on the file-backed runtime config
  the ACP agent reads at boot (`{"description": 12345}` persisted an int; `{"tools":
  [{"a":1},5]}` persisted objects into a list of server names; a deep value 500'd) — one
  function away from the list-field guard #427 already installed there;
* C's reserved half was live too, not merely its retired half: on a config with no `agents`
  map yet, `POST {"name": "personalclaw-lite", "system_prompt": …}` answered 200, and
  because the seeding migration is add-if-MISSING it never overwrote the impostor — so the
  background chore worker ran the caller's prompt, and PUT/DELETE then answered 403 because
  the name is reserved. The ABSENCE of a seeded profile was the protection, not a guard.

**No second validator.** The rules are `config/edit_spec.py`'s `coerce_edit_value` — the
one the `_EDITABLE_CONFIG` PATCH path, `PUT /api/config/personalclaw` and `personalclaw
config set` all share. This change contributes a spec TABLE (`_AGENT_FIELD_SPECS`) for the
agent profile and one shared entry point (`_staged_agent_fields`), so the create path, the
update path and the file-backed PATCH path cannot disagree about what a field is.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.config.loader import AgentProfile


def _declared(kind: type) -> tuple[str, ...]:
    """Field names whose DECLARED type on `AgentProfile` is *kind*.

    Derived, not listed: a field added to the profile joins the table-driven cases below
    automatically, which is the only way a per-field sweep stays a sweep.
    """
    return tuple(f.name for f in dataclasses.fields(AgentProfile) if f.type is kind)


# 13 declared-string fields, 1 declared-bool field, 3 declared-list fields.
_STR_FIELDS = _declared(str)
_LIST_FIELDS = _declared(list)

#: Values that are not a string. `True` is in here on purpose: `isinstance(True, int)`, and a
#: bare truthiness coercion is how a JSON `true` becomes the number 1 (or the string "True")
#: for a field that declares neither.
_NOT_A_STRING = (12345, {"a": 1}, [1, 2], None, True, 3.5)
_NOT_A_BOOL = (12345, "true", "false", {"a": 1}, [1], None)
_NOT_A_STR_LIST = (12345, "notalist", {"a": 1}, None, True, [1, 2], [{"a": 1}], ["ok", 5])


def _request(body: Any, *, method: str = "POST", match: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.method = method
    req.json = AsyncMock(return_value=body)
    req.match_info = match or {}
    req.get = lambda *a, **k: "dashboard"
    req.headers = {}
    req.app = {"state": MagicMock()}
    return req


def _nested(depth: int) -> list:
    """A `depth`-deep nest of lists, built ITERATIVELY (building it recursively would raise
    the RecursionError in the test rather than in the code under test)."""
    root: list = []
    cur = root
    for _ in range(depth - 1):
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config home. Never the real one — these tests write config.json."""
    d = tmp_path / "home"
    d.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: d)
    monkeypatch.setattr("personalclaw.dashboard.handlers.agents.config_dir", lambda: d)
    return d


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _error_text(resp) -> str:
    err = _body(resp)["error"]
    return err["message"] if isinstance(err, dict) else str(err)


def _agents_on_disk(home) -> dict:
    p = home / "config.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("agents", {})


async def _create(body) -> Any:
    from personalclaw.dashboard.handlers.agents import api_personalclaw_agents_create

    return await api_personalclaw_agents_create(_request(body))


async def _update(name, body) -> Any:
    from personalclaw.dashboard.handlers.agents import api_personalclaw_agent_update

    return await api_personalclaw_agent_update(_request(body, method="PUT", match={"name": name}))


# ── the spec table is the dataclass, not a hand-copy of it ────────────────────


class TestTheTableCannotDrift:
    """The write side and the read side must declare the same types. `JSON_SCHEMA` (what the
    loader validates a loaded config against) is generated from `AgentProfile`; so is this
    table's coverage assertion. A field added to the profile without a spec would silently
    return to the unvalidated behaviour this change removed."""

    def test_every_AgentProfile_field_has_a_spec(self):
        from personalclaw.dashboard.handlers.agents import _AGENT_FIELD_SPECS

        declared = {f.name for f in dataclasses.fields(AgentProfile)}
        assert set(_AGENT_FIELD_SPECS) == declared, (
            "the write-side table and AgentProfile disagree; missing="
            f"{sorted(declared - set(_AGENT_FIELD_SPECS))} extra="
            f"{sorted(set(_AGENT_FIELD_SPECS) - declared)}"
        )

    def test_each_spec_type_matches_the_dataclass_type(self):
        from personalclaw.dashboard.handlers.agents import _AGENT_FIELD_SPECS

        for kind, spec_type in ((str, "str"), (bool, "bool"), (list, "str_list")):
            for name in _declared(kind):
                assert _AGENT_FIELD_SPECS[name]["type"] == spec_type, name

    def test_the_detail_patch_keys_are_a_subset_of_the_same_table(self):
        """The file-backed PATCH path validates the seven fields it applies with this table
        rather than a dialect of its own."""
        from personalclaw.dashboard.handlers.agents import (
            _AGENT_DETAIL_PATCH_KEYS,
            _AGENT_FIELD_SPECS,
        )

        assert set(_AGENT_DETAIL_PATCH_KEYS) <= set(_AGENT_FIELD_SPECS)

    def test_no_spec_uses_a_type_coerce_edit_value_does_not_support(self):
        """`coerce_edit_value` answers status 500 for an unrecognised spec `type` — a bug in
        the table, not in the request. This is what keeps that branch unreachable, so every
        refusal from these handlers is a 4xx."""
        from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
        from personalclaw.dashboard.handlers.agents import _AGENT_FIELD_SPECS

        for name, spec in _AGENT_FIELD_SPECS.items():
            try:
                coerce_edit_value(name, object(), spec)
            except ConfigValueError as exc:
                assert exc.status == 400, f"{name} can produce a {exc.status}"


# ── #349 B — a wrong-typed value is a 4xx that names the field, and nothing persists ──


class TestScalarTypesOnCreate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", _STR_FIELDS)
    @pytest.mark.parametrize("bad", _NOT_A_STRING, ids=repr)
    async def test_a_non_string_is_refused_and_nothing_is_written(self, home, field, bad):
        resp = await _create({"name": "zz-probe", field: bad})
        assert resp.status == 400, f"{field}={bad!r} was accepted"
        assert field in _error_text(resp), "the refusal must name the field"
        assert "zz-probe" not in _agents_on_disk(home), "a refused create persisted the agent"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", _NOT_A_BOOL, ids=repr)
    async def test_natural_voice_requires_a_real_boolean(self, home, bad):
        """`bool("false")` is True, so coercing a bool would turn a request to switch a
        behaviour OFF into one that switches it ON — the defect `config/edit_spec.py` exists
        to stop re-deriving. The handler previously did exactly `bool(body[...])`."""
        resp = await _create({"name": "zz-nv", "natural_voice": bad})
        assert resp.status == 400
        assert "natural_voice" in _error_text(resp)
        assert "zz-nv" not in _agents_on_disk(home)


class TestListTypesOnCreate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", _LIST_FIELDS)
    @pytest.mark.parametrize("bad", _NOT_A_STR_LIST, ids=repr)
    async def test_a_non_string_list_is_refused(self, home, field, bad):
        """These fields were guarded against a non-LIST (silently ignored) but not against a
        list of non-strings: `tools: [{"a": 1}, 5]` persisted objects into what the schema
        declares as a list of strings, and `triggers: [1, 2]` was `str()`-coerced to
        `["1", "2"]` — the stringify-don't-refuse shape."""
        resp = await _create({"name": "zz-list", field: bad})
        assert resp.status == 400, f"{field}={bad!r} was accepted"
        assert field in _error_text(resp)
        assert "zz-list" not in _agents_on_disk(home)


class TestScalarTypesOnUpdate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", _STR_FIELDS)
    async def test_a_non_string_is_refused_and_the_profile_is_UNCHANGED(self, home, field):
        assert (await _create({"name": "zz-base", "description": "keep me"})).status == 200
        before = _agents_on_disk(home)["zz-base"]

        resp = await _update("zz-base", {field: {"an": "object"}})
        assert resp.status == 400, f"{field} was accepted"
        assert field in _error_text(resp)
        assert _agents_on_disk(home)["zz-base"] == before, "a refused update mutated the profile"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", _LIST_FIELDS)
    async def test_a_wrong_typed_list_no_longer_answers_200_having_done_nothing(self, home, field):
        """This deliberately RETIRES the previous ignore-it behaviour. Being told a write
        succeeded when it changed nothing is the failure mode `config/edit_spec.py` argues is
        worse than a refusal — nothing will ever look wrong. Clearing a list stays
        expressible, by sending `[]` (see the vacuity floor below)."""
        assert (await _create({"name": "zz-base", field: ["real"]})).status == 200
        before = _agents_on_disk(home)["zz-base"]

        resp = await _update("zz-base", {field: "notalist"})
        assert resp.status == 400
        assert field in _error_text(resp)
        assert _agents_on_disk(home)["zz-base"] == before


# ── #349 A — a pathological nest is a 4xx, not a RecursionError ───────────────


class TestNestingDepth:
    """Chain that produced the 500: handler → `cfg.save()` → `to_dict()` →
    `dataclasses.asdict` → `json.dumps`, both of which recurse. Nothing was persisted and
    the gateway survived, but a raw 500 with a traceback sat where a 400 belongs, on a write
    path any local API client can reach."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("depth", [13, 200, 1200, 2000])
    async def test_a_deep_scalar_is_a_400_on_create(self, home, depth):
        resp = await _create({"name": "zz-deep", "description": _nested(depth)})
        assert resp.status == 400
        assert "description" in _error_text(resp)
        assert "zz-deep" not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_a_deep_value_inside_a_LIST_field_is_a_400_too(self, home):
        """The reason the bound sits where every field passes instead of on the scalars: the
        same crash reproduced through `tools: [<2000-deep>]`, which is a well-typed `list`
        as far as an `isinstance` guard is concerned."""
        resp = await _create({"name": "zz-deeptools", "tools": [_nested(2000)]})
        assert resp.status == 400
        assert "tools" in _error_text(resp)
        assert "zz-deeptools" not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_a_deep_value_is_a_400_on_update_and_leaves_the_profile_alone(self, home):
        assert (await _create({"name": "zz-base", "system_prompt": "original"})).status == 200
        before = _agents_on_disk(home)["zz-base"]

        resp = await _update("zz-base", {"system_prompt": _nested(2000)})
        assert resp.status == 400
        assert "system_prompt" in _error_text(resp)
        assert _agents_on_disk(home)["zz-base"] == before

    @pytest.mark.asyncio
    async def test_a_body_deeper_than_the_TYPE_check_can_survive_is_still_a_400(self, home):
        """Why the depth check runs BEFORE the type check, and is not redundant with it.

        The type rule interpolates the rejected value into its SEL `resources` string, and
        `repr()` of a deep structure recurses too — measured on this interpreter, `repr` and
        `json.dumps` both raise `RecursionError` at ~20 000 levels. So a type-check-only
        guard does not remove the 500, it relocates it into the error path, where the answer
        is a traceback instead of a message. Driven past that measured threshold on purpose.
        """
        resp = await _create({"name": "zz-verydeep", "description": _nested(20_000)})
        assert resp.status == 400
        assert "description" in _error_text(resp)
        assert len(_error_text(resp)) < 200, "the refusal must not carry the offending value"
        assert "zz-verydeep" not in _agents_on_disk(home)

    def test_the_walk_is_safe_INDEPENDENTLY_of_the_cap(self, monkeypatch):
        """A recursive walker is accidentally safe at a cap of 12 — it returns at the cap, so
        it is only ever ~12 frames deep. That is what makes the recursive form a trap rather
        than a preference: raise the cap and the guard itself becomes the thing that raises
        `RecursionError`. Pinned by raising the cap and asserting the walk still answers."""
        from personalclaw.dashboard.handlers import agents as A

        monkeypatch.setattr(A, "_MAX_AGENT_BODY_DEPTH", 10_000_000)
        assert A._agent_value_too_deep(_nested(20_000)) is False

    def test_a_legitimately_shallow_value_is_not_flagged(self):
        """Vacuity floor for the depth guard itself: the shapes this endpoint accepts —
        scalars and lists of strings — must not trip it."""
        from personalclaw.dashboard.handlers.agents import _agent_value_too_deep

        assert _agent_value_too_deep("a string") is False
        assert _agent_value_too_deep(["a", "b"]) is False
        assert _agent_value_too_deep([]) is False
        assert _agent_value_too_deep(_nested(12)) is False


# ── #349 C — a name the system owns is refused, not reconciled after the fact ──


class TestUnavailableNames:
    @pytest.mark.asyncio
    async def test_a_retired_name_is_refused_instead_of_answering_ok_then_vanishing(self, home):
        from personalclaw.agents.defaults import RETIRED_AGENT_NAMES

        for name in RETIRED_AGENT_NAMES:
            resp = await _create({"name": name})
            assert resp.status == 403, f"{name} was accepted"
            assert name in _error_text(resp)
            assert name not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_a_reserved_name_is_refused_on_a_FIRST_EVER_create(self, home):
        """The live half the issue recorded as already-hardened. It was hardened only where
        the seeding migration had already run: with no `agents` map on disk, the duplicate
        check has nothing to match, so `personalclaw-lite` was created — and seeding being
        add-if-MISSING meant the caller's profile became the built-in's, permanently, because
        PUT and DELETE then refuse it as reserved."""
        from personalclaw.agents.defaults import RESERVED_AGENT_NAMES

        assert not (home / "config.json").exists(), "the premise is a config with no agents map"
        for name in sorted(RESERVED_AGENT_NAMES):
            resp = await _create({"name": name, "system_prompt": "impostor"})
            assert resp.status == 403, f"{name} was accepted on a fresh config"
            assert name in _error_text(resp)
            assert name not in _agents_on_disk(home)

    def test_the_refusal_precedes_the_write_not_the_reconcile(self):
        """The principle, stated once: this is the same set-time validation `api_default_agent`
        already does for a dangling default. A write that "succeeds" and is then undone by the
        next `AppConfig.load()` is indistinguishable from success to the caller.

        Matched on the CALL, not on the identifier: the handler's comment names the helper too,
        so an identifier match would be satisfied by prose after the call had been deleted."""
        import inspect

        from personalclaw.dashboard.handlers import agents as A

        src = inspect.getsource(A.api_personalclaw_agents_create)
        call = "_unavailable_agent_name(name)"
        assert call in src
        assert src.index(call) < src.index("cfg.save()"), "the name check must precede the write"

    @pytest.mark.asyncio
    async def test_an_ordinary_name_in_the_same_namespace_is_still_allowed(self, home):
        """Vacuity floor: the guard consults the reserved/retired SETS, not a `personalclaw-`
        prefix — a user may still name their own agent `personalclaw-notes`."""
        resp = await _create({"name": "personalclaw-notes"})
        assert resp.status == 200, _error_text(resp)
        assert "personalclaw-notes" in _agents_on_disk(home)


# ── PATCH /api/agents/detail/{name} — the same class, on the runtime config ───


@pytest.fixture
def file_backed(tmp_path, monkeypatch):
    """The per-file agent config the ACP agent reads at boot."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr("personalclaw.agent.agents_dir", lambda: agents_dir)
    path = agents_dir / "personalclaw.json"
    path.write_text(
        json.dumps(
            {
                "name": "personalclaw",
                "description": "the real one",
                "system_prompt": "be helpful",
                "model": "m1",
                "tools": ["server-a", "server-b"],
                "mcpServers": {"server-a": {"command": "x"}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


async def _patch_detail(name, body) -> Any:
    from personalclaw.dashboard.handlers.agents import api_agent_detail

    return await api_agent_detail(_request(body, method="PATCH", match={"name": name}))


class TestFileBackedPatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["model", "description", "system_prompt", "approval_mode"])
    @pytest.mark.parametrize("bad", [12345, {"a": 1}, [1, 2]], ids=repr)
    async def test_a_wrong_typed_scalar_is_refused_and_the_file_is_untouched(
        self, file_backed, field, bad
    ):
        before = file_backed.read_bytes()
        resp = await _patch_detail("personalclaw", {field: bad})
        assert resp.status == 400, f"{field}={bad!r} was accepted"
        assert field in _error_text(resp)
        assert file_backed.read_bytes() == before, "a refused PATCH rewrote the runtime config"

    @pytest.mark.asyncio
    async def test_a_list_of_non_strings_is_refused(self, file_backed):
        before = file_backed.read_bytes()
        resp = await _patch_detail("personalclaw", {"tools": [{"deep": [1]}, 5]})
        assert resp.status == 400
        assert "tools" in _error_text(resp)
        assert file_backed.read_bytes() == before

    @pytest.mark.asyncio
    async def test_a_deep_value_is_a_400_not_a_RecursionError(self, file_backed):
        before = file_backed.read_bytes()
        resp = await _patch_detail("personalclaw", {"description": _nested(2000)})
        assert resp.status == 400
        assert file_backed.read_bytes() == before
        json.loads(file_backed.read_text(encoding="utf-8"))  # still valid JSON

    @pytest.mark.asyncio
    async def test_a_legitimate_patch_still_applies(self, file_backed):
        """Vacuity floor, including the two behaviours this path already had: an empty scalar
        CLEARS the key, and an empty list is a real value (not a delete)."""
        resp = await _patch_detail(
            "personalclaw",
            {"description": "patched", "tools": ["server-a"], "system_prompt": ""},
        )
        assert resp.status == 200, _error_text(resp)
        data = json.loads(file_backed.read_text(encoding="utf-8"))
        assert data["description"] == "patched"
        assert data["tools"] == ["server-a"]
        assert "system_prompt" not in data, "an empty scalar must still clear the key"
        assert data["mcpServers"] == {"server-a": {"command": "x"}}, "an untouched key changed"

    @pytest.mark.asyncio
    async def test_the_validation_precedes_the_file_loop(self, file_backed):
        """Nothing is read or written before the refusal — so a malformed body is refused
        whether or not the addressed agent exists."""
        resp = await _patch_detail("no-such-agent", {"description": 12345})
        assert resp.status == 400
        assert "description" in _error_text(resp)


# ── the vacuity floor: a legitimate write still works, end to end ────────────


_FULL_BODY = {
    "name": "zz-real",
    "provider": "native",
    "provider_agent": "",
    "acp_mode": "",
    "default_dir": "/tmp/wd",
    "memory_store": "fs",
    "description": "a real agent",
    "system_prompt": "be helpful",
    "voice": "dry",
    "natural_voice": True,
    "model": "m1",
    "approval_mode": "auto",
    "skills": ["s1", "s2"],
    "tools": ["t1"],
    "triggers": ["tr1"],
    "source": "personalclaw",
    "specialty": "code",
    "route_hints": "python, rust",
}


class TestLegitimateWritesStillWork:
    @pytest.mark.asyncio
    async def test_a_create_carrying_every_field_round_trips_verbatim(self, home):
        """Every field, not a sample: a coercion table is exactly the kind of change that
        passes a spot check while quietly dropping the two fields nobody probed."""
        resp = await _create(dict(_FULL_BODY))
        assert resp.status == 200, _error_text(resp)

        from personalclaw.config.loader import AppConfig

        stored = dataclasses.asdict(AppConfig.load().agents["zz-real"])
        for key, value in _FULL_BODY.items():
            if key == "name":
                continue
            assert stored[key] == value, f"{key} did not round-trip"

    @pytest.mark.asyncio
    async def test_an_omitted_field_still_falls_back_to_the_dataclass_default(self, home):
        """The create path used to spell seventeen `body.get(key, <default>)` calls; it now
        passes only the keys present and lets `AgentProfile` supply the rest. Same answer —
        asserted, because "same answer" is the whole claim."""
        assert (await _create({"name": "zz-min"})).status == 200

        from personalclaw.config.loader import AppConfig

        stored = AppConfig.load().agents["zz-min"]
        assert stored == AgentProfile(), "an omitted field diverged from its declared default"

    @pytest.mark.asyncio
    async def test_an_update_applies_every_field_and_reports_them(self, home):
        assert (await _create({"name": "zz-real"})).status == 200
        body = {k: v for k, v in _FULL_BODY.items() if k != "name"}
        resp = await _update("zz-real", body)
        assert resp.status == 200, _error_text(resp)

        from personalclaw.config.loader import AppConfig

        stored = dataclasses.asdict(AppConfig.load().agents["zz-real"])
        for key, value in body.items():
            assert stored[key] == value, f"{key} did not apply"

    @pytest.mark.asyncio
    async def test_clearing_a_list_and_a_string_stays_expressible(self, home):
        """The refusal above only removes WRONG TYPES. `[]` and `""` are well-typed values and
        must keep meaning "empty", or the tightening would have removed a real capability."""
        assert (
            await _create({"name": "zz-real", "skills": ["s"], "description": "d"})
        ).status == 200
        resp = await _update("zz-real", {"skills": [], "description": ""})
        assert resp.status == 200, _error_text(resp)

        from personalclaw.config.loader import AppConfig

        stored = AppConfig.load().agents["zz-real"]
        assert stored.skills == []
        assert stored.description == ""

    @pytest.mark.asyncio
    async def test_an_unknown_key_is_still_ignored_rather_than_refused(self, home):
        """Deliberately unchanged. Unknown keys are dropped by the allowlist and never reach a
        serializer, so they cannot produce A's crash — refusing them would be a new refusal
        riding along on a type fix."""
        resp = await _create({"name": "zz-real", "totally_unknown": {"deep": _nested(2000)}})
        assert resp.status == 200, _error_text(resp)
        assert "totally_unknown" not in _agents_on_disk(home)["zz-real"]

    @pytest.mark.asyncio
    async def test_the_hardened_behaviours_the_issue_recorded_are_preserved(self, home):
        """#349 records a 200 KB `system_prompt` and a 100 000-element `triggers` list as
        persisting correctly, and the bounds in the spec table are sized so this stays true:
        a length cap would be a size policy smuggled in under a type fix."""
        big = {"name": "zz-big", "system_prompt": "x" * 200_000, "triggers": ["t"] * 100_000}
        resp = await _create(big)
        assert resp.status == 200, _error_text(resp)
        stored = _agents_on_disk(home)["zz-big"]
        assert len(stored["system_prompt"]) == 200_000
        assert len(stored["triggers"]) == 100_000

    @pytest.mark.asyncio
    async def test_the_pre_existing_refusals_are_unchanged(self, home):
        """The name rail this change does not touch, pinned so the new checks cannot be read
        as having replaced it."""
        assert (await _create({"name": "zz-real"})).status == 200
        assert (await _create({"name": "zz-real"})).status == 409  # duplicate
        assert (await _create({"name": "Has Spaces"})).status == 400  # regex
        assert (await _create({"name": ""})).status == 400  # empty
        assert (await _create({"name": 42})).status == 400  # wrong type
        assert (await _create([1, 2, 3])).status == 400  # body not an object
