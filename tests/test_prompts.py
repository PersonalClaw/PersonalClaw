"""Tests for prompt discovery through the PromptProvider seam.

Core discovers prompts ONLY via the vendor-neutral PromptProvider (the
bundled native filesystem provider backing ``~/.personalclaw/prompts/*.yaml``).
There is no marketplace / package on-disk-format coupling in core.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.chat import _expand_prompt_mention, _run_chat
from personalclaw.dashboard.handlers import (
    _list_provider_prompts,
    api_prompt_detail,
    api_prompts,
)

# ── Shared fixtures ──


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """All tests get an isolated $HOME and no project dir.

    The native prompt provider resolves its directory lazily via
    ``config_dir()`` (``$HOME/.personalclaw/prompts``), so patching
    ``Path.home`` isolates provider storage per-test.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.agent._project_dir", lambda: None)
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    # These tests assert on a user-only prompt store; skip seeding the bundled
    # default system prompt so discovery/listing assertions stay deterministic.
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


@pytest.fixture()
def mock_sel(monkeypatch):
    """Patch sel() in both chat and handlers modules."""
    m = MagicMock()
    monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: m)
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: m)
    return m


# ── Helpers ──


def _provider_prompt(tmp_path, name, content="Do the thing.", *, description="",
                     variables=None, tags=None):
    """Write a native-provider YAML prompt under ~/.personalclaw/prompts/."""
    d = tmp_path / ".personalclaw" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    import yaml

    payload = {"name": name, "content": content}
    if description:
        payload["description"] = description
    if variables:
        payload["variables"] = variables
    if tags:
        payload["tags"] = tags
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return p


def _api_request(name):
    r = MagicMock()
    r.match_info = {"name": name}
    return r


class _Session:
    """Minimal session stub for prompt tests."""

    def __init__(self):
        self.messages = []
        self.key = "t"
        self.agent = "personalclaw"
        self.model = None
        self._queue = []

    def append(self, role, text, cls):
        self.messages.append((role, text, cls))


class _State:
    _hook_store = None
    _yolo = False

    def push_refresh(self, *a):
        pass

    def __init__(self):
        self.sessions = type('_MockSessions', (), {
            'get_channel_link': lambda self, k: ('', ''),
            'set_channel_link': lambda self, k, t, c: None,
            'get_or_create': None, 'get_pid': lambda self, k: None,
            'set_approval_policy': lambda self, k, v: None,
            'check_context_usage': lambda self, k, c: None,
        })()

    def push_sessions_update(self):
        pass

    def broadcast_ws(self, *a, **kw):
        pass


def _ss():
    """Fresh state + session pair."""
    return _State(), _Session()


# ── Provider discovery (_list_provider_prompts) ──


class TestListProviderPrompts:
    def test_empty(self, tmp_path):
        assert _list_provider_prompts() == []

    def test_discovers_user_prompt(self, tmp_path):
        _provider_prompt(tmp_path, "my-prompt", "Do things.", description="A test prompt")
        r = _list_provider_prompts()
        assert len(r) == 1
        assert (r[0]["name"], r[0]["fullName"], r[0]["source"]) == (
            "my-prompt", "my-prompt", "user")
        assert r[0]["description"] == "A test prompt"

    def test_lists_multiple_sorted(self, tmp_path):
        _provider_prompt(tmp_path, "bravo")
        _provider_prompt(tmp_path, "alpha")
        names = [p["name"] for p in _list_provider_prompts()]
        assert names == ["alpha", "bravo"]

    def test_surfaces_variables_and_tags(self, tmp_path):
        _provider_prompt(
            tmp_path, "templated", "Hello {{who}}.",
            variables=[{"name": "who", "type": "string", "required": True}],
            tags=["greet"],
        )
        r = _list_provider_prompts()
        assert r[0]["tags"] == ["greet"]
        assert r[0]["variables"][0]["name"] == "who"


# ── _expand_prompt_mention (provider-backed @prompt expansion) ──


class TestExpandPromptMention:
    def test_resolves_bare_name(self, tmp_path):
        _provider_prompt(tmp_path, "p", "Instructions.")
        msg, status = _expand_prompt_mention("@p", _State(), _Session())
        assert status == "ok" and "Instructions." in msg
        assert msg.startswith("Execute the following instructions:")

    def test_appends_user_text(self, tmp_path):
        _provider_prompt(tmp_path, "g", "Generate.")
        msg, status = _expand_prompt_mention("@g for Q1", _State(), _Session())
        assert status == "ok" and "Generate." in msg and "for Q1" in msg

    def test_no_match(self, tmp_path):
        msg, status = _expand_prompt_mention("@nope hello", _State(), _Session())
        assert (msg, status) == ("@nope hello", "not_found")

    def test_package_qualified_matches_bare_name(self, tmp_path):
        """The provider has no package concept: ``@pkg/name`` matches bare ``name``."""
        _provider_prompt(tmp_path, "d", "Bare D.")
        msg, status = _expand_prompt_mention("@some-pkg/d", _State(), _Session())
        assert status == "ok" and "Bare D." in msg

    def test_shows_info_message(self, tmp_path):
        _provider_prompt(tmp_path, "t", "Body.")
        session = _Session()
        _expand_prompt_mention("@t", _State(), session)
        assert any("Loaded prompt" in m[1] for m in session.messages)

    def test_renders_inline_variables(self, tmp_path):
        _provider_prompt(
            tmp_path, "greet", "Hello {{who}}!",
            variables=[{"name": "who", "type": "string", "required": True}],
        )
        msg, status = _expand_prompt_mention('@greet who=World', _State(), _Session())
        assert status == "ok" and "Hello World!" in msg

    def test_missing_required_variable_blocked(self, tmp_path):
        _provider_prompt(
            tmp_path, "needvar", "Hello {{who}}!",
            variables=[{"name": "who", "type": "string", "required": True}],
        )
        session = _Session()
        msg, status = _expand_prompt_mention("@needvar", _State(), session)
        assert status == "blocked"
        assert msg == "@needvar"
        assert any("could not be rendered" in m[1] for m in session.messages)

    def test_too_large(self, tmp_path):
        _provider_prompt(tmp_path, "huge", "x" * 200_000)
        msg, status = _expand_prompt_mention("@huge", _State(), _Session())
        assert status == "too_large"


# ── API handlers ──


class TestApiPrompts:
    def test_list(self, tmp_path, mock_sel):
        _provider_prompt(tmp_path, "sop", "Content.")
        resp = asyncio.run(api_prompts(MagicMock()))
        body = json.loads(resp.body)
        assert resp.status == 200 and len(body) == 1 and body[0]["name"] == "sop"
        assert body[0]["source"] == "user"

    def test_list_empty(self, tmp_path, mock_sel):
        resp = asyncio.run(api_prompts(MagicMock()))
        assert resp.status == 200 and json.loads(resp.body) == []

    def test_detail_found(self, tmp_path, mock_sel):
        _provider_prompt(tmp_path, "hello", "Hello World.")
        resp = asyncio.run(api_prompt_detail(_api_request("hello")))
        body = json.loads(resp.body)
        assert resp.status == 200 and "Hello World." in body["content"]
        assert body["source"] == "user"
        mock_sel.log_tool_invocation.assert_called_once()

    def test_detail_not_found(self, tmp_path, mock_sel):
        resp = asyncio.run(api_prompt_detail(_api_request("nope")))
        assert resp.status == 404
        assert mock_sel.log_tool_invocation.call_args[1]["outcome"] == "not_found"

    def test_detail_package_qualified_matches_bare(self, tmp_path, mock_sel):
        """``GET /api/prompts/pkg/name`` resolves the bare ``name`` via the provider."""
        _provider_prompt(tmp_path, "d", "Bare content.")
        resp = asyncio.run(api_prompt_detail(_api_request("some-pkg/d")))
        assert resp.status == 200 and "Bare content." in json.loads(resp.body)["content"]


# ── _run_chat prompt paths (/prompts) ──


class TestRunChatPrompts:
    def test_slash_list(self, tmp_path, mock_sel):
        _provider_prompt(tmp_path, "review", "Do review.", description="Review code")
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts"))
        assert "@review" in sl.messages[-1][1]

    def test_slash_list_empty(self, tmp_path, mock_sel):
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts"))
        assert "No prompts found" in sl.messages[-1][1]

    def test_slash_get_ok(self, tmp_path, mock_sel, monkeypatch):
        _provider_prompt(tmp_path, "review", "Do review.")
        s, sl = _ss()
        captured = {}
        original_run_chat = _run_chat

        async def _mock_run_chat(state, session, msg, **kw):
            if msg.startswith("Execute the following instructions:"):
                captured["expanded"] = msg
                return
            await original_run_chat(state, session, msg, **kw)

        monkeypatch.setattr("personalclaw.dashboard.chat_runner._run_chat", _mock_run_chat)
        asyncio.run(_mock_run_chat(s, sl, "/prompts get review"))
        assert any("Loaded prompt" in m[1] for m in sl.messages)
        assert "Do review." in captured.get("expanded", "")

    def test_slash_get_no_name(self, tmp_path, mock_sel):
        """``/prompts get`` with no name falls through to the list handler."""
        _provider_prompt(tmp_path, "review", "Do review.")
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts get"))
        assert "@review" in sl.messages[-1][1]

    def test_slash_list_explicit(self, tmp_path, mock_sel):
        """``/prompts list`` works the same as ``/prompts``."""
        _provider_prompt(tmp_path, "review", "Do review.")
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts list"))
        assert "@review" in sl.messages[-1][1]

    def test_slash_get_not_found(self, tmp_path, mock_sel):
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts get nonexistent"))
        assert "not found" in sl.messages[-1][1]

    def test_slash_get_render_failure_blocked(self, tmp_path, mock_sel):
        """A prompt with a missing required variable is reported as blocked."""
        _provider_prompt(
            tmp_path, "needvar", "Hello {{who}}!",
            variables=[{"name": "who", "type": "string", "required": True}],
        )
        s, sl = _ss()
        asyncio.run(_run_chat(s, sl, "/prompts get needvar"))
        assert any("could not be rendered" in m[1] or "blocked" in m[1].lower()
                   for m in sl.messages)
