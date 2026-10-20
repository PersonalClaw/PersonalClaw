"""A scanner rule that names one language's API reads only files in that language.

``python_exec`` matched ``exec(`` in every script the scanner reads, JavaScript included, so a
UI bundle's ``RegExp.prototype.exec`` read as "This code runs an external program on your
machine." on the install consent (apps #137 rewrote two pages to ``String.match`` to get a clean
scan). The rule now reads Python, and JavaScript's own way to start a program, the
``child_process`` module, has a rule of its own, so a Node file that does run programs still says
so. A file with no suffix, and a bare blob with no file at all, name no language, so every
language's rule reads them.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.supply_chain import SkillScanner, rule_gloss, scan_dir

_REGEX_EXEC = "const m = /(\\d+)px/.exec(style);\nexport const width = m ? Number(m[1]) : 0;\n"
_RUNS_A_PROGRAM = "This code runs an external program on your machine."


def _findings(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    root = tmp_path / "staged"
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {(f.rule, f.path) for f in scan_dir(root).findings}


def test_a_javascript_regex_exec_is_not_a_program(tmp_path: Path) -> None:
    """🔴 Red on main: each of these read as ``python_exec``."""
    found = _findings(
        tmp_path,
        {
            "ui/bundle/index.mjs": _REGEX_EXEC,
            "server.js": "const id = RE.exec(line)[1];\n",
            "lib/parse.cjs": "while ((hit = pattern.exec(text)) !== null) out.push(hit);\n",
        },
    )
    assert found == set(), found


def test_python_that_runs_a_program_still_says_so(tmp_path: Path) -> None:
    """The control, green on main too: Python keeps the rule, and so does a script whose name
    says no language (a ``scripts/setup`` run by its ``#!/usr/bin/env python3`` line)."""
    found = _findings(
        tmp_path,
        {
            "provider.py": 'import subprocess\nsubprocess.run(["gh", "auth", "status"])\n',
            "scripts/setup": '#!/usr/bin/env python3\nimport os\nos.system("make")\n',
        },
    )
    assert ("python_exec", "provider.py") in found
    assert ("python_exec", "scripts/setup") in found


def test_node_that_starts_a_program_says_so(tmp_path: Path) -> None:
    """🔴 Red on main: ``spawn`` was never flagged, and ``exec`` only as ``python_exec``."""
    found = _findings(
        tmp_path,
        {
            "server.js": 'const { spawn } = require("node:child_process");\nspawn("ffmpeg");\n',
            "worker.mjs": 'import cp from "child_process";\ncp.exec("ls");\n',
        },
    )
    assert found == {("node_exec", "server.js"), ("node_exec", "worker.mjs")}, found
    assert rule_gloss("node_exec") == _RUNS_A_PROGRAM


def test_python_is_not_read_as_javascript(tmp_path: Path) -> None:
    """``node_exec`` reads JavaScript: Python that only NAMES the module runs nothing with it."""
    found = _findings(tmp_path, {"codegen.py": 'SNIPPET = "require(\\"child_process\\")"\n'})
    assert found == set(), found


def test_a_bare_blob_is_read_by_every_language_s_rule() -> None:
    """A blob with no file names no language, so no rule is skipped for it."""
    scanner = SkillScanner()
    python = scanner.scan_text('import os\nos.system("make")\n', surface="script")
    node = scanner.scan_text('require("child_process").spawn("make");\n', surface="script")
    assert "python_exec" in {f.rule for f in python.findings}
    assert "node_exec" in {f.rule for f in node.findings}
