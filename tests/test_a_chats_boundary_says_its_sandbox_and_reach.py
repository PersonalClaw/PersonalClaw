"""A chat's Execution Boundary control reads its sandbox and its reach from the gateway.

The control beside the composer shows where the next turn may act before it runs. Its permission
mode and working folder come with the chat's own read; the two readings no other read serves a
chat come from ``GET /api/chat/boundary``:

- **Sandbox**: whether the commands the turn starts run in this computer's sandbox, and what it
  hides from them. The built-in agent's shell is wrapped at ``auto`` (``sandbox.wrap_argv``); an
  agent CLI is wrapped as its runtime entry declares (its tier and its mode). So the reading follows
  the agent the chat runs on, and a host with no sandbox it can use says so.
- **Reach**: the egress tier of the chat's run, read through the one reader the egress guard and a
  command's launch ask (``guardrails.policy.profile_for_session``).

The gateway writes each reading's words, so the browser shows what the gateway does and works
nothing out for itself.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import sandbox
from personalclaw.guardrails import policy
from personalclaw.guardrails.policy import INTERACTIVE
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from tests.chat_test_helpers import _api_app, _make_state

AGENT_CLI = "acp:boundary-test-cli"

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


def _app(state) -> web.Application:
    from personalclaw.dashboard.chat import api_chat_boundary

    app = _api_app(state)
    app.router.add_get("/api/chat/boundary", api_chat_boundary)
    return app


async def _read(state, query: str = "") -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get(f"/api/chat/boundary{query}")
        return resp.status, await resp.json()


@pytest.fixture
def agent_cli() -> Iterator[dict[str, object]]:
    """An agent CLI's runtime entry, registered as a bundle registers one; the test fills in
    what it declares."""
    options: dict[str, object] = {"command": ["boundary-test-cli", "--acp"]}
    registry = get_default_registry()
    registry.unregister_entry(AGENT_CLI)
    registry.register_entry(
        ProviderEntry(name=AGENT_CLI, type="acp_agent", model="", options=options)
    )
    try:
        yield options
    finally:
        registry.unregister_entry(AGENT_CLI)


@pytest.fixture
def backend(monkeypatch) -> Iterator[list[str]]:
    """This computer's sandbox backend, as the probe would answer, and the modes it was asked at."""
    asked: list[str] = []
    answer = ["namespace"]

    def detect(config_mode: str = "auto") -> str:
        asked.append(config_mode)
        return "none" if config_mode == "off" else answer[0]

    monkeypatch.setattr(sandbox, "detect_backend", detect)
    yield answer
    asked.clear()


@pytest.mark.parametrize("found", ["namespace", "sandbox-exec"])
async def test_a_native_chats_commands_run_in_this_computers_sandbox(tmp_path, backend, found):
    backend[0] = found
    status, body = await _read(_make_state(tmp_path))
    assert status == 200
    reading = body["sandbox"]
    assert reading["confined"] is True
    assert reading["label"] == "Sandboxed"
    # What the standard level hides, named from the list the wrap itself hides.
    for folder in sandbox._STANDARD_DIRS:
        assert f"~/{folder}" in reading["said"]
    assert reading["said"].startswith("The commands it runs are sandboxed on this computer")


async def test_a_computer_with_no_sandbox_says_its_commands_run_unsandboxed(tmp_path, backend):
    backend[0] = "none"
    _, body = await _read(_make_state(tmp_path))
    assert body["sandbox"] == {
        "confined": False,
        "label": "Not sandboxed",
        "said": (
            "This computer has no sandbox PersonalClaw can use, so the commands it runs are not "
            "sandboxed: they run with your account's access."
        ),
    }


async def test_an_agent_cli_wrapped_by_this_computer_reads_its_own_entry(
    tmp_path, backend, agent_cli
):
    _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
    reading = body["sandbox"]
    assert reading["confined"] is True and reading["label"] == "Sandboxed"
    assert reading["said"].startswith(
        "Its agent CLI and the commands it starts are sandboxed on this computer"
    )

    backend[0] = "none"
    _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
    assert body["sandbox"]["confined"] is False
    assert body["sandbox"]["said"].startswith(
        "This computer has no sandbox PersonalClaw can use, so its agent CLI and the commands it "
        "starts are not sandboxed"
    )


async def test_an_agent_cli_at_the_strict_level_says_that_level(tmp_path, backend, agent_cli):
    agent_cli["sandbox_mode"] = "strict"
    _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
    assert body["sandbox"]["confined"] is True
    assert "at its strict level" in body["sandbox"]["said"]


async def test_an_agent_cli_that_sandboxes_itself_is_not_said_to_be_wrapped(
    tmp_path, backend, agent_cli
):
    agent_cli["sandbox_mode"] = "off"  # what `self_sandboxing` makes a bundle's entry carry
    _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
    assert body["sandbox"] == {
        "confined": True,
        "label": "The CLI's own sandbox",
        "said": (
            "This computer's sandbox does not wrap its agent CLI: its runtime says the CLI "
            "applies a sandbox of its own to the commands it starts."
        ),
    }


async def test_an_agent_cli_set_to_a_sandbox_tier_names_it(tmp_path, backend, agent_cli):
    from personalclaw.sandbox_providers import register_provider, unregister_provider

    class Tier:
        name = "boundary-test-vm"
        display_name = "Test VM"

        def __init__(self, up: bool) -> None:
            self.up = up

        def available(self) -> bool:
            return self.up

    agent_cli["sandbox"] = Tier.name
    register_provider(Tier(True))  # type: ignore[arg-type]
    try:
        _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
        assert body["sandbox"] == {
            "confined": True,
            "label": "Test VM",
            "said": "Its agent CLI runs in the Test VM sandbox, with the commands it starts.",
        }
        register_provider(Tier(False))  # type: ignore[arg-type]
        _, body = await _read(_make_state(tmp_path), f"?runtime={AGENT_CLI}")
        assert body["sandbox"]["confined"] is False
        assert body["sandbox"]["said"] == (
            "Its agent CLI is set to run in the Test VM sandbox, which is not available now."
        )
    finally:
        unregister_provider(Tier.name)


async def test_an_open_chat_is_read_for_the_agent_it_runs_on(tmp_path, backend, agent_cli):
    agent_cli["sandbox_mode"] = "off"
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()
    chat.acp_provider = AGENT_CLI
    # The chat's own binding wins over a runtime the page names: the reading is of the chat.
    _, body = await _read(state, f"?session={chat.key}&runtime=")
    assert body["sandbox"]["label"] == "The CLI's own sandbox"

    native = state.get_or_create_session()
    _, body = await _read(state, f"?session={native.key}&runtime={AGENT_CLI}")
    assert body["sandbox"]["said"].startswith("The commands it runs are sandboxed")


@pytest.mark.parametrize(
    ("tier", "label", "said"),
    [
        (
            "all",
            "Network on",
            "Its web tools reach public hosts, within your Network egress settings, and the "
            "commands it runs keep the network.",
        ),
        (
            "registry",
            "Package registries only",
            "Its web tools reach only package registries, and the commands it runs get no network.",
        ),
        (
            "listed",
            "Allowed hosts only",
            "Its web tools reach only your Allowed hosts, and the commands it runs get no network.",
        ),
        (
            "off",
            "No network",
            "Nothing it runs reaches the network. The model it runs on still answers.",
        ),
    ],
)
async def test_the_reach_is_the_runs_egress_tier(tmp_path, backend, monkeypatch, tier, label, said):
    asked: list[str] = []

    def profile_for_session(key: str):
        asked.append(key)
        return INTERACTIVE.with_overrides(egress_tier=tier)

    monkeypatch.setattr(policy, "profile_for_session", profile_for_session)
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()
    _, body = await _read(state, f"?session={chat.key}")
    assert body["reach"] == {"tier": tier, "label": label, "said": said}
    # Read for the chat's own run, the key its tool calls are bound to.
    assert asked == [chat.key]

    asked.clear()
    _, body = await _read(state)
    assert body["reach"]["tier"] == tier
    assert asked == [""]  # a new chat's run: unnamed, so attended


async def test_a_chat_with_nothing_narrowing_it_reaches_the_network(tmp_path, backend):
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()
    _, body = await _read(state, f"?session={chat.key}")
    assert body["reach"]["tier"] == policy.profile_for_session(chat.key).egress_tier == "all"


async def test_an_unknown_chat_is_not_read_as_a_new_one(tmp_path, backend):
    status, body = await _read(_make_state(tmp_path), "?session=no-such-chat")
    assert status == 404
    assert "sandbox" not in body


async def test_only_a_signed_in_owner_reaches_it(tmp_path, backend):
    """A caller with no credential is refused at the token gate and nothing is read; your cookie
    reaches it. An app is refused it as every owner-only read
    (``test_apps_see_only_their_own_conversations``)."""
    from aiohttp import DummyCookieJar

    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly
    from personalclaw.dashboard.chat import api_chat_boundary
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    assert isinstance(ROUTE_AUTHZ["GET /api/chat/boundary"], OwnerOnly)
    reached: list[str] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        reached.append(request.path)
        return await api_chat_boundary(request)

    app = web.Application(
        middlewares=[token_auth_middleware(port=10000, local_only=False), app_permission_middleware]
    )
    app["state"] = _make_state(tmp_path)
    app.router.add_get("/api/chat/boundary", handler)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get("/api/chat/boundary")
        assert resp.status == 403
        assert resp.headers.get("X-Auth-Required") == "true"
        assert "sandbox" not in await resp.text()
        assert reached == []

        owner = generate_token("owner", ttl_seconds=600)
        resp = await client.get("/api/chat/boundary", headers={"Cookie": f"pc_token_10000={owner}"})
        assert resp.status == 200
        assert reached == ["/api/chat/boundary"]


async def test_it_says_no_path_secret_or_command_and_changes_nothing(tmp_path, backend, agent_cli):
    """The answer is two readings of fixed words: nothing of the runtime's command, its
    environment, the chat's folder or the query is said back, and a read writes nothing."""
    agent_cli["command"] = [str(tmp_path / "bin" / "boundary-test-cli"), "--acp"]
    agent_cli["env"] = {"BOUNDARY_TEST_KEY": "fake-boundary-test-value"}
    state = _make_state(tmp_path)
    chat = state.get_or_create_session()
    chat.acp_provider = AGENT_CLI
    chat.workspace_dir = str(tmp_path / "work")
    before = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file())
    attrs = _fields(chat)

    for query in (f"?session={chat.key}", f"?runtime={AGENT_CLI}", "?runtime=acp:<b>" + "x" * 64):
        status, body = await _read(state, query)
        assert status == 200
        assert set(body) == {"sandbox", "reach"}
        assert set(body["sandbox"]) == {"confined", "label", "said"}
        assert set(body["reach"]) == {"tier", "label", "said"}
        said = repr(body)
        for leak in (
            str(tmp_path),
            "boundary-test-cli",
            "fake-boundary",
            "BOUNDARY_TEST_KEY",
            "<b>",
        ):
            assert leak not in said, (query, leak)

    assert sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()) == before
    assert _fields(chat) == attrs


def _fields(obj: object) -> dict[str, str]:
    """Every slot of *obj*, as it reads now."""
    names = [a for cls in type(obj).__mro__ for a in getattr(cls, "__slots__", ())]
    return {a: repr(getattr(obj, a, None)) for a in names}
