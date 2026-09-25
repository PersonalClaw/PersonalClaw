"""``verify_wheel.py`` must not pass on a wheel it did not actually exercise (#2758).

Two independent holes in the release path's one gate, both measured on 2026-09-07 in a tree
``git status`` reported as clean:

**It passed while the gateway it booted was logging ERRORs.** The wheel carried three
``personalclaw/apps/native/`` directories that existed neither in git nor on disk, two of them
naming factory functions the package no longer defines, and the verifier printed
``PASS: wheel contract met`` with ``Failed to enable extension`` tracebacks in its own captured
output. The evidence was in the gate's hands and nothing asserted on it — the same shape as a
check that passes while the thing it exists to prove never happened.

**The staging tree was stale.** ``python -m build`` does not clear ``build/``, and setuptools
re-used the copy it found, so the deleted app directories were packaged from
``build/lib/personalclaw/apps/native/``. ``DIST-3`` names the bare ``python -m build`` as *the*
release command, so anything ever removed from ``src/personalclaw/**`` could reappear in a
locally built wheel — and a container image, a ``pip install ./dist/*.whl`` or a hand-cut
release inherits it. Inert here; a deleted module that still *imports* would run.

This file is the rail for both fixes, plus the two ways each could quietly stop working:

* the marker strings assertion 6 greps for are pinned to the ``registry.py`` lines that EMIT
  them, so rewording a log message reds here instead of silently disarming the gate;
* the detector is driven from both sides (the real observed traceback line, and clean chatter
  that must NOT trip it), because a detector that matches nothing reads exactly like a pass;
* the staging-tree cleanup is asserted BEHAVIOURALLY against a planted stale tree, not by
  grepping the source for ``rmtree``;
* and ``_boot_and_probe`` is asserted to actually CALL the assertion, because a perfect
  detector with no call site is the failure mode this repo keeps finding.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
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


# ── The staging tree is cleared, judged behaviourally ─────────────────────────


def test_build_clears_the_stale_staging_tree(verify_wheel, tmp_path, monkeypatch) -> None:
    """The wheel cannot be built from directories the source tree no longer has.

    Planted with the ACTUAL payload #2758 found — one of the three app directories that existed
    only in ``build/lib`` — and a leftover ``dist/`` wheel whose version string sorts ABOVE any
    real one, because ``_find_wheel`` takes ``sorted(glob(...))[-1]`` (lexicographic, not
    newest-by-mtime) and would otherwise verify the leftover instead of the fresh build.
    """
    monkeypatch.chdir(tmp_path)
    stale_app = tmp_path / "build" / "lib" / "personalclaw" / "apps" / "native" / "native-workflows"
    stale_app.mkdir(parents=True)
    (stale_app / "app.json").write_text('{"name": "native-workflows"}', encoding="utf-8")
    leftover = tmp_path / "dist" / "personalclaw-99.9.9-py3-none-any.whl"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"not really a wheel")

    # Floor: if the plant did not land, "it was removed" is trivially true.
    assert stale_app.is_dir() and leftover.is_file()

    invocations: list[list[str]] = []

    def _fake_run(cmd, *args, **kwargs):
        invocations.append(list(cmd))
        return None

    monkeypatch.setattr(verify_wheel.subprocess, "run", _fake_run)
    verify_wheel._build_wheel()

    assert not (tmp_path / "build").exists(), (
        "build/ survived --build, so setuptools can re-use the stale staging tree and package "
        "directories the source tree does not have (#2758)"
    )
    assert not leftover.exists(), (
        "a leftover dist/ wheel survived --build; _find_wheel sorts lexicographically, so a "
        "higher version string would be verified in place of the wheel just built"
    )
    assert invocations, "--build removed the trees but never invoked the build command"
    assert invocations[-1][1:] == ["-m", "build", "--wheel"], invocations[-1]


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
