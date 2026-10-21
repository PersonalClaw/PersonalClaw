"""A mid-chat switch to a dearer model is asked about once, with both prices (#3808).

A chat's next turn re-reads the whole conversation, so a switch of model is paid for at once, at
the new model's input price. On a chat of 50,000 tokens or more the composer asks before a switch
to a dearer model, and ``GET /api/chat/switch-price`` gives it the numbers: the chat's context as
its ring measured it, and the input price of those tokens on the model its latest turn ran on and
on the one picked, through the one pricing function, so the prices the owner set apply.

The rates here are the owner's own (``model_prices.overrides``), fixed per test. The current model
is the one the chat names, or on Auto the one its latest turn was answered by (``answered_by``,
which the chat runner sets where it prices the turn:
``test_a_chat_turn_records_the_model_that_answered_it``).
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import save_overlay
from tests.chat_test_helpers import _api_app, _make_state

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

#: Per million input tokens: a small model, a large one five times dearer, one priced like the small
#: one, one between them, and one the owner runs for free.
SMALL, LARGE, LIKE_SMALL, MEDIUM, FREE = 0.5, 2.5, 0.5, 1.5, 0.0


@pytest.fixture(autouse=True)
def _prices():
    """The owner's prices for the three models, read fresh: the overlay memo is process-global."""
    rates_mod._overlay_cache = None
    save_overlay(
        {
            "work:small-1": {"in_per_mtok": SMALL, "out_per_mtok": 2.0},
            "work:large-1": {"in_per_mtok": LARGE, "out_per_mtok": 10.0},
            "work:twin-1": {"in_per_mtok": LIKE_SMALL, "out_per_mtok": 2.0},
            "work:medium-1": {"in_per_mtok": MEDIUM, "out_per_mtok": 6.0},
            "work:free-1": {"in_per_mtok": FREE, "out_per_mtok": 0.0},
        }
    )
    yield
    rates_mod._overlay_cache = None


def _app(state) -> web.Application:
    from personalclaw.dashboard.chat import api_chat_switch_price

    app = _api_app(state)
    app.router.add_get("/api/chat/switch-price", api_chat_switch_price)
    return app


async def _read(state, query: str) -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get(f"/api/chat/switch-price{query}")
        return resp.status, await resp.json()


def _chat(state, *, tokens: int | None, on: tuple[str, str] | None = ("work", "small-1")):
    """A chat on Auto whose ring measured *tokens* of a 200,000-token window, its latest turn
    answered by *on*."""
    chat = state.get_or_create_session()
    if tokens is not None:
        chat.context_usage = {"pct": tokens / 2_000, "window": 200_000}
    chat.answered_by = on
    return chat


async def test_a_large_chat_switching_to_a_dearer_model_is_asked_with_both_prices(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000)
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=large-1")
    assert status == 200
    assert body["ask"] is True
    assert body["tokens"] == 120_000
    assert body["threshold"] == 50_000
    assert body["current"] == {"provider": "work", "model": "small-1", "dollars": 0.06}
    assert body["next"] == {"provider": "work", "model": "large-1", "dollars": 0.3}


async def test_a_small_chat_is_not_asked(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=10_000)
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=large-1")
    assert status == 200
    assert body["ask"] is False
    assert body["tokens"] == 10_000


async def test_the_threshold_is_where_asking_starts(tmp_path):
    state = _make_state(tmp_path)
    at = _chat(state, tokens=50_000)
    assert (await _read(state, f"?session={at.key}&provider=work&model=large-1"))[1]["ask"] is True
    state2 = _make_state(tmp_path / "below")
    below = _chat(state2, tokens=49_998)
    assert (await _read(state2, f"?session={below.key}&provider=work&model=large-1"))[1][
        "ask"
    ] is False


@pytest.mark.parametrize("model", ["small-1", "twin-1"])
async def test_a_switch_that_costs_no_more_is_not_asked(tmp_path, model):
    state = _make_state(tmp_path)
    chat = _chat(
        state, tokens=120_000, on=("work", "large-1") if model == "small-1" else ("work", "small-1")
    )
    status, body = await _read(state, f"?session={chat.key}&provider=work&model={model}")
    assert status == 200
    assert body["ask"] is False


async def test_a_switch_to_a_model_nothing_prices_is_asked_with_its_price_unknown(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000)
    status, body = await _read(state, f"?session={chat.key}&provider=elsewhere&model=unpriced-9")
    assert status == 200
    assert body["ask"] is True
    assert body["next"]["dollars"] is None, "unknown is not free"
    assert body["current"]["dollars"] == 0.06


async def test_a_chat_whose_context_was_never_measured_is_not_asked(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=None)
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=large-1")
    assert status == 200
    assert body["ask"] is False
    assert body["tokens"] is None
    assert body["current"]["dollars"] is None and body["next"]["dollars"] is None


async def test_a_chat_with_no_turn_yet_is_not_asked(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000, on=None)
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=large-1")
    assert status == 200
    assert body["ask"] is False
    assert body["current"] == {"provider": "", "model": "", "dollars": None}


async def test_on_auto_the_current_model_is_the_one_the_latest_turn_was_answered_by(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000, on=("work", "large-1"))
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=medium-1")
    assert body["current"]["model"] == "large-1"
    assert body["ask"] is False, "medium-1 costs less than large-1"


@pytest.mark.parametrize("named", ["work:small-1", "work/small-1"])
async def test_a_chat_that_names_a_model_is_priced_against_it(tmp_path, named):
    """Switched large-1 -> small-1 with no turn since, the chat runs on small-1: a switch to
    medium-1 then costs more than its next reply would, though less than its last one did."""
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000, on=("work", "large-1"))
    chat.model = named
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=medium-1")
    assert body["current"] == {"provider": "work", "model": "small-1", "dollars": 0.06}
    assert body["ask"] is True


def test_a_named_model_reads_as_its_entry_and_id():
    from personalclaw.dashboard.chat_switch_price import named_model

    assert named_model("work:small-1") == ("work", "small-1")
    assert named_model("work:gpt-oss:20b") == ("work", "gpt-oss:20b")
    assert named_model("work/small-1") == ("work", "small-1")
    # An id may hold the other separator after the entry's own.
    assert named_model("bedrock/us.anthropic.claude-v1:0") == (
        "bedrock",
        "us.anthropic.claude-v1:0",
    )
    assert named_model("ollama/llama3:8b") == ("ollama", "llama3:8b")
    assert named_model("openrouter:meta-llama/llama-3") == ("openrouter", "meta-llama/llama-3")
    assert named_model("small-1") == ("", "small-1")
    assert named_model("") is None and named_model("Auto") is None


async def test_a_switch_to_a_model_known_to_be_free_is_not_asked(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000, on=("elsewhere", "unpriced-9"))
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=free-1")
    assert body["next"]["dollars"] == 0
    assert body["ask"] is False


async def test_an_agent_cli_chat_is_never_asked(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000)
    chat.acp_provider = "acp:switch-test-cli"
    status, body = await _read(state, f"?session={chat.key}&provider=work&model=large-1")
    assert status == 200
    assert body["ask"] is False


async def test_an_unknown_chat_or_no_model_is_refused(tmp_path):
    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000)
    assert (await _read(state, "?session=no-such-chat&provider=work&model=large-1"))[0] == 404
    assert (await _read(state, f"?session={chat.key}&provider=work"))[0] == 400


async def test_only_a_signed_in_owner_reaches_it_and_a_read_changes_nothing(tmp_path):
    """A caller with no credential is refused at the token gate and nothing is read; the owner's
    cookie reaches it, and its answer leaves the chat and the files as they were. An app is refused
    it as every owner-only read is."""
    from aiohttp import DummyCookieJar

    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly
    from personalclaw.dashboard.chat import api_chat_switch_price
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    assert isinstance(ROUTE_AUTHZ["GET /api/chat/switch-price"], OwnerOnly)
    reached: list[str] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        reached.append(request.path)
        return await api_chat_switch_price(request)

    state = _make_state(tmp_path)
    chat = _chat(state, tokens=120_000)
    app = web.Application(
        middlewares=[token_auth_middleware(port=10000, local_only=False), app_permission_middleware]
    )
    app["state"] = state
    app.router.add_get("/api/chat/switch-price", handler)
    query = f"/api/chat/switch-price?session={chat.key}&provider=work&model=large-1"
    model_before, usage_before = chat.model, dict(chat.context_usage)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get(query)
        assert resp.status == 403
        assert resp.headers.get("X-Auth-Required") == "true"
        assert "dollars" not in await resp.text()
        assert reached == []

        owner = generate_token("owner", ttl_seconds=600)
        resp = await client.get(query, headers={"Cookie": f"pc_token_10000={owner}"})
        assert resp.status == 200
        assert reached == ["/api/chat/switch-price"]
    assert (chat.model, chat.context_usage) == (model_before, usage_before)
