"""Tests for PATCH /api/config/personalclaw validators (enum, int, float, bool, str)."""

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _make_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


def _seed_config() -> dict:
    return {
        "agents": {
            "personalclaw": {
                "provider_agent": "personalclaw",
                "workspace": "default",
                "memory_store": "default",
            }
        },
        "default_agent": "personalclaw",
        "session": {"pool_agent": "", "timeout_secs": 3600, "autocompact_pct": 50.0},
        "agent": {"approval_mode": "auto", "sandbox": "auto"},
        "auto_update": False,
    }


@pytest.fixture
def tmp_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_seed_config()), encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=cfg_path):
        yield cfg_path


async def _patch(client, path, value):
    return await client.patch("/api/config/personalclaw", json={"path": path, "value": value})


# ── General ──────────────────────────────────────────────────────────────


class TestPatchGeneral:
    @pytest.mark.asyncio
    async def test_unknown_field_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "nonexistent.field", "x")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_json_body_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400


# ── Enum validator ───────────────────────────────────────────────────────


class TestEnumValidator:
    @pytest.mark.asyncio
    async def test_valid_enum_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", "interactive")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_invalid_enum_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", "bogus")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_enum_wrong_type_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", 123)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_nested_3part_path_writes_correctly(self, tmp_config) -> None:
        """P25: `dashboard.terminal.persist` is a 3-part nested path — the writer must
        create the intermediate `dashboard`/`terminal` objects and set the leaf, NOT
        clobber `data['dashboard']` with the bool. Guards the nested-path writer."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "dashboard.terminal.persist", True)
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text(encoding="utf-8"))
            # the leaf landed nested, and the section stayed an object (not a bool)
            assert saved["dashboard"]["terminal"]["persist"] is True
            assert isinstance(saved["dashboard"], dict)

    @pytest.mark.asyncio
    async def test_nested_3part_preserves_sibling_keys(self, tmp_config) -> None:
        """Setting the nested leaf must not drop a pre-existing sibling under the same
        parent (e.g. dashboard.terminal.enabled stays when persist is added)."""
        # seed a sibling first
        import json as _json

        data = _json.loads(tmp_config.read_text(encoding="utf-8"))
        data.setdefault("dashboard", {}).setdefault("terminal", {})["enabled"] = True
        tmp_config.write_text(_json.dumps(data), encoding="utf-8")
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "dashboard.terminal.persist", True)
            assert resp.status == 200
            saved = _json.loads(tmp_config.read_text(encoding="utf-8"))
            assert saved["dashboard"]["terminal"]["enabled"] is True  # sibling preserved
            assert saved["dashboard"]["terminal"]["persist"] is True


# ── Int validator ────────────────────────────────────────────────────────


class TestIntValidator:
    @pytest.mark.asyncio
    async def test_valid_int_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", 120)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_int_below_min_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", -1)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_int_above_max_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", 100000)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_int_non_numeric_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", "abc")
            assert resp.status == 400


# ── Float validator ──────────────────────────────────────────────────────


class TestFloatValidator:
    @pytest.mark.asyncio
    async def test_valid_float_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 25.0)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_float_below_min_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 1.0)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_above_max_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 95.0)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_nan_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", float("nan"))
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_non_numeric_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", "abc")
            assert resp.status == 400


# ── Bool validator ───────────────────────────────────────────────────────


class TestBoolValidator:
    @pytest.mark.asyncio
    async def test_valid_bool_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "auto_update", True)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_bool_non_bool_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "auto_update", "true")
            assert resp.status == 400


# ── Str validator (pool_agent) ───────────────────────────────────────────


class TestStrValidator:
    @pytest.mark.asyncio
    async def test_valid_agent_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "personalclaw")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_empty_string_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_non_string_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", 123)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_exceeds_max_len_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "a" * 257)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "nonexistent")
            assert resp.status == 400
            data = await resp.json()
            assert "invalid value" in data["error"]


# ── Egress validator (security.egress operator overrides) ──────────────────


class TestEgressValidator:
    @pytest.mark.asyncio
    async def test_valid_egress_persists(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {
                    "allow_hosts": ["nas.local"],
                    "deny_hosts": ["evil.com"],
                    "allow_private": True,
                },
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert saved == {
                "allow_hosts": ["nas.local"],
                "deny_hosts": ["evil.com"],
                "allow_private": True,
            }

    @pytest.mark.asyncio
    async def test_rejects_url_host(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {"allow_hosts": ["http://evil.com/x"], "deny_hosts": [], "allow_private": False},
            )
            assert resp.status == 400
            assert "bare domain" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_rejects_non_dict(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "security.egress", ["not", "a", "dict"])
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rejects_non_bool_private(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c, "security.egress", {"allow_hosts": [], "deny_hosts": [], "allow_private": "yes"}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_strips_unknown_keys(self, tmp_config) -> None:
        """Only the three known keys are persisted — a stray field can't be smuggled in."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {"allow_hosts": [], "deny_hosts": [], "allow_private": False, "evil": "x"},
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert "evil" not in saved


# ── Projection-rules validator (tools.projection_rules, TokenJuice OP6) ──────


class TestProjectionRulesValidator:
    def teardown_method(self):
        from personalclaw.tool_providers.projection import set_user_rules

        set_user_rules([])

    @pytest.mark.asyncio
    async def test_valid_rules_persist_and_apply_live(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {"name": "acme", "match_regex": r"^\[ACME\]", "strategy": "log"},
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"]
            assert saved == [{"name": "acme", "match_regex": r"^\[ACME\]", "strategy": "log"}]
        # Live-applied: the engine now dispatches a matching sample to 'log'.
        from personalclaw.tool_providers.projection import infer_content_type

        assert infer_content_type("[ACME] boot\nstep\n") == "log"

    @pytest.mark.asyncio
    async def test_rejects_invalid_regex(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c, "tools.projection_rules", [{"name": "x", "match_regex": "(", "strategy": "log"}]
            )
            assert resp.status == 400
            assert "regex" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_unknown_strategy(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "foo", "strategy": "nonsense"}],
            )
            assert resp.status == 400
            assert "strategy" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_non_list(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "tools.projection_rules", {"not": "a list"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_strips_unknown_keys(self, tmp_config) -> None:
        """Only name/match_regex/strategy persist — a stray field can't be smuggled in."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {"name": "acme", "match_regex": "foo", "strategy": "test", "evil": "x"},
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"][0]
            assert set(saved) == {"name", "match_regex", "strategy"}


# ── P11 engagement-ranking flag: the full config-flag thread (PATCH → config.json →
#    AppConfig.load reads it back). Guards the [[feedback_config_flag_two_maps]] footgun —
#    a flag missing from the load-map silently reads its default forever. ──


class TestEngagementRankingFlag:
    @pytest.mark.asyncio
    async def test_patch_writes_nested_inbox_flag(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "inbox.engagement_ranking_enabled", True)
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            assert saved["inbox"]["engagement_ranking_enabled"] is True

    @pytest.mark.asyncio
    async def test_patch_rejects_non_bool(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "inbox.engagement_ranking_enabled", "yes")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_half_life_float_bounds(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            assert (await _patch(c, "inbox.engagement_half_life_days", 6.5)).status == 200
            assert (await _patch(c, "inbox.engagement_half_life_days", -1.0)).status == 400
            assert (await _patch(c, "inbox.engagement_half_life_days", 999.0)).status == 400

    def test_flag_loads_from_config_json_not_just_default(self, tmp_path) -> None:
        """The load-map leg: a value in config.json must actually reach AppConfig — the
        exact gap the two-maps footgun creates (field on the dataclass but absent from
        AppConfig.load → always the default)."""
        from personalclaw.config.loader import AppConfig

        cfg = _seed_config()
        cfg["inbox"] = {
            "enabled": True,
            "engagement_ranking_enabled": True,
            "engagement_half_life_days": 3.25,
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with patch("personalclaw.config.loader.config_path", return_value=p):
            loaded = AppConfig.load()
        assert loaded.inbox.engagement_ranking_enabled is True
        assert loaded.inbox.engagement_half_life_days == 3.25
        # round-trips back out through to_dict (asdict(inbox)) too
        assert loaded.to_dict()["inbox"]["engagement_ranking_enabled"] is True


# ── agent.bot_name: sanitize at the WRITE boundary (S05 C6) — the file must
#    match what load() produces, or config.json carries markdown/braces while
#    runtime sees the stripped name (split-brain). ──


class TestBotNamePatch:
    @pytest.mark.asyncio
    async def test_sanitized_before_write(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", "**{Astra}** <script>")
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            # markdown/braces/angle brackets stripped by the loader's sanitizer
            assert saved["agent"]["bot_name"] == "Astra script"

    @pytest.mark.asyncio
    async def test_plain_name_passes_through(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", "Astra")
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            assert saved["agent"]["bot_name"] == "Astra"

    @pytest.mark.asyncio
    async def test_over_50_chars_rejected(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            assert (await _patch(c, "agent.bot_name", "x" * 51)).status == 400
