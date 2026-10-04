"""Nothing PersonalClaw ships runs PersonalClaw through whatever Python is on ``PATH``.

🔴 Before: the bundled ``optimize-harness`` template ran its three steps as
``python3 -m personalclaw.evals.optimize <step>``. That works only where the ``python3`` a step's
shell finds on ``PATH`` is the interpreter PersonalClaw is installed into: an activated checkout,
or the container image, whose environment leads its ``PATH``. A ``uv tool`` install (the documented
default) keeps PersonalClaw in an environment of its own, and the desktop app is a frozen bundle
with no interpreter at all, and in both every run of the template failed at its first step.

A step runs PersonalClaw as ``personalclaw <command>``, which a bash step resolves to this install's
own program (``tests/test_a_step_runs_this_installs_personalclaw.py``). This rail holds every file
the package ships beside its code to that: the workflow templates and their shared blocks, the
packs' templates and triggers, the skills, prompts and app manifests. A ``python``, ``python3`` or
``python3.N``, by any path, handed a module of the package (``-m personalclaw…``), code that names
it (``-c``) or a script inside it is a red, by file and by place.

The scan reads a JSON file's strings one by one, so a quote escaped in the file is the quote the
step's shell reads, and every other text file line by line. Its positive control runs it over the
shipped template with its three steps put back as they shipped, at the depths they sat (one at
the top, two in the search loop's body), and it must find all three.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The template whose steps this rail was written for, relative to the package.
_TEMPLATE = "workflows/bundled/optimize-harness/workflow.json"

#: Its three steps as the template shipped them, by node id.
SHIPPED_BEFORE = {
    "preflight": "python3 -m personalclaw.evals.optimize preflight </dev/null",
    "scope_check": "python3 -m personalclaw.evals.optimize scope-check </dev/null",
    "adjudicate": "python3 -m personalclaw.evals.optimize adjudicate </dev/null",
}

#: Read as JSON, string by string.
_JSON = frozenset({".json"})
#: Read as one JSON document per line.
_JSON_LINES = frozenset({".jsonl"})
#: Read as text, line by line.
_TEXT = frozenset({".md", ".yaml", ".yml", ".txt", ".toml", ".sh", ".js"})

#: A Python interpreter, by any path and version spelling, running PersonalClaw: a module of the
#: package, code that names it, or a script inside it. Its own flags (``-u``, ``-I``) may come
#: first.
BARE_INTERPRETER = re.compile(
    r"(?<![\w.-])(?:[\w.~/-]*/)?python(?:3(?:\.\d+)?)?(?![\w.-])"
    r"(?:\s+-[A-Za-z]+)*\s+"
    r"(?:-m\s*personalclaw\b"
    r"|-c\s+(?:'[^']*\bpersonalclaw\b[^']*'|\"[^\"]*\bpersonalclaw\b[^\"]*\")"
    r"|\S*\bpersonalclaw/\S*\.py\b)"
)


def _strings(value: Any, place: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a parsed JSON document, with where it sits (``root.children[0].config…``)."""
    if isinstance(value, str):
        yield place, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{place}.{key}" if place else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{place}[{index}]")


def _texts(path: Path) -> Iterator[tuple[str, str]]:
    """``(place, text)`` for everything *path* says, or nothing for a file that is not text."""
    if path.suffix in _JSON:
        yield from _strings(json.loads(path.read_text(encoding="utf-8")))
    elif path.suffix in _JSON_LINES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                for place, text in _strings(json.loads(line)):
                    yield f"line {number}: {place}", text
    elif path.suffix in _TEXT:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield f"line {number}", line


def scan(root: Path) -> tuple[list[tuple[str, str, str]], list[str]]:
    """``(file, place, what runs it)`` for each bare-interpreter run of PersonalClaw in a file
    under *root* that is not Python source, and every file read."""
    found: list[tuple[str, str, str]] = []
    read: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        rel = path.relative_to(root).as_posix()
        texts = list(_texts(path))
        if path.suffix in _JSON | _JSON_LINES | _TEXT:
            read.append(rel)
        for place, text in texts:
            found.extend((rel, place, match.group(0)) for match in BARE_INTERPRETER.finditer(text))
    return found, read


def _nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("children") or []:
        yield from _nodes(child)
    for key in ("body", "then", "otherwise", "default"):
        if isinstance(node.get(key), dict):
            yield from _nodes(node[key])
    for case in (node.get("cases") or {}).values():
        yield from _nodes(case)


# ── the rail ─────────────────────────────────────────────────────────────────


def test_nothing_shipped_runs_personalclaw_through_a_bare_interpreter() -> None:
    """🔴 Red before: the three ``optimize-harness`` steps."""
    found, read = scan(_PACKAGE)
    assert found == [], (
        "a shipped step runs PersonalClaw through whatever Python is on PATH, which in a uv tool "
        "install or the desktop app has no PersonalClaw. Run it as `personalclaw <command>` (a "
        f"bash step resolves that to this install's own program): {found}"
    )
    # Anti-vacuity: the walk read the libraries a step comes from, not an empty folder.
    assert _TEMPLATE in read
    assert (
        sum(r.startswith("workflows/bundled/") and r.endswith("/workflow.json") for r in read) >= 30
    )
    assert any(r.startswith("workflows/bundled/shared/") for r in read), "no shared block was read"
    assert any(r.startswith("packs/bundled/") and "/templates/" in r for r in read)
    assert any(r.startswith("skills/bundled/") for r in read), "no bundled skill was read"


def test_the_scan_finds_the_three_steps_the_template_shipped_with(tmp_path: Path) -> None:
    """POSITIVE CONTROL: the same scan, over the shipped template with its steps as they shipped,
    finds each of them where it sat."""
    spec = json.loads((_PACKAGE / _TEMPLATE).read_text(encoding="utf-8"))
    put_back = set()
    for node in _nodes(spec["root"]):
        if node.get("id") in SHIPPED_BEFORE:
            node["config"]["with"]["command"] = SHIPPED_BEFORE[node["id"]]
            put_back.add(node["id"])
    assert put_back == set(
        SHIPPED_BEFORE
    ), "the template no longer has the steps this control names"
    target = tmp_path / _TEMPLATE
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    found, read = scan(tmp_path)

    assert read == [_TEMPLATE]
    assert found == [
        (_TEMPLATE, "root.children[0].config.with.command", "python3 -m personalclaw"),
        (
            _TEMPLATE,
            "root.children[1].body.children[1].config.with.command",
            "python3 -m personalclaw",
        ),
        (
            _TEMPLATE,
            "root.children[1].body.children[3].config.with.command",
            "python3 -m personalclaw",
        ),
    ]


# ── what the scan reads as a bare interpreter ────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "python3 -m personalclaw.evals.optimize preflight </dev/null",
        "python -m personalclaw doctor",
        "python3.13 -m personalclaw gateway --port 10000",
        "/usr/bin/env python3 -m personalclaw.evals.child spec.json",
        "/opt/homebrew/bin/python3 -u -m personalclaw status",
        ".venv/bin/python -mpersonalclaw.evals.optimize adjudicate",
        "cd \"$PC_CWD\" && python3 -c 'import personalclaw; print(personalclaw.__version__)'",
        'python3 -c "from personalclaw.evals import optimize"',
        "python3 ~/src/personalclaw/evals/optimize.py preflight",
        "uv run python -m personalclaw.evals.optimize scope-check",
    ],
)
def test_the_scan_reads_every_spelling_of_an_interpreter_running_personalclaw(text: str) -> None:
    assert BARE_INTERPRETER.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        "personalclaw optimize-harness preflight </dev/null",
        "personalclaw doctor && python3 -m http.server 8000 --bind 127.0.0.1",
        "python -m pytest -n 0 --no-cov tests/test_x.py",
        "python3 scripts/sign_app.py gen-key --signer PersonalClaw --out-dir ~/keys",
        "python3 -c 'import json; print(json.dumps({}))'",
        "cpython3 -m personalclaw",
        "make test",
    ],
)
def test_the_scan_leaves_what_runs_no_personalclaw_through_an_interpreter_alone(text: str) -> None:
    assert not BARE_INTERPRETER.search(text), text
