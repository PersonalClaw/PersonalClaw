"""Every door through which text from outside reaches a model in core, listed, and the one way
through it.

Text that crossed the owner's trust boundary reaches a model only through ``outside_text.admit``:
the injection screen reads it, then it is fenced as data with its source. This census reads the
whole of ``src/personalclaw`` and finds every place that fences text for a model (a call to
``security.fence_untrusted``) and every place the injection screen reads text
(``triggers.screen.screen``), and holds each to one of the lists below:

* :data:`DOORS` — where text from outside crosses into a prompt through the one function, and
  what crosses. Each must call what it names (``admit``, ``admit_payload``, or a door that does)
  and neither fence nor screen anything itself.
* :data:`FENCED_WHERE_IT_ARRIVES` — text fenced on its way in, each naming the door that screens
  it before any model reads it.
* :data:`FENCED_NOT_SCREENED` — doors that fence text from outside but are not read by the screen
  yet. Shrink-only: one sent through ``admit`` moves to :data:`DOORS`; a new one fails here.
* :data:`OWN_WORDS` — fences around PersonalClaw's own words or the owner's, quoted as data to
  keep them apart from an instruction: not text from outside.
* :data:`SCREEN_ONLY` — where the screen reads text that no model is then handed through it.

A new fence or screen call anywhere else fails the census, naming the file and function; so does
a listed door that stops going through the one function, and a listed site that is gone. A door
that hands text on with neither a fence nor the screen is invisible to any scan: those were found
by tracing what reaches a prompt, and each is driven in
``tests/test_text_from_outside_is_screened_and_fenced_at_every_door.py``. The trigger fire's door
is also driven for every trigger kind there is, so a new kind passes through it too.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import textwrap
import types
from dataclasses import dataclass

import pytest

from personalclaw.triggers.models import KINDS

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The one module whose function both screens and fences: the door itself.
THE_DOOR = ("outside_text.py", "admit")


@dataclass(frozen=True)
class Door:
    """A place text from outside crosses into a prompt: what crosses, and the call that takes it
    through the one function."""

    carries: str
    via: str


#: Every door through which text from outside reaches a model through ``outside_text``.
DOORS: dict[tuple[str, str], Door] = {
    ("gateway.py", "GatewayOrchestrator._fire_store_trigger"): Door(
        "a stored trigger's fire of every kind (a clock's, a file's, a watched page's, an event's, "
        "a chained one, a webhook's request, a view's render): what it hands its action",
        via="hand_on",
    ),
    ("triggers/fire_facts.py", "hand_on"): Door(
        "a fire's payload words and its $CONTEXT line", via="admit_payload"
    ),
    ("triggers/fire_facts.py", "_Outside.__call__"): Door(
        "what started a run: a file's path, a message's sender, a page's items, a webhook's body, "
        "what a workflow run said it produced",
        via="admit",
    ),
    ("attachments.py", "reading"): Door("an Inbox message's attachments, for its fire", "admit"),
    ("hooks.py", "hand_on"): Door(
        "what a lifecycle trigger's event carried (a prompt, a reply, a tool's result)",
        via="admit_payload",
    ),
    ("hooks.py", "take_in"): Door("what a lifecycle trigger's action printed", via="admit"),
    ("packs/prompt_cards.py", "_fence"): Door("a pasted prompt card", via="admit"),
    ("webhook_callbacks.py", "restored_context"): Door(
        "the context an agent saved for a callback's turn", via="admit"
    ),
    ("turn_source.py", "model_text"): Door(
        "a row of words taken in from outside, as a scheduled run's result opened as a chat",
        via="admit",
    ),
    ("turn_source.py", "theirs"): Door(
        "a line someone other than the owner sent, in a conversation's history", via="admit"
    ),
    ("channel_history.py", "_by_participant"): Door(
        "a group channel's recent messages, each participant's apart", via="admit"
    ),
    ("context.py", "ContextBuilder.build_message"): Door(
        "the post that started a channel thread", via="admit"
    ),
    ("learning/refiner.py", "fenced_evidence"): Door(
        "a run ledger's evidence, for the refiner's prompt", via="admit"
    ),
    ("subagent_report.py", "handed_on"): Door(
        "a helper's report: to the chat it reports to, and as subagent_status reads it",
        via="admit",
    ),
}

#: Text fenced where it arrives, and the door that screens it before any model reads it.
FENCED_WHERE_IT_ARRIVES: dict[tuple[str, str], str] = {
    ("event_triggers.py", "fire_payload"): "an event's value: the stored trigger's fire",
    ("triggers/web_poll.py", "poll_one"): "a watched page's items: the stored trigger's fire",
    ("trigger_sources/registry.py", "emit"): "an app's event: the stored trigger's fire",
    ("inbound/framing.py", "fence_payload"): (
        "a webhook's body: the stored trigger's fire; and PersonalClaw's own data handed to "
        "another agent (MCP, A2A, the bridge), whose model is not ours"
    ),
}

#: Doors that fence text from outside and are not read by the screen yet. Shrink-only.
FENCED_NOT_SCREENED: dict[tuple[str, str], str] = {
    ("action_providers/knowledge_report_provider.py", "_build_prompt"): (
        "a knowledge item's excerpt, in a report action's prompt"
    ),
    ("action_providers/net_fetch_provider.py", "NetFetchActionProvider._to_result"): (
        "a page a net-fetch action fetched"
    ),
    ("agents/native/inbox_tool_defs.py", "inbox_item_text"): "an Inbox item the agent's tool reads",
    ("apps/app_manager.py", "InstallResult.fix_prompt"): (
        "an app's install log, to debug it in a chat"
    ),
    ("apps/messaging.py", "send_message"): "a message one app sends another",
    ("browse/loop.py", "_fence_page"): "a page the agent browses",
    ("channel_trust.py", "fence_channel_content"): (
        "a group channel message from someone other than the owner, as the turn it starts"
    ),
    ("dashboard/attachment_extract.py", "file_block"): "a file attached in a chat",
    ("dashboard/chat_runner.py", "_apply_screen_frame"): "what a shared screen shows, described",
    ("dashboard/chat_runner.py", "_inject_investigate_context"): (
        "an Inbox item or an artifact the owner opens in a chat to look into"
    ),
    (
        "dashboard/chat_runner.py",
        "_inject_knowledge_content",
    ): "a knowledge item attached to a chat",
    ("doc_parser.py", "text_for_model"): "a document's text",
    ("inbound/capture_store.py", "_fence"): "a page the browser connector captured",
    ("inbox_service.py", "fence_message_for_prompt"): "an Inbox message, for triage or a reply",
    ("inbox_service.py", "InboxService.generate_digest"): "a channel's messages, in its digest",
    ("knowledge/insights.py", "_insights_prompt"): "an ingested document, for its insights",
    ("knowledge/session_brief.py", "SessionBrief.render"): "knowledge items, in a session's brief",
    ("knowledge/source_digest.py", "fence_item"): "a watched source's item, in its digest",
    ("knowledge/source_streams.py", "fenced_snippet"): "a watched source's snippet",
    ("mcp_artifacts.py", "read_reply"): "an artifact's text, which may have come from anywhere",
    ("mcp_core.py", "_load_skill_resource"): "an installed skill's resource file",
    ("proactive/manifest.py", "render_manifest_lines"): "the triage digest's items",
    ("rooms/turn.py", "build_member_prompt"): "what the other members of a room said",
    ("rooms/turn.py", "_summarize_slice"): "a room's transcript, summarized",
    ("web/fetch.py", "web_extract"): "a page the agent fetched",
    ("workflows/bindings.py", "_pipe_fenced"): "a workflow step's output, read by a later step",
    ("workflows/bindings.py", "_pipe_fenced_sources"): "knowledge a workflow step reads",
    ("workflows/filedrop.py", "read_dropped_text"): "a file dropped to start a workflow",
}

#: Fences around PersonalClaw's own words or the owner's, which are not text from outside.
OWN_WORDS: dict[tuple[str, str], str] = {
    ("after_turn_review.py", "_build_ladder_prompt"): "a turn of the owner's chat, for its review",
    ("chat_recall.py", "render"): "the owner's earlier chats, recalled",
    ("inbox_service.py", "InboxService._within_limit"): "the agent's own draft, asked for again",
    ("learning_report.py", "narrate_identity_report"): "what PersonalClaw learned, narrated",
    ("learning/proposals.py", "enqueue"): "the excerpt a learning proposal quotes for review",
    ("learning/replay.py", "_candidate_prompt"): "a candidate PersonalClaw made, in a replay",
    ("memory_locality.py", "cross_partition_block"): "the owner's memory from another folder",
    ("proposer/brief.py", "HandoffBrief.render"): "what a stalled agent already tried",
    ("reply_answers.py", "_check_prompt"): "the owner's notes and instruction, and the draft",
    ("reply_grounding.py", "Grounding.prompt_block"): "the owner's notes a reply draws on",
    ("routing/proposals.py", "_fence"): "routing telemetry, for a routing proposal",
    ("skills/proposals.py", "enqueue"): "the trace a skill proposal was made from",
}

#: Where the injection screen reads text that no model is then handed through it.
SCREEN_ONLY: dict[tuple[str, str], str] = {
    ("guardrails/scan.py", "scan_outbound"): "a prompt bound for a remote model, before it leaves",
    ("learning/refiner.py", "screen_evidence"): "a refiner's evidence, clustered by statistics",
    ("planning/scratchpad.py", "triage_line"): "a scratchpad line, before it becomes an Inbox row",
    ("triggers/firepath.py", "evaluate"): "a fire's payload text, at its gate walk",
}


# ── the scan ──


def _screen_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names a module calls the screen by, and the names it holds the screen's module by."""
    functions: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.triggers.screen":
            functions.update(a.asname or a.name for a in node.names if a.name == "screen")
        elif isinstance(node, ast.ImportFrom) and node.module == "personalclaw.triggers":
            modules.update(a.asname or a.name for a in node.names if a.name == "screen")
        elif isinstance(node, ast.Import):
            modules.update(
                a.asname
                for a in node.names
                if a.name == "personalclaw.triggers.screen" and a.asname
            )
    return functions, modules


def scan(sources: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    """``{"fence": {(file, function)}, "screen": {(file, function)}}`` for *sources*, a map of a
    file's path under ``src/personalclaw`` to its text. A function is named with its class, as
    ``Class.method``; a call at a module's top level as ``<module>``."""
    found: dict[str, set[tuple[str, str]]] = {"fence": set(), "screen": set()}
    for rel, text in sources.items():
        tree = ast.parse(text)
        screen_functions, screen_modules = _screen_names(tree)

        def _walk(node: ast.AST, scope: tuple[str, ...], in_function: bool) -> None:
            for child in ast.iter_child_nodes(node):
                # A function is named with its class; a function nested in one is its outer one's.
                inner, inner_in_function = scope, in_function
                if isinstance(child, ast.ClassDef) and not in_function:
                    inner = (*scope, child.name)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not in_function:
                    inner, inner_in_function = (*scope, child.name), True
                if isinstance(child, ast.Call):
                    callee = child.func
                    name = (
                        callee.id
                        if isinstance(callee, ast.Name)
                        else callee.attr if isinstance(callee, ast.Attribute) else ""
                    )
                    where = (rel, ".".join(scope) or "<module>")
                    if name == "fence_untrusted":
                        found["fence"].add(where)
                    elif (isinstance(callee, ast.Name) and name in screen_functions) or (
                        name == "screen"
                        and isinstance(callee, ast.Attribute)
                        and isinstance(callee.value, ast.Name)
                        and callee.value.id in screen_modules
                    ):
                        found["screen"].add(where)
                _walk(child, inner, inner_in_function)

        _walk(tree, (), False)
    return found


def _tree() -> dict[str, str]:
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    }


def _function(text: str, qualname: str) -> ast.AST | None:
    """The function *qualname* (``name`` or ``Class.method``) in *text*, or None."""
    parts = qualname.split(".")
    nodes: list[ast.AST] = list(ast.parse(text).body)
    for depth, part in enumerate(parts):
        last = depth == len(parts) - 1
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef) if last else (ast.ClassDef,)
        match = next((n for n in nodes if isinstance(n, kinds) and n.name == part), None)
        if match is None:
            return None
        nodes = list(getattr(match, "body", []))
        if last:
            return match
    return None


def _calls(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            callee = call.func
            if isinstance(callee, ast.Name):
                out.add(callee.id)
            elif isinstance(callee, ast.Attribute):
                out.add(callee.attr)
    return out


def door_problems(sources: dict[str, str]) -> list[str]:
    """What is wrong with *sources* by this census: every problem, each named."""
    found = scan(sources)
    listed_fences = {
        THE_DOOR,
        *FENCED_WHERE_IT_ARRIVES,
        *FENCED_NOT_SCREENED,
        *OWN_WORDS,
    }
    listed_screens = {THE_DOOR, *SCREEN_ONLY}
    problems = [
        f"{rel}:{fn} fences text for a model outside the door: send it through "
        "outside_text.admit, or list what it fences"
        for rel, fn in sorted(found["fence"] - listed_fences)
    ]
    problems += [
        f"{rel}:{fn} screens text outside the door: send it through outside_text.admit, or list "
        "what it reads"
        for rel, fn in sorted(found["screen"] - listed_screens)
    ]
    problems += [
        f"{rel}:{fn} is listed but fences nothing now: move it or delete its row"
        for rel, fn in sorted(listed_fences - found["fence"])
    ]
    problems += [
        f"{rel}:{fn} is listed but screens nothing now: move it or delete its row"
        for rel, fn in sorted(listed_screens - found["screen"])
    ]
    for (rel, fn), door in sorted(DOORS.items()):
        node = _function(sources.get(rel, ""), fn) if rel in sources else None
        if node is None:
            problems.append(f"{rel}:{fn} is a door that no longer exists")
            continue
        calls = _calls(node)
        if door.via not in calls:
            problems.append(f"{rel}:{fn} hands on {door.carries} without {door.via}()")
        if (rel, fn) in found["fence"] or (rel, fn) in found["screen"]:
            problems.append(f"{rel}:{fn} fences or screens by itself, beside the door")
    return problems


# ── the rails ──


def test_every_place_core_fences_or_screens_text_for_a_model_is_listed():
    assert door_problems(_tree()) == []


def test_the_one_function_both_screens_and_fences_and_nothing_else_does_both():
    found = scan(_tree())
    assert found["fence"] & found["screen"] == {THE_DOOR}


def test_the_census_reads_the_whole_tree():
    """Vacuity: a scan that stopped matching would pass with nothing found. Floored at what it
    finds today."""
    found = scan(_tree())
    assert len(found["fence"]) >= 45 and len(found["screen"]) >= 5, found
    assert len(DOORS) >= 14


#: How many doors still fence text from outside without the screen. It may only fall: a door sent
#: through ``outside_text.admit`` moves its row to :data:`DOORS` and lowers this in the same change.
NOT_SCREENED_CEILING = 28


def test_the_doors_not_screened_yet_may_only_shrink():
    assert len(FENCED_NOT_SCREENED) <= NOT_SCREENED_CEILING, (
        "a door that fences text from outside without the screen was added: send it through "
        "outside_text.admit and list it in DOORS"
    )


# ── planted violations ──


_PLANTED_FENCE = """
def read_page(text):
    from personalclaw.security import fence_untrusted

    return fence_untrusted(text, source="web")
"""

_PLANTED_SCREEN = """
from personalclaw.triggers.screen import screen as sniff


def accept(text):
    return text if sniff(text).clean else ""
"""


def test_a_new_door_that_fences_without_the_screen_fails_the_census():
    tree = {**_tree(), "planted.py": _PLANTED_FENCE}
    assert door_problems(tree) == [
        "planted.py:read_page fences text for a model outside the door: send it through "
        "outside_text.admit, or list what it fences"
    ]


def test_a_new_screen_beside_the_door_fails_the_census():
    tree = {**_tree(), "planted.py": _PLANTED_SCREEN}
    assert door_problems(tree) == [
        "planted.py:accept screens text outside the door: send it through outside_text.admit, "
        "or list what it reads"
    ]


def test_a_door_that_stops_going_through_the_one_function_fails_the_census():
    tree = _tree()
    rel = "channel_history.py"
    tree[rel] = (
        tree[rel]
        .replace("from personalclaw.outside_text import admit, withheld", "from x import y")
        .replace("admitted = admit(", "admitted = fence_untrusted(")
    )
    problems = door_problems(tree)
    assert (
        f"{rel}:_by_participant fences text for a model outside the door: send it through "
        "outside_text.admit, or list what it fences"
    ) in problems
    assert any(
        p.startswith(f"{rel}:_by_participant hands on") and p.endswith("without admit()")
        for p in problems
    ), problems


# ── every trigger kind's fire passes the door ──


class _Recorder:
    def __init__(self) -> None:
        self.handed: list = []

    async def execute(self, config, ctx, timeout=30):
        self.handed.append(ctx)
        return types.SimpleNamespace(success=True, summary="done", stdout="", outcome="")


#: The prose keys whose value is a list of words: a page's new items, a fire's changed paths.
_LIST_KEYS = frozenset({"new_items", "changed", "paths", "added", "modified", "removed"})


def _leaves(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    return []


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_trigger_kind_s_fire_hands_on_through_the_door(kind, monkeypatch):
    """The fire's door, driven for every trigger kind there is: what the action is handed is what
    the door made of the payload, every word under the kind's prose keys fenced with the trigger
    as its source."""
    import personalclaw.action_providers as AP
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.security import is_fenced
    from personalclaw.triggers import fire_facts, grants
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.screen import prose_keys

    passed: list = []
    real = fire_facts.hand_on

    async def _spy(trigger, payload, *, context=""):
        made = await real(trigger, payload, context=context)
        passed.append(made)
        return made

    monkeypatch.setattr(fire_facts, "hand_on", _spy)
    recorder = _Recorder()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: recorder)
    trigger = Trigger(
        id=f"{kind}:census",
        name="Census",
        kind=kind,
        workflow={"inline": {"provider": "notify", "config": {"title_template": "New"}}},
    )
    grants.give(trigger)
    words = "Release 2.1 is out."
    payload = {
        "trigger_id": trigger.id,
        **{key: [words] if key in _LIST_KEYS else words for key in prose_keys(kind)},
    }

    asyncio.run(object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, payload))

    assert len(passed) == 1, f"a {kind} fire bypassed fire_facts.hand_on"
    (handed,) = recorder.handed
    assert handed.payload == passed[0].payload
    for key in prose_keys(kind):
        (leaf,) = _leaves(handed.payload[key])
        assert is_fenced(leaf) and words in leaf, f"{kind}.{key} reached the action unfenced"
        assert f"source=trigger:{trigger.id}" in leaf


def test_the_fire_dispatch_hands_on_through_the_door_before_its_action_runs():
    """Structural, beside the sweep above: the sweep proves the kinds that exist, and this fails a
    fire dispatch that would run its action before the door."""
    from personalclaw.gateway import GatewayOrchestrator

    node = ast.parse(textwrap.dedent(inspect.getsource(GatewayOrchestrator._fire_store_trigger)))
    lines = {
        name: [
            c.lineno
            for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == name
        ]
        for name in ("hand_on", "execute")
    }
    assert lines["hand_on"] and lines["execute"], lines
    assert min(lines["hand_on"]) < min(lines["execute"]), "the action can run before the door"
