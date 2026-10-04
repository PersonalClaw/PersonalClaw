"""The owner's trust in an MCP server's read-only labels has one read, and it checks the digest.

The trust covers the tools the owner saw when she gave it, sealed to a digest of each one's
definition (`personalclaw.mcp_read_only_trust`). That holds only while every read that can turn a
label into a read compares the digest: a read of the record that skipped it, or a risk taken of a
label with a trust that was not that answer, would let a tool she never saw run unasked. So:

* the record is read by its own module and nothing else: no other module names its files or reaches
  into what the module keeps;
* a label becomes a risk only through ``risk_from_annotations``, which every caller hands the
  literal ``True`` (PersonalClaw's own tools, which declare themselves) except
  ``mcp_client.declared_risk``, which hands it ``believes(server, tool)``, the digest check, and
  takes no answer from its caller;
* ``holds_trust`` (whether the owner trusts a server at all) is read only for the words that say
  why a tool is not believed;
* the server-wide read this replaced is gone.

Read from the source, so a new read is red the day it is written. Each check is proved to catch the
thing it exists for on a snippet that does it.
"""

from __future__ import annotations

import ast
import inspect
from functools import lru_cache
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
OWNER = SRC / "mcp_read_only_trust.py"
GATE = SRC / "mcp_client.py"

#: What only the record's own module may name: its records, by name and by file, and what it keeps
#: of them.
_RECORD_NAMES = ("mcp_read_only", "mcp_tool_descriptions")
_PRIVATE_PREFIX = "_"
_RECORD_CONSTANTS = {"TRUST_RECORD", "SEEN_RECORD"}

#: Where whether the owner trusts a server at all may be read: the words of a refusal or a note.
_STANDING_READERS = {SRC / "guardrails" / "policy.py"}


@lru_cache(maxsize=None)
def _tree() -> tuple[tuple[Path, str, ast.Module], ...]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        out.append((path, text, ast.parse(text)))
    return tuple(out)


def _record_reads(path: Path, tree: ast.Module) -> list[str]:
    """Where *tree* names the record or reaches into its module's own state."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = node.value.rsplit("/", 1)[-1].removesuffix(".json")
            if named in _RECORD_NAMES:
                found.append(f"{path.name}:{node.lineno} names the record: {node.value!r}")
        elif isinstance(node, ast.ImportFrom) and node.module == "personalclaw.mcp_read_only_trust":
            for alias in node.names:
                if alias.name.startswith(_PRIVATE_PREFIX) or alias.name in _RECORD_CONSTANTS:
                    found.append(f"{path.name}:{node.lineno} imports {alias.name}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "mcp_read_only_trust"
            and (node.attr.startswith(_PRIVATE_PREFIX) or node.attr in _RECORD_CONSTANTS)
        ):
            found.append(f"{path.name}:{node.lineno} reaches mcp_read_only_trust.{node.attr}")
    return found


def _called(node: ast.Call) -> str:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """Each call's id → the name of the function it is written in."""
    inside: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    inside[id(node)] = fn.name
    return inside


def _label_reads(path: Path, tree: ast.Module) -> tuple[list[str], int]:
    """Where *tree* takes a risk of a label with a trust that is not the literal ``True`` and is not
    the gate's digest check, and how many calls it read."""
    found, seen = [], 0
    inside = _enclosing_functions(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) == "risk_from_annotations"):
            continue
        seen += 1
        trusted = next((k.value for k in node.keywords if k.arg == "trusted"), None)
        if isinstance(trusted, ast.Constant) and trusted.value is True:
            continue
        if (
            path == GATE
            and inside.get(id(node)) == "declared_risk"
            and isinstance(trusted, ast.Call)
            and _called(trusted) == "believes"
        ):
            continue
        found.append(
            f"{path.name}:{node.lineno} takes a label's risk with trusted="
            f"{ast.unparse(trusted) if trusted is not None else '(missing)'}"
        )
    return found, seen


def _standing_reads(path: Path, tree: ast.Module) -> list[str]:
    """Where *tree* asks whether the owner trusts a server at all, outside the words that may."""
    if path == OWNER or path in _STANDING_READERS:
        return []
    return [
        f"{path.name}:{node.lineno} reads holds_trust"
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "holds_trust")
        or (isinstance(node, ast.Attribute) and node.attr == "holds_trust")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "holds_trust" for alias in node.names)
        )
    ]


# ── the tree ────────────────────────────────────────────────────────────────────────────────────


def test_only_its_own_module_reads_the_record():
    found = [
        hit for path, _text, tree in _tree() if path != OWNER for hit in _record_reads(path, tree)
    ]
    assert not found, (
        "the owner's trust is read only through `mcp_read_only_trust`, whose `believes` checks "
        "the digest:\n  " + "\n  ".join(found)
    )


def test_a_label_becomes_a_read_only_through_the_digest_check():
    found: list[str] = []
    calls = 0
    for path, _text, tree in _tree():
        hits, seen = _label_reads(path, tree)
        found += hits
        calls += seen
    # Vacuity: the gate's own call and PersonalClaw's own tools' calls are in the tree.
    assert calls >= 4, f"only {calls} calls to risk_from_annotations were read — parser drift?"
    assert not found, (
        "a tool's read-only label is taken only from `mcp_client.declared_risk`, which asks "
        "`mcp_read_only_trust.believes`; every other caller is PersonalClaw's own tool and passes "
        "trusted=True:\n  " + "\n  ".join(found)
    )


def test_the_gate_takes_no_answer_from_its_caller():
    from personalclaw.mcp_client import declared_risk

    assert list(inspect.signature(declared_risk).parameters) == ["server", "tool"]


def test_whether_a_server_is_trusted_at_all_is_read_only_for_words():
    found = [hit for path, _text, tree in _tree() for hit in _standing_reads(path, tree)]
    assert not found, (
        "`holds_trust` says whether the owner trusts a server at all, which is never a tool's "
        "answer; read it only where a refusal or a note is worded:\n  " + "\n  ".join(found)
    )
    used = [path for path, text, _tree_ in _tree() if "holds_trust" in text]
    assert set(used) >= _STANDING_READERS, "the allowed reader no longer reads it — prune the list"


def test_the_server_wide_read_it_replaced_is_gone():
    # The retired key is named where it is dropped and where the retirement is explained.
    retired_at = {SRC / "config" / "validation.py", OWNER}
    stale = [
        f"{path.relative_to(SRC)}"
        for path, text, _tree_ in _tree()
        if "read_only_labels_trusted" in text
        or ("mcp_read_only_servers" in text and path not in retired_at)
    ]
    assert not stale, stale


# ── each check catches what it exists for ───────────────────────────────────────────────────────


def _snippet(text: str) -> ast.Module:
    return ast.parse(text)


def test_the_record_check_catches_a_read_beside_the_module():
    tree = _snippet(
        "import json\n"
        "from personalclaw.owner_grants import grants_dir\n"
        "from personalclaw.mcp_read_only_trust import _sealed_tools\n"
        "from personalclaw import mcp_read_only_trust\n"
        "data = json.loads((grants_dir() / 'mcp_read_only.json').read_text())\n"
        "seen = mcp_read_only_trust._servers(mcp_read_only_trust.SEEN_RECORD)\n"
    )
    found = _record_reads(SRC / "elsewhere.py", tree)
    assert len(found) == 4, found


def test_the_label_check_catches_a_trust_that_skips_the_digest():
    tree = _snippet(
        "from personalclaw.mcp_read_only_trust import holds_trust\n"
        "def declared_risk(server, tool):\n"
        "    return risk_from_annotations(tool.annotations, trusted=holds_trust(server))\n"
        "def own(tool):\n"
        "    return risk_from_annotations(tool.annotations, trusted=True)\n"
        "def lenient(tool, trusted):\n"
        "    return risk_from_annotations(tool.annotations, trusted=trusted)\n"
    )
    # Named as the gate's own file, so only the digest check itself is let through there.
    found, seen = _label_reads(GATE, tree)
    assert seen == 3
    assert len(found) == 2, found
    assert _standing_reads(SRC / "elsewhere.py", tree)
