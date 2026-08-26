"""OU-9 — the approval brief over the core↔channel seam (ONBOARDING-UX, C2).

What these tests hold down, in priority order:

1. **The CALL SITE.** A real ``_interactive_approval`` callback, driven end to end,
   hands the channel an event whose payload carries the brief (tool + blast-radius
   line). Asserting a composer returns a dict would prove nothing about the seam.
2. **ADDITIVE.** A channel written against the ORIGINAL signature — explicit
   keyword-only params, no ``**kwargs``, no knowledge of the brief — still gets
   called and still decides. Pre-existing ``tool_meta`` keys survive. Each of these
   rails carries a vacuity assertion proving it reds under the change it forbids.
3. **One vocabulary, two languages.** The facet words and hint lists are parsed out
   of ``web/src/pages/chat/approvalMeta.ts`` (OU-7/OU-8) and compared to this
   module's, so the phone brief and the dashboard chips cannot drift.
4. **The honesty contract** — every boolean a positive claim, ``None`` when nothing
   was established, ``readOnly`` never over an established write.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.approval_brief import (
    APPROVAL_BRIEF_META_KEY,
    BLAST_RADIUS_FACET_ORDER,
    DESTRUCTIVE_HINTS,
    FACET_COPY,
    NETWORK_HINTS,
    READ_VERB_HINTS,
    SHELL_HINTS,
    WRITE_HINTS,
    attach_approval_brief,
    blast_radius_line,
    compose_approval_brief,
    derive_blast_radius,
    established_facets,
)
from personalclaw.llm_helpers import LLMEvent

_TS_SOURCE = Path(__file__).resolve().parents[1] / "web/src/pages/chat/approvalMeta.ts"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect the home for every test in this module.

    ``_interactive_approval`` emits SEL audit rows on the not-auto-approved path, and
    SEL writes under the home. ``PERSONALCLAW_HOME`` is the lever that actually works
    here (read per call, cached nowhere) — patching ``config.loader.config_dir`` misses
    stores that bound it at import. The binding is asserted, not assumed: a redirect
    that silently failed would let this module write to the owner's real
    ``~/.personalclaw``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    from personalclaw.config.loader import config_dir

    assert str(config_dir()).startswith(
        str(home)
    ), f"PERSONALCLAW_HOME redirect did not bind: config_dir()={config_dir()}"
    yield home


def _make_gateway():
    """The core approval harness, mirroring ``tests/test_approval_threading.py``."""
    from personalclaw.gateway import GatewayOrchestrator

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.sessions = MagicMock()
    gateway.sessions.get_pid = MagicMock(return_value=None)
    gateway._channel_delivery = MagicMock()
    gateway._channel_delivery.request_approval = AsyncMock(return_value=True)
    gateway.dashboard_state = MagicMock()
    gateway.dashboard_state.is_yolo_active.return_value = False
    gateway.dashboard_state._sessions = {}
    gateway.dashboard_state.request_approval = AsyncMock(return_value=True)
    gateway.dashboard_state.resolve_approval = MagicMock()
    gateway._owner_id = "U000"
    gateway._cfg = MagicMock()
    gateway._cfg.hooks = MagicMock()
    gateway._cfg.hooks.get = MagicMock(return_value=[])
    gateway._cfg.agent.max_subagents = 4
    gateway.sessions.get_channel = MagicMock(return_value=None)
    gateway.sessions.get_thread = MagicMock(return_value=None)
    gateway._approval_mode = None
    return gateway


def _event(title: str, **kw) -> LLMEvent:
    return LLMEvent(kind="permission_request", request_id="req1", title=title, **kw)


async def _drive(gateway, event) -> bool:
    """Run the real approval callback once, through the channel branch."""
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        approve_fn = gateway._interactive_approval("subagent")
        return await approve_fn(event, "1775113012.860459")


# ── 1. The call site ─────────────────────────────────────────────────────────────


class TestCallSiteCarriesTheBrief:
    """The brief reaches the channel through a real request_approval call."""

    @pytest.mark.asyncio
    async def test_channel_payload_carries_tool_and_blast_radius_line(self) -> None:
        """done_when: tool + blast-radius line arrive on the request_approval payload."""
        gateway = _make_gateway()
        assert await _drive(gateway, _event("web_fetch")) is True

        gateway._channel_delivery.request_approval.assert_awaited_once()
        delivered = gateway._channel_delivery.request_approval.call_args.args[0]
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]

        assert brief["tool"] == "web_fetch"
        assert brief["blastRadiusLine"] == "uses the network"
        assert brief["blastRadius"]["network"] is True

    @pytest.mark.asyncio
    async def test_channel_payload_carries_the_effective_risk(self) -> None:
        """`risk` is the EFFECTIVE per-invocation risk, not the DECLARED risk_level.

        A read-only `bash` call is declared destructive and resolves to safe. The
        channel never had that resolution before; the dashboard already showed it.
        """
        gateway = _make_gateway()
        event = _event(
            "bash",
            tool_kind="execute",
            risk_level="destructive",
            tool_input={"command": "ls -la"},
        )
        assert await _drive(gateway, event) is True

        delivered = gateway._channel_delivery.request_approval.call_args.args[0]
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert event.risk_level == "destructive"  # the declaration is untouched
        assert brief["risk"] == "safe"  # …and the brief carries the resolution
        assert brief["blastRadius"] == {
            "writes": False,
            "network": False,
            "shell": True,
            "readOnly": True,
        }
        assert brief["blastRadiusLine"] == "runs a command, reads only"

    @pytest.mark.asyncio
    async def test_a_mutating_command_does_not_claim_read_only(self) -> None:
        """The screening verdict OU-8 left unwired now reaches the brief."""
        gateway = _make_gateway()
        event = _event(
            "bash",
            tool_kind="execute",
            risk_level="destructive",
            tool_input={"command": "rm -rf build"},
        )
        assert await _drive(gateway, event) is True

        delivered = gateway._channel_delivery.request_approval.call_args.args[0]
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert brief["blastRadius"]["readOnly"] is False
        assert brief["risk"] == "destructive"

    @pytest.mark.asyncio
    async def test_nothing_established_ships_no_blast_radius_at_all(self) -> None:
        """An unrecognizable tool name yields a brief with NO blast-radius keys.

        Not an all-false object: on a phone that renders as "no writes, no network, no
        shell, not read-only" — a confident all-clear from zero evidence.
        """
        gateway = _make_gateway()
        assert await _drive(gateway, _event("frobnicate_xyzzy")) is True

        delivered = gateway._channel_delivery.request_approval.call_args.args[0]
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert brief["tool"] == "frobnicate_xyzzy"
        assert "blastRadius" not in brief
        assert "blastRadiusLine" not in brief

    @pytest.mark.asyncio
    async def test_dashboard_fallback_gets_no_brief_argument(self) -> None:
        """The dashboard remains the rich surface — its call is unchanged.

        With no channel registered nothing is stamped onto the dashboard's arguments:
        the brief is a compact summary for a surface with no room, and the dashboard
        composes its own richer view.
        """
        gateway = _make_gateway()
        gateway._channel_delivery = None
        assert await _drive(gateway, _event("web_fetch")) is True

        gateway.dashboard_state.request_approval.assert_awaited_once()
        kwargs = gateway.dashboard_state.request_approval.call_args.kwargs
        assert APPROVAL_BRIEF_META_KEY not in kwargs
        assert set(kwargs) == {"tool_input", "tool_purpose", "session"}


# ── 2. Additive, with vacuity proofs ────────────────────────────────────────────


class _OldShapedChannel:
    """A channel written against the signature that shipped BEFORE this atom.

    Deliberately no ``**kwargs`` and no knowledge of the brief. If core ever grows the
    seam by passing a new argument, calling this raises TypeError — which is exactly
    what the vacuity test below demonstrates.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.saw_tool_meta: dict | None = None

    async def request_approval(
        self,
        event,
        *,
        source: str,
        parent_session_key: str = "",
        sessions=None,
        on_prompted=None,
    ):
        self.calls += 1
        self.saw_tool_meta = dict(getattr(event, "tool_meta", {}) or {})
        return True


class TestAdditiveOnly:
    @pytest.mark.asyncio
    async def test_old_shaped_consumer_still_works_unchanged(self) -> None:
        """A pre-OU-9 channel is still called and its decision still stands."""
        gateway = _make_gateway()
        channel = _OldShapedChannel()
        gateway._channel_delivery = channel

        assert await _drive(gateway, _event("web_fetch")) is True
        assert channel.calls == 1
        # It CAN see the brief if it looks — it simply does not have to.
        assert APPROVAL_BRIEF_META_KEY in (channel.saw_tool_meta or {})

    @pytest.mark.asyncio
    async def test_vacuity_old_shaped_consumer_reds_on_a_new_argument(self) -> None:
        """VACUITY PROOF for the rail above.

        The rail is only meaningful if that signature is genuinely sensitive to a
        widened seam. Passing the brief as a new keyword — the obvious alternative
        implementation of this atom — raises TypeError, so the rail above is not
        vacuously green.
        """
        channel = _OldShapedChannel()
        with pytest.raises(TypeError):
            await channel.request_approval(
                _event("web_fetch"),
                source="subagent",
                brief={"tool": "web_fetch"},  # the shape this atom deliberately avoids
            )
        assert channel.calls == 0

    @pytest.mark.asyncio
    async def test_preexisting_tool_meta_keys_survive(self) -> None:
        """The brief is added BESIDE existing meta, never in place of it."""
        gateway = _make_gateway()
        event = _event("web_fetch", tool_meta={"ok": False, "content_type": "text/plain"})
        assert await _drive(gateway, event) is True

        delivered = gateway._channel_delivery.request_approval.call_args.args[0]
        assert delivered.tool_meta["ok"] is False
        assert delivered.tool_meta["content_type"] == "text/plain"
        assert APPROVAL_BRIEF_META_KEY in delivered.tool_meta

    def test_vacuity_replacing_tool_meta_would_red_that_rail(self) -> None:
        """VACUITY PROOF: the surviving-keys rail reds if the stamp replaced the dict."""
        original = {"ok": False, "content_type": "text/plain"}
        event = _event("web_fetch", tool_meta=dict(original))

        # What `attach_approval_brief` actually does: one added key.
        assert attach_approval_brief(event) is not None
        assert dict(event.tool_meta, **{APPROVAL_BRIEF_META_KEY: None}) != original
        for key, value in original.items():
            assert event.tool_meta[key] == value

        # What a REPLACING implementation would do — the rail above would fail.
        replaced = _event("web_fetch", tool_meta=dict(original))
        replaced.tool_meta = {APPROVAL_BRIEF_META_KEY: compose_approval_brief(replaced)}
        for key in original:
            assert key not in replaced.tool_meta

    def test_no_field_on_the_event_is_rewritten(self) -> None:
        """Every other event field is byte-identical after the stamp."""
        import dataclasses

        event = _event("bash", tool_kind="execute", risk_level="destructive")
        before = {
            f.name: getattr(event, f.name)
            for f in dataclasses.fields(event)
            if f.name != "tool_meta"
        }
        attach_approval_brief(event)
        after = {
            f.name: getattr(event, f.name)
            for f in dataclasses.fields(event)
            if f.name != "tool_meta"
        }
        assert before == after

    def test_an_event_that_cannot_carry_meta_is_left_alone(self) -> None:
        """No dict ``tool_meta`` → nothing stamped, nothing raised."""

        class Bare:
            title = "web_fetch"

        assert attach_approval_brief(Bare()) is None

    def test_an_event_with_no_tool_identity_gets_no_brief(self) -> None:
        assert compose_approval_brief(_event("")) is None


# ── 3. One vocabulary, two languages ───────────────────────────────────────────


def _ts_text() -> str:
    assert _TS_SOURCE.is_file(), f"OU-7's module moved: {_TS_SOURCE}"
    return _TS_SOURCE.read_text(encoding="utf-8")


def _ts_string_array(name: str) -> list[str]:
    """Pull a `const NAME ... = [ 'a', 'b' ]` string array out of the TypeScript."""
    match = re.search(rf"const {name}\b[^=]*=\s*\[(.*?)\]", _ts_text(), re.DOTALL)
    assert match, f"could not find {name} in {_TS_SOURCE.name}"
    return re.findall(r"'([^']*)'", match.group(1))


def _ts_facet_copy() -> dict[str, dict[str, str]]:
    block = re.search(r"const FACET_COPY\b.*?\n\}", _ts_text(), re.DOTALL)
    assert block, "could not find FACET_COPY"
    found = re.findall(
        r"(\w+):\s*\{\s*label:\s*'([^']*)',\s*detail:\s*'([^']*)'\s*\}", block.group(0)
    )
    return {k: {"label": label, "detail": detail} for k, label, detail in found}


class TestOneVocabularyAcrossLanguages:
    """The channel brief and the dashboard chips must say the same words.

    OU-7 put the facet words beside the derivation precisely so three surfaces could
    not invent three vocabularies. The channel brief is composed in Python, so the
    agreement is enforced here instead of by a compiler.
    """

    def test_the_parser_is_not_vacuous(self) -> None:
        """VACUITY PROOF for every comparison below.

        A regex that matched nothing would make each set-equality trivially compare
        two empties and pass. Pin the sizes first.
        """
        assert len(_ts_string_array("SHELL_HINTS")) >= 5
        assert len(_ts_string_array("NETWORK_HINTS")) >= 5
        assert len(_ts_string_array("WRITE_HINTS")) >= 15
        assert len(_ts_facet_copy()) == 4
        assert _ts_string_array("SHELL_HINTS") != _ts_string_array("NETWORK_HINTS")

    @pytest.mark.parametrize(
        "ts_name,py_value",
        [
            ("SHELL_HINTS", SHELL_HINTS),
            ("NETWORK_HINTS", NETWORK_HINTS),
            ("DESTRUCTIVE_HINTS", DESTRUCTIVE_HINTS),
            ("READ_VERB_HINTS", READ_VERB_HINTS),
            ("WRITE_HINTS", WRITE_HINTS),
        ],
    )
    def test_hint_lists_agree_with_the_frontend(self, ts_name: str, py_value) -> None:
        assert set(_ts_string_array(ts_name)) == set(
            py_value
        ), f"{ts_name} drifted between approvalMeta.ts and approval_brief.py"

    def test_facet_words_are_the_frontends_verbatim(self) -> None:
        assert _ts_facet_copy() == FACET_COPY

    def test_render_order_agrees_with_the_frontend(self) -> None:
        assert _ts_string_array("BLAST_RADIUS_FACET_ORDER") == list(BLAST_RADIUS_FACET_ORDER)

    def test_every_facet_has_words(self) -> None:
        """A fifth facet cannot be silently dropped from the brief."""
        assert set(BLAST_RADIUS_FACET_ORDER) == set(FACET_COPY)

    def test_the_write_hints_are_derived_from_the_gates_own_tuple(self) -> None:
        """Not a hand-copied list: adding a hint to task_modes flows into the brief."""
        from personalclaw.task_modes import _MUTATING_NAME_HINTS

        assert "schedule" in _MUTATING_NAME_HINTS and "schedule" in WRITE_HINTS
        # Re-homed to another facet, so they must NOT also mean "writes".
        for rehomed in ("exec", "spawn", "delete", "remove", "run"):
            assert rehomed in _MUTATING_NAME_HINTS or rehomed in DESTRUCTIVE_HINTS
            assert rehomed not in WRITE_HINTS


# ── 4. The honesty contract ────────────────────────────────────────────────────


class TestHonestyContract:
    def test_nothing_established_returns_none_not_all_false(self) -> None:
        assert derive_blast_radius("frobnicate_xyzzy") is None

    def test_an_established_write_never_claims_read_only(self) -> None:
        radius = derive_blast_radius("file_write", risk="safe")
        assert radius == {"writes": True, "network": False, "shell": False, "readOnly": False}

    def test_a_negative_screening_verdict_rules_the_read_claim_out(self) -> None:
        radius = derive_blast_radius("bash", risk="safe", read_only_command=False)
        assert radius is not None and radius["readOnly"] is False

    def test_an_unknown_risk_level_is_no_evidence(self) -> None:
        assert derive_blast_radius("do_thing", risk="apocalyptic") is None

    def test_a_read_verb_beats_a_broad_write_hint(self) -> None:
        """`schedule_list` matches the write fragment "schedule" but is plainly a read."""
        radius = derive_blast_radius("schedule_list")
        assert radius == {"writes": False, "network": False, "shell": False, "readOnly": True}

    def test_a_destructive_verb_wins_outright(self) -> None:
        radius = derive_blast_radius("memory_forget", risk="safe")
        assert radius is not None and radius["writes"] is True

    def test_an_mcp_prefix_is_stripped_before_matching(self) -> None:
        radius = derive_blast_radius("mcp/some-server/web_fetch")
        assert radius is not None and radius["network"] is True

    def test_established_facets_shows_only_positives(self) -> None:
        facets = established_facets(
            {"writes": True, "network": False, "shell": True, "readOnly": False}
        )
        assert [f["key"] for f in facets] == ["writes", "shell"]

    def test_established_facets_of_nothing_is_empty(self) -> None:
        assert established_facets(None) == []

    def test_the_line_is_empty_when_nothing_is_established(self) -> None:
        assert blast_radius_line(None) == ""

    def test_the_line_follows_the_declared_render_order(self) -> None:
        line = blast_radius_line(
            {"writes": True, "network": True, "shell": True, "readOnly": False}
        )
        assert line == "writes files, runs a command, uses the network"

    def test_an_unrecognized_tool_never_claims_reads_only(self) -> None:
        """The trap this atom fell into once, kept shut.

        ``classify_invocation``'s name-fallback branch answers READ_ONLY for any name
        with no mutating hint. Wiring it in as a "screening verdict" made EVERY unknown
        tool arrive on the phone claiming "reads only" — a positive claim from zero
        evidence. The brief now takes only the effective risk, which floors an unknown
        name at ``caution``.
        """
        for unknown in ("frobnicate_xyzzy", "quux", "mcp/server/do_thing"):
            brief = compose_approval_brief(_event(unknown))
            assert brief is not None
            assert brief["risk"] == "caution", unknown
            assert "blastRadius" not in brief, unknown
            assert "blastRadiusLine" not in brief, unknown

    def test_known_limitation_hint_matching_is_substring_not_word(self) -> None:
        """DOCUMENTED DEFECT, inherited from OU-7's module — not introduced here.

        The hint tuples match by SUBSTRING (``task_modes``' own scheme, mirrored by
        ``approvalMeta.ts``), so ``widget`` contains the read verb ``get`` and the read
        verb short-circuits BEFORE the write hints. ``artifact_widget_create`` therefore
        reads as "reads only" on every surface that renders this derivation — the chat
        chips and the toast today, and now the channel brief.

        Pinned rather than fixed: the fix is word-boundary matching in BOTH languages,
        which changes a shipped frontend surface OU-7 owns, so it is a follow-up atom.
        This test is where that atom will find the case; it should be inverted then.
        """
        assert derive_blast_radius("artifact_widget_create", risk="caution") == {
            "writes": False,
            "network": False,
            "shell": False,
            "readOnly": True,
        }
        # The frontend agrees, which is why this is drift-free but still wrong.
        assert "get" in "widget"
        assert "get" in _ts_string_array("READ_VERB_HINTS")

    def test_the_composer_never_inspects_a_command_string(self) -> None:
        """Screening stays owned by task_modes — this module re-implements none of it.

        Checked over the parsed IDENTIFIERS, not the text: the module's own prose names
        ``is_read_only_bash`` while explaining who owns it, and a text scan reads
        comments (which is how this rail was vacuous on its first draft).
        """
        import ast

        source = Path(__file__).resolve().parents[1] / "src/personalclaw/approval_brief.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for owned_elsewhere in ("is_read_only_bash", "extract_bash_command", "classify_invocation"):
            assert owned_elsewhere not in referenced
        # Vacuity: the walk really does see this module's identifiers.
        assert "resolve_effective_risk" in referenced
