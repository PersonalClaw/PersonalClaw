"""A save from a stale page is refused instead of overwriting a change made elsewhere.

A settings surface that saves a WHOLE list builds it from the copy it read. Two tabs read the same
list; tab A saves a change; tab B — still holding the old copy — saves a change of its own, and on
the old code that second save replaced the list, erasing tab A's change without a word. The same
happened when the gateway itself wrote the data between a page's read and its save.

The contract (`personalclaw/stale_write.py`): a read carries the document's ``revision``, a
whole-document write names its base in ``If-Match``, and a stale base is refused with
``409 stale_write`` before anything is written. Where the write can be a per-item operation — one
name into or out of an allowlist — it is one, and needs no revision at all.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


RULE_A = {"name": "a", "match_regex": "^\\[A\\]", "strategy": "log"}
RULE_B = {"name": "b", "match_regex": "^\\[B\\]", "strategy": "diff"}
RULE_C = {"name": "c", "match_regex": "^\\[C\\]", "strategy": "json"}


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_personalclaw_config,
        api_personalclaw_config_patch,
        api_security_egress,
    )

    app = web.Application()
    app.router.add_get("/api/config/personalclaw", api_personalclaw_config)
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    app.router.add_get("/api/security/egress", api_security_egress)
    return app


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"tools": {"projection_rules": [RULE_A]}, "agent": {"approval_mode": "auto"}}),
        encoding="utf-8",
    )
    with patch("personalclaw.config.loader.config_path", return_value=cfg):
        yield cfg


def _stored(cfg, *path):
    node = json.loads(cfg.read_text(encoding="utf-8"))
    for part in path:
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node


async def _read(c: TestClient, path: str):
    """What a tab paints: the document at *path* and the revision the same read reported."""
    body = await (await c.get("/api/config/personalclaw")).json()
    node = body
    for part in path.split("."):
        node = node[part]
    return node, body["revisions"][path]


async def _save(c: TestClient, path: str, value, base: str | None, **extra):
    headers = {"If-Match": f'"{base}"'} if base is not None else {}
    return await c.patch(
        "/api/config/personalclaw", json={"path": path, "value": value, **extra}, headers=headers
    )


def _rule_names(rules) -> list[str]:
    return [r["name"] for r in rules]


class TestRevisionOf:
    def test_key_order_does_not_change_it(self) -> None:
        assert revision_of({"a": 1, "b": [1, 2]}) == revision_of({"b": [1, 2], "a": 1})

    def test_any_value_change_does(self) -> None:
        assert revision_of([RULE_A]) != revision_of([RULE_A, RULE_B])
        assert revision_of("text") != revision_of("text ")

    def test_a_lone_surrogate_still_has_one(self) -> None:
        assert len(revision_of({"x": "\ud800"})) == 16


class TestTwoTabsOneList:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")  # both tabs paint this
            tab_a = [*rules, RULE_B]
            tab_b = [*rules, RULE_C]

            first = await _save(c, "tools.projection_rules", tab_a, base)
            assert first.status == 200

            second = await _save(c, "tools.projection_rules", tab_b, base)
            assert second.status == 409
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "replaces tools.projection_rules, which changed after" in err["message"]
            # The refusal hands out no revision: only a read of the document does.
            assert "revision" not in json.dumps(err)

        # Tab A's rule survived; tab B's copy was never written over it.
        assert _rule_names(_stored(config_file, "tools", "projection_rules")) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_reread_and_reapplied_the_save_lands(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")
            assert (await _save(c, "tools.projection_rules", [*rules, RULE_B], base)).status == 200
            assert (await _save(c, "tools.projection_rules", [*rules, RULE_C], base)).status == 409
            # What "Reload and reapply" does: read again, put the change on top, save over that.
            latest, fresh = await _read(c, "tools.projection_rules")
            assert fresh != base
            again = await _save(c, "tools.projection_rules", [*latest, RULE_C], fresh)
            assert again.status == 200
        assert _rule_names(_stored(config_file, "tools", "projection_rules")) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_a_write_that_names_no_base_is_refused(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            resp = await _save(c, "tools.projection_rules", [RULE_C], None)
            assert resp.status == 428
            assert (await resp.json())["error"]["code"] == "revision_required"
        assert _rule_names(_stored(config_file, "tools", "projection_rules")) == ["a"]

    @pytest.mark.asyncio
    async def test_the_quoted_and_weak_forms_of_the_header_both_match(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "tools.projection_rules", "value": [*rules, RULE_B]},
                headers={"If-Match": f'W/"{base}"'},
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_a_scalar_write_needs_no_base(self, config_file) -> None:
        # Only a DOCUMENT is guarded: one scalar written over another is the edit the user made.
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "agent.approval_mode", "value": "interactive"},
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_the_write_response_carries_the_new_revision(self, config_file) -> None:
        # A page that stays open saves again from the response, not from its first read.
        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")
            body = await (await _save(c, "tools.projection_rules", [*rules, RULE_B], base)).json()
            nxt = body["revisions"]["tools.projection_rules"]
            assert nxt == revision_of(body["tools"]["projection_rules"])
            assert (await _save(c, "tools.projection_rules", [RULE_A], nxt)).status == 200


class TestTheGatewayWritingTheSameData:
    @pytest.mark.asyncio
    async def test_a_server_side_write_between_read_and_save_is_not_undone(
        self, config_file
    ) -> None:
        from personalclaw.config.loader import AppConfig, ProjectionRuleConfig

        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")  # the page paints [a]

            # The gateway writes the same list itself — the load → mutate → save shape its own
            # config writers use (`AppConfig.save`), while the page is open.
            cfg = AppConfig.load()
            cfg.tools.projection_rules.append(ProjectionRuleConfig(**RULE_B))
            cfg.save()

            resp = await _save(c, "tools.projection_rules", [*rules, RULE_C], base)
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "stale_write"
        assert _rule_names(_stored(config_file, "tools", "projection_rules")) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_a_writer_holding_the_config_lock_is_seen_by_the_check(self, config_file) -> None:
        """The check runs INSIDE the config transaction: a save sent while another writer — the
        CLI, a second process — holds config.json's lock waits for it, then compares against what
        that writer stored. Checked before the lock, it passed against the older list and wrote
        over the other writer's, which put its own copy back after — both answered success."""
        import asyncio
        import threading

        from personalclaw.config.transactions import mutate_config

        holding, release = threading.Event(), threading.Event()

        def other_writer() -> None:
            def change(document: dict) -> None:
                document.setdefault("tools", {})["projection_rules"] = [RULE_A, RULE_B]
                holding.set()
                assert release.wait(10), "the test never let the other writer finish"

            mutate_config(change)

        async with TestClient(TestServer(_app())) as c:
            rules, base = await _read(c, "tools.projection_rules")  # the page paints [a]
            writer = threading.Thread(target=other_writer)
            writer.start()
            assert await asyncio.to_thread(holding.wait, 10), "the other writer never took the lock"
            save = asyncio.create_task(_save(c, "tools.projection_rules", [*rules, RULE_C], base))
            await asyncio.sleep(0.3)  # the save is waiting for the lock
            assert not save.done(), "the save did not wait for the writer holding the lock"
            release.set()
            resp = await save
            await asyncio.to_thread(writer.join, 10)
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "stale_write"
        assert _rule_names(_stored(config_file, "tools", "projection_rules")) == ["a", "b"]


class TestOneNameInOrOut:
    @pytest.mark.asyncio
    async def test_two_tabs_granting_different_servers_keep_both(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            for server in ("alpha", "beta"):  # each tab painted the same empty list
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={
                        "path": "security.mcp_elicitation_servers",
                        "add": server,
                        "confirm": True,
                    },
                )
                assert resp.status == 200
        assert _stored(config_file, "security", "mcp_elicitation_servers") == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_a_revoke_from_a_stale_tab_removes_only_that_server(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            for server in ("alpha", "beta"):
                await c.patch(
                    "/api/config/personalclaw",
                    json={
                        "path": "security.mcp_elicitation_servers",
                        "add": server,
                        "confirm": True,
                    },
                )
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.mcp_elicitation_servers", "remove": "alpha"},
            )
            assert resp.status == 200
            # Removing again is the same outcome, not an error.
            again = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.mcp_elicitation_servers", "remove": "alpha"},
            )
            assert again.status == 200
        assert _stored(config_file, "security", "mcp_elicitation_servers") == ["beta"]

    @pytest.mark.asyncio
    async def test_places_outside_the_home_are_allowed_and_revoked_one_at_a_time(
        self, config_file
    ) -> None:
        # Settings → Security → Outside PersonalClaw's home: each switch is one place. Two tabs
        # painted the same empty list and each allowed a different place; then one tab, still on
        # its old copy, turned its own place off. The other tab's place stays allowed.
        async with TestClient(TestServer(_app())) as c:
            for place in ("agent-skills", "huggingface-cache"):
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "security.outside_home", "add": place, "confirm": True},
                )
                assert resp.status == 200
            off = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.outside_home", "remove": "agent-skills"},
            )
            assert off.status == 200
            # Allowing one still asks the owner first.
            unasked = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.outside_home", "add": "agent-skills"},
            )
            assert unasked.status == 400
            assert (await unasked.json())["error"]["code"] == "confirmation_required"
        assert _stored(config_file, "security", "outside_home") == ["huggingface-cache"]

    @pytest.mark.asyncio
    async def test_adding_a_grant_still_needs_consent(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.mcp_elicitation_servers", "add": "alpha"},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "confirmation_required"
        assert _stored(config_file, "security", "mcp_elicitation_servers") == {}

    @pytest.mark.asyncio
    async def test_removing_a_denied_pattern_still_needs_consent(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            ok = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.denied_commands", "add": "rm .*"},
            )
            assert ok.status == 200
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "security.denied_commands", "remove": "rm .*"},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "confirmation_required"
        assert _stored(config_file, "security", "denied_commands") == ["rm .*"]

    @pytest.mark.asyncio
    async def test_an_item_is_validated_as_the_list_would_be(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw", json={"path": "security.denied_commands", "add": "(["}
            )
            assert resp.status == 400
            assert "invalid regex" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_add_on_a_field_that_is_not_a_list_of_names_is_refused(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw", json={"path": "tools.projection_rules", "add": "x"}
            )
            assert resp.status == 400
            assert "whole 'value'" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_value_and_add_together_is_refused(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "voice.exit_phrases", "value": ["stop"], "add": "halt"},
            )
            assert resp.status == 400


class TestEgressIsOneDocument:
    @pytest.mark.asyncio
    async def test_its_read_carries_the_revision_the_patch_compares(self, config_file) -> None:
        async with TestClient(TestServer(_app())) as c:
            eg = await (await c.get("/api/security/egress")).json()
            base = eg.pop("revision")
            assert eg == {"allow_hosts": [], "deny_hosts": [], "allow_private": False}
            first = await _save(c, "security.egress", {**eg, "deny_hosts": ["evil.example"]}, base)
            assert first.status == 200
            # A second tab, from the same read, turning on private networks with consent:
            second = await _save(
                c, "security.egress", {**eg, "allow_private": True}, base, confirm=True
            )
            assert second.status == 409
        assert _stored(config_file, "security", "egress") == {
            "allow_hosts": [],
            "deny_hosts": ["evil.example"],
            "allow_private": False,
        }


class TestTheReadCarriesRevisions:
    @pytest.mark.asyncio
    async def test_every_document_field_has_one_and_it_describes_the_value_beside_it(
        self, config_file
    ) -> None:
        from personalclaw.dashboard.handlers.core import _DOCUMENT_PATHS

        async with TestClient(TestServer(_app())) as c:
            body = await (await c.get("/api/config/personalclaw")).json()
        assert set(body["revisions"]) == set(_DOCUMENT_PATHS)
        for path in _DOCUMENT_PATHS:
            node = body
            for part in path.split("."):
                node = node[part]
            assert body["revisions"][path] == revision_of(node), path
        # Positive control: the ten document fields, not zero of them.
        assert len(_DOCUMENT_PATHS) >= 10
