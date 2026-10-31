"""An account's connection health, kept apart from its settings, and what a capability needs of
its account (#3842).

Each account of an app carries its connection's health: connected, signing in, refused or
unreachable, with the sentence and the time of the last good contact. A capability reports it
(``personalclaw.sdk.health``); a failure it reports stays until that same capability next
succeeds, so another capability's success never hides it, and nothing of it is a setting. An
account with more than one connection (reading and sending mail) shows each, the account's own
health being the worse. A capability that needs an account field the account leaves empty is not
built, and says which, while the account's other capabilities run. A capability's success may
carry a one-line note, shown with its last good report on its row.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_account_and_capability_settings import APP, SEARCH_KEY, TOKEN, _install, _manifest

from personalclaw.apps import account_health
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.secret_fields import SECRET_MASK
from personalclaw.config import loader as config_loader
from personalclaw.providers import registry as registry_module
from personalclaw.providers.settings import ProviderSettings
from personalclaw.sdk import health


@pytest.fixture
def home():
    registry_module.reset_provider_registry()
    account_health.reset()
    yield config_loader.config_dir()
    account_health.reset()
    registry_module.reset_provider_registry()


CONNECTIONS = [
    {"id": "reading", "label": "Reading mail"},
    {"id": "sending", "label": "Sending mail"},
]


def _gateway() -> web.Application:
    from personalclaw.dashboard.handlers.apps import register_app_routes

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner])
    register_app_routes(gw)
    return gw


async def _row(client: TestClient, app: str = APP) -> dict[str, Any]:
    listed = await (await client.get("/api/apps")).json()
    return next(row for row in listed["apps"] if row["name"] == app)


async def _settings(client: TestClient) -> list[Any]:
    """Every settings read of the app, as the routes answer them."""
    reads = [f"/api/apps/{APP}/config"]
    reads += [f"/api/apps/{APP}/capabilities/{cid}/config" for cid in ("inbox", "search")]
    out = []
    for url in reads:
        body = await (await client.get(url)).json()
        out.append({k: body[k] for k in ("config", "revision")})
    return out


# ── health apart from settings ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_token_marks_the_account_and_leaves_every_setting_as_it_is(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN, "server": "mail.example.invalid"})
    ProviderSettings.update_capability(APP, "search", {"max_results": 5})
    async with TestClient(TestServer(_gateway())) as client:
        before = await _settings(client)
        assert (await _row(client))["accounts"] == [
            {"id": "default", "label": "", "health": "unknown", "reason": "", "lastOk": None}
        ], "nothing is known of a connection until a capability reports"

        health.report_refused(APP, "inbox", "The mail server refused the token.")
        account = (await _row(client))["accounts"][0]
        assert account["health"] == "refused"
        assert account["reason"] == "The mail server refused the token."
        assert await _settings(client) == before, "a refusal changes no setting"
        assert "health" not in json.dumps(before), "no settings read carries health"

        health.report_ok(APP, "search")
        account = (await _row(client))["accounts"][0]
        assert account["health"] == "refused", "another capability's success does not hide it"
        assert account["lastOk"] is not None, "it is still a good contact"

        health.report_ok(APP, "inbox")
        account = (await _row(client))["accounts"][0]
        assert (account["health"], account["reason"]) == ("connected", "")
        assert await _settings(client) == before


def test_the_worst_failure_outstanding_is_the_account_s(home) -> None:
    _install(_manifest())
    health.report_unreachable(APP, "search", "The search server did not answer.")
    health.report_refused(APP, "inbox", "The mail server refused the token.")
    assert account_health.accounts(APP)[0]["health"] == "refused"
    health.report_ok(APP, "inbox")
    row = account_health.accounts(APP)[0]
    assert (row["health"], row["reason"]) == ("unreachable", "The search server did not answer.")
    health.report_signing_in(APP, "inbox")
    assert account_health.accounts(APP)[0]["health"] == "unreachable"
    health.report_ok(APP, "search")
    assert account_health.accounts(APP)[0]["health"] == "signing_in"


def test_each_account_has_its_own_health(home) -> None:
    _install(_manifest())
    work = ProviderSettings.add_account(APP, "Work", "work")
    health.report_refused(APP, "inbox", "Refused.", account=work)
    health.report_ok(APP, "inbox", account="default")
    rows = {row["id"]: row for row in account_health.accounts(APP)}
    assert rows["default"]["health"] == "connected"
    assert (rows[work]["health"], rows[work]["label"]) == ("refused", "Work")


def test_an_account_added_mid_poll_leaves_the_running_capability_s_report_as_it_was(home):
    _install(_manifest())
    health.report_ok(APP, "inbox")
    ProviderSettings.add_account(APP, "Work", "work")
    health.report_refused(APP, "inbox", "The mail server refused the token.")
    rows = {row["id"]: row for row in account_health.accounts(APP)}
    assert rows["default"]["health"] == "refused", "it runs under the first account"
    assert rows["work"]["health"] == "unknown"
    health.report_ok(APP, "inbox")
    assert {r["id"]: r["health"] for r in account_health.accounts(APP)} == {
        "default": "connected",
        "work": "unknown",
    }


def test_a_report_about_an_account_that_is_gone_records_nothing(home, caplog) -> None:
    _install(_manifest())
    work = ProviderSettings.add_account(APP, "Work", "work")
    ProviderSettings.remove_account(APP, work)
    with caplog.at_level("DEBUG", logger="personalclaw.apps.account_health"):
        health.report_refused(APP, "inbox", "Refused.", account=work)
        health.report_ok(APP, "inbox", account="ghost")
    assert "no account 'work' any more" in caplog.text
    assert [row["health"] for row in account_health.accounts(APP)] == ["unknown"]


# ── two connections on one account ───────────────────────────────────────────────────────────


def test_each_connection_has_its_health_and_the_account_the_worse(home) -> None:
    _install(_manifest(connections=CONNECTIONS))
    health.report_ok(APP, "inbox", connection="reading")
    health.report_refused(APP, "inbox", "The SMTP server refused the login.", connection="sending")
    row = account_health.accounts(APP)[0]
    by_id = {c["id"]: c for c in row["connections"]}
    assert by_id["reading"]["health"] == "connected" and by_id["reading"]["lastOk"]
    assert by_id["sending"] == {
        "id": "sending",
        "label": "Sending mail",
        "health": "refused",
        "reason": "The SMTP server refused the login.",
        "lastOk": None,
    }
    assert (row["health"], row["reason"]) == ("refused", "The SMTP server refused the login.")

    health.report_unreachable(
        APP, "search", "The IMAP server did not answer.", connection="reading"
    )
    by_id = {c["id"]: c for c in account_health.accounts(APP)[0]["connections"]}
    assert by_id["reading"]["health"] == "unreachable"
    assert by_id["sending"]["reason"] == "The SMTP server refused the login.", "left as it was"


def test_an_account_is_connected_only_when_every_connection_is(home) -> None:
    _install(_manifest(connections=CONNECTIONS))
    health.report_ok(APP, "inbox", connection="reading")
    row = account_health.accounts(APP)[0]
    assert row["health"] == "unknown", "sending has not been checked"
    health.report_ok(APP, "inbox", connection="sending")
    assert account_health.accounts(APP)[0]["health"] == "connected"


def test_the_healths_are_the_sdk_s_constants_worst_first() -> None:
    assert health.HEALTHS == ("refused", "unreachable", "signing_in", "unknown", "connected")
    assert (health.REFUSED, health.UNREACHABLE, health.SIGNING_IN) == health.HEALTHS[:3]
    assert (health.UNKNOWN, health.CONNECTED) == health.HEALTHS[3:]


def test_an_app_with_two_connections_reports_on_one_of_them(home) -> None:
    _install(_manifest(connections=CONNECTIONS))
    with pytest.raises(ValueError, match="connection"):
        health.report_ok(APP, "inbox")
    with pytest.raises(ValueError, match="connection"):
        health.report_ok(APP, "inbox", connection="calendar")
    assert account_health.accounts(APP)[0]["health"] == "unknown"


# ── refusals ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda: health.report_ok("no-such-app", "inbox"),
        lambda: health.report_ok(APP, "calendar"),
        lambda: health.report_ok(APP, "inbox", connection="reading"),
        lambda: health.report_refused(APP, "inbox", ""),
        lambda: health.report_refused(APP, "inbox", " \n\t"),
    ],
    ids=["ghost-app", "undeclared-capability", "undeclared-connection", "silent", "blank"],
)
def test_a_report_the_app_cannot_make_is_refused_and_records_nothing(home, call) -> None:
    _install(_manifest())
    with pytest.raises(ValueError):
        call()
    assert account_health.accounts(APP)[0]["health"] == "unknown"
    assert all(row["lastOk"] is None for row in account_health.capabilities(APP))


def _refusals(**fields: Any) -> list[str]:
    return AppManifest.from_dict(_manifest(**fields)).validate()


def _requiring(fields: list[str], at: int = 1) -> dict[str, Any]:
    raw = _manifest()
    raw["providers"][at]["requiresAccountFields"] = fields
    return raw


@pytest.mark.parametrize(
    "raw,named",
    [
        (_requiring(["bucket"]), "providers[1].requiresAccountFields names bucket"),
        (_requiring(["max_results"]), "providers[1].requiresAccountFields names max_results"),
        (_manifest(connections=CONNECTIONS[:1]), "connections"),
        (_manifest(connections=[CONNECTIONS[0], CONNECTIONS[0]]), "connections"),
        (_manifest(connections=[{"id": "Reading", "label": "x"}, CONNECTIONS[1]]), "connections"),
    ],
    ids=["not-an-account-field", "a-capability-field", "one", "twice", "not-kebab"],
)
def test_each_wrong_declaration_is_refused(raw, named) -> None:
    errors = AppManifest.from_dict(raw).validate()
    assert any(named in e for e in errors), errors


def test_connections_and_required_fields_need_account_settings() -> None:
    raw = _requiring(["token"])
    raw["connections"] = CONNECTIONS
    del raw["settings"]
    errors = AppManifest.from_dict(raw).validate()
    assert any("requiresAccountFields" in e for e in errors), errors
    assert any(e.startswith("connections") for e in errors), errors


def test_a_multi_instance_capability_requires_fields_of_its_instance_schema() -> None:
    raw = {
        "name": "cloud-models",
        "version": "1.0.0",
        "displayName": "Cloud",
        "description": "x",
        "providers": [
            {
                "type": "model",
                "implementation": "provider:create",
                "id": "video",
                "label": "Make videos",
                "multiInstance": True,
                "settingsSchema": {"type": "object", "properties": {"bucket": {"type": "string"}}},
                "requiresAccountFields": ["bucket"],
            }
        ],
    }
    assert AppManifest.from_dict(raw).validate() == []
    raw["providers"][0]["requiresAccountFields"] = ["region"]
    assert any("names region" in e for e in AppManifest.from_dict(raw).validate())


def test_the_fixtures_are_valid() -> None:
    assert _refusals(connections=CONNECTIONS) == []
    assert AppManifest.from_dict(_requiring(["server"])).validate() == []


# ── needs setup ──────────────────────────────────────────────────────────────────────────────


class _Handler:
    def __init__(self, built: list[str]) -> None:
        self.built = built

    def create(self, ext):
        self.built.append(ext.capability)
        return object()

    def register(self, ext, instance):
        return None

    def deregister(self, ext, instance):
        return None


@pytest.mark.asyncio
async def test_a_capability_whose_account_field_is_empty_needs_setup_while_the_other_runs(
    home, monkeypatch
) -> None:
    manifest = _install(_requiring(["server"]))
    ProviderSettings.update_account(APP, {"token": TOKEN})
    built: list[str] = []
    registry = registry_module.get_provider_registry()
    registry.register_type_handler("inbox", _Handler(built))  # type: ignore[arg-type]
    registry.register_type_handler("search", _Handler(built))  # type: ignore[arg-type]
    registry.register(manifest, enabled=True)

    assert built == ["inbox"], "the other capability of the account is built"
    search = registry.get(APP, "search")
    assert not search.enabled
    assert search.error == "Needs setup: the account's server is empty."
    async with TestClient(TestServer(_gateway())) as client:
        rows = {r["id"]: r for r in (await _row(client))["capabilities"]}
        assert rows["search"]["needsSetup"] == ["server"]
        assert rows["inbox"]["needsSetup"] == []

        read = await (await client.get(f"/api/apps/{APP}/config")).json()
        put = await client.put(
            f"/api/apps/{APP}/config",
            json={"token": TOKEN, "server": "mail.example.invalid"},
            headers={"If-Match": read["revision"]},
        )
        assert put.status == 200, await put.text()
        assert registry.get(APP, "search").enabled, "filling the field builds it"
        assert (await _row(client))["capabilities"][1]["needsSetup"] == []
    assert built.count("search") == 1


def test_a_secret_field_counts_as_set_when_the_store_holds_it(home) -> None:
    _install(_requiring(["token"], at=0))
    from personalclaw.apps.account_settings import missing_account_fields

    assert missing_account_fields(APP, "inbox") == ["token"]
    ProviderSettings.update_account(APP, {"token": TOKEN})
    assert missing_account_fields(APP, "inbox") == []
    assert missing_account_fields(APP, "search") == [], "search requires nothing"


def test_a_secret_whose_key_the_store_lost_counts_as_empty(home) -> None:
    from personalclaw.apps.account_settings import missing_account_fields
    from personalclaw.config import secret_refs
    from personalclaw.config.credentials import delete_credential
    from personalclaw.providers.settings import load_stored_account

    _install(_requiring(["token"], at=0))
    ProviderSettings.update_account(APP, {"token": TOKEN})
    delete_credential(secret_refs.ref_key(load_stored_account(APP)["token"]))
    assert missing_account_fields(APP, "inbox") == ["token"], "a reference to nothing is empty"


def test_a_further_account_needs_its_own_field(home) -> None:
    _install(_requiring(["server"]))
    from personalclaw.apps.account_settings import missing_account_fields

    ProviderSettings.update_account(APP, {"server": "mail.example.invalid"})
    work = ProviderSettings.add_account(APP, "Work", "work")
    assert missing_account_fields(APP, "search") == []
    assert missing_account_fields(APP, "search", work) == ["server"], "nothing is inherited"


# ── a capability's last good report and its note ─────────────────────────────────────────────


def test_a_success_shows_its_time_and_note_on_the_capability_s_row(home) -> None:
    _install(_manifest())
    health.report_ok(APP, "search", note="Checked 4 min ago; newest 2 h ago")
    rows = {r["id"]: r for r in account_health.capabilities(APP)}
    assert rows["search"]["note"] == "Checked 4 min ago; newest 2 h ago"
    assert rows["search"]["lastOk"] is not None
    assert (rows["inbox"]["lastOk"], rows["inbox"]["note"]) == (None, "")
    assert rows["search"]["label"] == "Search your mail"


def test_a_note_is_one_short_line_of_text(home) -> None:
    _install(_manifest())
    health.report_ok(APP, "search", note="first line\nsecond\tline\x00" + "x" * 300)
    note = {r["id"]: r for r in account_health.capabilities(APP)}["search"]["note"]
    assert "\n" not in note and "\x00" not in note and "\t" not in note
    assert note.startswith("first line second line")
    assert len(note) <= account_health.NOTE_MAX


def test_a_failure_keeps_the_last_good_report(home) -> None:
    _install(_manifest())
    health.report_ok(APP, "search", note="Checked now")
    health.report_unreachable(APP, "search", "The search server did not answer.")
    row = {r["id"]: r for r in account_health.capabilities(APP)}["search"]
    assert row["note"] == "Checked now" and row["lastOk"] is not None


def test_a_capability_an_update_drops_reports_nothing_any_more(home) -> None:
    _install(_manifest())
    health.report_refused(APP, "search", "Refused.")
    dropped = _manifest(version="1.1.0")
    dropped["providers"] = dropped["providers"][:1]
    _install(dropped)
    assert account_health.accounts(APP)[0]["health"] == "unknown"
    assert [row["id"] for row in account_health.capabilities(APP)] == ["inbox"]


def test_a_removed_account_s_health_goes_with_it(home) -> None:
    _install(_manifest())
    work = ProviderSettings.add_account(APP, "Work", "work")
    health.report_refused(APP, "inbox", "Refused.", account=work)
    ProviderSettings.remove_account(APP, work)
    ProviderSettings.add_account(APP, "Work again", "work")
    rows = {row["id"]: row for row in account_health.accounts(APP)}
    assert rows["work"]["health"] == "unknown", "an account added again starts afresh"


# ── what a sentence or a note may show ───────────────────────────────────────────────────────


def test_a_sentence_never_shows_one_of_the_account_s_secrets(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    health.report_unreachable(
        APP, "search", f"GET https://search.example.invalid/q?key={SEARCH_KEY} failed: timed out"
    )
    health.report_refused(APP, "inbox", f"login refused for token {TOKEN}\nretry later")
    health.report_ok(APP, "search", note=f"Checked with {TOKEN}")
    shown = json.dumps([account_health.accounts(APP), account_health.capabilities(APP)])
    assert TOKEN not in shown and SEARCH_KEY not in shown
    reason = account_health.accounts(APP)[0]["reason"]
    assert reason.startswith("login refused for token ") and "\n" not in reason


def test_a_sentence_shaped_like_a_credential_is_masked_too(home) -> None:
    _install(_manifest())
    leaked = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    health.report_refused(APP, "inbox", f"GitHub said 401 for {leaked}")
    assert leaked not in account_health.accounts(APP)[0]["reason"]


def test_a_sentence_is_one_line_of_at_most_200_characters(home) -> None:
    _install(_manifest())
    health.report_refused(APP, "inbox", "refused\r\n" + "y" * 400)
    reason = account_health.accounts(APP)[0]["reason"]
    assert len(reason) == account_health.SENTENCE_MAX == 200
    assert reason.startswith("refused y") and "\r" not in reason


# ── a save that changes the account ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_save_that_changes_the_account_sets_its_failures_back_to_unknown(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    health.report_ok(APP, "search")
    health.report_refused(APP, "inbox", "The mail server refused the token.")
    async with TestClient(TestServer(_gateway())) as client:
        read = await (await client.get(f"/api/apps/{APP}/config")).json()
        same = await client.put(
            f"/api/apps/{APP}/config", json=read["config"], headers={"If-Match": read["revision"]}
        )
        assert same.status == 200, await same.text()
        assert (await _row(client))["accounts"][0]["health"] == "refused", "nothing changed"

        read = await (await client.get(f"/api/apps/{APP}/config")).json()
        fixed = await client.put(
            f"/api/apps/{APP}/config",
            json={"token": "fixture-new-token-55aa"},
            headers={"If-Match": read["revision"]},
        )
        assert fixed.status == 200, await fixed.text()
        account = (await _row(client))["accounts"][0]
    assert (account["health"], account["reason"]) == (
        "unknown",
        "Not checked since the account was changed.",
    )
    assert account["lastOk"] is not None, "the last good contact stays"
    health.report_ok(APP, "inbox")
    assert account_health.accounts(APP)[0]["health"] == "connected"


def test_a_further_account_s_change_leaves_the_first_as_it_was(home) -> None:
    _install(_manifest())
    work = ProviderSettings.add_account(APP, "Work", "work")
    health.report_refused(APP, "inbox", "Refused.", account="default")
    health.report_refused(APP, "inbox", "Refused.", account=work)
    ProviderSettings.update_account(APP, {"token": "fixture-work-token-77bb"}, work)
    rows = {row["id"]: row for row in account_health.accounts(APP)}
    assert rows["default"]["health"] == "refused"
    assert rows[work]["health"] == "unknown"


# ── what goes away takes its reports with it ─────────────────────────────────────────────────


def test_a_capability_an_update_drops_takes_its_reports_with_it(home) -> None:
    from personalclaw.apps import capability_lifecycle, manager

    first = AppManifest.from_dict(_manifest())
    _install(_manifest())
    health.report_refused(APP, "search", "Refused.")
    dropped = _manifest(version="1.1.0")
    dropped["providers"] = dropped["providers"][:1]
    capability_lifecycle.after_update(
        manager.InstalledApp(name=APP, version="1.0.0"), first, AppManifest.from_dict(dropped)
    )
    _install(_manifest(version="1.2.0"))
    assert account_health.accounts(APP)[0]["health"] == "unknown", "back, with nothing old"


def test_an_uninstalled_app_takes_its_reports_with_it(home) -> None:
    from personalclaw.apps import app_manager

    _install(_manifest())
    health.report_refused(APP, "inbox", "Refused.")
    assert app_manager.uninstall(APP)
    assert account_health.accounts(APP)[0]["health"] == "unknown"


def test_reports_from_many_threads_at_once(home) -> None:
    import threading

    _install(_manifest())
    failures: list[BaseException] = []

    def report(i: int) -> None:
        try:
            for n in range(40):
                if (i + n) % 2:
                    health.report_ok(APP, "search", note=f"poll {n}")
                else:
                    health.report_unreachable(APP, "inbox", f"timeout {n}")
                account_health.accounts(APP)
                account_health.forget(APP, "calendar")
        except BaseException as exc:  # noqa: BLE001 — the test reports it
            failures.append(exc)

    threads = [threading.Thread(target=report, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    health.report_ok(APP, "inbox")
    assert account_health.accounts(APP)[0]["health"] == "connected"


# ── an older core ────────────────────────────────────────────────────────────────────────────


def test_either_key_outside_a_multi_instance_capability_needs_the_account_settings_feature() -> (
    None
):
    """v0.3.0 has no ``account-settings`` feature, so it refuses an app that uses either key
    beside account settings, at install, enable and start (checked on the released tree: it
    reads both keys as unknown and answers ``incompatible``, missing ``account-settings``). A
    ``multiInstance`` capability's ``requiresAccountFields`` it drops when it reads the manifest,
    and builds the capability as it always did."""
    raw = _requiring(["server"])
    raw["connections"] = CONNECTIONS
    raw["requiresCoreFeatures"] = []
    errors = AppManifest.from_dict(raw).validate()
    assert any("account-settings" in e for e in errors), errors


def test_a_save_that_changes_a_capability_s_settings_sets_its_failure_back_to_unknown(home):
    _install(_manifest())
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    health.report_ok(APP, "search")
    health.report_refused(APP, "search", "The search server refused the key.")
    ProviderSettings.update_capability(APP, "search", {"search_key": SECRET_MASK})
    assert account_health.accounts(APP)[0]["health"] == "refused", "nothing changed"

    ProviderSettings.update_capability(APP, "search", {"search_key": "fixture-new-search-key-99"})
    account = account_health.accounts(APP)[0]
    assert (account["health"], account["reason"]) == (
        "unknown",
        "Not checked since its settings were changed.",
    )
    assert account["lastOk"] is not None, "the last good contact stays"
    health.report_ok(APP, "inbox")
    assert account_health.accounts(APP)[0]["health"] == "unknown", "only search's next report"
    health.report_ok(APP, "search")
    assert account_health.accounts(APP)[0]["health"] == "connected"


def test_a_capability_s_change_leaves_another_capability_s_failure(home) -> None:
    _install(_manifest())
    health.report_unreachable(APP, "inbox", "The mail server did not answer.")
    health.report_refused(APP, "search", "The search server refused the key.")
    ProviderSettings.update_capability(APP, "search", {"max_results": 9})
    account = account_health.accounts(APP)[0]
    assert (account["health"], account["reason"]) == (
        "unreachable",
        "The mail server did not answer.",
    )


def test_a_value_given_again_counts_as_a_change(home) -> None:
    """No secret's value is read to tell whether a save changed it, so the same value entered
    again counts as a change: it only sets the failure back to unknown until the next report."""
    _install(_manifest())
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    health.report_refused(APP, "search", "The search server refused the key.")
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    assert account_health.accounts(APP)[0]["health"] == "unknown"


def _no_value_read(monkeypatch) -> list[str]:
    """Make every read of a secret's value fail, and list the ones tried."""
    from personalclaw.config import credentials, secret_refs

    tried: list[str] = []

    def refuse(key: str) -> str:
        tried.append(key)
        raise AssertionError(f"a secret's value was read: {key}")

    monkeypatch.setattr(credentials, "get_credential", refuse)
    monkeypatch.setattr(secret_refs, "get_credential", refuse)
    return tried


@pytest.mark.asyncio
async def test_a_save_reads_no_secret_value_to_tell_whether_it_changed(home, monkeypatch) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    health.report_refused(APP, "inbox", "The mail server refused the token.")
    health.report_refused(APP, "search", "The search server refused the key.")
    with monkeypatch.context() as patched:
        tried = _no_value_read(patched)
        # The whole writes: update_account and update_capability merge, so they read the values
        # to hand back, after the save.
        ProviderSettings.save(APP, {"token": SECRET_MASK, "search_key": SECRET_MASK})
        ProviderSettings.save_capability(APP, "search", {"search_key": SECRET_MASK})
        async with TestClient(TestServer(_gateway())) as client:
            read = await (await client.get(f"/api/apps/{APP}/config")).json()
            same = await client.put(
                f"/api/apps/{APP}/config",
                json=read["config"],
                headers={"If-Match": read["revision"]},
            )
            assert same.status == 200, await same.text()
    assert tried == []
    assert account_health.accounts(APP)[0]["health"] == "refused", "a save as shown changes nothing"
    assert {r["id"]: r for r in account_health.capabilities(APP)}["search"]["lastOk"] is None


# ── a report reads nothing, so it may be made on the loop ────────────────────────────────────


def _not_on(thread: int, real):
    def guarded(*args, **kwargs):
        if threading.get_ident() == thread:
            raise AssertionError(f"{real.__name__} ran on the loop")
        return real(*args, **kwargs)

    return guarded


@pytest.mark.asyncio
async def test_a_report_made_on_the_loop_reads_no_credential_record_or_file(
    home, monkeypatch
) -> None:
    from personalclaw.apps import app_manager
    from personalclaw.config import credentials, secret_refs
    from personalclaw.providers import accounts, settings

    manifest = _install(_manifest())
    registry_module.get_provider_registry().register(manifest, enabled=False)
    work = ProviderSettings.add_account(APP, "Work", "work")
    ProviderSettings.update_account(APP, {"token": TOKEN}, work)
    loop = threading.get_ident()
    for module, name in [
        (credentials, "get_credential"),
        (secret_refs, "get_credential"),
        (credentials, "credential_names"),
        (accounts, "read_record"),
        (settings, "load_stored_account"),
        (settings, "load_stored_capability"),
        (app_manager, "_manifest_of"),
    ]:
        monkeypatch.setattr(module, name, _not_on(loop, getattr(module, name)))

    started = time.monotonic()
    health.report_refused(APP, "inbox", f"login refused for token {TOKEN}", account=work)
    health.report_ok(APP, "search", account=work, note=f"Checked with {TOKEN}")
    health.report_unreachable(APP, "search", "The search server did not answer.")
    assert time.monotonic() - started < 0.5

    rows = {r["id"]: r for r in await asyncio.to_thread(account_health.accounts, APP)}
    caps = await asyncio.to_thread(account_health.capabilities, APP, work)
    assert (rows[work]["health"], rows["default"]["health"]) == ("refused", "unreachable")
    assert rows[work]["reason"].startswith("login refused for token ")
    assert TOKEN not in json.dumps([rows, caps]), "masked where it is read, off the loop"


def test_a_sentence_is_masked_once_and_then_only_its_masked_text_is_held(home, monkeypatch) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    health.report_refused(APP, "inbox", f"login refused for token {TOKEN}")
    reads: list[str] = []
    real = account_health._secret_values
    monkeypatch.setattr(account_health, "_secret_values", lambda *a: reads.append(a[0]) or real(*a))
    first = account_health.accounts(APP)[0]["reason"]
    assert account_health.accounts(APP)[0]["reason"] == first and TOKEN not in first
    assert reads == [APP], "the second read masks nothing again"
    held = [r.sentence for rs in account_health._open.values() for r in rs.values()]
    assert [t.raw for t in held] == [""], "the text as reported is not kept once masked"


def test_a_sentence_whose_secrets_cannot_be_read_shows_nothing_yet(home, monkeypatch) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    health.report_refused(APP, "inbox", f"login refused for token {TOKEN}")
    with monkeypatch.context() as patched:
        patched.setattr(account_health, "_secret_values", lambda *a: None)
        row = account_health.accounts(APP)[0]
    assert (row["health"], row["reason"]) == ("refused", "")
    assert account_health.accounts(APP)[0]["reason"].startswith("login refused for token ")


def test_a_save_drops_the_notes_a_replaced_secret_could_be_in(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    health.report_ok(APP, "inbox", note=f"Signed in with {TOKEN}")
    health.report_ok(APP, "search", note=f"Queried with {SEARCH_KEY}")
    ProviderSettings.update_capability(APP, "search", {"search_key": "fixture-new-search-key-99"})
    notes = {r["id"]: (r["note"], r["lastOk"]) for r in account_health.capabilities(APP)}
    assert notes["search"][0] == "" and notes["search"][1] is not None
    assert notes["inbox"][0].startswith("Signed in with "), "another capability's note stays"

    health.report_ok(APP, "inbox", note=f"Signed in with {TOKEN}")
    ProviderSettings.update_account(APP, {"token": "fixture-new-token-55aa"})
    notes = {r["id"]: (r["note"], r["lastOk"]) for r in account_health.capabilities(APP)}
    assert notes["inbox"][0] == "" and notes["inbox"][1] is not None
    assert TOKEN not in json.dumps(notes)


def test_a_report_about_an_account_added_again_is_kept(home) -> None:
    _install(_manifest())
    ProviderSettings.add_account(APP, "Work", "work")
    ProviderSettings.remove_account(APP, "work")
    health.report_refused(APP, "inbox", "Refused.", account="work")
    ProviderSettings.add_account(APP, "Work again", "work")
    health.report_refused(APP, "inbox", "Refused again.", account="work")
    rows = {row["id"]: row for row in account_health.accounts(APP)}
    assert (rows["work"]["health"], rows["work"]["reason"]) == ("refused", "Refused again.")


def test_a_sentence_carries_no_direction_or_other_format_character(home) -> None:
    _install(_manifest())
    health.report_refused(APP, "inbox", "abc \u202edcba\u202c \u2066x\u2069 \u200bend\u0007")
    assert account_health.accounts(APP)[0]["reason"] == "abc dcba x end"


# ── the list reads the rows off the loop ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_apps_list_builds_its_health_rows_off_the_loop(home, monkeypatch) -> None:
    _install(_manifest())
    health.report_refused(APP, "inbox", "The mail server refused the token.")
    loop = threading.get_ident()
    threads: list[int] = []
    for name in ("accounts", "capabilities"):
        real = getattr(account_health, name)

        def seen(*args, _real=real, **kwargs):
            threads.append(threading.get_ident())
            return _real(*args, **kwargs)

        monkeypatch.setattr(account_health, name, seen)
    async with TestClient(TestServer(_gateway())) as client:
        row = await _row(client)
    assert row["accounts"][0]["health"] == "refused"
    assert len(threads) == 2 and loop not in threads


def test_the_list_reads_the_credential_store_s_names_once(home, monkeypatch) -> None:
    from personalclaw.config import credentials
    from personalclaw.dashboard.handlers import apps as apps_handlers

    raw = _requiring(["server"])
    raw["providers"][0]["requiresAccountFields"] = ["token"]
    _install(raw)
    calls: list[int] = []
    real = credentials.credential_names
    monkeypatch.setattr(credentials, "credential_names", lambda: calls.append(1) or real())
    rows = apps_handlers._health_rows([APP])
    needs = {c["id"]: c["needsSetup"] for c in rows[APP]["capabilities"]}
    assert needs == {"inbox": ["token"], "search": ["server"]}
    assert len(calls) == 1
