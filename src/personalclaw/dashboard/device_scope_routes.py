"""Every route a scoped session may reach, and every route of a family a scope touches that it
may not: ``DEVICE_SCOPE_ROUTES``, the one table ``device_scopes``' gate reads.

Keyed on ``(method, canonical route template)``, HEAD read as GET. A row names the scope that
admits it (``SHELL`` for every scoped session, ``PUBLIC`` for an auth-exempt route) and its target
rule, or is :data:`OUTSIDE`. A route with no row is refused like an ``OUTSIDE`` one; the census
(``tests/test_the_device_scope_table_holds_every_route.py``) holds every route of
:data:`FAMILIES` to an explicit row, so none is forgotten.

This release admits the shell, ``status`` and the ``loops`` reads. Every steer, answer, approval,
create and land is ``OUTSIDE`` until the client's own principal exists in the approval and loop
doors. The socket (``/api/ws``) is the shell's, and hears only the frames
``device_scopes.FRAME_SCOPES`` admits; it and a loop's stream close when the session ends.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Every scoped session's (its page, its sign-in and its sign-out), whatever its scope.
SHELL = "shell"
#: Auth-exempt: answered to anyone, so to a scoped session too.
PUBLIC = "public"

#: A row's target rule: `loop` holds for a loops-table row only, so a run-backed id (a workflow run
#: started as a loop) or an unknown one is refused.
TARGET_LOOP = "loop"


@dataclass(frozen=True)
class Row:
    """One route's verdict: the scope that admits it, and the rule its target is held to."""

    scope: str
    target: str = ""
    #: Whether a scoped request may give consent (``safety_flags.CONFIRM_FIELDS``) here. No row in
    #: this release does.
    consent: bool = False


#: Not reachable from any scope.
OUTSIDE = Row(scope="")

#: The route families a scope touches. Every route under one, matched as a string prefix (so
#: ``/api/loops-archive`` is one), needs an explicit row.
FAMILIES: tuple[str, ...] = (
    "/api/loops",
    "/api/approvals",
    "/api/waiting",
    "/api/status",
    "/api/healthz",
    "/api/design",
    "/api/tasks",
    "/api/projects",
    "/api/agents",
    "/api/artifacts",
    "/api/chat/sessions/{session}/questions",
    "/api/auth",
    "/api/ws",
    "/api/browse/connector",
)

DEVICE_SCOPE_ROUTES: dict[tuple[str, str], Row] = {
    # A browser connector's session reaches its own routes and nothing else (connector_origin).
    ("POST", "/api/browse/connector"): Row("connector"),  # attach
    ("DELETE", "/api/browse/connector"): Row("connector"),  # detach
    ("GET", "/api/browse/connector"): Row("connector"),  # whether it is attached
    ("GET", "/api/browse/connector/tabs"): Row("connector"),  # the tabs runs asked it for
    ("POST", "/api/browse/connector/tabs/{request_id}"): Row("connector"),  # a tab it opened
    ("GET", "/api/auth/session"): Row(
        SHELL
    ),  # the account view, cut to {signed_in, scope, expires_at}
    ("GET", "/api/auth-status"): Row(SHELL),  # its remaining minutes
    ("POST", "/api/auth/logout"): Row(SHELL),  # its own sign-out (sign_in_pair.SIGN_OUT_PATH)
    ("GET", "/api/healthz"): Row(PUBLIC),  # auth-exempt; carries the version
    ("GET", "/api/status"): Row(
        "status"
    ),  # cut to {version, uptime_secs, healthy, update_available}
    ("GET", "/api/loops"): Row("loops"),  # cut to the loops table's rows
    ("GET", "/api/loops/{id}"): Row("loops", target=TARGET_LOOP),
    ("GET", "/api/loops/{id}/report"): Row("loops", target=TARGET_LOOP),
    ("GET", "/api/loops/{id}/kept-work"): Row("loops", target=TARGET_LOOP),
    ("GET", "/api/loops/{id}/merge"): Row(
        "loops", target=TARGET_LOOP
    ),  # the review, not the answer
    ("GET", "/api/loops/{id}/conflict"): Row(
        "loops", target=TARGET_LOOP
    ),  # the review, not the answer
    ("GET", "/api/loops/{id}/plan-session"): Row("loops", target=TARGET_LOOP),
    ("GET", "/api/loops/{id}/design/tokens"): Row("loops", target=TARGET_LOOP),
    ("GET", "/api/design/tokens/default"): Row("loops"),
    ("GET", "/api/loops/{id}/stream"): Row("loops", target=TARGET_LOOP),  # closes when it ends
    ("GET", "/api/ws"): Row(SHELL),  # its frames are filtered (device_scopes.FRAME_SCOPES)
    # ── outside every scope in this release ──
    ("GET", "/api/agents"): OUTSIDE,
    ("POST", "/api/agents"): OUTSIDE,
    ("GET", "/api/agents/day"): OUTSIDE,
    ("DELETE", "/api/agents/detail/{name}"): OUTSIDE,
    ("GET", "/api/agents/detail/{name}"): OUTSIDE,
    ("PATCH", "/api/agents/detail/{name}"): OUTSIDE,
    ("POST", "/api/agents/export"): OUTSIDE,
    ("GET", "/api/agents/installed"): OUTSIDE,
    ("POST", "/api/agents/routing/dismiss"): OUTSIDE,
    ("GET", "/api/agents/routing/status"): OUTSIDE,
    ("POST", "/api/agents/routing/unmute"): OUTSIDE,
    ("POST", "/api/agents/sync"): OUTSIDE,
    ("DELETE", "/api/agents/{name}"): OUTSIDE,
    ("PUT", "/api/agents/{name}"): OUTSIDE,
    ("GET", "/api/approvals"): OUTSIDE,
    ("POST", "/api/approvals/{id}/{action}"): OUTSIDE,
    ("GET", "/api/artifacts"): OUTSIDE,
    ("POST", "/api/artifacts"): OUTSIDE,
    ("GET", "/api/artifacts/deployed"): OUTSIDE,
    ("GET", "/api/artifacts/folders"): OUTSIDE,
    ("POST", "/api/artifacts/folders"): OUTSIDE,
    ("DELETE", "/api/artifacts/folders/{id}"): OUTSIDE,
    ("PATCH", "/api/artifacts/folders/{id}"): OUTSIDE,
    ("GET", "/api/artifacts/pinned"): OUTSIDE,
    ("DELETE", "/api/artifacts/{slug}"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}"): OUTSIDE,
    ("PATCH", "/api/artifacts/{slug}"): OUTSIDE,
    ("POST", "/api/artifacts/{slug}/changed"): OUTSIDE,
    ("DELETE", "/api/artifacts/{slug}/deploy"): OUTSIDE,
    ("POST", "/api/artifacts/{slug}/deploy"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/events"): OUTSIDE,
    ("POST", "/api/artifacts/{slug}/events"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/extract"): OUTSIDE,
    ("PATCH", "/api/artifacts/{slug}/folder"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/model"): OUTSIDE,
    ("PUT", "/api/artifacts/{slug}/model"): OUTSIDE,
    ("POST", "/api/artifacts/{slug}/pin"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/raw"): OUTSIDE,
    ("PUT", "/api/artifacts/{slug}/raw"): OUTSIDE,
    ("POST", "/api/artifacts/{slug}/regenerate"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/versions"): OUTSIDE,
    ("GET", "/api/artifacts/{slug}/versions/{version}"): OUTSIDE,
    ("POST", "/api/auth/confirm"): OUTSIDE,
    ("POST", "/api/auth/enroll/complete"): OUTSIDE,
    ("POST", "/api/auth/enroll/start"): OUTSIDE,
    ("POST", "/api/auth/login"): OUTSIDE,
    ("POST", "/api/auth/password"): OUTSIDE,
    ("POST", "/api/auth/rotate-key"): OUTSIDE,
    ("POST", "/api/auth/signed-urls"): OUTSIDE,
    ("GET", "/api/auth/status"): OUTSIDE,
    ("POST", "/api/chat/sessions/{session}/questions/{question}/answer"): OUTSIDE,
    ("POST", "/api/loops"): OUTSIDE,
    ("POST", "/api/loops/classify"): OUTSIDE,
    ("POST", "/api/loops/validate"): OUTSIDE,
    ("DELETE", "/api/loops/{id}"): OUTSIDE,
    ("PATCH", "/api/loops/{id}"): OUTSIDE,
    ("PUT", "/api/loops/{id}"): OUTSIDE,
    ("PUT", "/api/loops/{id}/agent-cli-self-approval"): OUTSIDE,
    ("POST", "/api/loops/{id}/autopilot"): OUTSIDE,
    ("POST", "/api/loops/{id}/commit"): OUTSIDE,
    ("POST", "/api/loops/{id}/conflict"): OUTSIDE,
    ("POST", "/api/loops/{id}/grill-tree"): OUTSIDE,
    ("DELETE", "/api/loops/{id}/kept-work/{task_id}"): OUTSIDE,
    ("POST", "/api/loops/{id}/kept-work/{task_id}/merge"): OUTSIDE,
    ("POST", "/api/loops/{id}/merge"): OUTSIDE,
    ("POST", "/api/loops/{id}/nudge"): OUTSIDE,
    ("POST", "/api/loops/{id}/plan/approve"): OUTSIDE,
    ("POST", "/api/loops/{id}/plan/comment"): OUTSIDE,
    ("POST", "/api/loops/{id}/plan/edit"): OUTSIDE,
    ("POST", "/api/loops/{id}/plan/retry"): OUTSIDE,
    ("POST", "/api/loops/{id}/plan/start"): OUTSIDE,
    ("POST", "/api/loops/{id}/queue"): OUTSIDE,
    ("GET", "/api/projects"): OUTSIDE,
    ("POST", "/api/projects"): OUTSIDE,
    ("POST", "/api/projects/import"): OUTSIDE,
    ("GET", "/api/projects/settings"): OUTSIDE,
    ("PUT", "/api/projects/settings"): OUTSIDE,
    ("DELETE", "/api/projects/{project_id}"): OUTSIDE,
    ("GET", "/api/projects/{project_id}"): OUTSIDE,
    ("PUT", "/api/projects/{project_id}"): OUTSIDE,
    ("POST", "/api/projects/{project_id}/context-adapters/regenerate"): OUTSIDE,
    ("GET", "/api/projects/{project_id}/export"): OUTSIDE,
    ("GET", "/api/projects/{project_id}/linked"): OUTSIDE,
    ("GET", "/api/projects/{project_id}/work"): OUTSIDE,
    ("POST", "/api/projects/{project_id}/work/claim"): OUTSIDE,
    ("POST", "/api/projects/{project_id}/work/release"): OUTSIDE,
    ("GET", "/api/tasks"): OUTSIDE,
    ("POST", "/api/tasks"): OUTSIDE,
    ("POST", "/api/tasks/bulk"): OUTSIDE,
    ("GET", "/api/tasks/graph"): OUTSIDE,
    ("GET", "/api/tasks/providers"): OUTSIDE,
    ("GET", "/api/tasks/ready"): OUTSIDE,
    ("POST", "/api/tasks/search"): OUTSIDE,
    ("DELETE", "/api/tasks/{task_id}"): OUTSIDE,
    ("GET", "/api/tasks/{task_id}"): OUTSIDE,
    ("PUT", "/api/tasks/{task_id}"): OUTSIDE,
    ("GET", "/api/tasks/{task_id}/comments"): OUTSIDE,
    ("POST", "/api/tasks/{task_id}/comments"): OUTSIDE,
    ("DELETE", "/api/tasks/{task_id}/comments/{comment_id}"): OUTSIDE,
    ("GET", "/api/waiting"): OUTSIDE,
    ("GET", "/api/ws/terminal/{session_id}"): OUTSIDE,
}
