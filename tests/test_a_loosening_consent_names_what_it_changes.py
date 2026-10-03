"""A "Loosen a security setting?" question names what the write changes, from and to.

🔴 Before: the question was the field's sentence alone, written once per field, so the owner was
asked the same thing whatever they had typed. A daily cap of $33.50 typed as $10,033.50 (a
select-all that missed) was asked "The agent may spend more money per day — 0 removes the
limit." — the words a raise to $100 got — and nothing in the dialog said which number was about
to be stored. The refusal now carries ``change``, what the write changes from and to in the
field's own terms, and for a raise of ten times or more one more sentence, ``caution``. Both ride
``error.detail`` beside the sentence, and the message says them too, for a client that reads
only the message.

These drive the real config PATCH handler on an isolated home.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_relax_security import _config_app, _patch, _seed

CAP = "guardrails.budgets.max_dollars_per_day"
CAP_SENTENCE = "The agent may spend more money per day — 0 removes the limit."


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """An isolated home, and a config file in it."""
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    with patch("personalclaw.config.loader.config_path", return_value=path):
        yield path


@pytest.fixture(autouse=True)
def sel_rows():
    """The security-audit rows the handler writes, kept off the real log."""
    fake = MagicMock()
    with patch("personalclaw.dashboard.handlers.sel", return_value=fake):
        yield fake.log_api_access


async def _asked(config_file, seed: dict, field: str, value: Any) -> dict:
    """The question the owner's unconsented write is answered with, and that nothing was saved."""
    before = _seed(config_file, seed)
    async with TestClient(TestServer(_config_app())) as c:
        resp = await _patch(c, field, value)
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"]["code"] == "confirmation_required", body
    assert config_file.read_text(encoding="utf-8") == before, "nothing may be written"
    return body["error"]


@pytest.mark.asyncio
async def test_the_cap_question_names_the_cap_it_changes_from_and_to(config_file):
    asked = await _asked(
        config_file, {"guardrails": {"budgets": {"max_dollars_per_day": 33.5}}}, CAP, 100
    )

    assert asked["detail"]["consent"] == CAP_SENTENCE
    assert asked["detail"]["change"] == "$33.50 → $100.00"
    assert "caution" not in asked["detail"], "a raise under ten times asks for no second look"


@pytest.mark.asyncio
async def test_a_typo_far_past_the_cap_is_asked_about_twice(config_file):
    """The measured case: $33.50 stored, $10,033.50 typed — about 300 times the cap."""
    asked = await _asked(
        config_file, {"guardrails": {"budgets": {"max_dollars_per_day": 33.5}}}, CAP, 10033.5
    )

    detail = asked["detail"]
    assert detail["consent"] == CAP_SENTENCE, "the field's sentence is unchanged"
    assert detail["change"] == "$33.50 → $10,033.50"
    assert detail["caution"] == (
        "That is about 300 times the current limit, so check the number before you allow it."
    )
    # A client that reads only the message is asked the same question.
    assert "$33.50 → $10,033.50" in asked["message"]
    assert detail["caution"] in asked["message"]


@pytest.mark.asyncio
async def test_ten_times_is_where_the_second_look_starts(config_file):
    seed = {"guardrails": {"budgets": {"max_tokens_per_day": 1_000_000}}}
    field = "guardrails.budgets.max_tokens_per_day"

    just_under = await _asked(config_file, seed, field, 9_999_999)
    at_ten = await _asked(config_file, seed, field, 10_000_000)

    assert just_under["detail"]["change"] == "1,000,000 tokens → 9,999,999 tokens"
    assert "caution" not in just_under["detail"]
    assert at_ten["detail"]["caution"].startswith("That is 10 times the current limit")


@pytest.mark.asyncio
async def test_removing_a_limit_says_it_is_removed(config_file):
    asked = await _asked(
        config_file, {"guardrails": {"budgets": {"max_dollars_per_day": 33.5}}}, CAP, 0
    )

    assert asked["detail"]["change"] == "$33.50 → No limit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "seed", "value", "change"),
    [
        ("agent.yolo", {}, True, "Off → On"),
        (
            "agent.approval_mode",
            {"agent": {"approval_mode": "interactive"}},
            "auto",
            "Ask each time → Auto",
        ),
        ("guardrails.scan_mode", {"guardrails": {"scan_mode": "block"}}, "warn", "Block → Warn"),
        ("auth.session_ttl", {"auth": {"session_ttl": "30d"}}, "90d", "30 days → 90 days"),
        ("auth.lockout_window", {"auth": {"lockout_window": "15m"}}, "1m", "15 minutes → 1 minute"),
        ("sandbox.max_rss_mb", {"sandbox": {"max_rss_mb": 4096}}, 0, "4,096 MB → No limit"),
        (
            "external_access.auto_disable_after_breaches",
            {"external_access": {"auto_disable_after_breaches": 10}},
            0,
            "10 → Never",
        ),
        (
            "durability.sync_encrypt",
            {"durability": {"sync_encrypt": "on"}},
            "off",
            "Always encrypt → Never encrypt",
        ),
        (
            "security.denied_commands",
            {"security": {"denied_commands": ["^rm "]}},
            [],
            "Removes “^rm ”",
        ),
        (
            "agent.subagent_cwd_allowed_roots",
            {"agent": {"subagent_cwd_allowed_roots": ["/srv/notes"]}},
            ["/srv/notes", "/srv/projects"],
            "Adds “/srv/projects”",
        ),
        (
            "security.egress",
            {"security": {"egress": {"deny_hosts": ["tracker.example"]}}},
            {"allow_hosts": ["nas.example"], "deny_hosts": [], "allow_private": True},
            "Private and LAN addresses: blocked → allowed\n"
            "Allowed hosts: adds “nas.example”\n"
            "Denied hosts: removes “tracker.example”",
        ),
    ],
)
async def test_each_kind_of_control_says_what_it_changes_in_its_own_terms(
    config_file, field, seed, value, change
):
    asked = await _asked(config_file, seed, field, value)

    assert asked["detail"]["change"] == change


class _Transport:
    """A sync transport the registry knows, as an installed sync app registers one."""

    name = "shared-folder"
    display_name = "Shared Folder"


@pytest.fixture
def transport():
    from personalclaw.sync_transports.registry import register_transport, unregister_transport

    register_transport(_Transport())  # type: ignore[arg-type]
    yield _Transport.name
    unregister_transport(_Transport.name)


@pytest.mark.asyncio
async def test_the_sync_questions_name_the_transport_the_home_goes_through(config_file, transport):
    """🔴 Before: "a different transport" and "the configured transport" named neither."""
    chosen = await _asked(config_file, {}, "durability.sync_transport", transport)
    switched_on = await _asked(
        config_file,
        {"durability": {"sync_transport": transport}},
        "durability.sync_enabled",
        True,
    )
    with_none = await _asked(config_file, {}, "durability.sync_enabled", True)

    assert chosen["detail"]["change"] == "None → Shared Folder"
    assert switched_on["detail"]["change"] == "Off → On, through Shared Folder"
    assert with_none["detail"]["change"] == "Off → On, with no transport chosen yet"


@pytest.mark.asyncio
async def test_a_transport_nothing_answers_to_is_shown_as_typed(config_file):
    asked = await _asked(config_file, {}, "durability.sync_transport", "not-installed")

    assert asked["detail"]["change"] == "None → “not-installed”"


@pytest.mark.asyncio
async def test_with_the_owners_yes_the_value_is_stored_as_sent(config_file):
    import json

    _seed(config_file, {"guardrails": {"budgets": {"max_dollars_per_day": 33.5}}})
    async with TestClient(TestServer(_config_app())) as c:
        resp = await _patch(c, CAP, 10033.5, confirm=True)
        assert resp.status == 200, await resp.text()
    stored = json.loads(config_file.read_text(encoding="utf-8"))
    assert stored["guardrails"]["budgets"]["max_dollars_per_day"] == 10033.5
