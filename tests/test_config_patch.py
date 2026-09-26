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

    # ── `path` is the SELECTOR: a missing/unusable one is a malformed request (#2926) ──
    # Every arm asserts the message NAMES the fault, because the defect was not the status
    # (already 400) but the sentence: `path` fell through to the allowlist check and produced
    # `field not editable: ` — a field name that is blank. Asserting only the status would
    # have passed before the fix, so each case pins the prose.

    @pytest.mark.asyncio
    async def test_absent_path_names_the_missing_field_not_a_blank_one(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch("/api/config/personalclaw", json={"value": 1})
            assert resp.status == 400
            error = (await resp.json())["error"]
            assert "missing required 'path'" in error
            assert "not editable" not in error

    @pytest.mark.asyncio
    async def test_empty_path_names_the_empty_selector_not_a_blank_field(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch("/api/config/personalclaw", json={"path": "", "value": 1})
            assert resp.status == 400
            error = (await resp.json())["error"]
            assert "'path' is empty" in error
            assert "not editable" not in error

    @pytest.mark.asyncio
    async def test_whitespace_only_path_is_refused_as_empty(self, tmp_config) -> None:
        # `"   "` is a name for nothing: it rendered as `field not editable:    `, which is the
        # same illegible sentence as the empty-string case with the blank harder to see.
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch("/api/config/personalclaw", json={"path": "   ", "value": 1})
            assert resp.status == 400
            assert "'path' is empty" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_non_string_path_is_refused_by_name(self, tmp_config) -> None:
        # An unhashable `path` used to raise TypeError out of `_EDITABLE_CONFIG.get()`; the
        # request-shape boundary caught it and answered a generic `bad_request`. The handler
        # owns this refusal, so it says which key was wrong.
        async with TestClient(TestServer(_make_app())) as c:
            for bad in (["agent.yolo"], {"a": 1}, 7):
                resp = await c.patch("/api/config/personalclaw", json={"path": bad, "value": 1})
                assert resp.status == 400, bad
                assert "'path' must be a string" in (await resp.json())["error"], bad

    @pytest.mark.asyncio
    async def test_unknown_field_still_names_the_field(self, tmp_config) -> None:
        # The arms above must not swallow the genuine allowlist rejection they sit in front of.
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "not.real", 1)
            assert resp.status == 400
            assert (await resp.json())["error"] == "field not editable: not.real"

    @pytest.mark.asyncio
    async def test_no_refusal_message_has_a_blank_field_name(self, tmp_config) -> None:
        # The invariant the issue actually asks for, across every malformed-`path` shape: no
        # refusal ends in a dangling separator with nothing after it.
        bodies = [{"value": 1}, {"path": "", "value": 1}, {"path": "  ", "value": 1}]
        async with TestClient(TestServer(_make_app())) as c:
            for body in bodies:
                resp = await c.patch("/api/config/personalclaw", json=body)
                error = (await resp.json())["error"]
                assert error == error.strip(), body
                assert not error.rstrip().endswith(":"), body


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
            resp = await _patch(c, "updates.check_enabled", True)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_bool_non_bool_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "updates.check_enabled", "true")
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
    # The cases that ADD an allow host or allow private addresses widen the guard, which is the
    # consented direction (`edit_spec.loosens_egress`), so they send `confirm: true` — what they
    # pin is how a valid value is stored, not the consent (tests/test_apps_cannot_relax_security).
    @pytest.mark.asyncio
    async def test_valid_egress_persists(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch_consented(
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
    @pytest.mark.parametrize("bad", ["*", "*.example.com", "example.*", "a*b.com"])
    async def test_rejects_a_wildcard_host(self, tmp_config, bad) -> None:
        """Issue 2956. These were accepted with 200 OK, persisted, and echoed back by
        `GET /api/security/egress` as configured policy — while `net.guard.host_matches`
        implements exactly one rule ("a bare domain covers its subdomains") and no glob, so
        every one of them matched NOTHING. `deny_hosts: ["*.example.com"]` therefore read as
        blocking a domain family and blocked nothing, which is strictly weaker than the bare
        form the user probably meant. The refusal names that form.
        """
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {"allow_hosts": [], "deny_hosts": [bad], "allow_private": False},
            )
            assert resp.status == 400
            err = (await resp.json())["error"]
            assert "wildcard" in err and "bare domain" in err

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["", ".", "..", "-bad.com", "bad-.com", "a..b"])
    async def test_rejects_a_host_no_host_can_ever_match(self, tmp_config, bad) -> None:
        """The other half of 2956: `host_matches` skips a pattern that normalises to nothing
        (`if not p: continue`), so `""` and `"."` were accepted, shown in the UI, written to
        `config.json` — and inert. An entry that can never match is refused, not stored."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {"allow_hosts": [bad], "deny_hosts": [], "allow_private": False},
            )
            assert resp.status == 400
            assert "can ever match" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_a_host_is_normalised_to_what_the_matcher_compares(self, tmp_config) -> None:
        """`host_matches` lowercases and strips a trailing dot before comparing, so storing the
        raw value would leave `config.json` and the Settings panel showing something other than
        what is enforced — the split-brain this module's `sanitize` hook exists to prevent."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch_consented(
                c,
                "security.egress",
                {
                    "allow_hosts": ["EXAMPLE.COM."],
                    "deny_hosts": ["Evil.COM"],
                    "allow_private": False,
                },
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert saved["allow_hosts"] == ["example.com"]
            assert saved["deny_hosts"] == ["evil.com"]

    @pytest.mark.asyncio
    async def test_an_ipv4_literal_is_still_accepted(self, tmp_config) -> None:
        """The documented homelab case. `net.guard` matches against `urlparse().hostname`, so a
        literal address is a legitimate entry and the new host rule must not close it."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch_consented(
                c,
                "security.egress",
                {"allow_hosts": ["192.168.1.5", "nas"], "deny_hosts": [], "allow_private": False},
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert saved["allow_hosts"] == ["192.168.1.5", "nas"]

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
            # The rule persists (the file may carry the full dataclass form with
            # default op fields — load()'s migration write-back serializes via asdict).
            assert saved[0]["name"] == "acme"
            assert saved[0]["match_regex"] == r"^\[ACME\]"
            assert saved[0]["strategy"] == "log"
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
        """Only allowlisted rule fields persist — a stray field can't be smuggled in."""
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
            assert "evil" not in saved
            allowed = {"name", "match_regex", "strategy", "head", "tail", "keep", "skip", "count"}
            assert set(saved) <= allowed

    @pytest.mark.asyncio
    async def test_op_fields_validate_and_persist(self, tmp_config) -> None:
        """Rule ops v2 (§2.3): head/tail ints + keep/skip/count regexes round-trip;
        a bad op regex or negative count is rejected at the boundary."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {
                        "name": "ops",
                        "match_regex": r"^\[SVC\]",
                        "strategy": "log",
                        "head": 5,
                        "tail": 3,
                        "skip": r"^DEBUG",
                        "count": r"^heartbeat",
                    },
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"][0]
            assert saved["head"] == 5 and saved["tail"] == 3
            assert saved["skip"] == r"^DEBUG" and saved["count"] == r"^heartbeat"
            # bad op regex → 400
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "ok", "strategy": "log", "keep": "("}],
            )
            assert resp.status == 400
            # negative head → 400
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "ok", "strategy": "log", "head": -1}],
            )
            assert resp.status == 400


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


# ── agent.bot_name at the WRITE boundary. The file must match what load() produces (S05 C6), and
#    the answer must match the file: this route used to answer 200 for `Chloé's Aide` with
#    `Chlos Aide` already in the body, and Settings showed "Saved" beside the typed value. ──


class TestBotNamePatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name",
        ["Zoë", "Chloé's Aide", "Chloé\u2019s Aide", "Björn", "小助手", "प्रिया", "مساعد"],
    )
    async def test_a_name_in_any_script_is_stored_as_typed(self, tmp_config, name) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", name)
            assert resp.status == 200
            assert (await resp.json())["agent"]["bot_name"] == name, "the answer is the stored name"
        assert json.loads(tmp_config.read_text())["agent"]["bot_name"] == name

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "named"),
        [
            ("**{Astra}** <script>", ["“*”", "“{”", "“}”", "“<”", "“>”"]),
            ("{{x}}", ["“{”", "“}”"]),
            ("`tick`", ["“`”"]),
            ("Bob\x07", ["U+0007"]),
            ("Bob\u202eevil", ["U+202E RIGHT-TO-LEFT OVERRIDE"]),
        ],
    )
    async def test_a_character_a_name_cannot_carry_is_refused_by_name(
        self, tmp_config, value, named
    ) -> None:
        """Refused, not stripped: dropping characters stores a name nobody typed, and the caller
        has been told it succeeded — the one outcome `config/edit_spec.py` exists to prevent."""
        before = tmp_config.read_text()
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", value)
            assert resp.status == 400
            error = (await resp.json())["error"]
        for glyph in named:
            assert glyph in error, f"the refusal must name {glyph}: {error!r}"
        assert tmp_config.read_text() == before, "a refused name must not reach the file"

    @pytest.mark.asyncio
    async def test_surrounding_whitespace_is_trimmed(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", "  Astra  ")
            assert resp.status == 200
            assert (await resp.json())["agent"]["bot_name"] == "Astra"
        assert json.loads(tmp_config.read_text())["agent"]["bot_name"] == "Astra"

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


# ── ET-4: apps.registry_source_enabled ───────────────────────────────────────


class TestAppsRegistrySource:
    """A real PATCH round-trip for the seeding flag: allowlisted → persisted → reloaded.

    The `_EDITABLE_CONFIG` entry is the wiring point `test_config_roundtrip.py` cannot see
    (it checks dataclass/load/to_dict only), and the toggle in Settings › Apps is useless
    without it — so it gets driven end to end here rather than asserted structurally."""

    @pytest.mark.asyncio
    async def test_patch_persists_and_reloads(self, tmp_config) -> None:
        from personalclaw.config.loader import AppConfig

        assert AppConfig.load().apps.registry_source_enabled is True  # shipped default
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "apps.registry_source_enabled", False)
            assert resp.status == 200, await resp.text()
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["apps"]["registry_source_enabled"] is False
        # …and the loader reads back what the PATCH wrote (not the default).
        assert AppConfig.load().apps.registry_source_enabled is False

    @pytest.mark.asyncio
    async def test_patch_rejects_a_non_bool(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "apps.registry_source_enabled", "sure")
            assert resp.status == 400
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "apps" not in raw or "registry_source_enabled" not in raw.get("apps", {})


# ── agent.log_level applies LIVE, not only at the next restart (#673) ─────────


class TestLogLevelAppliesLive:
    @pytest.mark.asyncio
    async def test_patching_log_level_sets_the_live_logger(self, tmp_config) -> None:
        import logging

        lg = logging.getLogger("personalclaw")
        prior = lg.level
        try:
            # A known baseline distinct from the target so the assertion is meaningful.
            lg.setLevel(logging.WARNING)
            async with TestClient(TestServer(_make_app())) as c:
                resp = await _patch(c, "agent.log_level", "DEBUG")
                assert resp.status == 200
            # The Agent-defaults PATCH must APPLY the level live (the Diagnostics
            # POST /api/logs/level path already did), not merely persist it.
            assert lg.level == logging.DEBUG
            # …and it still persists for the restart path.
            saved = json.loads(tmp_config.read_text(encoding="utf-8"))
            assert saved["agent"]["log_level"] == "DEBUG"
        finally:
            lg.setLevel(prior)


# ── agent.yolo applies LIVE in BOTH directions, not only at the next start (#672) ──


def _make_app_with_state(state) -> web.Application:
    app = _make_app()
    app["state"] = state
    return app


class _FakeState:
    """Just the two members the yolo seam touches — enable/disable recording."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def enable_yolo(self, *, from_config: bool = False) -> None:
        self.calls.append(("enable", from_config))

    def disable_yolo(self) -> None:
        self.calls.append(("disable",))


class TestYoloAppliesLive:
    @pytest.mark.asyncio
    async def test_turning_yolo_off_revokes_the_bypass_immediately(self, tmp_config) -> None:
        # The security-relevant direction (#672): revoking must not wait for a
        # restart, and must go through state.disable_yolo() so the trust_mode
        # on-disable callback clears untrusted per-session auto-approve policies.
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch(c, "agent.yolo", False)
            assert resp.status == 200
        assert ("disable",) in state.calls

    @pytest.mark.asyncio
    async def test_sel_down_fails_the_whole_patch_closed(self, tmp_config) -> None:
        # The endpoint's own write-audit is deliberately unguarded, so a dead
        # audit sink fails the WHOLE patch closed (500) — the yolo seam never
        # runs in either direction. This pins the ordering: the seam sits AFTER
        # the write-audit, so a future reorder cannot grant an unaudited bypass.
        # Consent is sent, so this reaches the write-audit rather than the consent refusal.
        state = _FakeState()
        with patch("personalclaw.sel.sel", side_effect=RuntimeError("sel down")):
            async with TestClient(TestServer(_make_app_with_state(state))) as c:
                resp = await _patch_consented(c, "agent.yolo", True)
                assert resp.status == 500
        assert state.calls == []

    @pytest.mark.asyncio
    async def test_turning_yolo_on_applies_live_with_startup_semantics(self, tmp_config) -> None:
        # Enable mirrors the boot seed: permanent (from_config=True), so the same
        # key means the same thing whether it was read at startup or patched live.
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch_consented(c, "agent.yolo", True)
            assert resp.status == 200
        assert ("enable", True) in state.calls
        # …and it still persists for the restart path.
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["agent"]["yolo"] is True

    @pytest.mark.asyncio
    async def test_no_state_does_not_break_the_config_write(self, tmp_config) -> None:
        # The seam degrades to persist-only when no dashboard state is attached
        # (test apps, early startup) — never a 500 on the config write itself.
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch_consented(c, "agent.yolo", True)
            assert resp.status == 200
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["agent"]["yolo"] is True


async def _patch_consented(client, path, value):
    return await client.patch(
        "/api/config/personalclaw", json={"path": path, "value": value, "confirm": True}
    )


# ── Turning YOLO ON needs the owner's consent ON THE WIRE, not only in a dialog ──
#
# The Settings hub's tile switch PATCHed `agent.yolo: true` one click and ~41 ms after the
# click, while the Agent defaults panel one click away asked first. The dialog was the only
# gate, so the second writer skipped it by not knowing it existed. The core now refuses the
# relaxing direction without `confirm: true` — so a writer that forgets the dialog fails
# loudly instead of silently turning every tool-approval confirmation off.


class TestYoloOnNeedsConsent:
    @pytest.mark.asyncio
    async def test_yolo_on_without_consent_is_refused_and_nothing_changes(self, tmp_config) -> None:
        state = _FakeState()
        before = tmp_config.read_text(encoding="utf-8")
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch(c, "agent.yolo", True)
            assert resp.status == 400
            body = await resp.json()
        assert body["error"]["code"] == "confirmation_required"
        # The sentence names what is being consented to, not just the flag.
        assert "tool-approval" in body["error"]["message"]
        assert tmp_config.read_text(encoding="utf-8") == before, "nothing may be written"
        assert state.calls == [], "and the bypass must not go live"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("consent", ["true", 1, "yes", {"ok": True}, False, None])
    async def test_only_the_json_literal_true_is_consent(self, tmp_config, consent) -> None:
        # `safety_flags.confirm_granted`'s rule, the one every consent door shares: a stringified
        # or truthy value is not a yes (`confirm: "false"` once deleted data at HTTP 200).
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "agent.yolo", "value": True, "confirm": consent},
            )
            assert resp.status == 400
        assert state.calls == []

    @pytest.mark.asyncio
    async def test_yolo_on_with_consent_is_written_and_goes_live(self, tmp_config) -> None:
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch_consented(c, "agent.yolo", True)
            assert resp.status == 200
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["agent"]["yolo"] is True
        assert ("enable", True) in state.calls

    @pytest.mark.asyncio
    async def test_yolo_off_never_needs_consent(self, tmp_config) -> None:
        # Revoking the bypass is the direction a broken or confused client must always be able
        # to take — a consent requirement there would be a lock on the emergency exit.
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch(c, "agent.yolo", False)
            assert resp.status == 200
        assert ("disable",) in state.calls


# ── `updates.pin` must be a release VERSION, refused at write time otherwise ──
#
# The resolvers match a pin EXACTLY against the release tags, so a pin shaped like anything
# else can never name a release — and storing one silently stopped every update: the check
# reported nothing available and every apply refused (a pin-miss never falls back to latest).


class TestVersionPinShape:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        ["not-a-version!!", "0.2", "0.2.x", ">=0.2", "latest", "0.3.0rc1", "v", "0.1.3 extra"],
    )
    async def test_a_pin_that_can_never_name_a_release_is_refused(self, tmp_config, bad) -> None:
        before = tmp_config.read_text(encoding="utf-8")
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "updates.pin", bad)
            assert resp.status == 400
            error = (await resp.json())["error"]
        # The refusal teaches the accepted shape with a release that exists.
        assert "not a release version" in error
        assert "0.1.3" in error
        assert tmp_config.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sent, stored",
        [
            ("0.1.3", "0.1.3"),
            # Well-shaped but unpublished: storable — only the network knows it names nothing,
            # and the update check reports that as `pin_miss` instead.
            ("0.2.1", "0.2.1"),
            ("v0.1.3", "0.1.3"),  # the resolvers' spelling
            ("  0.1.3  ", "0.1.3"),
            ("0.3.0-rc.1", "0.3.0-rc.1"),
            ("", ""),  # clearing the pin — back to following the channel
        ],
    )
    async def test_a_release_version_is_stored_in_the_resolvers_spelling(
        self, tmp_config, sent, stored
    ) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "updates.pin", sent)
            assert resp.status == 200, await resp.text()
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["updates"]["pin"] == stored
