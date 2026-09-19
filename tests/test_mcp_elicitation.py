"""``MBR-1`` — an MCP server asks the USER, through the confirmation boundary we own.

Every assertion here is made against a REAL stdio MCP server (the pattern
``test_mcp_client.py`` established), driven through the real
:class:`~personalclaw.mcp_client.McpServerConn`, so what is proven is the wire behaviour
and not a mock's agreement with itself. Three things are deliberately observed *from the
server's side of the connection*:

* **The advertised capabilities**, read off ``ctx.session.client_params`` — i.e. what the
  server actually received in ``initialize``. Asserting our own config would prove nothing
  about the handshake, and the whole default-off claim IS a claim about the handshake.
* **That an ungranted request RETURNS**, not merely that it errors. The fixture tool
  catches ``McpError`` and reports it as a normal tool result, and the call is additionally
  wrapped in a short ``wait_for`` — so a hang fails the test in seconds instead of passing
  as a refusal after the 120s call ceiling. A hang that "looks like" a denial is the exact
  failure mode ``done_when`` clause 1 names.
* **The user's real answer reaching the server.** The fixture returns the ``ElicitResult``
  verbatim, so the accepted content is asserted on the far end of the protocol rather than
  on the callback's return value.

The config home is a ``tmp_path`` for every test in this module (``fresh_home``), so a
fresh-install default is asserted from a temp ``config_dir`` and the real
``~/.personalclaw`` is never read or written.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from personalclaw.mcp_client import McpClientRegistry, mcp_sdk_available

pytestmark = pytest.mark.skipif(
    not mcp_sdk_available(), reason="requires the optional 'mcp' SDK extra"
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A stdio MCP server that reports the client's handshake and issues elicitation/create.
#
# `advertised_capabilities` is the instrument for clauses 1 and 3: it returns what THIS
# server was told at initialize, so "the capability is absent for server B" is measured on
# B's own connection rather than inferred from config.
#
# `ask` never raises. A refusal arrives as `McpError`, and re-raising it would surface as a
# generic tool failure indistinguishable from a crash or a timeout — so it is caught and
# reported as data, which is what lets one assertion cover both the error SHAPE and the
# fact that the call came back at all.
_FIXTURE_SERVER = textwrap.dedent('''
    import json

    from mcp.server.fastmcp import Context, FastMCP
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("elicit-fixture")


    @mcp.tool()
    async def advertised_capabilities(ctx: Context) -> str:
        """The client capabilities this server received in initialize."""
        params = ctx.session.client_params
        caps = params.capabilities.model_dump(exclude_none=True) if params else {}
        return json.dumps(caps, sort_keys=True, default=str)


    @mcp.tool()
    async def ask(ctx: Context) -> str:
        """Issue a confirmation-shaped elicitation/create; report whatever comes back."""
        try:
            result = await ctx.session.elicit_form(
                message="Delete the staging database?",
                requestedSchema={
                    "type": "object",
                    "properties": {"confirm": {"type": "boolean"}},
                    "required": ["confirm"],
                },
            )
        except McpError as exc:
            return json.dumps(
                {"outcome": "error", "code": exc.error.code, "message": exc.error.message}
            )
        return json.dumps(
            {"outcome": "result", "action": result.action, "content": result.content}
        )


    @mcp.tool()
    async def ask_for_a_string(ctx: Context) -> str:
        """A form a yes/no cannot answer — must be refused, not answered with a default."""
        try:
            result = await ctx.session.elicit_form(
                message="What is your API key?",
                requestedSchema={
                    "type": "object",
                    "properties": {"api_key": {"type": "string"}},
                    "required": ["api_key"],
                },
            )
        except McpError as exc:
            return json.dumps(
                {"outcome": "error", "code": exc.error.code, "message": exc.error.message}
            )
        return json.dumps(
            {"outcome": "result", "action": result.action, "content": result.content}
        )


    if __name__ == "__main__":
        mcp.run()
    ''')

#: Bound on every server-initiated round trip below. Long enough for a subprocess
#: handshake on a loaded machine, far short of `mcp_client._CALL_TIMEOUT_SECS` (120s), so a
#: wedged callback fails here as a timeout instead of silently spending two minutes and
#: then reading as a refusal.
_ROUND_TRIP_TIMEOUT = 45.0


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """A temp config home — the fresh-install state, never the operator's real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _grant(home: Path, *servers: str) -> None:
    """Write the per-server elicitation grant into the temp home's config.json."""
    (home / "config.json").write_text(
        json.dumps({"security": {"mcp_elicitation_servers": list(servers)}}),
        encoding="utf-8",
    )


@pytest.fixture()
def server_spec(tmp_path):
    script = tmp_path / "elicit_server.py"
    script.write_text(_FIXTURE_SERVER, encoding="utf-8")
    return {"command": sys.executable, "args": [str(script)]}


@pytest.fixture()
def live_state():
    """A DashboardState registered as the process-wide one, then un-registered.

    The elicitation seam resolves the state through
    ``inbox_providers.native_source.get_dashboard_state`` (a core module must not import
    ``dashboard/``), so a test that wants the approval path has to install one. The
    teardown matters: a state left registered would leak into every later test in the
    session as a surprise gateway.
    """
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.inbox_providers import native_source

    prior = native_source.get_dashboard_state()
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    native_source.set_dashboard_state(state)
    try:
        yield state
    finally:
        native_source.set_dashboard_state(prior)


async def _call(conn, tool: str) -> tuple[bool, str]:
    """Invoke a fixture tool under the module's own timeout (see `_ROUND_TRIP_TIMEOUT`)."""
    return await asyncio.wait_for(conn.call_tool(tool, {}), timeout=_ROUND_TRIP_TIMEOUT)


async def _caps(conn) -> dict:
    ok, raw = await _call(conn, "advertised_capabilities")
    assert ok, f"fixture handshake probe failed: {raw}"
    return json.loads(raw)


async def _answer_the_next_approval(state, decision: bool) -> dict:
    """Wait for the approval record to appear, snapshot it, and answer it.

    Returns the record as the surfaces see it — this dict IS both the ``approval`` WS
    payload and the ``GET /api/approvals`` row that ``ApprovalCard.tsx`` renders, so
    observing it here is observing the existing approval path rather than a proxy for it.
    """
    for _ in range(int(_ROUND_TRIP_TIMEOUT * 100)):
        pending = [dict(v) for v in state._pending_approvals.values()]
        if pending:
            row = pending[0]
            state.resolve_approval(str(row["id"]), decision)
            return row
        await asyncio.sleep(0.01)
    raise AssertionError("no approval record was ever published")


# ── clause 1 — default off: capability absent, and a request refused rather than hung ──


@pytest.mark.asyncio
async def test_the_default_install_advertises_no_elicitation_capability(fresh_home, server_spec):
    """Clause 1a. Nothing is written to the temp home: this is the shipped default."""
    assert not (fresh_home / "config.json").exists()
    reg = McpClientRegistry()
    reg.load_from_specs({"asker": server_spec})
    conn = reg.get("asker")
    assert conn is not None
    try:
        caps = await _caps(conn)
        assert "elicitation" not in caps, f"capability advertised while ungranted: {caps}"
    finally:
        await conn.shutdown()


@pytest.mark.asyncio
async def test_an_ungranted_elicitation_gets_a_typed_error_and_the_call_returns(
    fresh_home, server_spec
):
    """Clause 1b — BOTH halves.

    The error shape is asserted (a typed JSON-RPC ``-32600`` with a refusal message), and
    so is the fact that the server's request came back inside the timeout. Either half
    alone is passable by a bug: a hang would satisfy "no wrong answer was given", and an
    error asserted without a timeout bound would sit for the 120s call ceiling first.
    """
    from mcp import types as mcp_types

    reg = McpClientRegistry()
    reg.load_from_specs({"asker": server_spec})
    conn = reg.get("asker")
    assert conn is not None
    try:
        ok, raw = await _call(conn, "ask")
        assert ok, f"the elicitation round trip did not complete: {raw}"
        answer = json.loads(raw)
        assert answer["outcome"] == "error", f"ungranted request was answered: {answer}"
        assert answer["code"] == mcp_types.INVALID_REQUEST
        assert "not supported" in str(answer["message"]).lower()
    finally:
        await conn.shutdown()


# ── clause 2 — granted: the existing approval path, and the user's real answer back ──


@pytest.mark.asyncio
async def test_a_granted_elicitation_surfaces_on_the_existing_approval_path(
    fresh_home, server_spec, live_state
):
    """Clause 2. The approval record is observed, then answered, then its answer is
    asserted on the SERVER's side of the wire — so nothing here can pass on a callback
    that returns the right shape without ever asking a human."""
    _grant(fresh_home, "asker")
    reg = McpClientRegistry()
    reg.load_from_specs({"asker": server_spec})
    conn = reg.get("asker")
    assert conn is not None
    try:
        caps = await _caps(conn)
        assert "elicitation" in caps, f"grant did not reach the handshake: {caps}"

        call = asyncio.create_task(_call(conn, "ask"))
        row = await _answer_the_next_approval(live_state, True)

        # The record ApprovalCard.tsx renders: `tool` is its subject, `tool_purpose` its
        # purpose line. The server's own question must be the text the human reads, and
        # the subject must name the server, or the card cannot say who is asking.
        assert row["tool"] == "mcp_elicitation:asker"
        assert row["tool_purpose"] == "Delete the staging database?"
        assert row["source"] == "mcp:asker"

        ok, raw = await call
        assert ok, f"the elicitation round trip did not complete: {raw}"
        answer = json.loads(raw)
        assert answer["outcome"] == "result"
        assert answer["action"] == "accept"
        assert answer["content"] == {"confirm": True}
    finally:
        await conn.shutdown()


@pytest.mark.asyncio
async def test_a_denied_elicitation_returns_decline_and_no_content(
    fresh_home, server_spec, live_state
):
    """Clause 2's known-false half. Without it the test above passes on a callback that
    hard-codes ``accept`` and never reads the user's decision at all."""
    _grant(fresh_home, "asker")
    reg = McpClientRegistry()
    reg.load_from_specs({"asker": server_spec})
    conn = reg.get("asker")
    assert conn is not None
    try:
        call = asyncio.create_task(_call(conn, "ask"))
        await _answer_the_next_approval(live_state, False)
        ok, raw = await call
        assert ok, f"the elicitation round trip did not complete: {raw}"
        answer = json.loads(raw)
        assert answer["action"] == "decline"
        assert answer["content"] is None
    finally:
        await conn.shutdown()


@pytest.mark.asyncio
async def test_a_form_a_confirmation_cannot_answer_is_refused_not_defaulted(
    fresh_home, server_spec, live_state
):
    """The lazy implementation the atom names, from the other direction.

    A granted server asking for a *string* must be refused with a typed error, and the
    user must NOT be troubled for it — an answer this boundary cannot supply is not worth
    their attention, and supplying ``""`` or a schema default would be inventing consent.
    """
    _grant(fresh_home, "asker")
    reg = McpClientRegistry()
    reg.load_from_specs({"asker": server_spec})
    conn = reg.get("asker")
    assert conn is not None
    try:
        ok, raw = await _call(conn, "ask_for_a_string")
        assert ok, f"the elicitation round trip did not complete: {raw}"
        answer = json.loads(raw)
        assert answer["outcome"] == "error", f"a string form was answered: {answer}"
        assert "confirmation" in str(answer["message"]).lower()
        assert (
            not live_state._pending_approvals
        ), "the user was asked a question we could not deliver"
    finally:
        await conn.shutdown()


# ── clause 3 — per-server, proven with two servers in ONE test ──


@pytest.mark.asyncio
async def test_granting_server_a_leaves_server_b_unchanged(fresh_home, server_spec):
    """Clause 3. Two live connections, one grant, both handshakes read.

    Asserted in one test on purpose: two separate tests would each prove only their own
    half, and "per-server" is a claim about the two together.
    """
    _grant(fresh_home, "server_a")
    reg = McpClientRegistry()
    reg.load_from_specs({"server_a": server_spec, "server_b": server_spec})
    a, b = reg.get("server_a"), reg.get("server_b")
    assert a is not None and b is not None
    try:
        caps_a, caps_b = await _caps(a), await _caps(b)
        assert "elicitation" in caps_a, f"the granted server was not advertised: {caps_a}"
        assert "elicitation" not in caps_b, f"the grant leaked to server B: {caps_b}"
    finally:
        await a.shutdown()
        await b.shutdown()


# ── clause 4 — the config round trip, including the door the roundtrip file cannot see ──


def test_the_grant_defaults_empty_on_a_fresh_install(fresh_home):
    """Clause 4/1. A capability a third party gets by default is not a granted capability."""
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().security.mcp_elicitation_servers == []


def test_the_grant_is_patch_editable(fresh_home):
    """Clause 4. The write path — a field with no allowlist entry leaves
    ``test_config_roundtrip.py`` green while the Tools-page control 400s."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["security.mcp_elicitation_servers"]["type"] == "str_list"


def test_the_grant_survives_a_save_load_round_trip(fresh_home):
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    cfg.security.mcp_elicitation_servers = ["one", "two"]
    cfg.save()
    assert AppConfig.load().security.mcp_elicitation_servers == ["one", "two"]


def test_a_blank_or_non_string_grant_entry_is_dropped(fresh_home):
    """A ``""`` in the list would match no server while making the grant list look
    non-empty — a consent surface that reads as "something is granted" when nothing is."""
    from personalclaw.config.loader import AppConfig

    (fresh_home / "config.json").write_text(
        json.dumps({"security": {"mcp_elicitation_servers": ["  ok  ", "", "   ", 7, None]}}),
        encoding="utf-8",
    )
    assert AppConfig.load().security.mcp_elicitation_servers == ["ok"]


def test_the_dashboard_control_reaches_the_field(fresh_home):
    """Clause 4's second half — reachable from the dashboard, not config-file-only.

    The trap this guards is the one the capability census recorded for the auth selector:
    a complete backend with no selector over it. Asserted on the SOURCE because a control
    is only reachable if it is wired to the field's real dotted path — a typo there is
    invisible to every Python rail and to a passing frontend build.
    """
    api_ts = (REPO_ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "'security.mcp_elicitation_servers'" in api_ts
    assert "setMcpElicitationServers" in api_ts

    tools_page = (REPO_ROOT / "web" / "src" / "pages" / "tools" / "ToolsPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "onToggleElicitation" in tools_page
    assert "api.setMcpElicitationServers" in tools_page
    assert "ask you questions" in tools_page


# ── clause 5 — the token count moved, and the inbound non-goal still stands ──


def test_the_tree_now_carries_more_than_one_elicit_token(fresh_home):
    """Clause 5a. Before this atom the whole of ``src/personalclaw`` held exactly ONE
    ``elicit`` token, and it was a refusal comment. Counted with git so the measurement is
    the one the atom states, not a re-derivation of it."""
    out = subprocess.run(
        ["git", "grep", "-c", "elicit", "--", "src/personalclaw"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = [line for line in out.splitlines() if line.strip()]
    assert len(files) > 1, f"the capability left no trace in src/: {out!r}"


def test_the_inbound_out_of_scope_note_is_left_intact(fresh_home):
    """Clause 5b. This atom is the CLIENT half. The read-only inbound surface's written
    refusal to issue server→client requests is a deliberate non-goal, and an implementer
    reading "elicitation" as one feature is exactly how it would get deleted."""
    text = (REPO_ROOT / "src" / "personalclaw" / "inbound" / "mcp_http.py").read_text(
        encoding="utf-8"
    )
    assert "**Deliberately unmet clauses of LATER drafts:** anything requiring" in text
    assert "(elicitation, sampling) or a resumable event stream is out of scope" in text


# ── the unit-level rail under the content mapping ──


@pytest.mark.parametrize(
    "schema,expected",
    [
        # A bare confirmation — nothing to fill.
        ({"type": "object"}, {}),
        ({"type": "object", "properties": {}}, {}),
        # Booleans are what a yes can truthfully fill, and a yes fills them with True.
        ({"properties": {"confirm": {"type": "boolean"}}}, {"confirm": True}),
        (
            {"properties": {"a": {"type": "boolean"}, "b": {"type": "boolean"}}},
            {"a": True, "b": True},
        ),
        # Anything else is unanswerable from one bit — None, never a fabricated value.
        ({"properties": {"name": {"type": "string"}}}, None),
        ({"properties": {"n": {"type": "integer"}}}, None),
        # One unanswerable field poisons the whole form: a partial content dict would not
        # satisfy the requested schema anyway.
        ({"properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}}, None),
        # A property whose type is absent is not a boolean — do not guess.
        ({"properties": {"confirm": {}}}, None),
        (None, None),
    ],
)
def test_confirmation_content_never_invents_an_answer(schema, expected):
    from personalclaw.mcp_elicitation import confirmation_content

    assert confirmation_content(schema) == expected


def test_the_grant_check_is_fail_closed_on_a_broken_config(fresh_home):
    """An unreadable config must not read as a grant."""
    from personalclaw.mcp_elicitation import elicitation_granted

    (fresh_home / "config.json").write_text("{not json", encoding="utf-8")
    assert elicitation_granted("anything") is False


def test_an_unnamed_server_is_never_granted(fresh_home):
    from personalclaw.mcp_elicitation import elicitation_granted

    _grant(fresh_home, "real")
    assert elicitation_granted("") is False
    assert elicitation_granted("   ") is False
    assert elicitation_granted("real") is True


def test_no_callback_is_built_for_an_ungranted_server(fresh_home):
    """The seam itself: ``None`` is what keeps the capability off the wire, so it is
    asserted directly rather than only through its downstream handshake effect."""
    from personalclaw.mcp_elicitation import elicitation_callback_for

    _grant(fresh_home, "granted")
    assert elicitation_callback_for("denied") is None
    assert elicitation_callback_for("granted") is not None
