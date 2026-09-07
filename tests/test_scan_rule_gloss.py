"""The scanner-rule gloss has ONE home, and it survives packaging (#2633).

``personalclaw skills install`` refused a skill and printed one bare row per finding —
``- [high] python_exec in scripts/fetch.py: subprocess.run([...])`` — then stopped at
eight with nothing said. Two defects on the one output whose entire job is to justify a
refusal: the row names the scanner's pattern but never says what the skill could DO, and a
ninth finding vanished silently.

The gloss already existed, in TypeScript (``web/src/lib/scanFindings.ts``), and the CLI is
Python. Duplicating the map in Python is the defect #2535 removed — two literals for one
fact, which drift — and reading the TS at runtime cannot work, because the wheel ships
``web/dist``, not ``web/src``. So the map moved to a language-neutral
``personalclaw/scan_rule_gloss.json``: the CLI reads it at RUNTIME, the Vite build imports
it at BUILD time, one literal for every consumer.

🔴 THE RISK THIS FILE EXISTS FOR. A new non-``.py`` file is NOT shipped in a wheel by
default. If the packaging declaration is ever dropped, or the file arrives empty, the gloss
disappears — and disappearing quietly is precisely the failure mode the map was written to
prevent ("the failure mode here is silence, and silence is what the user reads as 'probably
fine'"). So a repo-tree check is NOT evidence here: ``test_the_gloss_ships_inside_a_built_wheel``
builds a real wheel and reads the bytes back out of the artifact, and
``test_a_reachable_but_empty_map_is_a_hard_error`` covers the other direction, where the
file is present but its contents are gone.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

import personalclaw.supply_chain as sc
from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillsMarketplace,
    SkillsRegistry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GLOSS_JSON = Path(sc.__file__).with_name("scan_rule_gloss.json")
_SCAN_FINDINGS_TS = _REPO_ROOT / "web" / "src" / "lib" / "scanFindings.ts"
#: Where the file lands inside a wheel — the path the CLI resolves at runtime.
_WHEEL_MEMBER = "personalclaw/scan_rule_gloss.json"


# ── One home, read by every consumer ────────────────────────────────────────


def test_the_map_is_data_not_a_python_literal() -> None:
    """The canonical map is the JSON file, and the loader is the only way in."""
    assert _GLOSS_JSON.is_file(), f"{_GLOSS_JSON} is the canonical gloss home"
    gloss = sc.load_scan_rule_gloss()
    assert gloss, "the map must not be reachable-but-empty"
    raw = json.loads(_GLOSS_JSON.read_text(encoding="utf-8"))
    assert gloss == {k: v for k, v in raw.items() if not k.startswith("_")}


def test_an_unglossed_rule_reads_as_empty_never_as_its_own_name() -> None:
    """``rule_gloss`` mirrors ``ruleGloss`` in TS: unknown → ``''``. Echoing the rule name
    back as though it explained something is the defect, not the fix."""
    assert sc.rule_gloss("no_such_rule_exists") == ""
    assert sc.rule_gloss("") == ""
    assert sc.rule_gloss("python_exec") == "This code runs an external program on your machine."


def test_the_frontend_reads_the_same_file_rather_than_a_second_literal() -> None:
    """The whole point of the move. If ``scanFindings.ts`` ever re-declares the sentences
    inline, there are two copies of one fact again and the CLI drifts from the dialog."""
    src = _SCAN_FINDINGS_TS.read_text(encoding="utf-8")
    assert re.search(
        r"import \w+ from '(\.\./)+src/personalclaw/scan_rule_gloss\.json'", src
    ), "web/src/lib/scanFindings.ts must import the canonical gloss JSON"
    # Sampled from all three bands, so a partial re-inlining is caught too.
    for rule in ("python_exec", "destructive_root", "injection_ignore"):
        sentence = sc.load_scan_rule_gloss()[rule]
        assert (
            sentence not in src
        ), f"{rule}'s sentence is inlined in scanFindings.ts — the map has two copies again"


def test_the_cli_cap_matches_the_frontend_cap() -> None:
    """Two surfaces, two literal ``8``s — a cap is a per-surface layout choice, so it did
    NOT move into the shared data file. That makes it exactly the kind of number that
    drifts unnoticed, so it is pinned here instead: a user comparing the CLI's list against
    the dialog's must not be shown different amounts of the same refusal."""
    from personalclaw.cli import _SKILL_FINDINGS_SHOWN

    ts = _SCAN_FINDINGS_TS.read_text(encoding="utf-8")
    m = re.search(r"export const SCAN_FINDINGS_SHOWN = (\d+)", ts)
    assert m, "SCAN_FINDINGS_SHOWN not found in web/src/lib/scanFindings.ts"
    assert _SKILL_FINDINGS_SHOWN == int(m.group(1)), (
        f"the CLI shows {_SKILL_FINDINGS_SHOWN} findings but the consent surfaces show "
        f"{m.group(1)} — one refusal, two different amounts of it"
    )


# ── The map cannot go silently missing ──────────────────────────────────────


def test_a_reachable_but_empty_map_is_a_hard_error(tmp_path, monkeypatch) -> None:
    """ "File present, contents lost" must not pass. An empty map is indistinguishable at
    the call site from "no rule is glossed", which is the silence this map removes — so the
    loader raises instead of handing back ``{}``."""
    for body in ("{}", '{"_comment": ["rationale only, no rules"]}'):
        empty = tmp_path / "scan_rule_gloss.json"
        empty.write_text(body, encoding="utf-8")
        monkeypatch.setattr(sc, "SCAN_RULE_GLOSS_PATH", empty)
        sc.load_scan_rule_gloss.cache_clear()
        with pytest.raises(RuntimeError, match="no rule glosses"):
            sc.load_scan_rule_gloss()
    monkeypatch.setattr(sc, "SCAN_RULE_GLOSS_PATH", tmp_path / "absent.json")
    sc.load_scan_rule_gloss.cache_clear()
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        sc.load_scan_rule_gloss()
    monkeypatch.undo()
    sc.load_scan_rule_gloss.cache_clear()


def test_pyproject_declares_the_gloss_as_package_data() -> None:
    """A cheap static guard on the declaration itself. NOT the packaging evidence — a
    declaration can be right while the build still drops the file, which is why the test
    below opens a real wheel."""
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[tool\.setuptools\.package-data\](.*?)(?=^\[|\Z)", text, re.S | re.M)
    assert block, "pyproject has no [tool.setuptools.package-data]"
    assert '"scan_rule_gloss.json"' in block.group(0), (
        "scan_rule_gloss.json is not declared as package data — a non-.py file is not "
        "shipped by default, so the wheel would carry no gloss at all"
    )


def test_the_gloss_ships_inside_a_built_wheel(tmp_path) -> None:
    """THE packaging evidence: read the sentences back out of a real built artifact.

    Built in a subprocess (no chdir, no setuptools state, in this repo's own tree) via the
    PEP 517 backend directly, so it needs neither the ``build`` CLI nor the ``wheel``
    package. Asserting on the wheel's own bytes is the only check that covers the whole
    chain — declaration, build step, and archive layout — and it is the chain whose failure
    mode is a CLI that quietly stops explaining anything."""
    out = tmp_path / "whl"
    out.mkdir()
    script = (
        "import warnings;warnings.filterwarnings('ignore')\n"
        "from setuptools import build_meta\n"
        f"print(build_meta.build_wheel({str(out)!r}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stderr[-4000:]}"
    wheel = out / proc.stdout.strip().splitlines()[-1]
    assert wheel.is_file(), f"backend reported {wheel.name} but it is not there"

    with zipfile.ZipFile(wheel) as z:
        assert _WHEEL_MEMBER in z.namelist(), (
            f"{_WHEEL_MEMBER} is absent from {wheel.name} — an installed personalclaw "
            "would have no gloss, and every refused install would go back to bare rows"
        )
        packaged = json.loads(z.read(_WHEEL_MEMBER).decode("utf-8"))

    shipped = {k: v for k, v in packaged.items() if not k.startswith("_")}
    assert shipped == sc.load_scan_rule_gloss(), "the wheel's gloss differs from the source's"
    # The sentences themselves survived, not just a well-formed empty object.
    assert shipped.get("python_exec", "").endswith(".")
    assert len(shipped) >= 15


# ── The CLI, driven against a real refusal ──────────────────────────────────


class _FakeMarketplace(SkillsMarketplace):
    """A marketplace whose bytes the test controls. Everything downstream — staging, the
    scanner, the verdict, the refusal — is the real thing; only the network is not."""

    def __init__(self, files: list[dict[str, str]], tier: str = "community") -> None:
        self._files = files
        self._tier = tier

    def search(self, query, limit=20):  # noqa: ANN001, ANN201, D102
        return []

    def fetch(self, skill_id):  # noqa: ANN001, ANN201, D102
        return SkillDetail(id=skill_id, name=skill_id, files=self._files)

    @property
    def marketplace_type(self) -> str:  # noqa: D102
        return "fake"

    @property
    def trust_tier(self) -> str:  # noqa: D102
        return self._tier


def _drive_install(monkeypatch, capsys, tmp_path, files: list[dict[str, str]]) -> str:
    """Run the real ``personalclaw skills install`` handler and return what it printed."""
    registry = SkillsRegistry()
    registry.register("fake", _FakeMarketplace(files))
    monkeypatch.setattr(
        "personalclaw.skills.marketplace.get_default_skills_registry", lambda: registry
    )
    from personalclaw.cli import _handle_skills

    _handle_skills(
        Namespace(
            skills_command="install",
            id="suspect",
            marketplace="fake",
            target=str(tmp_path / "live"),
            force=False,
        )
    )
    return capsys.readouterr().out


def _many_python_exec(n: int) -> list[dict[str, str]]:
    """``n`` findings from real content: one ``python_exec`` per script file."""
    files = [{"path": "SKILL.md", "contents": "---\nname: suspect\n---\n\n# suspect\n"}]
    files += [
        {
            "path": f"scripts/step{i}.py",
            "contents": f'import subprocess\nsubprocess.run(["echo", "{i}"])\n',
        }
        for i in range(n)
    ]
    return files


def test_a_refused_install_explains_each_rule_in_plain_language(
    monkeypatch, capsys, tmp_path
) -> None:
    """The defect, end to end. A real refusal on real content must not stop at the
    scanner's own vocabulary: the sentence a non-expert can act on has to be on screen."""
    out = _drive_install(
        monkeypatch,
        capsys,
        tmp_path,
        [
            {"path": "SKILL.md", "contents": "---\nname: suspect\n---\n\n# suspect\n"},
            {
                "path": "scripts/fetch.py",
                "contents": 'import subprocess\nsubprocess.run(["curl", "-s", "http://x/y"])\n',
            },
        ],
    )
    assert "❌ Install refused" in out
    # The technical row is untouched — the gloss complements the evidence, never replaces it.
    assert "python_exec" in out and "scripts/fetch.py" in out
    assert (
        "This code runs an external program on your machine." in out
    ), f"the refused install printed no plain-language gloss:\n{out}"


def test_a_ninth_finding_is_never_dropped_in_silence(monkeypatch, capsys, tmp_path) -> None:
    """Nine findings printed eight and said nothing, so a user counting the rows got a wrong
    answer about what the scanner found — on the output whose only job is to justify the
    refusal. The cap stays; the silence does not."""
    out = _drive_install(monkeypatch, capsys, tmp_path, _many_python_exec(9))
    rows = [ln for ln in out.splitlines() if ln.lstrip().startswith("- [")]
    assert len(rows) == 8, f"expected the cap to hold at 8 rows, got {len(rows)}:\n{out}"
    assert "+1 more finding not shown" in out, f"the ninth finding vanished silently:\n{out}"


def test_a_full_but_untruncated_list_claims_nothing_is_hidden(
    monkeypatch, capsys, tmp_path
) -> None:
    """Exactly at the cap hides nothing, so the residue line must not appear. A note that
    always shows is a new false statement, not a fix for the old one."""
    out = _drive_install(monkeypatch, capsys, tmp_path, _many_python_exec(8))
    rows = [ln for ln in out.splitlines() if ln.lstrip().startswith("- [")]
    assert len(rows) == 8
    assert "not shown" not in out, f"nothing was hidden, so nothing may be claimed:\n{out}"
