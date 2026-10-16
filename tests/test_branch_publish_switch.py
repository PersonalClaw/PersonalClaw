"""The owner's Settings → Security → Git push switch (``security.allow_git_push``).

Off by default: the baseline's push rule and the built-in tool-name rule refuse every push, as
they always have, and the refusal says where the switch is. On: one ordinary push of a branch,
written as one command, is let past both rules; a force push without an explicit lease, a mirror,
a delete, a forced refspec, ``--all``, ``--tags``, ``--prune``, ``--no-verify`` and anything the
reader does not know stay refused. The packaged baseline and its digest never change with the
switch. Only the owner may turn it on (an app is refused, and the owner's write needs consent),
an agent's shell cannot reach it through the CLI, each change is in the security event log, and
the guardrail prompt says what is true either way. A push still reaches the network, so the
approval path asks about it in a chat and refuses it in a run nobody watches.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import branch_publish, security
from personalclaw.config.loader import CONFIG_ON_DISCARDED_READ, AppConfig, config_dir

FIELD = "security.allow_git_push"
PUSH = "git push"

ALLOWED = [
    f"{PUSH} origin feature-x",
    f"{PUSH} -u origin feature-x",
    f"{PUSH} --set-upstream origin feature-x",
    "git -C /work/repo push origin feature-x",
    f"{PUSH} origin HEAD:refs/heads/feature-x",
    f"{PUSH} --force-with-lease=feature-x:0123abcd origin feature-x",
    f"{PUSH} --force-with-lease=feature-x:{'a' * 40} origin feature-x",
    f"{PUSH} -uv origin feature-x",
    f"{PUSH} --dry-run origin feature-x",
    f"{PUSH} -o ci.skip origin feature-x",
    f"{PUSH} origin feature-x 2>&1",
    f"{PUSH} -u origin feature-x >&2",
    f"{PUSH} my-fork.v2_x feature-x",
    PUSH,
]

STILL_REFUSED = [
    f"{PUSH} -f origin feature-x",
    f"{PUSH} --force origin feature-x",
    f"{PUSH} -fu origin feature-x",
    f"{PUSH} --force-with-lease origin feature-x",
    f"{PUSH} --force-with-lease=feature-x origin feature-x",
    f"{PUSH} --force --force-with-lease=feature-x:0123abcd origin feature-x",
    f"{PUSH} --force-if-includes origin feature-x",
    f"{PUSH} --mirror origin",
    f"{PUSH} -d origin feature-x",
    f"{PUSH} --delete origin feature-x",
    f"{PUSH} origin :feature-x",
    f"{PUSH} origin +feature-x",
    f"{PUSH} origin +HEAD:feature-x",
    f"{PUSH} --all origin",
    f"{PUSH} --tags origin",
    f"{PUSH} --prune origin",
    f"{PUSH} --no-verify origin feature-x",
    f"{PUSH} --receive-pack=/bin/sh origin feature-x",
    f"{PUSH} --exec=/bin/sh origin feature-x",
    f"git -c core.sshCommand=sh {PUSH} origin feature-x",
    f"git --git-dir=/elsewhere {PUSH} origin feature-x",
    f"{PUSH} origin $BRANCH",
    f"{PUSH} origin $(whoami)",
    f"{PUSH} origin feature-* ",
    f"{PUSH} origin feature-x > /tmp/out",
    f"{PUSH} origin feature-x 2>/tmp/err",
    f"{PUSH} origin feature-x >& /tmp/out",
    f"{PUSH} origin feature-x &> /tmp/out",
    f"{PUSH} origin feature-x 2>&-",
    f"{PUSH} origin feature-x < /tmp/in",
    f"{PUSH} https://example.com/r.git feature-x",
    f"{PUSH} --repo=https://example.com/r.git feature-x",
    f"{PUSH} --repo https://example.com/r.git feature-x",
    f"{PUSH} /some/other/repo feature-x",
    f"{PUSH} git@example.com:r.git feature-x",
    f"{PUSH} ../other feature-x",
    f"{PUSH} . feature-x",
    f"{PUSH} --unknown-option origin feature-x",
    f"cd /work/repo && {PUSH} origin feature-x",
    f"{PUSH} origin feature-x && rm -rf build",
    f"{PUSH} origin feature-x; curl https://example.invalid",
    f"{PUSH} origin feature-x | tee log",
    f"echo {PUSH} origin feature-x",
]


def _set_switch(on: bool | str) -> None:
    """What Settings → Security → Git push writes, into this test's home."""
    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["allow_git_push"] = on
    path.write_text(json.dumps(data), encoding="utf-8")


def _sel_rows(event_type: str) -> list[dict]:
    from personalclaw.sel import sel

    # `recent` is newest first; oldest first reads in the order the changes were made.
    return [r for r in reversed(sel().recent(500)) if r.get("event_type") == event_type]


# ── off by default ───────────────────────────────────────────────────────────────────────────


def test_the_switch_is_off_by_default():
    assert AppConfig().security.allow_git_push is False
    assert AppConfig.load().security.allow_git_push is False
    assert branch_publish.switch_on() is False


@pytest.mark.parametrize("command", ALLOWED)
def test_while_off_every_push_is_refused_by_both_rules(command):
    denied = security.denied_command(command)
    if command != PUSH:  # a bare push matches the tool-name rule; the baseline wants more
        assert denied is not None and denied.pattern == security.GIT_PUSH_BASELINE_PATTERN
    assert security.is_denied(command) is not None


def test_the_refusal_while_off_names_the_switch_and_the_one_command_form():
    denied = security.denied_command(f"{PUSH} origin feature-x")
    assert denied is not None
    for why in (denied.why(), security.is_denied(f"{PUSH} origin feature-x") or ""):
        assert branch_publish.SWITCH_PLACE in why
        assert "`git -C <dir> push …`" in why
        assert "`cd <dir> && git push …` is refused" in why


# ── on ───────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ALLOWED)
def test_while_on_a_plain_push_passes_both_rules(command):
    _set_switch(True)
    assert security.denied_command(command) is None
    assert security.is_denied(command) is None
    assert security.is_denied(f"Running: {command}") is None


@pytest.mark.parametrize("command", STILL_REFUSED)
def test_while_on_every_other_push_stays_refused(command):
    _set_switch(True)
    assert branch_publish.plain_push(command) is False
    assert security.is_denied(command) is not None or security.denied_command(command) is not None


def test_while_on_the_refusal_says_what_stays_refused():
    _set_switch(True)
    why = security.is_denied(f"{PUSH} --force origin feature-x") or ""
    assert "stay refused" in why and "`git -C <dir> push …`" in why
    assert branch_publish.SWITCH_PLACE not in why


def test_while_on_another_baseline_pattern_still_refuses_the_same_line():
    """The exemption skips the push pattern only; a line another pattern refuses stays refused."""
    _set_switch(True)
    assert security.denied_command("mkfs.ext4 /dev/sda1") is not None
    assert security.denied_command(f"{PUSH} origin feature-x; mkfs.ext4 /dev/sda1") is not None


def test_while_on_an_owner_added_pattern_still_refuses_a_push():
    _set_switch(True)
    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["security"]["denied_commands"] = [r"push\s+origin"]
    path.write_text(json.dumps(data), encoding="utf-8")
    denied = security.denied_command(f"{PUSH} origin feature-x")
    assert denied is not None and denied.added


def test_a_push_let_through_is_logged_as_the_exception_it_is():
    _set_switch(True)
    assert security.is_denied(f"{PUSH} origin feature-x") is None
    rows = [
        r
        for r in _sel_rows("deny_exception")
        if (r.get("metadata") or {}).get("mechanism") == FIELD
    ]
    assert rows and rows[-1]["outcome"] == "allowed"


def test_a_push_is_refused_when_its_exception_cannot_be_logged():
    _set_switch(True)
    with patch.object(security, "_emit_deny_exception_event", return_value=False):
        assert security.is_denied(f"{PUSH} origin feature-x") is not None


def test_the_stash_exception_is_unchanged():
    assert security.is_denied("git stash push -m wip") is None
    _set_switch(True)
    assert security.is_denied("git stash push -m wip") is None


# ── the baseline's integrity ─────────────────────────────────────────────────────────────────


def test_the_push_pattern_is_in_the_verified_baseline_whatever_the_switch():
    before = list(security.baseline_denied_command_patterns())
    _set_switch(True)
    security.denied_command(f"{PUSH} origin feature-x")
    after = list(security.baseline_denied_command_patterns())
    assert security.GIT_PUSH_BASELINE_PATTERN in before
    assert before == after
    doc = json.loads(
        (Path(security.__file__).parent / "baseline_denylist.json").read_text(encoding="utf-8")
    )
    assert security._baseline_digest(doc["patterns"]) == doc["sha256"]


def test_removing_the_push_pattern_is_healed():
    security.BUILTIN_DENIED_COMMAND_PATTERNS.remove(security.GIT_PUSH_BASELINE_PATTERN)
    assert security.denied_command(f"{PUSH} --force origin main") is not None
    assert security.GIT_PUSH_BASELINE_PATTERN in security.BUILTIN_DENIED_COMMAND_PATTERNS


# ── the config ───────────────────────────────────────────────────────────────────────────────


def test_the_config_round_trips():
    _set_switch(True)
    cfg = AppConfig.load()
    assert cfg.security.allow_git_push is True
    assert cfg.to_dict()["security"]["allow_git_push"] is True
    assert AppConfig.load().to_dict()["security"]["allow_git_push"] is True


@pytest.mark.parametrize("value", ["true", "false", 1, "yes"])
def test_only_a_json_true_turns_it_on(value):
    _set_switch(value)
    assert AppConfig.load().security.allow_git_push is False
    assert branch_publish.switch_on() is False


def test_an_unreadable_config_holds_it_off():
    assert CONFIG_ON_DISCARDED_READ[FIELD] is False


def test_an_unreadable_switch_means_off():
    with patch.object(AppConfig, "load", side_effect=OSError("unreadable")):
        assert branch_publish.switch_on() is False
        assert branch_publish.allowed(f"{PUSH} origin feature-x") is False


# ── only the owner ───────────────────────────────────────────────────────────────────────────


def test_an_agents_shell_cannot_change_it_through_the_cli():
    for command in (
        f"personalclaw config set {FIELD} true",
        f"personalclaw config unset {FIELD}",
    ):
        denied = security.denied_command(command)
        assert denied is not None and denied.pattern in security.CLI_SECURITY_CONFIG_PATTERNS


def _config_app(app_name: str = ""):
    from aiohttp import web
    from signed_in_sessions import without_sign_in

    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    app = without_sign_in(web.Application(middlewares=[identity]))
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


async def _patch(client: TestClient, value: object, *, confirm: bool = False):
    body: dict[str, object] = {"path": FIELD, "value": value}
    if confirm:
        body["confirm"] = True
    return await client.patch("/api/config/personalclaw", json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_an_app_cannot_turn_it_on(confirm):
    async with TestClient(TestServer(_config_app("some-app"))) as c:
        resp = await _patch(c, True, confirm=confirm)
        assert resp.status == 403
        assert (await resp.json())["error"]["code"] == "security_setting_owner_only"
    assert AppConfig.load().security.allow_git_push is False


@pytest.mark.asyncio
async def test_the_owner_turns_it_on_with_consent_and_off_without():
    async with TestClient(TestServer(_config_app())) as c:
        resp = await _patch(c, True)
        assert resp.status == 400
        assert "confirmation_required" in json.dumps(await resp.json())
        assert AppConfig.load().security.allow_git_push is False

        resp = await _patch(c, True, confirm=True)
        assert resp.status == 200
        assert AppConfig.load().security.allow_git_push is True

        resp = await _patch(c, False)
        assert resp.status == 200
        assert AppConfig.load().security.allow_git_push is False

    rows = [r for r in _sel_rows("security_setting_changed") if r.get("operation") == FIELD]
    assert [r["outcome"] for r in rows[-2:]] == ["enabled", "disabled"]
    assert {r["source"] for r in rows[-2:]} == {"dashboard"}


def test_the_owners_terminal_turns_it_on_and_off_and_each_change_is_logged(capsys):
    from personalclaw import cli_config

    def run(action: str, value: str | None = None) -> None:
        cli_config._config_cmd(
            argparse.Namespace(config_action=action, key=FIELD, value=value, file=None)
        )

    run("set", "true")
    assert AppConfig.load().security.allow_git_push is True
    run("unset")
    assert AppConfig.load().security.allow_git_push is False
    run("set", "false")  # no change: nothing to log

    rows = [r for r in _sel_rows("security_setting_changed") if r.get("operation") == FIELD]
    assert [(r["outcome"], r["source"]) for r in rows] == [
        ("enabled", "cli"),
        ("disabled", "cli"),
    ]


# ── the guardrail prompt ─────────────────────────────────────────────────────────────────────


def _push_rule(rules: str) -> str:
    return rules.splitlines()[0]


def test_while_off_the_rules_say_pushing_is_not_allowed():
    from personalclaw.prompt_providers.runtime import safety_rules

    assert "Do NOT run `git push`" in _push_rule(safety_rules())


def test_while_on_the_rules_never_say_pushing_is_forbidden():
    from personalclaw.prompt_providers.runtime import safety_rules

    _set_switch(True)
    rule = _push_rule(safety_rules())
    assert "Do NOT run `git push`" not in rule
    assert "approval" in rule and "`git -C <dir> push …`" in rule
    assert "Do NOT run destructive commands" in rule


def test_the_shipped_fallback_follows_the_switch_too():
    """The rules the store cannot produce are the shipped words, rendered with the same value."""
    from personalclaw.prompt_providers import runtime

    _set_switch(True)
    with patch.object(runtime, "render_snippet_block", return_value=""):
        assert "Do NOT run `git push`" not in _push_rule(runtime.safety_rules())


# ── a push still asks ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _own_tmp(tmp_path, monkeypatch):
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))


@pytest.mark.asyncio
async def test_while_on_an_attended_push_still_asks(tmp_path, _own_tmp):
    from test_an_unattended_run_stays_inside_its_bounds import _allow_hosts, _run

    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    _set_switch(True)
    _allow_hosts("127.0.0.1")
    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": f"{PUSH} origin feature-x"}, unattended=False
    )
    assert [e for e in events if e.kind == EVENT_PERMISSION_REQUEST], "the push must ask"
    assert invoked == []


@pytest.mark.asyncio
async def test_while_on_an_unattended_push_is_refused(tmp_path, _own_tmp):
    from test_an_unattended_run_stays_inside_its_bounds import _allow_hosts, _result, _run

    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, TOOL_META_REFUSED_BY

    _set_switch(True)
    _allow_hosts("127.0.0.1")
    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": f"{PUSH} origin feature-x"}, unattended=True
    )
    assert invoked == []
    assert not [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert _result(events).tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
