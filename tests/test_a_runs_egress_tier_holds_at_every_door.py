"""A run's egress tier holds for every network call made inside the run, not only its page fetch.

A run's safety profile names an egress tier (``off``, ``listed``, ``registry`` or ``all``), and the
operator ceiling bounds it. Only the agent's page fetch read it, so a run whose tier allowed it no
network at all still reached its search provider's host, and an app that fetches through the SDK,
a generated image's download and the browser's navigations were held to the owner's Network egress
settings alone. The guard now narrows every request it is asked about by the run the call is made
for, so each door that asks it is held to the tier; a call no run is made for (the owner's own
action in the app) keeps to her settings as before.

Every endpoint here is a stand-in on this machine or a reserved name, so nothing leaves it.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from personalclaw import mcp_core
from personalclaw.config.loader import config_dir
from personalclaw.guardrails.ceiling import ceiling_path, reset_ceiling
from personalclaw.net import guard
from personalclaw.net.guard import evaluate
from personalclaw.net.policy import (
    BROWSE,
    CONNECTOR,
    FETCH_ACTION,
    LOOPBACK_INTERNAL,
    MEDIA,
    WEBHOOK,
    egress_policy_for,
    egress_policy_for_profile,
    sync_egress_policy,
)
from personalclaw.search_providers import registry
from personalclaw.search_providers.base import (
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)
from personalclaw.sel import sel

#: What a run with no network is told, in the words the agent's page fetch has always used.
EGRESS_OFF_REASON = "egress is off for this run (safety profile egress tier 'off')"
#: A chat's session, as the built-in agent binds it around each tool call it dispatches.
CHAT = "dashboard:research-chat"
#: A host on no list, under a reserved name: refused or never looked up.
ELSEWHERE = "search.invalid"


class _StandIn(http.server.ThreadingHTTPServer):
    """A search provider's API on this machine that records each request it is sent."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _StandInHandler)
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _StandInHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        body = json.dumps(
            {"results": [{"url": "https://example.com/a", "title": "A", "content": "a page"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _SearchApp(SearchProvider):
    """A search provider shaped as the search apps are: one request through the SDK's guarded
    fetch under the connector policy and the owner's settings, and a refusal said in the guard's
    words (``personalclaw.sdk.net.egress_refusal``)."""

    def __init__(self, endpoint: str, name: str = "stand-in") -> None:
        self._endpoint = endpoint
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Stand-in search"

    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities()

    async def is_available(self) -> bool:
        return True

    async def search(self, query: str, **_kw) -> SearchResult:
        from personalclaw.sdk.net import (
            CONNECTOR,
            EgressBlocked,
            egress_policy_for,
            egress_refusal,
            fetch,
        )

        url = f"{self._endpoint}/search?q={query}"
        try:
            resp = await fetch(url, policy=egress_policy_for(CONNECTOR), method="GET")
        except EgressBlocked as e:
            raise RuntimeError(egress_refusal(url, e.decision)) from e
        hits = [
            SearchHit(url=r["url"], title=r["title"], snippet=r["content"])
            for r in json.loads(resp.text)["results"]
        ]
        return SearchResult(results=hits, provider=self.name, query=query)


@pytest.fixture
def stand_in(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    server = _StandIn()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def searches(monkeypatch, stand_in):
    """The stand-in, the one search provider registered, on a host the owner allowed (as she
    allows her own search server on this machine or her network)."""
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_provider_app", {})
    monkeypatch.setattr(registry, "_checks", {})
    provider = _SearchApp(stand_in.url)
    registry.register_provider(provider, app="stand-in-search")
    _owner_allows("127.0.0.1")
    return provider


@pytest.fixture
def looked_up(monkeypatch):
    """Every name the guard looks up; a reserved name answers nothing."""
    names: list[str] = []

    def resolve(host: str) -> list[str]:
        names.append(host)
        if host == "127.0.0.1":
            return ["127.0.0.1"]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(guard, "_resolve", resolve)
    return names


def _owner_allows(*hosts: str) -> None:
    """The owner's Allowed hosts in Settings → Security → Network egress."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": list(hosts)}}}), encoding="utf-8"
    )


def _ceiling(egress: str) -> None:
    """The operator ceiling bounding every run's egress tier to *egress*."""
    path = ceiling_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": egress}}}), encoding="utf-8"
    )
    reset_ceiling()


@contextmanager
def _in_run(session_key: str = CHAT):
    """A call made inside a run, bound as every seam that dispatches a tool call binds it."""
    token = mcp_core.set_current_session_key(session_key)
    try:
        yield
    finally:
        mcp_core.reset_current_session_key(token)


def _egress_rows(outcome: str) -> list[tuple[str, str]]:
    return [
        (row.get("caller_identity", ""), row.get("resources", ""))
        for row in reversed(sel().recent(300))
        if row.get("operation") == "egress_fetch" and row.get("outcome") == outcome
    ]


# ── a search provider's request ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_search_in_a_run_whose_egress_is_off_sends_nothing(searches, stand_in):
    _ceiling("off")

    with _in_run(), pytest.raises(RuntimeError) as refused:
        await registry.search_with_fallback("search-general", "tide tables")

    said = str(refused.value)
    assert stand_in.paths == [], "a run with no network reached its search provider"
    assert f"was not reached: {EGRESS_OFF_REASON}." in said, said
    assert "Allowed hosts" not in said, "no host setting lifts a run's tier, so none is named"
    url = f"{stand_in.url}/search?q=tide tables"
    assert ("net.fetch:connector", url) in _egress_rows("denied")


@pytest.mark.asyncio
async def test_a_search_in_a_listed_run_reaches_only_the_allowed_hosts(
    searches, stand_in, looked_up
):
    _ceiling("listed")

    with _in_run():
        found = await registry.search_with_fallback("search-general", "tide tables")
        elsewhere = _SearchApp(f"https://{ELSEWHERE}", name="elsewhere")
        with pytest.raises(RuntimeError) as refused:
            await elsewhere.search("tide tables")

    assert [h.url for h in found.results] == ["https://example.com/a"]
    assert len(stand_in.paths) == 1
    said = str(refused.value)
    assert "this run reaches only the hosts it lists" in said, said
    assert f"add {ELSEWHERE} to Allowed hosts" in said, said
    assert ELSEWHERE not in looked_up, "a host off the run's list was looked up"


@pytest.mark.asyncio
async def test_a_search_in_an_ordinary_run_still_reaches_its_provider(searches, stand_in):
    with _in_run():
        found = await registry.search_with_fallback("search-general", "tide tables")

    assert [h.url for h in found.results] == ["https://example.com/a"]
    assert len(stand_in.paths) == 1 and stand_in.paths[0].startswith("/search?q=tide")
    assert _egress_rows("denied") == []


@pytest.mark.asyncio
async def test_the_owners_own_test_of_a_provider_is_held_to_her_settings_alone(searches, stand_in):
    """Her Test in Settings is made for no run: a ceiling that turns every run's egress off does
    not reach it, and her Network egress settings still do."""
    _ceiling("off")

    check = await registry.check_provider(searches)

    assert check.ok, check.detail
    assert len(stand_in.paths) == 1


# ── every door that asks the guard ───────────────────────────────────────────


def test_every_door_that_asks_the_guard_is_held_to_the_run(looked_up):
    _ceiling("off")
    policies = [
        egress_policy_for(CONNECTOR),
        egress_policy_for(WEBHOOK),
        egress_policy_for(MEDIA),
        egress_policy_for(BROWSE),
        egress_policy_for(FETCH_ACTION).with_overrides(allow_hosts=("feeds.example",)),
        sync_egress_policy("https://storage.example"),
    ]
    with _in_run():
        decisions = [evaluate("https://feeds.example/x", p) for p in policies]
        own = evaluate("http://127.0.0.1:9/api/self", LOOPBACK_INTERNAL)
    looked_up_in_the_run = list(looked_up)
    outside = evaluate("https://feeds.example/x", egress_policy_for(CONNECTOR))

    assert [d.category for d in decisions] == ["egress_off"] * len(policies)
    assert {d.reason for d in decisions} == {EGRESS_OFF_REASON}
    assert "feeds.example" not in looked_up_in_the_run, "a run with no network looked a host up"
    assert own.allow, "the gateway's calls to itself carry nothing off this machine"
    assert outside.category == "unresolvable", "a call no run is made for is not narrowed"


def test_the_refusal_says_which_bound_turned_egress_off(looked_up):
    _ceiling("off")

    with _in_run():
        decision = evaluate("https://feeds.example/x", CONNECTOR)

    hints = " ".join(decision.recovery_hints)
    assert str(ceiling_path()) in hints and '"egress": "off"' in hints, hints
    assert "restarts" in hints, hints


@pytest.mark.asyncio
async def test_a_page_fetch_in_a_run_whose_egress_is_off_is_refused_and_audited(stand_in):
    from personalclaw.web.fetch import web_fetch

    _owner_allows("127.0.0.1")
    _ceiling("off")
    url = f"{stand_in.url}/page"

    with _in_run():
        outcome = await web_fetch(url, session_key=CHAT, require_provenance=False)

    assert not outcome.ok and outcome.error == EGRESS_OFF_REASON
    assert outcome.risk_level == "destructive"
    assert stand_in.paths == []
    assert ("net.fetch:strict", url) in _egress_rows("denied")


@pytest.mark.asyncio
async def test_a_render_in_a_run_whose_egress_is_off_is_refused_and_audited(monkeypatch):
    from personalclaw.web import render

    monkeypatch.setattr(render, "is_available", lambda: True)
    _ceiling("off")
    url = "https://pages.example/app"

    with _in_run():
        result = await render.render_url(url, resolver=lambda host: ["93.184.216.34"])

    assert not result.ok and result.error == EGRESS_OFF_REASON
    assert ("web.render:strict", url) in _egress_rows("denied")


# ── a tier only ever narrows ─────────────────────────────────────────────────


def test_a_registry_tier_adds_no_host_to_a_surface_that_names_its_own():
    pinned = FETCH_ACTION.with_overrides(allow_hosts=("feeds.example",))

    narrowed = egress_policy_for_profile(pinned, "registry")

    assert narrowed is not None and narrowed.allow_only
    assert narrowed.allow_hosts == ("feeds.example",)


def test_a_registry_run_still_asks_before_its_shell_reaches_a_registry():
    """The hosts a shell command reaches unasked are the owner's list; a registry tier lets the
    run reach a registry, and does not make a command that does one a person never sees."""
    from personalclaw.run_bounds import shell_egress_policy

    _owner_allows("git.example")
    _ceiling("registry")

    policy = shell_egress_policy("cron:nightly")

    assert policy is not None and policy.allow_hosts == ("git.example",)


# ── the tool server an agent CLI runs ────────────────────────────────────────


def test_an_agent_clis_tool_call_is_held_to_its_sessions_tier(monkeypatch, looked_up):
    from personalclaw import memory_writes

    _ceiling("off")
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", CHAT)
    monkeypatch.setitem(mcp_core._SESSION_MODES, CHAT, memory_writes.PERSISTENT)
    seen: dict[str, str] = {}

    def call(name: str, raw_args: dict) -> str:
        seen["session"] = mcp_core.get_current_session_key()
        seen["category"] = evaluate(
            "https://media.example/x.png", egress_policy_for(MEDIA)
        ).category
        return "{}"

    monkeypatch.setattr(mcp_core, "_aggregated_call_tool", call)
    mcp_core._call_as_its_session("image_generate", {})

    assert seen == {"session": CHAT, "category": "egress_off"}
    assert mcp_core.get_current_session_key() == ""


# ── a program an app's code starts ───────────────────────────────────────────

APP = "egress-tier-fixture"
GIT = "/nonexistent/pc-fixture/git"
REPO = "https://code.invalid/owner/repo.git"


@pytest.fixture
def app(tmp_path, monkeypatch):
    from personalclaw import app_code
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.apps.native_contract import load_bundle_module
    from personalclaw.providers.registry import get_provider_registry

    folder = tmp_path / APP
    folder.mkdir()
    (folder / "provider.py").write_text(
        "import subprocess\n\n\ndef launch(argv):\n"
        "    return subprocess.run(argv, capture_output=True, check=False)\n",
        encoding="utf-8",
    )
    manifest = AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "displayName": "Egress tier fixture",
            "description": "Starts a program.",
            "launches": [{"program": "git", "why": "Fetches a repo.", "hosts": ["code.invalid"]}],
        }
    )
    monkeypatch.setitem(
        get_provider_registry()._extensions, APP, SimpleNamespace(manifest=manifest)
    )
    _owner_allows()
    module = load_bundle_module(folder, APP, "provider")
    yield module
    app_code.release(APP)


def test_a_program_an_app_starts_in_a_run_whose_egress_is_off_is_stopped(app):
    from personalclaw.apps.launch_egress import LaunchRefused

    _ceiling("off")

    with _in_run(), pytest.raises(LaunchRefused) as refused:
        app.launch([GIT, "clone", REPO])
    with pytest.raises(FileNotFoundError):  # no run: the host it declares is let through
        app.launch([GIT, "clone", REPO])

    said = str(refused.value)
    assert APP in said and "code.invalid" in said and EGRESS_OFF_REASON in said, said
    assert "Allowed hosts" not in said, said
