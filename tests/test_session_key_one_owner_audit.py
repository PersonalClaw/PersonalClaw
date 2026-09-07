"""ONE OWNER for a session's on-disk identity — a derived rail, not a hand-list.

A chat session's conversation log lives under one of two key shapes: the bare key (a
channel-provider thread persists under its own key, exactly as the channel app wrote it)
or the ``dashboard:`` form. Deciding WHICH is a question about the disk, and
``chat_utils`` owns it: :func:`resolve_history_key` asks the log which candidate key has
metadata, and :func:`persisted_history_key` wraps that with the write-side fallback.

Two shapes of call site answered the question themselves instead, and both were live
defects:

* ``_history_key_for(x)`` PREFIXES — it never looks at the disk. Eight sites passed its
  answer straight to a ``conversation_log`` read or write, so a session whose file is not
  under that shape got read as empty and written to a second, empty file beside the real
  transcript. ``api_chat_session_delete`` was one of them, which quietly turned a hard
  delete into an orphaned-file leak.
* a raw session NAME off the wire (``request.match_info["session"]``, or a body field
  defaulting to it) is not a key at all. ``api_chat_session_resume`` read four disk facts
  with it and answered ``200`` with ``messages: []`` and ``meta_closed`` still true for
  every non-resident dashboard session — while the canonical form sat computed four lines
  above, used only for an in-memory comparison.

So this rail is a taint analysis for exactly those two edges. It asserts a NEGATIVE (no
site reaches disk from either source), which is why the vacuity floor below matters more
than the assertion: a taint rule that resolves nothing is green for free.

The site set is DERIVED, never listed. The keyed ``ConversationLog`` methods come from
parsing ``history.py`` (every method whose first parameter is ``key``), and the call sites
come from parsing every module under ``dashboard/``. Add a keyed method to
``ConversationLog``, or a new handler, and both are picked up with no edit here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
DASHBOARD = SRC / "dashboard"
HISTORY = SRC / "history.py"

#: The owner module. It is the ONE place allowed to compose a key from a shape, because
#: composing candidate shapes is precisely its job.
OWNER_MODULE = DASHBOARD / "chat_utils.py"

#: The functions that answer "which key is this session's file under?" from the DISK.
OWNER_CALLS = frozenset({"resolve_history_key", "persisted_history_key"})

#: The prefix-only helper. Correct for normalising two in-memory keys into one space;
#: never correct as the key handed to a ``conversation_log`` read or write.
PREFIX_ONLY = "_history_key_for"

#: ``str`` methods that return a string derived from their receiver, so taint flows
#: through them (``name.removeprefix("dashboard_")`` is still the wire name).
_STR_METHODS = frozenset(
    {
        "removeprefix",
        "removesuffix",
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "format",
        "join",
        "replace",
    }
)


# ── deriving the keyed method set ────────────────────────────────────────────────


def keyed_conversation_log_methods() -> frozenset[str]:
    """Every ``ConversationLog`` method whose first parameter is a session ``key``."""
    tree = ast.parse(HISTORY.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "ConversationLog"):
            continue
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [a.arg for a in fn.args.args]
            if len(args) >= 2 and args[0] == "self" and args[1] == "key":
                out.add(fn.name)
    return frozenset(out)


# ── the taint analysis ───────────────────────────────────────────────────────────


class _KeyFlow(ast.NodeVisitor):
    """Resolve, per function, where each keyed ``conversation_log`` key came from.

    Intra-procedural and deliberately shallow. Taint follows string plumbing only —
    assignment, ``or``, ``str`` method calls, f-strings, comprehension targets — and
    NEVER through attribute access. ``session.key`` is not the wire name that fetched
    the session; it is the session's own identity, already namespaced-or-bare. Following
    it would flag every handler that ever reads ``match_info["session"]``, which is the
    over-approximation that makes a rail like this get deleted instead of fixed.
    """

    def __init__(self, keyed: frozenset[str], module: Path) -> None:
        self.keyed = keyed
        self.module = module
        self.is_owner_module = module == OWNER_MODULE
        #: (lineno, method, key source, classification)
        self.sites: list[tuple[int, str, str, str]] = []
        self._scope: list[dict[str, list[ast.expr]]] = [{}]

    # -- scope handling ----------------------------------------------------------

    def _enter(self) -> None:
        self._scope.append({})

    def _exit(self) -> None:
        self._scope.pop()

    def _bind(self, name: str, value: ast.expr) -> None:
        self._scope[-1].setdefault(name, []).append(value)

    def _defs(self, name: str) -> list[ast.expr]:
        for frame in reversed(self._scope):
            if name in frame:
                return frame[name]
        return []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._enter()
        self.generic_visit(node)
        self._exit()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._bind(target.id, node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, node.iter)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, node.iter)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.generic_visit(node)

    visit_SetComp = visit_ListComp  # type: ignore[assignment]
    visit_GeneratorExp = visit_ListComp  # type: ignore[assignment]

    # -- provenance --------------------------------------------------------------

    def _reaches(
        self, expr: ast.expr | None, want: str, seen: frozenset[int] = frozenset()
    ) -> bool:
        """Whether *expr* transitively reaches a source of kind *want*.

        *want* is ``"owner"`` (a disk-resolving call), ``"prefix"`` (``_history_key_for``)
        or ``"wire_session"`` (``request.match_info["session"]``).
        """
        if expr is None or id(expr) in seen:
            return False
        seen = seen | {id(expr)}

        if isinstance(expr, ast.Call):
            fname = ""
            if isinstance(expr.func, ast.Name):
                fname = expr.func.id
            elif isinstance(expr.func, ast.Attribute):
                fname = expr.func.attr
            if want == "owner" and fname in OWNER_CALLS:
                return True
            if want == "prefix" and fname == PREFIX_ONLY:
                return True
            # Argument taint is NOT generic. `state._sessions.get(name)` takes the wire
            # name and returns a session OBJECT — propagating through it would taint
            # `session.key` and every attribute reachable from it, i.e. every handler
            # that ever read `match_info["session"]`. So only genuine string plumbing
            # propagates: a mapping `.get`'s DEFAULT (`body.get("key", name)`) and the
            # receiver of a `str` method (`name.removeprefix("dashboard_")`).
            if fname == "get":
                return any(self._reaches(a, want, seen) for a in expr.args[1:])
            if fname in _STR_METHODS and isinstance(expr.func, ast.Attribute):
                return self._reaches(expr.func.value, want, seen)
            if fname in OWNER_CALLS or fname == PREFIX_ONLY:
                # A resolver called with a tainted name still yields a resolved key for
                # the OTHER clauses' purposes; only its own clause classifies it.
                return False
            return False

        if isinstance(expr, ast.Subscript):
            if want == "wire_session":
                # request.match_info["session"] — the raw wire session NAME.
                sl = expr.slice
                if (
                    isinstance(expr.value, ast.Attribute)
                    and expr.value.attr == "match_info"
                    and isinstance(sl, ast.Constant)
                    and sl.value == "session"
                ):
                    return True
            return self._reaches(expr.value, want, seen)

        if isinstance(expr, ast.BoolOp):
            return any(self._reaches(v, want, seen) for v in expr.values)
        if isinstance(expr, ast.IfExp):
            return self._reaches(expr.body, want, seen) or self._reaches(expr.orelse, want, seen)
        if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
            return any(self._reaches(e, want, seen) for e in expr.elts)
        if isinstance(expr, ast.JoinedStr):
            return any(self._reaches(v, want, seen) for v in expr.values)
        if isinstance(expr, ast.FormattedValue):
            return self._reaches(expr.value, want, seen)
        if isinstance(expr, ast.Name):
            return any(self._reaches(d, want, seen) for d in self._defs(expr.id))
        # ast.Attribute is deliberately NOT traversed — see the class docstring.
        return False

    # -- the call sites ----------------------------------------------------------

    def _is_conversation_log(self, expr: ast.expr, seen: frozenset[int] = frozenset()) -> bool:
        """Whether *expr* denotes the conversation log, through local aliases.

        ``session_key_exists`` calls it as ``log.has_log(...)`` after ``log =
        state.conversation_log``, so matching only on the spelled-out receiver would let
        every aliased call site out of the rail — including that one, which is where the
        both-forms probe lives.
        """
        if id(expr) in seen:
            return False
        seen = seen | {id(expr)}
        if "conversation_log" in ast.unparse(expr):
            return True
        if isinstance(expr, ast.Name):
            return any(self._is_conversation_log(d, seen) for d in self._defs(expr.id))
        if isinstance(expr, ast.BoolOp):
            return any(self._is_conversation_log(v, seen) for v in expr.values)
        return False

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.generic_visit(node)
        if not (isinstance(node.func, ast.Attribute) and node.func.attr in self.keyed):
            return
        if not self._is_conversation_log(node.func.value):
            return
        if not node.args:
            return
        key = node.args[0]
        src = ast.unparse(key)
        if self._reaches(key, "owner"):
            kind = "owner"
        elif self._reaches(key, "prefix") and not self.is_owner_module:
            kind = "BYPASS_PREFIX_ONLY"
        elif self._reaches(key, "wire_session"):
            kind = "BYPASS_RAW_WIRE_NAME"
        else:
            kind = "other"
        self.sites.append((node.lineno, node.func.attr, src, kind))


def audit_source(
    source: str, *, path: Path, keyed: frozenset[str]
) -> list[tuple[int, str, str, str]]:
    """Classify every keyed ``conversation_log`` call site in *source*."""
    flow = _KeyFlow(keyed, path)
    flow.visit(ast.parse(source))
    return flow.sites


def audit_tree() -> list[tuple[Path, int, str, str, str]]:
    """Classify every keyed ``conversation_log`` call site under ``dashboard/``."""
    keyed = keyed_conversation_log_methods()
    out: list[tuple[Path, int, str, str, str]] = []
    for path in sorted(DASHBOARD.rglob("*.py")):
        for lineno, method, src, kind in audit_source(
            path.read_text(encoding="utf-8"), path=path, keyed=keyed
        ):
            out.append((path, lineno, method, src, kind))
    return out


# ── the rule ─────────────────────────────────────────────────────────────────────


def test_no_keyed_conversation_log_call_bypasses_the_one_owner():
    """No dashboard disk access may key off a hand-formed prefix or a raw wire name."""
    bypasses = [row for row in audit_tree() if row[4].startswith("BYPASS")]
    assert not bypasses, "keyed conversation_log call sites bypassing the one owner:\n" + "\n".join(
        f"  {p.relative_to(SRC.parent)}:{ln} {m}({src})  → {kind}"
        for p, ln, m, src, kind in bypasses
    )


# ── vacuity floor ────────────────────────────────────────────────────────────────
#
# Everything above asserts an ABSENCE. These four tests are what stop that absence from
# being an artefact of an analyzer that resolves nothing: the derived method set is
# non-trivial, the scan population is large, the analyzer demonstrably RECOGNISES the
# owner on real code, and the two bypass detectors demonstrably FIRE on planted source
# while leaving the owner-routed form of the same code alone.


def test_keyed_method_set_is_derived_and_populated():
    keyed = keyed_conversation_log_methods()
    assert len(keyed) >= 15, f"derived too few keyed ConversationLog methods: {sorted(keyed)}"
    # Spot-check the ones the two symptoms actually went through.
    for name in ("get_metadata", "read_messages", "read_messages_chained", "_path", "has_log"):
        assert name in keyed, f"{name} must be derived as key-taking"


def test_scan_population_is_non_trivial_and_owner_is_recognised():
    rows = audit_tree()
    assert len(rows) >= 25, f"analyzer found only {len(rows)} keyed call sites — it stopped seeing"
    owner_routed = [r for r in rows if r[4] == "owner"]
    assert len(owner_routed) >= 10, (
        f"analyzer resolved the owner at only {len(owner_routed)} sites — a taint rule that "
        "cannot recognise the owner is green for free"
    )
    # The prefix helper must still be a live name in the tree, or clause one is matching
    # something that no longer exists.
    assert any(
        PREFIX_ONLY in p.read_text(encoding="utf-8") for p in DASHBOARD.rglob("*.py")
    ), f"{PREFIX_ONLY} is gone — clause one now matches nothing"


#: Isolates clause ONE: a hand-formed prefix reaching disk. Deliberately takes the name
#: as a plain argument so the wire clause cannot also fire and mask it.
_PLANTED_PREFIX_BYPASS = """
from personalclaw.dashboard.chat_utils import _history_key_for

def persist_thing(state, session_name):
    history_key = _history_key_for(session_name)
    return state.conversation_log.get_metadata(history_key)
"""

_PLANTED_WIRE_BYPASS = """
async def api_thing(request, state):
    name = request.match_info["session"]
    body = await request.json()
    history_key = body.get("key", name)
    return state.conversation_log.read_messages_chained(history_key)
"""

_PLANTED_OWNER_ROUTED = """
from personalclaw.dashboard.chat_utils import persisted_history_key

async def api_thing(request, state):
    name = request.match_info["session"]
    body = await request.json()
    history_key = body.get("key", name)
    resolved = persisted_history_key(state.conversation_log, history_key)
    return state.conversation_log.read_messages_chained(resolved)
"""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (_PLANTED_PREFIX_BYPASS, "BYPASS_PREFIX_ONLY"),
        (_PLANTED_WIRE_BYPASS, "BYPASS_RAW_WIRE_NAME"),
    ],
)
def test_detector_fires_on_planted_bypass(source, expected):
    sites = audit_source(
        source, path=DASHBOARD / "planted.py", keyed=keyed_conversation_log_methods()
    )
    assert sites, "analyzer found no call site in the planted source at all"
    assert [s[3] for s in sites] == [expected], sites


def test_detector_is_quiet_on_the_owner_routed_form():
    sites = audit_source(
        _PLANTED_OWNER_ROUTED, path=DASHBOARD / "planted.py", keyed=keyed_conversation_log_methods()
    )
    assert [s[3] for s in sites] == ["owner"], sites


def test_owner_module_may_compose_the_prefix():
    """``chat_utils`` is the one place allowed to build a key from a shape."""
    sites = audit_source(
        _PLANTED_PREFIX_BYPASS, path=OWNER_MODULE, keyed=keyed_conversation_log_methods()
    )
    assert [s[3] for s in sites] == ["other"], sites
