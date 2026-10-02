"""A chore never rides a kept session: every session core acquires is a conversation, or one turn.

Measured on a running install: every chore of every chat (a chat's title and tags, its organize
proposal, its follow-up chips, its memory consolidation) was sent on one background session the
session manager kept for the day. A session keeps every turn it is sent, so each chore was handed
every earlier chore's prompt and answer, whichever chat that one was made for, and a consolidation
stored words from another chat as this one's facts. Clearing the model client's history before a
chore did not help: the check looked for an attribute the native runtime does not have, so the
clear never ran. A chore is one fresh call now (``chores.run_chore``), and asks for no session.

Three halves.

* The census reads every place core acquires a session (``SessionManager.get_or_create``) and
  fails for one that is not named below with what its session is: a conversation that keeps its
  turns on purpose (:data:`CONVERSATIONS`), or a single turn whose session a named function ends
  once the turn is over (:data:`ONE_TURN`, checked to call ``reset``). A chore that took a session
  to send its prompt reds here, and so does a one-turn session nothing ends.
* No code clears a model client's history from outside the client: a clear that has to guess
  where a client keeps its turns misses the one that keeps them elsewhere.
* Every chore named in :data:`CHORES` reaches its model through the chore helper.

What the census cannot see: a session acquired through a name other than ``get_or_create``.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Sessions that keep their turns because the turns are one conversation, by (file under
#: ``src/personalclaw``, function), with what the conversation is.
CONVERSATIONS: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): "a chat's own turns",
    ("dashboard/side.py", "_run_side_turn"): (
        "a side chat's questions beside its chat, until the side chat is closed"
    ),
    ("rooms/turn.py", "member_session"): "a room member's turns in its room",
    ("subagent.py", "SubagentManager._run_inner"): "a subagent's run, keyed per spawn",
    ("gateway.py", "GatewayOrchestrator._init_subagents._subagent_done"): (
        "a finished subagent's result, read into the conversation that started it (a channel "
        "thread's, a scheduled job's)"
    ),
}

#: Sessions for one turn, by the function that acquires one, with the function that ends it.
ONE_TURN: dict[tuple[str, str], tuple[str, str]] = {
    ("gateway.py", "GatewayOrchestrator._run_heartbeat_task"): (
        "gateway.py",
        "GatewayOrchestrator._run_heartbeat_task",
    ),
    ("dashboard/handlers/hooks.py", "_run_hook_inner"): (
        "dashboard/handlers/hooks.py",
        "_run_hook_agent",
    ),
    ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"): (
        "dashboard/handlers/optimizer.py",
        "handle_optimize._optimize",
    ),
    ("dashboard/handlers/agent_marketplace.py", "api_agent_marketplace_test"): (
        "dashboard/handlers/agent_marketplace.py",
        "api_agent_marketplace_test",
    ),
}

#: The manager's own re-acquisition: it rebuilds a stale runtime for the request just made.
REACQUIRES = frozenset({("session.py", "SessionManager.get_or_create")})

#: The background chores, by (file, function), each of which must ask the chore helper.
CHORES: dict[tuple[str, str], str] = {
    ("dashboard/chat_title.py", "_title_once"): "a chat's title and tags",
    ("dashboard/chat_title.py", "_generate_title_via_provider"): "a title asked for again",
    ("dashboard/chat_followups.py", "_generate_followups"): "a chat's follow-up chips",
    ("session_organize.py", "_llm_proposal"): "a chat's organize proposal",
    ("suggestions.py", "generate_suggestions"): "the dashboard's suggestions",
    ("dashboard/chat_folders.py", "_generate_folder_icon"): "a folder's icon",
    ("context.py", "compress_thread_history"): "a reopened chat's condensed history",
    ("history.py", "HistoryConsolidator._call_llm"): "a chat's memory consolidation",
}

#: What asks the chore helper: the helper, and the chat-chore form of it.
_HELPERS = frozenset({"run_chore", "chat_chore"})

#: Where a model client keeps a conversation's turns: a chat-completions client's ``_history``, the
#: native loop's ``_messages``.
_TURNS = frozenset({"_history", "_messages"})


def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in *tree* by its qualified name (``Class.method.inner``)."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = [*scope, child.name]
                if not isinstance(child, ast.ClassDef):
                    found[".".join(name)] = child
                visit(child, name)
            else:
                visit(child, scope)

    visit(tree, [])
    return found


def acquisitions(source: str) -> list[str]:
    """The qualified name of the function around each ``….get_or_create(…)`` in *source*,
    once per call."""
    tree = ast.parse(source)
    out: list[str] = []
    for qualname, func in _functions(tree).items():
        inner = {
            id(n)
            for f in ast.walk(func)
            if f is not func and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(f)
        }
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_or_create"
                and id(node) not in inner
            ):
                out.append(qualname)
    return out


def ends_its_session(source: str, qualname: str) -> bool:
    """Whether the function *qualname* in *source* ends a session (``….reset(…)``)."""
    func = _functions(ast.parse(source)).get(qualname)
    return func is not None and any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "reset"
        for n in ast.walk(func)
    )


def asks_the_chore_helper(source: str, qualname: str) -> bool:
    func = _functions(ast.parse(source)).get(qualname)
    return func is not None and any(
        isinstance(n, ast.Call) and _called(n) in _HELPERS for n in ast.walk(func)
    )


def outside_history_clears(source: str) -> list[int]:
    """Lines that reach into another object's turns (:data:`_TURNS`): ``hasattr(x, "_history")``
    or ``x._messages…`` where ``x`` is not ``self``."""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and _called(node) == "hasattr"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _TURNS
        ):
            lines.add(node.lineno)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _TURNS
            and not (isinstance(node.value, ast.Name) and node.value.id == "self")
        ):
            lines.add(node.lineno)
    return sorted(lines)


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _census() -> list[tuple[str, str]]:
    sites: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        sites.extend((rel, q) for q in acquisitions(path.read_text(encoding="utf-8")))
    return sites


def _accounted(site: tuple[str, str]) -> bool:
    return site in CONVERSATIONS or site in ONE_TURN or site in REACQUIRES


# ── the census ──────────────────────────────────────────────────────────────────────────────


def test_every_session_core_acquires_is_a_conversation_or_one_turn():
    unaccounted = sorted({site for site in _census() if not _accounted(site)})
    assert not unaccounted, (
        "these acquire a session, and a session keeps every turn it is sent. A chore is a call "
        "of its own (`chores.run_chore`, or `chat_title.chat_chore` for a chat's): it never asks "
        "for a session. A conversation goes in CONVERSATIONS with what it is; a single turn in "
        f"ONE_TURN with the function that ends its session: {unaccounted}"
    )


def test_a_one_turn_session_is_ended_by_the_function_named():
    for site, (rel, ender) in ONE_TURN.items():
        assert ends_its_session(_read(rel), ender), (
            f"{site} is listed as one turn, but {rel}:{ender} never ends its session "
            "(`sessions.reset(key)`): the next turn would be sent this one"
        )


def test_every_listing_still_names_a_real_acquisition():
    """A listing that outlived its site would excuse the next session taken there."""
    sites = set(_census())
    for listing in (CONVERSATIONS, ONE_TURN, REACQUIRES):
        stale = sorted(set(listing) - sites)
        assert not stale, f"no longer acquires a session, drop the listing: {stale}"


def test_the_census_finds_the_sessions_it_exists_for():
    """The floor: a scan that found nothing would pass for free."""
    sites = _census()
    assert len(sites) >= 10, sites
    for known in (
        ("dashboard/chat_runner.py", "run_chat"),
        ("gateway.py", "GatewayOrchestrator._run_heartbeat_task"),
        ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"),
        ("session.py", "SessionManager.get_or_create"),
    ):
        assert known in sites, (known, sites)


def test_the_census_fails_a_chore_that_takes_a_session():
    """The rail's teeth, on code that is not in the tree."""
    planted = (
        "async def title_the_chat(state, prompt):\n"
        "    client, _new, _resumed = await state.sessions.get_or_create('chores')\n"
        "    async for event in client.stream(prompt):\n"
        "        pass\n"
    )
    (site,) = [("planted.py", q) for q in acquisitions(planted)]
    assert site == ("planted.py", "title_the_chat")
    assert not _accounted(site)


def test_a_one_turn_session_nothing_ends_is_caught():
    keeps = (
        "async def rewrite(sessions, key, prompt):\n"
        "    client, _new, _resumed = await sessions.get_or_create(key)\n"
        "    try:\n"
        "        return await stream_and_collect(client, prompt)\n"
        "    finally:\n"
        "        sessions.release(key)\n"
    )
    ends = keeps + "        await sessions.reset(key)\n"
    assert not ends_its_session(keeps, "rewrite")
    assert ends_its_session(ends, "rewrite")


# ── nothing clears a model client's history from outside it ─────────────────────────────────


def test_no_code_reaches_into_a_model_clients_history():
    reaching = {
        path.relative_to(SRC).as_posix(): lines
        for path in sorted(SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
        and (lines := outside_history_clears(path.read_text(encoding="utf-8")))
    }
    assert not reaching, (
        "these reach into a model client's turns from outside it. A chore that needs a fresh "
        "conversation is a call of its own (`chores.run_chore`); it does not clear one it "
        f"shares: {reaching}"
    )


def test_the_history_scan_tells_a_client_clearing_its_own_from_one_cleared_outside():
    own = "class Client:\n    def reset(self):\n        self._history.clear()\n"
    outside = (
        "async def chore(client):\n"
        "    if hasattr(client, '_history'):\n"
        "        client._history.clear()\n"
        "    client._messages.clear()\n"
    )
    assert outside_history_clears(own) == []
    assert outside_history_clears(outside) == [2, 3, 4]
    # And a client's own clear is in the tree, so the scan reads the files it exists for.
    own_clears = [
        rel
        for rel in ("llm/openai.py", "llm/anthropic.py")
        if "self._history.clear()" in _read(rel)
    ]
    assert own_clears, "the providers' own history clears moved: the vacuity control is gone"


# ── every chore asks the chore helper ───────────────────────────────────────────────────────


def test_every_chore_reaches_its_model_through_the_chore_helper():
    missing = [
        f"{rel}:{qualname} ({what})"
        for (rel, qualname), what in CHORES.items()
        if not asks_the_chore_helper(_read(rel), qualname)
    ]
    assert not missing, (
        "these chores no longer ask the chore helper, so nothing says each is a fresh call "
        f"sent only its own prompt: {missing}"
    )


def test_the_chore_helper_check_has_teeth():
    asks = "async def title(prompt, who):\n    return await chores.run_chore(prompt, usage=who)\n"
    streams = (
        "async def title(client, prompt):\n    return await stream_and_collect(client, prompt)\n"
    )
    assert asks_the_chore_helper(asks, "title")
    assert not asks_the_chore_helper(streams, "title")
