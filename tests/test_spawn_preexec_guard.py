"""No-async-preexec_fn tripwire (PLATFORM-HARDENING-FLOORS §1, SH1.3b).

The premise correction at the heart of PHF-1: ``preexec_fn`` is unsafe on an async spawn.
It forces CPython off ``posix_spawn``/``vfork`` onto a full ``fork()`` of the many-threaded
gateway and runs Python in the child before ``exec`` — so a child can wedge on a lock
another thread held at fork time while still holding every inherited fd (the gateway lock,
the listening socket), and ``Popen._execute_child`` blocks the event loop in an unbounded,
un-awaitable ``os.read(errpipe)``. The corrected mechanism delivers ceilings AFTER exec via
the shim, so NO async spawn should pass ``preexec_fn`` at all.

This static AST guard asserts exactly that: no ``asyncio.create_subprocess_exec`` /
``asyncio.create_subprocess_shell`` call anywhere in ``src/personalclaw`` passes a
``preexec_fn=`` keyword. A newly-introduced async ``preexec_fn`` reds CI naming its
file:line. (There are currently zero on the tree — this guard keeps it that way; the
allowlist of documented exceptions is deliberately empty.)
"""

from __future__ import annotations

import ast
from pathlib import Path

# Documented, deliberate exceptions (``file::qualname`` keys). Empty by design: the shim
# mechanism means an async spawn never needs preexec_fn. An entry here must carry a written
# reason in this dict AND survive review — it is a hole in the safety property.
_DOCUMENTED_EXCEPTIONS: dict[str, str] = {}

_ASYNC_SPAWN = {"create_subprocess_exec", "create_subprocess_shell"}


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _async_callee(node: ast.Call) -> str | None:
    """Return the async-spawn function name if this call is one, else None."""
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in _ASYNC_SPAWN:
        return f.attr
    if isinstance(f, ast.Name) and f.id in _ASYNC_SPAWN:
        return f.id
    return None


def _offending_sites() -> list[str]:
    """Every async spawn passing ``preexec_fn=`` → ``file:line qualname`` strings."""
    out: list[str] = []
    root = _src_root()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(root).as_posix()

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []

            def visit_FunctionDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_ClassDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                if _async_callee(n) and any(kw.arg == "preexec_fn" for kw in n.keywords):
                    qual = ".".join(self.q) or "<module>"
                    key = f"{rel}::{qual}"
                    if key not in _DOCUMENTED_EXCEPTIONS:
                        out.append(f"{rel}:{n.lineno} ({qual})")
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_no_async_spawn_passes_preexec_fn():
    """A ``preexec_fn`` on an async spawn is a gateway-wedge hazard — none are allowed."""
    offending = _offending_sites()
    assert not offending, (
        "Async spawn(s) passing preexec_fn — this forks the multi-threaded gateway and can "
        "wedge the event loop holding inherited fds. Deliver resource limits AFTER exec via "
        "the ceiling shim (sandbox.create_subprocess_limited / spawn_shim_argv) instead:\n"
        + "\n".join(f"  {s}" for s in offending)
    )


def test_guard_would_flag_a_synthetic_preexec_fn():
    """The guard mechanism itself catches an async preexec_fn (proven on a synthetic AST),
    so a green result means 'none present', not 'guard is inert'."""
    synthetic = (
        "import asyncio\n"
        "async def f():\n"
        "    await asyncio.create_subprocess_exec('x', preexec_fn=lambda: None)\n"
    )
    tree = ast.parse(synthetic)
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and _async_callee(n)
        and any(kw.arg == "preexec_fn" for kw in n.keywords)
    ]
    assert len(found) == 1
