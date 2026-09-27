"""``verify_wheel.py`` must not pass on a wheel it did not actually exercise (#2758).

Two independent holes in the release path's one gate, both measured on 2026-09-07 in a tree
``git status`` reported as clean:

**It passed while the gateway it booted was logging ERRORs.** The wheel carried three
``personalclaw/apps/native/`` directories that existed neither in git nor on disk, two of them
naming factory functions the package no longer defines, and the verifier printed
``PASS: wheel contract met`` with ``Failed to enable extension`` tracebacks in its own captured
output. The evidence was in the gate's hands and nothing asserted on it — the same shape as a
check that passes while the thing it exists to prove never happened.

**The staging tree was stale.** A setuptools build does not clear ``build/``, and setuptools
re-used the copy it found, so the deleted app directories were packaged from
``build/lib/personalclaw/apps/native/``. So anything ever removed from ``src/personalclaw/**``
could reappear in a locally built wheel — and a container image, a ``pip install
./dist/*.whl`` or a hand-cut release inherits it. Inert here; a deleted module that still
*imports* would run. ``make build`` (``verify_wheel.py --build``) is now the one command that
builds a distribution, and it cleans every generated tree first, then inspects what it built
against the source and rebuilds the wheel from the sdist to prove the two are the same bytes.

This file is the rail for both fixes, plus the two ways each could quietly stop working:

* the marker strings assertion 6 greps for are pinned to the ``registry.py`` lines that EMIT
  them, so rewording a log message reds here instead of silently disarming the gate;
* the detector is driven from both sides (the real observed traceback line, and clean chatter
  that must NOT trip it), because a detector that matches nothing reads exactly like a pass;
* the staging-tree cleanup is asserted BEHAVIOURALLY against a planted stale tree, not by
  grepping the source for ``rmtree``;
* the content inspection is driven over a REAL setuptools wheel and sdist, and then over three
  broken copies of them, because an inspection that cannot fail proves nothing;
* and ``_boot_and_probe`` is asserted to actually CALL the assertion, because a perfect
  detector with no call site is the failure mode this repo keeps finding.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VERIFY_WHEEL = _REPO_ROOT / "scripts" / "verify_wheel.py"
_REGISTRY = _REPO_ROOT / "src" / "personalclaw" / "providers" / "registry.py"

#: The exact line the issue observed, verbatim — a real sample beats an invented one.
_OBSERVED_FAILURE = (
    "ERROR personalclaw.providers.registry: Failed to enable extension run-workflow-action"
)

#: Ordinary boot chatter. Mentions extensions, apps and even the word "error" in a URL-ish
#: shape, so a marker set that had rotted into something over-broad is caught too.
_CLEAN_TRANSCRIPT = [
    "INFO personalclaw.dashboard.server: gateway starting on 127.0.0.1:10101",
    "INFO personalclaw.providers.registry: Enabled extension native-tasks (type=tool)",
    "INFO personalclaw.providers.registry: Enabled extension personalclaw-memory (type=tool)",
    "INFO personalclaw.apps.manager: 30 bundled apps discovered",
    "INFO aiohttp.access: 127.0.0.1 GET /api/healthz 200",
]


def _load_verify_wheel():
    """Import ``scripts/verify_wheel.py`` by path — ``scripts/`` is not a package."""
    spec = importlib.util.spec_from_file_location("_verify_wheel_under_test", _VERIFY_WHEEL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify_wheel():
    return _load_verify_wheel()


# ── The markers are pinned to the code that emits them ────────────────────────


def test_assertion_six_exists_at_all_and_matches_something(verify_wheel) -> None:
    """Vacuity floor. ``getattr``, not attribute access, so a MISSING surface reports as this
    cell's message rather than as an AttributeError in every cell below.

    An empty marker tuple and an absent detector are the same defect wearing two hats: the gate
    goes back to passing on a wheel whose bundled apps do not load (#2758).
    """
    for name in ("extension_failures", "extensions_enabled", "_assert_every_bundled_app_enabled"):
        assert callable(getattr(verify_wheel, name, None)), (
            f"scripts/verify_wheel.py has no {name}() — the wheel gate cannot see a bundled app "
            f"that failed to load, which is the whole of #2758."
        )
    assert getattr(verify_wheel, "_EXTENSION_FAILURE_MARKERS", ()), (
        "verify_wheel greps for NO failure markers, so assertion 6 can never fire and the gate "
        "is back to passing on a wheel whose bundled apps do not load (#2758)"
    )
    assert getattr(verify_wheel, "_EXTENSION_SUCCESS_MARKER", ""), (
        "verify_wheel has no POSITIVE marker, so assertion 6 is back to 'no bad news' — which "
        "is also what an extension loader that never ran says"
    )


def test_every_marker_is_still_emitted_by_the_registry(verify_wheel) -> None:
    """A log-message reword must red HERE, not disarm the gate silently.

    Assertion 6 is a string match over the gateway's output, so its correctness depends on
    ``registry.py`` still emitting those strings. That coupling is invisible from either side —
    which is exactly the kind of coupling that rots — so it is stated here.

    Looped inside the cell rather than parametrized: a ``@parametrize`` over the module's
    constant runs at COLLECTION time, so a verifier missing the constant entirely takes this
    whole file down as a collection error instead of reporting the floor above.
    """
    source = _REGISTRY.read_text(encoding="utf-8")
    missing = [m for m in verify_wheel._EXTENSION_FAILURE_MARKERS if m not in source]
    assert not missing, (
        f"verify_wheel.py's assertion 6 greps the gateway output for {missing!r}, but "
        f"providers/registry.py no longer contains those strings. Either the log was reworded "
        f"(update the marker) or the branch was deleted (drop the marker) — leaving it stands "
        f"as a check that can never fire."
    )


# ── The detector, driven from both sides ──────────────────────────────────────


def test_the_observed_failure_line_is_detected(verify_wheel) -> None:
    """The real #2758 line, verbatim."""
    assert verify_wheel.extension_failures([_OBSERVED_FAILURE]) == [_OBSERVED_FAILURE]


def test_a_failure_is_found_among_ordinary_chatter(verify_wheel) -> None:
    """It has to survive being buried — the real transcript is mostly INFO lines."""
    transcript = [*_CLEAN_TRANSCRIPT[:2], _OBSERVED_FAILURE, *_CLEAN_TRANSCRIPT[2:]]
    assert verify_wheel.extension_failures(transcript) == [_OBSERVED_FAILURE]


def test_clean_chatter_does_not_trip_the_detector(verify_wheel) -> None:
    """The other side: assertion 6 must not red on a healthy boot.

    A gate that fires on a clean wheel gets switched off, so this is load-bearing rather than
    decorative — and the sample deliberately contains ``Enabled extension`` lines, which share
    most of their text with ``Failed to enable extension``.
    """
    assert verify_wheel.extension_failures(_CLEAN_TRANSCRIPT) == []


def test_the_assertion_fails_the_script_and_not_just_the_detector(verify_wheel) -> None:
    """The wiring, not the predicate: a hit must exit non-zero.

    ``_fail`` calls ``sys.exit(1)``, so a detected failure has to arrive as ``SystemExit`` —
    otherwise the detector could be perfect while the script still printed PASS.
    """
    with pytest.raises(SystemExit) as exc:
        verify_wheel._assert_every_bundled_app_enabled([_OBSERVED_FAILURE], 30)
    assert exc.value.code == 1
    verify_wheel._assert_every_bundled_app_enabled(list(_CLEAN_TRANSCRIPT), 30)  # no raise


# ── Assertion 6 must not pass on an EMPTY evidence window ─────────────────────
#
# MEASURED on the 0.1.3 wheel: at the gateway's default log level the whole boot emits TWO
# lines and neither mentions an extension, so "no failure markers" was equally true of a wheel
# with thirty healthy apps and of one whose apps all failed before the window opened. Booting
# `--verbose` widens it to 182 lines / 30 enables, and the floor below is what makes the widening
# load-bearing rather than incidental.


def test_a_transcript_that_enabled_nothing_fails_the_gate(verify_wheel) -> None:
    """'No bad news' is not a pass when the evidence window was empty."""
    with pytest.raises(SystemExit) as exc:
        verify_wheel._assert_every_bundled_app_enabled([], 30)
    assert exc.value.code == 1
    # Not merely the empty case: chatter with no enable at all must fail too, because that is
    # exactly what the pre-verbose default level produced.
    silent = [
        "05:08:10 WARNING personalclaw.dashboard.server: PERSONALCLAW_AUTH_MODE=none",
        "Created default config: /tmp/pc_verify_wheel_x/home/config.json",
    ]
    with pytest.raises(SystemExit):
        verify_wheel._assert_every_bundled_app_enabled(silent, 30)


def test_the_success_marker_is_still_emitted_by_the_registry(verify_wheel) -> None:
    """The positive marker is pinned to its emitter for the same reason the failure ones are."""
    source = _REGISTRY.read_text(encoding="utf-8")
    assert verify_wheel._EXTENSION_SUCCESS_MARKER in source, (
        f"verify_wheel looks for {verify_wheel._EXTENSION_SUCCESS_MARKER!r} as proof the "
        f"registry ran, but providers/registry.py no longer logs it — the floor would then red "
        f"every release for a log reword rather than for a broken wheel."
    )


def test_the_gateway_is_booted_verbose_before_the_subcommand() -> None:
    """``--verbose`` is what opens the evidence window, and its POSITION is load-bearing.

    It is a top-level flag: measured, ``gateway --test-mode --verbose`` exits 2 with
    "unrecognized arguments: --verbose", so a well-meaning tidy-up that moves it after the
    subcommand would break the boot outright. Asserted over the argv literal in the AST rather
    than by grepping for the string, so a mention in a comment cannot satisfy it.
    """
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    argvs = [
        [el.value for el in node.elts if isinstance(el, ast.Constant)]
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        and any(isinstance(el, ast.Constant) and el.value == "personalclaw" for el in node.elts)
    ]
    boots = [argv for argv in argvs if "gateway" in argv]
    assert len(boots) == 1, f"expected exactly one gateway argv literal, found {argvs!r}"
    argv = boots[0]
    assert "--verbose" in argv, (
        f"the gateway is booted without --verbose ({argv!r}), so the registry's enable lines "
        f"stay at INFO under a WARNING root level and assertion 6 judges an EMPTY transcript"
    )
    assert argv.index("--verbose") < argv.index("gateway"), (
        f"--verbose sits AFTER the subcommand in {argv!r}. It is a top-level flag; there it "
        f"makes the gateway exit 2 with 'unrecognized arguments'."
    )


def test_the_bundled_app_count_reads_the_wheel(verify_wheel, tmp_path) -> None:
    """The count reported beside the enables comes from the artifact, not from the source tree."""
    import zipfile as _zipfile

    wheel = tmp_path / "fake-0.0.0-py3-none-any.whl"
    with _zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("personalclaw/apps/native/alpha/app.json", "{}")
        zf.writestr("personalclaw/apps/native/alpha/provider.py", "")
        zf.writestr("personalclaw/apps/native/beta/app.json", "{}")
        zf.writestr("personalclaw/static/dist/index.html", "<html></html>")
    assert verify_wheel.bundled_app_count(wheel) == 2
    empty = tmp_path / "empty-0.0.0-py3-none-any.whl"
    with _zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("personalclaw/__init__.py", "")
    assert verify_wheel.bundled_app_count(empty) == 0


def test_the_boot_probe_actually_calls_the_assertion() -> None:
    """A detector with no call site is the failure class this repo keeps finding.

    Asserted over the AST rather than by string search so a mention inside a docstring or a
    comment cannot satisfy it: the name has to appear as a CALL inside ``_boot_and_probe``,
    which is the function ``main`` uses to exercise the wheel.
    """
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    probes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_boot_and_probe"
    ]
    assert len(probes) == 1, "scripts/verify_wheel.py has no single `_boot_and_probe` function"
    called = {
        child.func.id
        for child in ast.walk(probes[0])
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_assert_every_bundled_app_enabled" in called, (
        "_boot_and_probe does not call _assert_every_bundled_app_enabled, so the extension "
        "check exists and never runs — assertion 6 would be declared and unexecuted."
    )


# ── The licence notices: checked at build time, served by the installed wheel ─


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, f"scripts/verify_wheel.py has no single `{name}` function"
    return found[0]


def test_the_boot_probe_asks_the_installed_gateway_for_its_licence_notices() -> None:
    called = {
        child.func.id
        for child in ast.walk(_function("_boot_and_probe"))
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_assert_notices_served" in called, (
        "_boot_and_probe does not call _assert_notices_served, so assertion 9 is declared and "
        "never runs"
    )


def test_the_notices_assertion_fails_on_the_dashboard_html_an_unrouted_path_answers(
    verify_wheel, monkeypatch
) -> None:
    """An unrouted path gets the SPA shell with a 200, so a status check alone passes it."""
    served = {
        path: (
            200,
            "text/plain; charset=utf-8",
            f"{title}\nPersonalClaw's own code is MIT-licensed",
        )
        for path, title in verify_wheel._NOTICE_TITLES.items()
    }
    monkeypatch.setattr(
        verify_wheel, "_http_get", lambda url, *_a, **_k: served[url.removeprefix("http://gw")]
    )
    verify_wheel._assert_notices_served("http://gw")

    served["/THIRD_PARTY_NOTICES_NPM.txt"] = (200, "text/html", "<!doctype html><html>…")
    with pytest.raises(SystemExit):
        verify_wheel._assert_notices_served("http://gw")


def test_the_canonical_build_checks_the_licences_of_what_the_spa_build_emitted() -> None:
    """``make build`` fails when a bundled npm package, font or binary has no licence notice:
    the check runs on the SPA it just built, before any artifact is made from it."""
    commands = [
        [elt.value for elt in node.args[0].elts if isinstance(elt, ast.Constant)]
        for node in ast.walk(_function("_canonical_distribution_build"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run"
        and isinstance(node.args[0], ast.List)
    ]
    spa = next(i for i, cmd in enumerate(commands) if cmd[-2:] == ["run", "build"])
    check = next(i for i, cmd in enumerate(commands) if "scripts/check_asset_licenses.py" in cmd)
    wheel = next(i for i, cmd in enumerate(commands) if "--wheel" in cmd)
    assert "--built-web" in commands[check]
    assert spa < check < wheel, f"the licence check runs out of order: {commands}"


# ── Every staging tree is cleared, judged behaviourally ───────────────────────


def test_the_canonical_build_clears_every_stale_output(verify_wheel, tmp_path) -> None:
    """The artifacts cannot re-use a file the source tree or the SPA no longer has.

    Planted with the ACTUAL payload #2758 found — one of the three app directories that existed
    only in ``build/lib`` — a leftover ``dist/`` wheel whose version string sorts ABOVE any real
    one (``_find_wheel`` takes ``sorted(glob(...))[-1]``, lexicographic, not newest-by-mtime),
    an ``egg-info`` naming a deleted file, a stale ``web/dist`` and the dev link to it.
    """
    stale_app = tmp_path / "build" / "lib" / "personalclaw" / "apps" / "native" / "native-workflows"
    stale_app.mkdir(parents=True)
    (stale_app / "app.json").write_text('{"name": "native-workflows"}', encoding="utf-8")
    leftover = tmp_path / "dist" / "personalclaw-99.9.9-py3-none-any.whl"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"not really a wheel")
    egg_info = tmp_path / "src" / "personalclaw.egg-info"
    egg_info.mkdir(parents=True)
    (egg_info / "SOURCES.txt").write_text("deleted.py\n", encoding="utf-8")
    web_dist = tmp_path / "web" / "dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text("stale", encoding="utf-8")
    static_dist = tmp_path / "src" / "personalclaw" / "static" / "dist"
    static_dist.parent.mkdir(parents=True)
    static_dist.symlink_to(web_dist)
    keep = tmp_path / "src" / "personalclaw" / "__init__.py"
    keep.write_text("", encoding="utf-8")

    # Floor: if the plant did not land, "it was removed" is trivially true.
    assert stale_app.is_dir() and leftover.is_file() and static_dist.is_symlink()

    verify_wheel._clean_distribution_outputs(tmp_path)

    for relative in verify_wheel._STALE_OUTPUTS:
        path = tmp_path / relative
        assert not path.exists() and not path.is_symlink(), f"stale output survived: {relative}"
    assert keep.is_file(), "the clean reached past the generated trees into the source"


def test_the_build_fixes_the_zip_epoch_and_refuses_one_a_zip_cannot_hold(
    verify_wheel, monkeypatch
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert verify_wheel._build_environment()["SOURCE_DATE_EPOCH"] == "315532800"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    assert verify_wheel._build_environment()["SOURCE_DATE_EPOCH"] == "1700000000"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    with pytest.raises(SystemExit):
        verify_wheel._build_environment()


def test_sdist_normalization_makes_identical_trees_identical_bytes(verify_wheel, tmp_path) -> None:
    """Two sdists of one tree, built at different times by different users, differ only in
    archive metadata — and after normalization they do not differ at all."""

    def write_variant(path: Path, *, mtime: int, uid: int, owner: str) -> None:
        with tarfile.open(path, "w:gz") as archive:
            directory = tarfile.TarInfo("personalclaw-0.0.1")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            body = b"same source payload\n"
            member = tarfile.TarInfo("personalclaw-0.0.1/README.md")
            member.mode = 0o644
            member.size = len(body)
            for info in (directory, member):
                info.mtime, info.uid, info.gid = mtime, uid, uid
                info.uname = info.gname = owner
            archive.addfile(directory)
            archive.addfile(member, io.BytesIO(body))

    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    write_variant(first, mtime=1_700_000_000, uid=501, owner="first")
    write_variant(second, mtime=1_800_000_000, uid=1001, owner="second")
    assert first.read_bytes() != second.read_bytes(), "the variants must differ before"

    verify_wheel.normalize_sdist(first, epoch=315532800)
    verify_wheel.normalize_sdist(second, epoch=315532800)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        for member in archive.getmembers():
            assert member.mtime == 315532800
            assert member.uid == member.gid == 0
            assert member.uname == member.gname == ""


def test_the_build_backend_is_the_setuptools_the_lock_resolves() -> None:
    """``make build`` is byte-reproducible only on one exact backend, and the packaging tests
    below drive the backend CI's environment has — the one ``uv.lock`` resolves. The pin and
    the lock drift apart the first time one is bumped alone, and then the tests exercise a
    different setuptools than the release builds with."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((_REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "setuptools"]
    assert len(locked) == 1, f"uv.lock must resolve setuptools exactly once, found {locked}"
    assert pyproject["build-system"]["requires"] == [f"setuptools=={locked[0]}"]


# ── The inspection passes a real distribution and fails a broken one ──────────


def _inspectable_project(tmp_path: Path) -> Path:
    """A small package built by this repository's ``setup.py``, ``MANIFEST.in`` and
    package-data reader, with every kind of member the inspection derives: a module, a
    package-data file, a bundled app, the dashboard, the entry point and the licence."""
    project = tmp_path / "project"
    package = project / "src" / "personalclaw"
    app = package / "apps" / "native" / "demo"
    app.mkdir(parents=True)
    (package / "config").mkdir()
    for init in (package, package / "apps", package / "config"):
        (init / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    (package / "config" / "defaults.json").write_text("{}\n", encoding="utf-8")
    (app / "app.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    (app / "provider.py").write_text("", encoding="utf-8")
    dist = project / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    (project / "scripts").mkdir()
    shutil.copy2(_REPO_ROOT / "scripts" / "backend_bundle_manifest.py", project / "scripts")
    shutil.copy2(_REPO_ROOT / "setup.py", project)
    shutil.copy2(_REPO_ROOT / "MANIFEST.in", project)
    (project / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (project / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "personalclaw"
version = "0.0.1"
description = "Inspection fixture"
readme = "README.md"
license = "MIT"
requires-python = ">=3.12,<3.14"
dependencies = ["aiohttp>=3.9,<4"]

[project.optional-dependencies]
test = ["pytest>=7"]

[project.urls]
Homepage = "https://example.invalid/personalclaw"

[project.scripts]
personalclaw = "personalclaw.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
personalclaw = ["config/defaults.json", "apps/native/*/app.json", "apps/native/*/*.py"]
""".lstrip(),
        encoding="utf-8",
    )
    return project


def _backend(project: Path, call: str, out: Path) -> Path:
    """One setuptools ``build_meta`` hook, in-process in a fresh interpreter (no network)."""
    out.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import warnings; warnings.filterwarnings('ignore')\n"
            f"from setuptools import build_meta\nprint(build_meta.{call}({str(out)!r}))",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"{call} failed:\n{proc.stderr[-3000:]}"
    return out / proc.stdout.strip().splitlines()[-1]


def _rewrite_wheel(source: Path, target: Path, *, drop: str = "", corrupt: str = "") -> None:
    """Copy *source* to *target* without member *drop*, or with *corrupt*'s bytes changed."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w") as out:
        for info in src.infolist():
            if info.filename == drop:
                continue
            body = src.read(info.filename)
            if info.filename.endswith(".dist-info/RECORD") and drop:
                body = b"".join(
                    line + b"\n"
                    for line in body.splitlines()
                    if not line.startswith(drop.encode() + b",")
                )
            if info.filename == corrupt:
                body += b"# tampered after the RECORD was written\n"
            out.writestr(info, body)


@pytest.mark.timeout(240)
def test_the_inspection_passes_a_real_distribution_and_fails_a_broken_one(
    verify_wheel, tmp_path, capsys
) -> None:
    project = _inspectable_project(tmp_path)
    sdist = _backend(project, "build_sdist", tmp_path / "sdist")
    wheel = _backend(project, "build_wheel", tmp_path / "wheel")

    # The real artifacts pass: this is the floor that makes the refusals below mean something.
    verify_wheel.inspect_sdist(sdist, root=project)
    verify_wheel.inspect_wheel(wheel, root=project)
    passed = capsys.readouterr().out
    assert "OK: sdist payload complete" in passed and "OK: wheel payload complete" in passed

    # 1. A declared package-data file the wheel does not carry (its RECORD row dropped too, so
    #    the payload check is what has to catch it).
    missing = tmp_path / "missing.whl"
    _rewrite_wheel(wheel, missing, drop="personalclaw/config/defaults.json")
    with pytest.raises(SystemExit):
        verify_wheel.inspect_wheel(missing, root=project)
    assert "personalclaw/config/defaults.json" in capsys.readouterr().err

    # 2. A member whose bytes no longer match what RECORD says was shipped.
    tampered = tmp_path / "tampered.whl"
    _rewrite_wheel(wheel, tampered, corrupt="personalclaw/cli.py")
    with pytest.raises(SystemExit):
        verify_wheel.inspect_wheel(tampered, root=project)
    assert "RECORD digest or size is wrong for personalclaw/cli.py" in capsys.readouterr().err

    # 3. Metadata that no longer says what pyproject.toml says.
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('"0.0.1"', '"0.0.2"'), encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        verify_wheel.inspect_wheel(wheel, root=project)
    assert "Version" in capsys.readouterr().err


# ── A relative --wheel reaches every process that opens it ────────────────────


@pytest.mark.timeout(240)
def test_a_relative_wheel_from_the_checkout_root_reaches_the_installed_probe(
    verify_wheel, tmp_path, monkeypatch, capsys
) -> None:
    """``--wheel dist/<name>.whl`` from the checkout root, the shape ``release.yml`` passes.

    🔴 Measured by the batch validation of the change that added assertion 7's probe. The probe
    runs with the scratch home as its working directory (so no source tree can stand in for the
    install), the wheel path arrived RELATIVE, and ``gate_wheel`` died with
    ``FileNotFoundError: 'dist/personalclaw-0.2.0-py3-none-any.whl'`` in a directory that has no
    ``dist/``. Every earlier drive of the gate had passed an absolute path.

    Driven through ``main()`` with the REAL probe subprocess, because the defect is two processes
    disagreeing about the working directory, and a faked subprocess cannot disagree. The install
    and the boot are stubbed: pip would need the network, a gateway takes a minute, and neither is
    where the path went wrong. The test's own interpreter stands in for the installed wheel's.
    That works because CI installs the package (``uv sync``) instead of putting ``src/`` on
    ``PYTHONPATH``, which the probe's ``-I`` would ignore.
    """
    root = tmp_path / "checkout"
    wheel_name = "personalclaw-0.2.0-py3-none-any.whl"
    (root / "dist").mkdir(parents=True)
    with zipfile.ZipFile(root / "dist" / wheel_name, "w") as zf:
        zf.writestr("personalclaw/__init__.py", "")
        zf.writestr("personalclaw/static/dist/index.html", "<!doctype html>")
    monkeypatch.chdir(root)

    handed: dict[str, Path] = {}
    monkeypatch.setattr(verify_wheel, "_make_venv", lambda venv_root: Path(sys.executable))
    monkeypatch.setattr(
        verify_wheel, "_pip_install_wheel", lambda py, wheel: handed.update(pip=wheel)
    )
    monkeypatch.setattr(
        verify_wheel,
        "_boot_and_probe",
        lambda py, home, bundled_apps=0: handed.update(home=home),
    )
    # The content inspection is a process-local read and has its own tests above; this fixture
    # wheel is two files, so the real one would refuse it before the path under test is reached.
    monkeypatch.setattr(
        verify_wheel, "inspect_wheel", lambda wheel, root=None: handed.update(inspected=wheel)
    )
    monkeypatch.setattr(sys, "argv", ["verify_wheel.py", "--wheel", f"dist/{wheel_name}"])

    try:
        rc = verify_wheel.main()
    except SystemExit as exc:
        pytest.fail(
            f"verify_wheel exited {exc.code} on a relative --wheel from the checkout root:\n"
            f"{capsys.readouterr().err[-2500:]}"
        )
    assert rc == 0
    out = capsys.readouterr().out
    assert "read from the installed package, not the repository" in out, out
    assert "carries no model weight" in out, out
    # Every process that opens the wheel was handed a path that does not depend on its cwd.
    assert handed["pip"] == (root / "dist" / wheel_name).resolve(), handed
    assert handed["inspected"] == handed["pip"], handed
    assert handed["home"].is_absolute(), handed


def test_the_wheel_it_verifies_is_named_absolutely(verify_wheel, tmp_path, monkeypatch) -> None:
    """``_find_wheel`` is where the path enters, so it is where the path becomes absolute."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "personalclaw-0.2.0-py3-none-any.whl").write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    expected = (tmp_path / "dist" / "personalclaw-0.2.0-py3-none-any.whl").resolve()
    assert verify_wheel._find_wheel("dist/*.whl") == expected
    assert verify_wheel._find_wheel("dist/personalclaw-0.2.0-py3-none-any.whl") == expected
    assert verify_wheel._find_wheel(None) == expected
