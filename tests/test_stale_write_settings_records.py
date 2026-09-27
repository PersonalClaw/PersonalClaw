"""A settings save built from a stale copy never overwrites a change made since.

Seven settings routes took a WHOLE record — a model chain, a routing order, a use case's
settings file, an instance's config, an app's settings file — or a whole list inside one
(a notification rule's targets and keywords), built by the page from the copy it read. When
another tab, or the gateway itself, wrote the same data between that read and the save, the
save replaced it and the change made elsewhere was gone without a word.

Two cures, per route (`personalclaw/stale_write.py`):

* **a revision** — the read carries the record's ``revision``, the write names it in
  ``If-Match``, and a record that changed since is refused with ``409 stale_write`` (a write
  that names none gets ``428 revision_required``): the model chain, the routing order, the
  use-case settings, an instance's config, and an app's settings through both of its routes;
* **one entry per edit** — the notification rules, whose only lists (``targets``,
  ``conditions.keywords``) now change by ``{"add": name}`` / ``{"remove": name}`` applied to
  what is stored at the moment of the write, which cannot undo anyone else's change.

Each route is driven the way its page drives it — read, then save from that read — with a
second writer between the two: another tab, or the real server-side function that writes the
same store (``ensure_target``, a provider's removal, an accepted routing proposal, a channel
app saving through the SDK, an app saving its own settings).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str | None) -> dict[str, str]:
    """The header a page's save sends — or none, for a client that never read."""
    return {"If-Match": f'"{revision}"'} if revision is not None else {}


async def _error_code(resp) -> str:
    return (await resp.json())["error"]["code"]


# ── 1. PUT /api/notifications/rules — one entry per edit, no revision ─────────────────────────


@pytest.fixture
def rules_home(tmp_path, monkeypatch):
    from personalclaw import notification_rules as nr
    from personalclaw.providers import entity_routes as er

    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(nr, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    return tmp_path


@asynccontextmanager
async def _rules_client():
    from personalclaw.providers.entity_routes import register_entity_routes

    app = web.Application()
    register_entity_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


async def _rule_row(c: TestClient, key: str) -> dict:
    """The matrix row a tab paints for *key*."""
    body = await (await c.get("/api/notifications/rules")).json()
    return next(r for r in body["rules"] if r["key"] == key)


def _stored_rule(home: Path, key: str) -> dict:
    doc = json.loads((home / "entity_settings" / "notification_rules.json").read_text())
    return doc["rules"][key]


class TestNotificationRulesChangeOneEntryPerEdit:
    @pytest.mark.asyncio
    async def test_two_tabs_ticking_different_targets_keep_both(self, rules_home) -> None:
        async with _rules_client() as c:
            painted = await _rule_row(c, "cron/result")  # both tabs paint this row
            assert painted["targets"] == ["dashboard"]
            for target in ("native", "push"):
                resp = await c.put(
                    "/api/notifications/rules",
                    json={"rules": {"cron/result": {"targets": {"add": target}}}},
                )
                assert resp.status == 200, await resp.text()
            assert (await _rule_row(c, "cron/result"))["targets"] == ["dashboard", "native", "push"]
        assert _stored_rule(rules_home, "cron/result")["targets"] == ["dashboard", "native", "push"]

    @pytest.mark.asyncio
    async def test_the_phone_turning_push_on_survives_a_tab_opened_before_it(
        self, rules_home
    ) -> None:
        from personalclaw import notification_rules as nr

        async with _rules_client() as c:
            painted = await _rule_row(c, "approval/requested")  # the tab paints [dashboard]
            assert painted["configured"] is False and painted["targets"] == ["dashboard"]

            # Settings → Notifications is open; the phone taps "Turn on push" — the real writer.
            assert nr.ensure_target("approval", "requested", "push") is True

            # The tab ticks Desktop: one target in, applied to what is stored now.
            resp = await c.put(
                "/api/notifications/rules",
                json={"rules": {"approval/requested": {"targets": {"add": "native"}}}},
            )
            assert resp.status == 200, await resp.text()
        assert nr.resolve_rule("approval", "requested").targets == ("dashboard", "push", "native")

    @pytest.mark.asyncio
    async def test_removing_a_target_leaves_the_others_another_tab_added(self, rules_home) -> None:
        async with _rules_client() as c:
            for target in ("native", "push"):
                await c.put(
                    "/api/notifications/rules",
                    json={"rules": {"cron/result": {"targets": {"add": target}}}},
                )
            resp = await c.put(
                "/api/notifications/rules",
                json={"rules": {"cron/result": {"targets": {"remove": "native"}}}},
            )
            assert resp.status == 200
            # Removing what is already gone is the same outcome, not an error.
            again = await c.put(
                "/api/notifications/rules",
                json={"rules": {"cron/result": {"targets": {"remove": "native"}}}},
            )
            assert again.status == 200
        assert _stored_rule(rules_home, "cron/result")["targets"] == ["dashboard", "push"]

    @pytest.mark.asyncio
    async def test_removing_the_last_target_keeps_the_dashboard(self, rules_home) -> None:
        async with _rules_client() as c:
            resp = await c.put(
                "/api/notifications/rules",
                json={"rules": {"cron/result": {"targets": {"remove": "dashboard"}}}},
            )
            assert resp.status == 200
        assert _stored_rule(rules_home, "cron/result")["targets"] == ["dashboard"]

    @pytest.mark.asyncio
    async def test_a_keyword_and_the_name_mention_toggle_keep_each_others_change(
        self, rules_home
    ) -> None:
        async with _rules_client() as c:
            painted = await _rule_row(c, "inbox/alert")
            assert painted["conditions"] == {"keywords": [], "name_mention": False}
            # Tab A adds a keyword; tab B, from the same paint, turns name mention on.
            first = await c.put(
                "/api/notifications/rules",
                json={"rules": {"inbox/alert": {"conditions": {"keywords": {"add": "deploy"}}}}},
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                "/api/notifications/rules",
                json={"rules": {"inbox/alert": {"conditions": {"name_mention": True}}}},
            )
            assert second.status == 200
            row = await _rule_row(c, "inbox/alert")
        assert row["conditions"] == {"keywords": ["deploy"], "name_mention": True}

    @pytest.mark.asyncio
    async def test_a_keyword_is_added_and_removed_as_the_matrix_shows_it(self, rules_home) -> None:
        async with _rules_client() as c:
            for kw in ("deploy", " deploy ", "prod"):
                await c.put(
                    "/api/notifications/rules",
                    json={"rules": {"inbox/alert": {"conditions": {"keywords": {"add": kw}}}}},
                )
            await c.put(
                "/api/notifications/rules",
                json={"rules": {"inbox/alert": {"conditions": {"keywords": {"remove": "deploy"}}}}},
            )
            row = await _rule_row(c, "inbox/alert")
        assert row["conditions"]["keywords"] == ["prod"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "rule",
        [
            {"targets": ["dashboard", "native"]},
            {"conditions": {"keywords": ["deploy"]}},
            {"targets": {"add": "native", "remove": "push"}},
            {"targets": {"add": "hologram"}},
            {"conditions": {"keywords": {"add": "  "}}},
        ],
        ids=["whole-targets", "whole-keywords", "two-edits", "unknown-target", "blank-keyword"],
    )
    async def test_a_whole_list_or_a_malformed_edit_is_refused(self, rules_home, rule) -> None:
        from personalclaw import notification_rules as nr

        nr.save_rules({"rules": {"cron/result": {"targets": ["dashboard", "push"]}}})
        before = (rules_home / "entity_settings" / "notification_rules.json").read_bytes()
        async with _rules_client() as c:
            resp = await c.put("/api/notifications/rules", json={"rules": {"cron/result": rule}})
            assert resp.status == 400, await resp.text()
            assert await _error_code(resp) == "invalid_request"
        after = (rules_home / "entity_settings" / "notification_rules.json").read_bytes()
        assert after == before, "a refused edit moved the rules file"


# ── 2. PUT /api/models/active/{use_case} — the chain carries a revision ───────────────────────


@pytest.fixture
def chain_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.providers import use_cases as uc

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1", "p2", "p3"})
    uc.save_active_models({"chat": ["p1:m1", "p2:m2"]})
    return tmp_path


@asynccontextmanager
async def _models_client():
    from personalclaw.dashboard.handlers.model_registry import (
        api_models_active,
        api_models_active_set,
    )

    app = web.Application()
    app.router.add_get("/api/models/active", api_models_active)
    app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
    async with TestClient(TestServer(app)) as client:
        yield client


async def _read_chain(c: TestClient, use_case: str) -> tuple[list[str], str]:
    body = await (await c.get("/api/models/active")).json()
    return body["use_cases"][use_case], body["revisions"][use_case]


async def _save_chain(c: TestClient, use_case: str, models: list[str], base: str | None):
    return await c.put(
        f"/api/models/active/{use_case}", json={"models": models}, headers=_based_on(base)
    )


def _stored_chain(use_case: str) -> list[str]:
    from personalclaw.providers.use_cases import load_active_models

    return load_active_models().get(use_case, [])


class TestTheModelChainIsWrittenOverTheCopyItWasBuiltFrom:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, chain_home) -> None:
        async with _models_client() as c:
            chain, base = await _read_chain(c, "chat")  # both tabs paint [p1, p2]
            assert base == revision_of(chain)
            first = await _save_chain(c, "chat", [*chain, "p3:m3"], base)  # tab A appends
            assert first.status == 200, await first.text()
            second = await _save_chain(c, "chat", chain[::-1], base)  # tab B reorders
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "the chat model chain" in err["message"]
        assert _stored_chain("chat") == ["p1:m1", "p2:m2", "p3:m3"]

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_base_is_refused(self, chain_home) -> None:
        async with _models_client() as c:
            resp = await _save_chain(c, "chat", ["p3:m3"], None)
            assert resp.status == 428
            assert await _error_code(resp) == "revision_required"
        assert _stored_chain("chat") == ["p1:m1", "p2:m2"]

    @pytest.mark.asyncio
    async def test_a_providers_removal_between_read_and_save_is_not_undone(
        self, chain_home
    ) -> None:
        from personalclaw.dashboard.handlers.providers import _drop_provider_active_models

        async with _models_client() as c:
            chain, base = await _read_chain(c, "chat")  # the panel paints [p1, p2]
            # Settings → Providers removes p2 while the Models panel is open — the real prune.
            _drop_provider_active_models("p2")
            assert _stored_chain("chat") == ["p1:m1"]
            resp = await _save_chain(c, "chat", chain[::-1], base)  # the panel's reorder
            assert resp.status == 409
            assert await _error_code(resp) == "stale_write"
        assert _stored_chain("chat") == ["p1:m1"], "the removed provider came back"

    @pytest.mark.asyncio
    async def test_the_response_carries_the_new_revision(self, chain_home) -> None:
        async with _models_client() as c:
            chain, base = await _read_chain(c, "chat")
            body = await (await _save_chain(c, "chat", chain[::-1], base)).json()
            assert body["revision"] == revision_of(body["models"])
            again = await _save_chain(c, "chat", chain, body["revision"])
            assert again.status == 200


# ── 3. PUT /api/models/routing-policy — a class's order carries a revision ────────────────────


_UC, _QC = "reasoning", "summarize"


@pytest.fixture
def routing_home(tmp_path, monkeypatch):
    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader
    from personalclaw.routing import policy, proposals

    monkeypatch.setattr(config_pkg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(proposals, "_default_home", lambda: tmp_path)
    assert policy._default_home() == tmp_path, "the fixture did not redirect the policy home"
    policy.set_order(_UC, _QC, ["p:a", "p:b"], home=tmp_path)
    return tmp_path


@asynccontextmanager
async def _routing_client():
    from personalclaw.dashboard.handlers.model_telemetry import register_model_telemetry_routes

    app = web.Application()
    register_model_telemetry_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


async def _read_cell(c: TestClient, use_case: str, query_class: str) -> tuple[list[str], str]:
    body = await (await c.get("/api/models/routing-policy")).json()
    row = next(r for r in body["use_cases"] if r["use_case"] == use_case)
    order = (row["classes"].get(query_class) or {}).get("order", [])
    return order, row["order_revisions"][query_class]


async def _save_order(c: TestClient, order: list[str], base: str | None, qc: str = _QC):
    return await c.put(
        "/api/models/routing-policy",
        json={"use_case": _UC, "query_class": qc, "order": order},
        headers=_based_on(base),
    )


def _stored_order(query_class: str = _QC) -> list[str]:
    from personalclaw.routing import policy

    return policy.table_order(_UC, query_class)


class TestARoutingOrderIsWrittenOverTheCopyItWasBuiltFrom:
    @pytest.mark.asyncio
    async def test_the_second_reorder_from_the_same_base_is_refused(self, routing_home) -> None:
        async with _routing_client() as c:
            order, base = await _read_cell(c, _UC, _QC)
            assert order == ["p:a", "p:b"]
            first = await _save_order(c, ["p:b", "p:a"], base)
            assert first.status == 200, await first.text()
            second = await _save_order(c, ["p:a", "p:b", "p:c"], base)
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "the reasoning routing order for summarize requests" in err["message"]
        assert _stored_order() == ["p:b", "p:a"]

    @pytest.mark.asyncio
    async def test_an_order_that_names_no_base_is_refused_and_a_mode_needs_none(
        self, routing_home
    ) -> None:
        from personalclaw.routing import policy

        async with _routing_client() as c:
            resp = await _save_order(c, ["p:b", "p:a"], None)
            assert resp.status == 428
            assert await _error_code(resp) == "revision_required"
            # Mode and pin are single values: one written over another is the user's own edit.
            mode = await c.put(
                "/api/models/routing-policy", json={"use_case": _UC, "mode": "learned"}
            )
            assert mode.status == 200
        assert _stored_order() == ["p:a", "p:b"]
        assert policy.mode_for(_UC) == "learned"

    @pytest.mark.asyncio
    async def test_a_class_with_no_recorded_order_has_a_revision_too(self, routing_home) -> None:
        async with _routing_client() as c:
            order, base = await _read_cell(c, _UC, "long_reasoning")
            assert order == []
            assert (await _save_order(c, ["p:a"], base, qc="long_reasoning")).status == 200
            assert (await _save_order(c, ["p:b"], base, qc="long_reasoning")).status == 409
        assert _stored_order("long_reasoning") == ["p:a"]

    @pytest.mark.asyncio
    async def test_an_accepted_proposal_between_read_and_save_is_not_undone(
        self, routing_home
    ) -> None:
        from personalclaw.routing import policy, proposals

        # A proposal may only land on a cell whose order a person has not set by hand.
        policy.set_order(_UC, _QC, ["p:a", "p:b"], home=routing_home, basis={"source": "heuristic"})
        async with _routing_client() as c:
            order, base = await _read_cell(c, _UC, _QC)  # the tab paints [a, b]
            prop = proposals.propose(
                use_case=_UC,
                query_class=_QC,
                current=["p:a", "p:b"],
                proposed=["p:b", "p:a"],
                evidence={"n": 12},
                home=routing_home,
            )
            assert prop is not None
            # Accepted from the proposals queue — the real writer — while the table is open.
            assert proposals.accept(prop.id, home=routing_home) is True
            resp = await _save_order(c, [*order, "p:c"], base)
            assert resp.status == 409
            assert await _error_code(resp) == "stale_write"
        assert _stored_order() == ["p:b", "p:a"]
        assert policy.order_basis(_UC, _QC)["proposal_id"] == prop.id

    @pytest.mark.asyncio
    async def test_the_response_carries_the_cells_new_revision(self, routing_home) -> None:
        async with _routing_client() as c:
            _, base = await _read_cell(c, _UC, _QC)
            body = await (await _save_order(c, ["p:b", "p:a"], base)).json()
            _, fresh = await _read_cell(c, _UC, _QC)
            assert body["order_revision"] == fresh != base
            assert (await _save_order(c, ["p:a", "p:b"], body["order_revision"])).status == 200


# ── 4. PUT /api/models/use-cases/{use_case}/settings — the file carries a revision ────────────


@pytest.fixture
def settings_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.providers.use_cases import save_use_case_settings

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    save_use_case_settings("tts", {"enabled": True, "speed": 1.0})
    return tmp_path


@asynccontextmanager
async def _instance_routes_client():
    from personalclaw.providers.instance_routes import register_instance_routes

    app = web.Application()
    register_instance_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


async def _read_settings(c: TestClient, use_case: str) -> tuple[dict, str]:
    body = await (await c.get(f"/api/models/use-cases/{use_case}/settings")).json()
    return body["settings"], body["revision"]


async def _save_settings(c: TestClient, use_case: str, settings: dict, base: str | None):
    return await c.put(
        f"/api/models/use-cases/{use_case}/settings", json=settings, headers=_based_on(base)
    )


class TestUseCaseSettingsAreWrittenOverTheCopyTheyWereBuiltFrom:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, settings_home) -> None:
        from personalclaw.providers.use_cases import load_use_case_settings

        async with _instance_routes_client() as c:
            settings, base = await _read_settings(c, "tts")
            assert base == revision_of(settings)
            first = await _save_settings(c, "tts", {**settings, "speed": 1.4}, base)
            assert first.status == 200, await first.text()
            second = await _save_settings(c, "tts", {**settings, "enabled": False}, base)
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "the tts settings" in err["message"]
        assert load_use_case_settings("tts") == {"enabled": True, "speed": 1.4}

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_base_is_refused(self, settings_home) -> None:
        from personalclaw.providers.use_cases import load_use_case_settings

        async with _instance_routes_client() as c:
            resp = await _save_settings(c, "tts", {"enabled": False}, None)
            assert resp.status == 428
            assert await _error_code(resp) == "revision_required"
        assert load_use_case_settings("tts") == {"enabled": True, "speed": 1.0}

    @pytest.mark.asyncio
    async def test_a_channel_apps_save_between_read_and_save_is_not_undone(
        self, settings_home
    ) -> None:
        from personalclaw.providers.use_cases import load_use_case_settings
        from personalclaw.sdk.channel import save_use_case_settings

        async with _instance_routes_client() as c:
            settings, base = await _read_settings(c, "tts")  # Settings → Voice is open
            # The Slack voice modal saves through the SDK — the real writer of this file.
            save_use_case_settings("tts", {**settings, "speech_voice": "nova"})
            resp = await _save_settings(c, "tts", {**settings, "speed": 1.2}, base)
            assert resp.status == 409
            assert await _error_code(resp) == "stale_write"
        assert load_use_case_settings("tts")["speech_voice"] == "nova"

    @pytest.mark.asyncio
    async def test_the_response_carries_the_new_revision(self, settings_home) -> None:
        async with _instance_routes_client() as c:
            settings, base = await _read_settings(c, "tts")
            body = await (await _save_settings(c, "tts", {**settings, "speed": 1.1}, base)).json()
            assert body["revision"] == revision_of(body["settings"])
            again = await _save_settings(c, "tts", {**settings, "speed": 1.3}, body["revision"])
            assert again.status == 200


# ── 5. PUT /api/providers/{name}/instances/{id} — an instance's config carries a revision ─────


_INSTANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string", "x-meta": {"label": "API Key", "sensitive": True}},
        "default_model": {"type": "string"},
        "endpoint": {"type": "string"},
    },
}


class _ToolsConfig:
    type = "tool"
    entity = ""
    capabilities: list[str] = []
    multiInstance = True
    settingsSchema = _INSTANCE_SCHEMA


class _ToolsExt:
    name = "fake-tools"
    enabled = False  # keeps the provider re-cycle out of the write path
    error = ""
    provider_config = _ToolsConfig()


class _ToolsRegistry:
    def get(self, name):
        return _ToolsExt() if name == "fake-tools" else None

    def disable(self, name):  # pragma: no cover - not reached with enabled=False
        return None

    def enable(self, name):
        return True


@pytest.fixture
def instance_home(tmp_path):
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.registry.get_provider_registry", lambda: _ToolsRegistry()),
        patch("personalclaw.agent.agents_dir", lambda: tmp_path / "agents"),
    ):
        yield tmp_path


_INSTANCES = "/api/providers/fake-tools/instances"


async def _create_instance(c: TestClient) -> str:
    resp = await c.post(
        _INSTANCES,
        json={
            "display_name": "Primary",
            "config": {
                "api_key": "fake-key-fixture-not-real",
                "default_model": "m1",
                "endpoint": "e1",
            },
        },
    )
    assert resp.status == 201, await resp.text()  # a create names no revision
    return (await resp.json())["instance"]["id"]


async def _read_instance(c: TestClient, instance_id: str) -> tuple[dict, str]:
    body = await (await c.get(_INSTANCES)).json()
    inst = next(i for i in body["instances"] if i["id"] == instance_id)
    return inst["config"], inst["revision"]


async def _save_instance(c: TestClient, instance_id: str, config: dict, base: str | None):
    return await c.put(
        f"{_INSTANCES}/{instance_id}", json={"config": config}, headers=_based_on(base)
    )


def _stored_instance_config(home: Path, instance_id: str) -> dict:
    path = home / "extensions" / "fake-tools" / "instances" / f"{instance_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["config"]


class TestAnInstanceIsWrittenOverTheCopyItWasBuiltFrom:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, instance_home) -> None:
        async with _instance_routes_client() as c:
            instance_id = await _create_instance(c)
            config, base = await _read_instance(c, instance_id)
            assert base == revision_of(config), "the revision is of the masked config beside it"
            first = await _save_instance(c, instance_id, {**config, "default_model": "m2"}, base)
            assert first.status == 200, await first.text()
            second = await _save_instance(c, instance_id, {**config, "endpoint": "e2"}, base)
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert f"the settings of instance {instance_id!r}" in err["message"]
        stored = _stored_instance_config(instance_home, instance_id)
        assert (stored["default_model"], stored["endpoint"]) == ("m2", "e1")

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_base_is_refused(self, instance_home) -> None:
        async with _instance_routes_client() as c:
            instance_id = await _create_instance(c)
            config, _ = await _read_instance(c, instance_id)
            resp = await _save_instance(c, instance_id, {**config, "endpoint": "e9"}, None)
            assert resp.status == 428
            assert await _error_code(resp) == "revision_required"
        assert _stored_instance_config(instance_home, instance_id)["endpoint"] == "e1"

    @pytest.mark.asyncio
    async def test_the_response_carries_the_new_revision_and_keeps_the_secret(
        self, instance_home
    ) -> None:
        async with _instance_routes_client() as c:
            instance_id = await _create_instance(c)
            config, base = await _read_instance(c, instance_id)
            resp = await _save_instance(c, instance_id, {**config, "default_model": "m2"}, base)
            inst = (await resp.json())["instance"]
            assert inst["revision"] == revision_of(inst["config"])
            assert "fake-key-fixture-not-real" not in json.dumps(
                inst
            ), "a revision response leaked it"
            again = await _save_instance(c, instance_id, inst["config"], inst["revision"])
            assert again.status == 200


# ── 6/7. The app's settings file — PATCH /api/providers/{name}/config, PUT /api/apps/{name}/config


_APP = "stale-probe"
_APP_SCHEMA = {
    "type": "object",
    "properties": {"room": {"type": "string"}, "greeting": {"type": "string"}},
}


class _AppProviderConfig:
    type = "task"
    entity = ""
    capabilities: list[str] = []
    multiInstance = False
    settingsSchema = _APP_SCHEMA


class _AppExt:
    name = _APP
    enabled = False  # keeps the provider rebuild out of the write path
    error = ""
    provider_config = _AppProviderConfig()


class _AppRegistry:
    def get(self, name):
        return _AppExt() if name == _APP else None


@pytest.fixture
def app_home(tmp_path):
    from personalclaw.apps import manager

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        bundle = tmp_path / "apps" / _APP
        (bundle / "data").mkdir(parents=True)
        (bundle / "app.json").write_text(
            json.dumps(
                {
                    "name": _APP,
                    "version": "1.0.0",
                    "displayName": "Stale Probe",
                    "description": "fixture",
                    "setup": {"configSchema": _APP_SCHEMA},
                }
            ),
            encoding="utf-8",
        )
        from personalclaw.providers.settings import ProviderSettings

        ProviderSettings.save(_APP, {"room": "general", "greeting": "hi"})
        yield tmp_path


@asynccontextmanager
async def _app_settings_client():
    from personalclaw.dashboard.handlers.apps import api_app_config_get, api_app_config_put
    from personalclaw.providers import routes as provider_routes

    with patch.object(provider_routes, "get_provider_registry", lambda: _AppRegistry()):
        app = web.Application()
        app.router.add_get("/api/apps/{name}/config", api_app_config_get)
        app.router.add_put("/api/apps/{name}/config", api_app_config_put)
        app.router.add_get("/api/providers/{name}/config", provider_routes.handle_get_config)
        app.router.add_patch("/api/providers/{name}/config", provider_routes.handle_patch_config)
        async with TestClient(TestServer(app)) as client:
            yield client


async def _read_app_config(c: TestClient, route: str) -> tuple[dict, str]:
    body = await (await c.get(route)).json()
    return body["config"], body["revision"]


async def _save_app_config(c: TestClient, route: str, values: dict, base: str | None):
    send = c.put if route.startswith("/api/apps/") else c.patch
    return await send(route, json=values, headers=_based_on(base))


def _stored_app_config() -> dict:
    from personalclaw.providers.settings import load_stored

    return load_stored(_APP)


_APP_ROUTES = [f"/api/apps/{_APP}/config", f"/api/providers/{_APP}/config"]


class TestAnAppsSettingsAreWrittenOverTheCopyTheyWereBuiltFrom:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", _APP_ROUTES, ids=["apps-put", "providers-patch"])
    async def test_the_second_save_from_the_same_base_is_refused(self, app_home, route) -> None:
        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, route)
            assert base == revision_of(config)
            first = await _save_app_config(c, route, {**config, "room": "ops"}, base)
            assert first.status == 200, await first.text()
            second = await _save_app_config(c, route, {**config, "greeting": "hello"}, base)
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert _APP in err["message"]
        assert _stored_app_config() == {"room": "ops", "greeting": "hi"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", _APP_ROUTES, ids=["apps-put", "providers-patch"])
    async def test_a_save_that_names_no_base_is_refused(self, app_home, route) -> None:
        async with _app_settings_client() as c:
            resp = await _save_app_config(c, route, {"room": "x", "greeting": "y"}, None)
            assert resp.status == 428
            assert await _error_code(resp) == "revision_required"
        assert _stored_app_config() == {"room": "general", "greeting": "hi"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", _APP_ROUTES, ids=["apps-put", "providers-patch"])
    async def test_the_apps_own_save_between_read_and_save_is_not_undone(
        self, app_home, route
    ) -> None:
        from personalclaw.sdk.settings import ProviderSettings

        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, route)  # the form paints general/hi
            # The app saves its own settings through the SDK while the form is open.
            ProviderSettings.update(_APP, {"room": "incidents"})
            resp = await _save_app_config(c, route, {**config, "greeting": "yo"}, base)
            assert resp.status == 409
            assert await _error_code(resp) == "stale_write"
        assert _stored_app_config() == {"room": "incidents", "greeting": "hi"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", _APP_ROUTES, ids=["apps-put", "providers-patch"])
    async def test_the_response_carries_the_new_revision(self, app_home, route) -> None:
        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, route)
            body = await (await _save_app_config(c, route, {**config, "room": "ops"}, base)).json()
            assert body["revision"] == revision_of(body["config"])
            again = await _save_app_config(c, route, body["config"], body["revision"])
            assert again.status == 200

    @pytest.mark.asyncio
    async def test_one_routes_save_makes_the_others_copy_stale(self, app_home) -> None:
        """Both routes write the SAME file — so a form open on either is stale after the other."""
        apps_route, providers_route = _APP_ROUTES
        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, providers_route)  # Settings → Providers
            _, apps_base = await _read_app_config(c, apps_route)  # Apps → Configure
            assert (
                await _save_app_config(c, apps_route, {**config, "room": "a"}, apps_base)
            ).status == 200
            resp = await _save_app_config(c, providers_route, {**config, "greeting": "b"}, base)
            assert resp.status == 409
        assert _stored_app_config() == {"room": "a", "greeting": "hi"}
