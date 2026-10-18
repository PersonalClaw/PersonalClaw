"""The source-tree checks split from
``tests/test_chat_session_resurrection_audit.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import chat_session_resurrection_audit_shared
import source_index
from chat_session_resurrection_audit_shared import (
    _CLIENT_NAMED_MUST_GUARD,
    _CREATES_BY_DESIGN,
    _CREATOR,
    _OWNER,
    SRC,
    scan_source,
)

_isolate_home = chat_session_resurrection_audit_shared._isolate_home


def scan() -> tuple[dict[str, list[int]], dict[str, list[int]], int]:
    """``({creator_key: lines}, {guard_key: lines}, files_scanned)`` over the tree."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scanned = 0
    for path, tree in source_index.package_trees():
        scan_source(tree, path.relative_to(SRC).as_posix(), creators, guards)
        scanned += 1
    return creators, guards, scanned


#: Vacuity floor for the walk. The tree has ~950 modules; a scan that walks a handful
#: has lost its root, and "no unmapped call sites" would read as an improvement.
FILES_SCANNED_FLOOR = 800


#: The ``{session}``-addressed route population, derived from the real route table.
#: A FLOOR: the family this audit reasons about ("every other writer to a session")
#: is exactly this set, and a derivation that stopped finding it would make the
#: classification above look complete when it had simply gone blind. Re-measured at 50
#: on this tree: 47 chat-session routes + 3 ``/api/skills/ephemeral/{session}`` routes.
SESSION_ROUTE_FLOOR = 45


def test_the_census_is_not_vacuous():
    """Every assertion below is only as good as the walk that produced it."""
    creators, guards, scanned = scan()
    assert scanned >= FILES_SCANNED_FLOOR, (
        f"the census walked only {scanned} modules (floor {FILES_SCANNED_FLOOR}) — the "
        f"scan lost its root, so 'no unguarded writer' would read as an improvement. "
        f"Check SRC={SRC}."
    )
    assert creators, (
        f"no `{_CREATOR}` call site found ANYWHERE — the matcher is broken, and the "
        f"allowlists below are being compared against an empty set"
    )
    assert guards, (
        f"no `{_OWNER}` call site found anywhere — either the owner was deleted (and "
        f"then the two client-named writers resurrect again) or the matcher is broken"
    )


def test_the_session_route_family_is_still_derivable():
    """The floor under "47 of the other 49 already refuse".

    This audit's scope claim is about the ``{session}``-addressed route FAMILY. That
    family is derived from the real route table rather than hand-listed, so this asserts
    the derivation still finds it — otherwise a future reader could conclude the family
    is two routes wide because the walk broke.
    """
    routes = _session_routes()
    assert len(routes) >= SESSION_ROUTE_FLOOR, (
        f"only {len(routes)} '{{session}}'-addressed routes derived (floor "
        f"{SESSION_ROUTE_FLOOR}). The route-table walk lost its target, so this audit's "
        f"scope claim is unverified. Routes found: {sorted(routes)}"
    )
    # The two writers this module exists for must be IN the derived family, not assumed.
    assert ("POST", "/api/chat/sessions/{session}/resume") in routes, sorted(routes)


def _session_routes() -> set[tuple[str, str]]:
    """``{(method, path)}`` for every ``{session}``-addressed route, from the AST of the
    modules that register them. Derived, not hand-listed."""
    out: set[tuple[str, str]] = set()
    for rel in (
        "dashboard/routes.py",
        "dashboard/session_bulk.py",
        "dashboard/session_starters.py",
    ):
        tree = source_index.tree(SRC / rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if not attr.startswith("add_"):
                continue
            method = attr[4:].upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not node.args:
                continue
            p = node.args[0]
            if isinstance(p, ast.Constant) and isinstance(p.value, str) and "{session}" in p.value:
                out.add((method, p.value))
    return out


def test_every_session_creator_call_site_is_classified():
    """Every ``get_or_create_session`` site is in exactly one allowlist.

    A new/unmapped site reds CI by name, forcing the author to decide whether the
    session name can arrive from a request (→ it must call the owner) or is minted /
    server-derived (→ creates-by-design).
    """
    creators, _guards, _scanned = scan()
    allow = set(_CLIENT_NAMED_MUST_GUARD) | set(_CREATES_BY_DESIGN)

    unmapped = sorted(set(creators) - allow)
    assert not unmapped, (
        f"Unmapped `{_CREATOR}` call site(s) — classify each in "
        f"tests/test_chat_session_resurrection_audit.py as CLIENT-NAMED (the session "
        f"name comes from a request → call "
        f"chat_persistence.{_OWNER} first and 404 `session_not_found` on a miss) or "
        f"CREATES-BY-DESIGN (no name, or a server-derived one):\n"
        + "\n".join(
            f"  {k}  ({', '.join(f'{k.split('::')[0]}:{ln}' for ln in creators[k])})"
            for k in unmapped
        )
    )

    stale = sorted(allow - set(creators))
    assert not stale, (
        f"Stale allowlist entr(y/ies) — no such `{_CREATOR}` site exists anymore; remove "
        f"from the allowlist:\n" + "\n".join(f"  {k}" for k in stale)
    )


def test_every_client_named_writer_actually_calls_the_owner():
    """The teeth. Classification alone would be a comment; this is the check.

    A handler listed as CLIENT-NAMED must call
    ``chat_persistence.session_key_exists`` in the SAME function that reaches
    ``get_or_create_session``. Deleting the guard while leaving the allowlist entry in
    place reds here — which is the failure mode a hand-maintained list cannot catch.
    """
    _creators, guards, _scanned = scan()
    missing = sorted(set(_CLIENT_NAMED_MUST_GUARD) - set(guards))
    assert not missing, (
        f"client-named writer(s) reach `{_CREATOR}` without asking `{_OWNER}` first, so a "
        f"hard-deleted session key can be written back into existence:\n"
        + "\n".join(f"  {k}  — {_CLIENT_NAMED_MUST_GUARD[k]}" for k in missing)
    )
