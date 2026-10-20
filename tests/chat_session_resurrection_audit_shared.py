"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_chat_session_resurrection_audit.py``

Its checks:
``tests/rails/test_chat_session_resurrection_audit_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


#: The one owner. A client-named writer must call this before creating anything.
_OWNER = "session_key_exists"


#: The creator whose miss-branch mints a blank session — the mechanism being audited.
_CREATOR = "get_or_create_session"


#    proved first. Every key here is additionally asserted to call the owner. ──
_CLIENT_NAMED_MUST_GUARD: dict[str, str] = {
    "dashboard/chat_handlers.py::api_chat": (
        "POST /api/chat — `body['session']` is client-supplied; the resurrection this "
        "module is named for"
    ),
    "dashboard/chat_handlers.py::api_chat_session_resume": (
        "POST /api/chat/sessions/{session}/resume — `match_info['session']` / "
        "`body['key']` are client-supplied; the second resurrection"
    ),
}


#    all (the callee auto-generates ``chat-N-<ts>``), or the name is SERVER-derived
#    from a resource that owns it (a cron job, a loop, a plan, an API client id), or
#    the site sits BEHIND an existence check that is the check. ──
_CREATES_BY_DESIGN: dict[str, str] = {
    # ── no name at all → the callee mints `chat-N-<ts>` ──
    "channel_inbound.py::_route_to_session": (
        "no name — an unlinked channel thread mints a session, then link_channel binds it"
    ),
    "dashboard/handlers/investigate.py::api_investigate": "no name — investigate mints its own",
    "gateway.py::GatewayOrchestrator._deliver_result": "no name — result delivery mints its own",
    "dashboard/chat_fork.py::api_chat_session_fork": (
        "name=None — a fork mints a NEW key; the PARENT it reads is resolved and 404s"
    ),
    "dashboard/chat_fork.py::api_chat_session_fork_rewound": ("name=None — same as the fork above"),
    # ── the explicit create route ──
    "dashboard/chat_handlers.py::api_chat_session_create": (
        "POST /api/chat/sessions IS the create verb — this is the one route whose job "
        "is to mint a key, and it is what a client uses instead of naming a dead one. "
        "Its `body['name']` IS client-supplied (measured: naming a hard-deleted key "
        "here does re-materialise it), so this row is a JUDGMENT, not a mechanical "
        "fact: a create verb cannot distinguish 'hard-deleted' from 'never existed' "
        "(no tombstone is kept), so refusing an unknown name would break creation "
        "outright. And the hazard the two guarded writers carry is *implicit* "
        "continuation — a send or a resume asserts 'this session exists, continue it' "
        "— where an explicit create asserts the opposite. A name that IS a kept chat's "
        "opens that chat (it is rehydrated first), so no blank session is minted over a "
        "kept transcript. The dashboard never sends a session KEY here (ChatPage's "
        "ensureSession and a document's comment chat both omit `name`)"
    ),
    # ── SERVER-derived key: the owning resource's identity IS the session name, so a
    #    miss means 'this resource has no session yet', never 'the client typed a key' ──
    "dashboard/schedule_inject.py::inject_schedule_result_to_session": (
        "name=`cron-{job.id}` — derived from the cron job that owns the session"
    ),
    "loop/manager.py::start": "name=`session_key(loop_id)` — derived from the loop",
    "loop/manager.py::spawn_task_worker": (
        "name=`task_session_key(loop.id, task.id)` — derived from the loop task"
    ),
    "planning/runner.py::run_planner_pass": "name=`skey` — derived from the planner pass",
    "dashboard/approval_state.py::DashboardApprovalState._channel_chat": (
        "name = a conversation a channel runs itself, linked to the channel's thread before its "
        "turn runs (`holds_channel_chat`), and reached only when nothing of it is persisted: its "
        "first turn is still running, and the channel writes that turn under the same key"
    ),
    "inbound/openai_dialect.py::handle_chat_completions": (
        "name=`session_key_for(client_id, tag)` — namespaced by the AUTHENTICATED client "
        "id, and create-on-first-use is this dialect's whole contract (it has no delete "
        "verb of its own, so there is no deleted state for a client to resurrect through)"
    ),
    # ── behind the existence check, or driven BY the log ──
    "dashboard/chat_persistence.py::_rehydrate_session_from_history": (
        "reached only AFTER resolve_history_key + get_metadata confirmed the key is "
        "persisted — this site IS the existence check the owner delegates to"
    ),
    "dashboard/chat_persistence.py::restore_recent_sessions": (
        "iterates keys the conversation log itself listed, so existence is a given"
    ),
}


def scan_source(source: str | ast.Module, rel: str, creators: dict, guards: dict) -> None:
    """Record every ``get_or_create_session`` / owner call site in one module.

    Split out so the mechanism can be exercised against SYNTHETIC source (see
    :func:`test_the_scanner_sees_a_planted_unguarded_writer`). A census whose only test
    is "the tree currently measures N" cannot distinguish "no unguarded writers" from
    "the matcher is broken", and that distinction is the whole point.
    """
    tree = source if isinstance(source, ast.Module) else ast.parse(source)

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.q: list[str] = []

        def visit_FunctionDef(self, n) -> None:  # noqa: ANN001
            self.q.append(n.name)
            self.generic_visit(n)
            self.q.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n) -> None:  # noqa: ANN001
            self.q.append(n.name)
            self.generic_visit(n)
            self.q.pop()

        def visit_Call(self, n: ast.Call) -> None:
            f = n.func
            callee = (
                f.attr
                if isinstance(f, ast.Attribute)
                else (f.id if isinstance(f, ast.Name) else "")
            )
            key = f"{rel}::{'.'.join(self.q) or '<module>'}"
            if callee == _CREATOR:
                creators.setdefault(key, []).append(n.lineno)
            elif callee == _OWNER:
                guards.setdefault(key, []).append(n.lineno)
            self.generic_visit(n)

    V().visit(tree)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Every write lands in tmp_path — never the real ``~/.personalclaw``."""
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path
