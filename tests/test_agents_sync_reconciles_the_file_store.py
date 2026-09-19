"""`POST /api/agents/sync` never read `AGENTS_DIR`, so file-installed agents stayed hidden (#344).

The whole handler was::

    async def _do_agents_sync(request):
        cfg = AppConfig.load()
        cfg.save()
        return web.json_response({"ok": True, "synced": []})

`git log -S"_do_agents_sync"` returns exactly one commit (the initial public commit), so this
never worked — it is not a body a refactor dropped.

**Why "a no-op that reports nothing" understates it.** PersonalClaw keeps agents in two
places, and this endpoint is the ONLY thing that reconciles them:

* `config.json`'s `agents` map — the ONLY store `GET /api/agents` lists;
* `AGENTS_DIR`, in two layouts — the flat `<name>.json` that `PATCH`/`DELETE
  /api/agents/detail/{name}`, `chat_persistence`, `session.py` and `skills.py` all read, and
  the local agent marketplace's `<name>/agent.json`.

So an agent that arrives as a FILE — `handlers/agents.py`'s own comment names the flows: "a
marketplace activate, an app, a restored snapshot" — never appeared anywhere in the UI, and
the one control offered to fix that performed a load/save. That is a gap on the install path,
not a missing toast.

**And the load/save was not inert.** `cfg.save()` rewrote the whole of the user's
`config.json` on every press to bump `lastTouchedAt` — a full-file write of live config in
answer to a control that reported nothing. The write is now conditional on an actual change,
which is asserted below on the file's BYTES.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.config.loader import AgentProfile, AppConfig


def _request() -> MagicMock:
    req = MagicMock()
    req.method = "POST"
    req.json = AsyncMock(return_value={})
    req.match_info = {}
    req.get = lambda *a, **k: "dashboard"
    req.headers = {}
    req.app = {"state": MagicMock()}
    return req


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config home with an isolated ``AGENTS_DIR``.

    🪤 `AGENTS_DIR` is a MODULE-LEVEL constant evaluated at import (`agent.py:93`), so moving
    `config_dir` does not move it — `mcp_discovery.py:220` already records that trap. The
    constant itself has to be patched, and so does the local marketplace's base dir, which
    the registry singleton resolved at import time for the same reason.
    """
    d = tmp_path / "home"
    agents = d / "agents"
    agents.mkdir(parents=True)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: d)
    monkeypatch.setattr("personalclaw.dashboard.handlers.agents.config_dir", lambda: d)
    monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents)

    from personalclaw.agents.marketplace import get_default_agent_registry

    monkeypatch.setattr(get_default_agent_registry().get("local"), "_base", agents)
    return d


def _write_flat_agent(home, name: str, **fields: Any) -> None:
    """The flat `AGENTS_DIR/<name>.json` layout (an app bundle / restored snapshot)."""
    (home / "agents" / f"{name}.json").write_text(
        json.dumps({"name": name, **fields}), encoding="utf-8"
    )


def _write_marketplace_agent(home, name: str, **fields: Any) -> None:
    """The local agent marketplace's `AGENTS_DIR/<name>/agent.json` layout."""
    d = home / "agents" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.json").write_text(json.dumps({"name": name, **fields}), encoding="utf-8")


async def _sync(req=None) -> Any:
    from personalclaw.dashboard.handlers.agents import api_personalclaw_agents_sync

    return await api_personalclaw_agents_sync(req or _request())


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _agents_on_disk(home) -> dict:
    p = home / "config.json"
    return json.loads(p.read_text(encoding="utf-8")).get("agents", {}) if p.exists() else {}


# ── the deliverable: a file-installed agent becomes visible ───────────────────


class TestBothLayoutsAreReconciled:
    @pytest.mark.asyncio
    async def test_a_flat_file_agent_is_folded_in_and_named(self, home):
        _write_flat_agent(home, "snapshot-bot", description="restored from a snapshot", model="m1")
        body = _body(await _sync())
        assert body["ok"] is True
        assert body["synced"] == ["snapshot-bot"]
        # It is in config.json, which is the store GET /api/agents reads — the actual gap.
        assert "snapshot-bot" in _agents_on_disk(home)
        assert _agents_on_disk(home)["snapshot-bot"]["description"] == "restored from a snapshot"
        assert "snapshot-bot" in AppConfig.load().agents

    @pytest.mark.asyncio
    async def test_a_marketplace_agent_is_folded_in_too(self, home):
        """The layout the old docstring's "marketplace-installed agents" meant. Enumerated
        through the REGISTRY, not by globbing a subdirectory, so a marketplace that stores its
        definitions elsewhere is listed by its own `list()`."""
        _write_marketplace_agent(home, "research-bot", description="reads papers")
        body = _body(await _sync())
        assert body["synced"] == ["research-bot"]
        assert "research-bot" in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_both_layouts_in_one_pass(self, home):
        _write_flat_agent(home, "flat-one")
        _write_marketplace_agent(home, "mkt-one")
        body = _body(await _sync())
        assert sorted(body["synced"]) == ["flat-one", "mkt-one"]
        assert body["scanned"] == 2

    @pytest.mark.asyncio
    async def test_the_agents_list_endpoint_then_shows_it(self, home):
        """End to end on the wire, because "it is in the config object" is not the claim —
        the claim is that the user can now see it."""
        from personalclaw.dashboard.handlers.agents import api_personalclaw_agents

        _write_flat_agent(home, "store-bot", description="from the Store")
        before = [a["name"] for a in _body(await api_personalclaw_agents(_request()))["agents"]]
        assert "store-bot" not in before
        await _sync()
        after = [a["name"] for a in _body(await api_personalclaw_agents(_request()))["agents"]]
        assert "store-bot" in after


# ── what must NOT be folded in ────────────────────────────────────────────────


class TestTheRefusals:
    @pytest.mark.asyncio
    async def test_the_acp_runtime_config_is_not_an_agent_profile(self, home):
        """`personalclaw.json` is what `rebuild_agent_config()` writes: `mcpServers`, `hooks`,
        a `prompt` URI, and a `tools` list of MCP SERVER REFS (`@personalclaw-core`) rather
        than tool-name patterns. Folding it in would copy those refs into an
        `AgentProfile.tools` that means something else. The sibling DELETE path already
        special-cases this filename, so the rule is not new."""
        (home / "agents" / "personalclaw.json").write_text(
            json.dumps(
                {
                    "name": "personalclaw",
                    "tools": ["@personalclaw-core", "@personalclaw-memory"],
                    "mcpServers": {"personalclaw-core": {"command": "/bin/true"}},
                }
            ),
            encoding="utf-8",
        )
        body = _body(await _sync())
        assert body["synced"] == [] and body["scanned"] == 0
        assert "personalclaw" not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_an_agent_already_in_the_config_is_not_duplicated(self, home):
        """Case-insensitively, via the same `_resolve_agent_name` the CRUD paths use."""
        cfg = AppConfig.load()
        cfg.agents["Research-Bot"] = AgentProfile(description="the user's own")
        cfg.save()
        _write_flat_agent(home, "research-bot", description="the file's")
        body = _body(await _sync())
        assert body["synced"] == []
        on_disk = _agents_on_disk(home)
        assert "research-bot" not in on_disk
        assert on_disk["Research-Bot"]["description"] == "the user's own"

    @pytest.mark.asyncio
    async def test_a_reserved_name_is_skipped_and_reported(self, home):
        """A reserved system agent is owned by the seeding migration; `PUT`/`DELETE` both
        answer 403 for one, and create refuses it. Folding one in from a file would install
        an impostor the background workers then run."""
        _write_flat_agent(home, "personalclaw-lite", system_prompt="hijacked")
        body = _body(await _sync())
        assert body["synced"] == [] and body["skipped"] == ["personalclaw-lite"]
        assert _agents_on_disk(home).get("personalclaw-lite", {}).get("system_prompt") != "hijacked"

    @pytest.mark.asyncio
    async def test_a_retired_name_is_skipped(self, home):
        """`RETIRED_AGENT_NAMES` are pruned by the very next config load, so folding one in
        would report success and leave nothing behind — the exact lie `_unavailable_agent_name`
        exists to refuse."""
        _write_flat_agent(home, "personalclaw-autonomous")
        body = _body(await _sync())
        assert body["skipped"] == ["personalclaw-autonomous"]

    @pytest.mark.asyncio
    async def test_a_wrong_typed_field_is_refused_not_persisted(self, home):
        """A file on disk is not a request, so one bad field must not fail the whole sync —
        but it must not land a wrong type in `config.json` either. That is #349's defect
        class, and the sync passes through the SAME `_AGENT_FIELD_SPECS` table the three
        write paths use rather than trusting the file."""
        _write_flat_agent(home, "good-bot", description="fine")
        _write_flat_agent(home, "bad-bot", description=12345)
        body = _body(await _sync())
        assert body["synced"] == ["good-bot"]
        assert body["skipped"] == ["bad-bot"]
        assert "bad-bot" not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_an_unparseable_file_is_named_rather_than_silently_dropped(self, home):
        """Reporting a smaller scan without saying why is how a user concludes the sync
        worked and their agent simply is not there."""
        (home / "agents" / "broken.json").write_text("{not json", encoding="utf-8")
        body = _body(await _sync())
        assert body["unreadable"] == ["broken.json"]
        assert "Could not read 1: broken.json." in body["message"]

    @pytest.mark.asyncio
    async def test_the_files_own_name_field_passes_the_same_guard_as_a_POST(self, home):
        """A file's `name` is NOT the filename — the four readers of this layout prefer the
        internal field — so an app bundle or a restored snapshot chooses it. `POST /api/agents`
        refuses a name outside `^[a-z0-9][a-z0-9-]{0,62}$` "so names can't be later
        interpolated"; a JSON file on disk is not a more trustworthy source than a POST."""
        (home / "agents" / "sneaky.json").write_text(
            json.dumps({"name": "../../etc/passwd", "description": "x"}), encoding="utf-8"
        )
        body = _body(await _sync())
        assert body["synced"] == []
        assert body["skipped"] == ["../../etc/passwd"]
        assert "../../etc/passwd" not in _agents_on_disk(home)

    @pytest.mark.asyncio
    async def test_a_refused_name_cannot_decide_the_size_of_the_report(self, home):
        """The refused name is echoed into a toast, a response body and a SEL `resources`
        column. An accepted name is bounded by the regex at 63 chars; a REFUSED one came out
        of the file and is not, so it is clipped before it reaches any of the three."""
        (home / "agents" / "huge.json").write_text(
            json.dumps({"name": "A" * 5000}), encoding="utf-8"
        )
        body = _body(await _sync())
        assert len(body["skipped"]) == 1
        assert len(body["skipped"][0]) == 61 and body["skipped"][0].endswith("…")
        assert len(body["message"]) < 200


# ── the write, and the sentence ───────────────────────────────────────────────


class TestItOnlyWritesWhenSomethingChanged:
    @pytest.mark.asyncio
    async def test_nothing_to_sync_does_not_rewrite_config_json(self, home):
        """🪤 The finding the issue added on re-verification: the old body's unconditional
        `load(); save()` produced a NEW md5 on every press, rewriting 31 top-level keys to
        move a timestamp. Asserted on the bytes, because "no agents were added" was already
        true of the broken version."""
        AppConfig.load().save()
        p = home / "config.json"
        before = p.read_bytes()
        body = _body(await _sync())
        assert body["synced"] == []
        assert p.read_bytes() == before, "an empty sync must not touch the user's config"

    @pytest.mark.asyncio
    async def test_a_real_sync_does_write(self, home):
        AppConfig.load().save()
        before = (home / "config.json").read_bytes()
        _write_flat_agent(home, "new-bot")
        await _sync()
        assert (home / "config.json").read_bytes() != before

    @pytest.mark.asyncio
    async def test_the_refresh_push_is_gated_on_the_same_change(self, home):
        """A refresh broadcast for an unchanged list is the client-side form of the same
        lie — it re-renders identical rows and reads as "nothing happened"."""
        quiet = _request()
        await _sync(quiet)
        quiet.app["state"].push_refresh.assert_not_called()

        loud = _request()
        _write_flat_agent(home, "new-bot")
        await _sync(loud)
        loud.app["state"].push_refresh.assert_called_once_with("agents")


class TestTheSentence:
    """A server-composed sentence is a UI surface: the frontend renders `message` verbatim
    rather than re-deriving one from three arrays."""

    @pytest.mark.asyncio
    async def test_nothing_missing_says_so_truthfully(self, home):
        assert "Already up to date" in _body(await _sync())["message"]

    @pytest.mark.asyncio
    async def test_it_names_the_agents_that_became_visible(self, home):
        _write_flat_agent(home, "store-bot")
        msg = _body(await _sync())["message"]
        assert msg.startswith("Added 1 agent from your agent files: store-bot.")

    @pytest.mark.asyncio
    async def test_it_pluralises(self, home):
        _write_flat_agent(home, "a-bot")
        _write_flat_agent(home, "b-bot")
        assert "Added 2 agents from your agent files:" in _body(await _sync())["message"]

    @pytest.mark.asyncio
    async def test_the_skipped_count_is_part_of_the_same_sentence(self, home):
        _write_flat_agent(home, "ok-bot")
        _write_flat_agent(home, "personalclaw-lite")
        msg = _body(await _sync())["message"]
        assert "Added 1 agent" in msg and "Skipped 1: personalclaw-lite." in msg


class TestTheWireContract:
    @pytest.mark.asyncio
    async def test_synced_is_a_list_of_names_not_a_count(self, home):
        """`web/src/lib/api.ts` declared `synced?: number` while the server has always
        returned a list, so nothing could have rendered it. The contract settled on the LIST
        — the deliverable is visibility, so the answer says WHICH agents appeared — and the
        TypeScript type is what moved."""
        _write_flat_agent(home, "store-bot")
        synced = _body(await _sync())["synced"]
        assert isinstance(synced, list) and synced == ["store-bot"]

    @pytest.mark.asyncio
    async def test_every_advertised_key_is_present_even_when_empty(self, home):
        """The client destructures all of them; an absent key would be `undefined.length`."""
        body = _body(await _sync())
        assert set(body) == {"ok", "synced", "skipped", "unreadable", "scanned", "message"}
        for key in ("synced", "skipped", "unreadable"):
            assert body[key] == []

    def test_the_sync_keys_are_a_subset_of_the_one_validator_table(self):
        """No dialect: the sync validates with `_AGENT_FIELD_SPECS`, like the create, update
        and file-backed PATCH paths. `source` is deliberately absent — it is STAMPED, because
        a file that named its own origin could claim `builtin`."""
        from personalclaw.dashboard.handlers.agents import (
            _AGENT_FIELD_SPECS,
            _AGENT_SYNC_KEYS,
        )

        assert set(_AGENT_SYNC_KEYS) <= set(_AGENT_FIELD_SPECS)
        assert "source" not in _AGENT_SYNC_KEYS
