"""The source-tree checks split from
``tests/test_destructive_actions_confirm.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index

import personalclaw

SRC = Path(personalclaw.__file__).parent


class TestNoNewUngatedDestructiveRoute:
    """The rail. The two fixes above are point fixes; this is what makes the class stay fixed.

    Every POST route whose path carries a destructive verb must gate on ``confirm`` in its own
    handler, or be listed in ``_REVIEWED_UNGATED`` with a reason. A new ungated destructive verb
    then reds here instead of shipping.
    """

    # Verbs that destroy or discard state under a name that is NOT "delete". Delete routes are
    # excluded deliberately: the verb itself warns, and they are gated at the UI by `confirmDelete`.
    _DESTRUCTIVE = re.compile(r"/(merge|reset|purge|wipe|clear|prune|revoke|rotate)\b")

    # What "gates on confirm" LOOKS like in a handler body. Since issue 3000 there is exactly
    # one answer: `safety_flags.confirm_granted`, the single strict predicate every confirm door
    # calls. A bare `get("confirm")` is deliberately NOT a tell — see
    # `test_the_gate_tell_is_the_predicate_not_a_bare_field_read` for why that was the bug.
    _GATE_TELLS = ("confirm_granted(",)

    # path -> why it legitimately needs no confirm gate. Every entry was read before being
    # written; "pre-existing" is not a reason, it is a TODO wearing one.
    _REVIEWED_UNGATED: dict[str, str] = {
        # A SECURITY action, and friction here is the harm: the owner reaches for this when a
        # device may be compromised, and it must take effect immediately. Confirming a lockout
        # protects nothing and delays containment.
        "/api/devices/{id}/revoke": "security containment — must not be slowed by a prompt",
        # The same containment for an integration's token: a leaked one must stop at once.
        "/api/devices/integrations/{id}/revoke": (
            "security containment — a leaked integration token must stop at once"
        ),
        # RECOVERY, not destruction: it archives the existing SEL log under a timestamp and starts
        # a fresh HMAC chain, and it exists to recover from a chain that is already broken.
        "/api/sel/rotate": "recovery path; archives rather than discards",
        # Notifications are transient by construction and re-emitted by their sources; clearing
        # them discards a view, not a record.
        "/api/notifications/clear": "transient view state, re-emitted by its sources",
        # The INVERSE of destructive: it un-suppresses a producer, restoring visibility that was
        # previously hidden. Nothing is lost by running it.
        "/api/feedback/producers/clear": "un-suppresses — restores state rather than removing it",
    }

    def _post_routes(self) -> list[tuple[str, str, Path]]:
        found = []
        pat = re.compile(r"""add_post\(\s*["']([^"']+)["']\s*,\s*([A-Za-z_][\w.]*)""")
        for py in source_index.package_files():
            for path, handler in pat.findall(source_index.text(py)):
                found.append((path, handler, py))
        return found

    def test_the_route_scan_actually_finds_routes(self):
        # FLOOR 1: if `add_post` registration ever changes shape, every assertion below would pass
        # by iterating nothing. This is the only check that can see a broken scanner.
        routes = self._post_routes()
        assert len(routes) > 100, f"route scan found only {len(routes)} POST routes — scanner broke"

    def test_the_destructive_filter_actually_matches(self):
        # FLOOR 2: and if the verb pattern breaks, the candidate set empties and the real
        # assertion passes vacuously. Two floors, because one cannot see the other's failure.
        candidates = [r for r in self._post_routes() if self._DESTRUCTIVE.search(r[0])]
        assert len(candidates) >= 3, f"destructive-verb filter matched {len(candidates)} routes"

    def test_every_destructive_post_route_gates_on_confirm(self):
        ungated = []
        for path, handler, py in self._post_routes():
            if not self._DESTRUCTIVE.search(path):
                continue
            if path in self._REVIEWED_UNGATED:
                continue
            src = source_index.text(py)
            name = handler.rsplit(".", 1)[-1]
            m = re.search(rf"^async def {re.escape(name)}\(.*?(?=^async def |\Z)", src, re.S | re.M)
            body = m.group(0) if m else ""
            if not any(tell in body for tell in self._GATE_TELLS):
                ungated.append(f"{path} -> {name} ({py.relative_to(SRC)})")
        assert not ungated, "destructive POST routes with no confirm gate:\n" + "\n".join(ungated)

    def test_the_gate_tell_is_the_predicate_not_a_bare_field_read(self):
        """FLOOR 3, added with issue 3000. This rail used to look for `get("confirm")` in the
        handler body — it detected the IMPLEMENTATION, not the property. Two consequences:

        * It read a handler as gated on any incidental read of the field, including one that
          coerced with `bool(...)` — which is the defect 3000 turned out to be: nine doors read
          `confirm` and accepted the string `"false"` as a yes, and this rail called all nine
          gated.
        * When the coercion was centralised into `safety_flags.confirm_granted`, the tell
          vanished from every handler and four correctly-gated routes read as ungated.

        So the tell is now the PREDICATE CALL, and `tests/test_confirm_gate_parity.py` bans the
        bare field read outright. The two rails are complementary: that one says nobody may read
        the field directly, this one says a destructive route must actually call the predicate.
        A bare `get("confirm")` must therefore NOT satisfy this rail, or the pair would let a
        route regress to its own coercion while both stayed green.
        """
        assert self._GATE_TELLS == ("confirm_granted(",)
        assert not any("get(" in tell for tell in self._GATE_TELLS)

    def test_the_exemption_list_cannot_rot(self):
        """An exemption for a route that no longer exists silently widens the rail."""
        live = {p for p, _, _ in self._post_routes()}
        stale = [p for p in self._REVIEWED_UNGATED if p not in live]
        assert not stale, f"_REVIEWED_UNGATED names routes that no longer exist: {stale}"
