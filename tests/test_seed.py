"""Tests for ``personalclaw.seed`` — fixture seeding of ``$PERSONALCLAW_HOME``.

Covers fixture resolution and name guards, the safety rails (home unset,
main-home protection, non-empty target, symlinked target), ``--seed-replace``,
the CLI wiring, and the SEL audit emission contract.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import seed as seed_mod


def test_seed_empty_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``personalclaw seed --fixture empty`` writes fixture.yaml into $PERSONALCLAW_HOME."""
    # copytree refuses an existing dst, so don't pre-create it.
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    seed_mod.seed("empty")

    out_file = target / "fixture.yaml"
    assert out_file.is_file(), f"expected {out_file} to exist after seed"
    # Exact match guards against accidental fixture tampering.
    assert out_file.read_text(encoding="utf-8").strip() == "schema-version: 2026-04-28"


def test_seed_unset_home_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset $PERSONALCLAW_HOME raises SeedError with exit code 2."""
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty")

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert "PERSONALCLAW_HOME" in str(excinfo.value)


def test_seed_unknown_fixture_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown fixture name raises SeedError with exit code 2.

    Regression guard for ``_resolve_fixture`` — if fixture dir lookup ever
    silently falls through to ``copytree``, the user would hit a raw
    ``FileNotFoundError`` instead of a friendly rail error.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("this-fixture-does-not-exist")

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    msg = str(excinfo.value)
    assert "unknown fixture" in msg
    # Discoverability: the error must list the shipped fixtures so the user
    # doesn't have to read the ``tests_fixtures/`` tree.
    assert "Available fixtures:" in msg
    assert "empty" in msg
    # Target must not be written when fixture lookup fails.
    assert not target.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../../.ssh",
        "../.aws",
        "foo/bar",
        "foo\\bar",
        "..",
        "./empty",
        ".",
        "",
        "./",
        # SEC-1: NUL byte + control chars must be caught at the empty-or-root
        # gate before ``(root / name).resolve()`` raises ``ValueError`` and
        # escapes ``seed_cmd``'s ``except SeedError`` (bypassing both the
        # ``seed: error:`` ASCII prefix AND the SEL audit emit).
        "foo\x00bar",
        "\x00",
        "foo\nbar",
    ],
)
def test_seed_path_traversal_rejected(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names with path separators or '..' are rejected before copytree runs.

    Two escape classes are blocked: (1) names like ``../../.ssh`` would escape
    the fixtures tree; (2) names like ``"."`` or ``""`` resolve to the fixtures
    root itself, which would ``copytree`` the entire ``tests_fixtures/`` tree
    into ``$PERSONALCLAW_HOME``.

    Also pins *which* gate rejects each input class — without the
    per-branch message assertion below, the post-resolve ``candidate ==
    resolved_root`` gate would silently catch ``"."`` / ``""`` / ``"./"``
    even if the upfront empty-name check were deleted, and CI would still
    pass. Pinning the error message forces a test failure if the guard
    ordering is ever reshuffled.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed(name)

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    # Target must not be written.
    assert not target.exists()

    # Pin which gate rejected each name. New "empty-or-root" cases must
    # hit the first gate; the old path-separator/``..`` cases must hit
    # the second. ``"./empty"`` is interesting: it has ``/`` so it hits
    # the separator gate, not the empty-or-root gate. NUL-byte / control-
    # char names (``"foo\x00bar"``, ``"foo\nbar"``) also hit gate 1 via
    # the ``ord(c) < 0x20`` check (SEC-1 regression guard).
    if name in ("", ".", "./") or any(ord(c) < 0x20 for c in name):
        assert "empty or refers to the root" in str(
            excinfo.value
        ), f"expected empty-or-root gate to reject {name!r}, got: {excinfo.value}"
    else:
        assert "path separators or '..'" in str(
            excinfo.value
        ), f"expected path-separator gate to reject {name!r}, got: {excinfo.value}"


@patch("personalclaw.seed.sel")
def test_seed_cmd_exit_code_on_unset(
    mock_sel: MagicMock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """seed_cmd returns 2 and prints to stderr when $PERSONALCLAW_HOME is unset.

    ``sel`` is patched to keep the test hermetic: the real ``sel()``
    singleton resolves its log dir from ``Path.home()``, NOT
    ``$PERSONALCLAW_HOME``, and would otherwise append real audit events to
    the dev's own ``~/.personalclaw/security_events.jsonl`` HMAC chain.
    """
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)

    args = type("Args", (), {"seed": "empty"})()
    rc = seed_mod.seed_cmd(args)

    assert rc == seed_mod.EXIT_RAIL
    err = capsys.readouterr().err
    assert "PERSONALCLAW_HOME" in err
    # Plain ASCII prefix so non-UTF-8 terminals don't swallow the message.
    assert err.startswith("seed: error:")


def test_seed_cli_flag_registered(tmp_path: Path) -> None:
    """``personalclaw gateway --help`` mentions ``--seed FIXTURE``.

    Tracer-bullet acceptance from the  ticket: prove the CLI
    wiring end-to-end. The seed primitive is invoked as
    ``personalclaw gateway --seed <fixture>`` (it seeds ``$PERSONALCLAW_HOME``
    and THEN continues into the gateway event loop) — we can't let the
    subprocess actually run because ``run_gateway`` is a long-lived
    server. ``--help`` exits 0 after printing usage, which is enough to
    verify the flag is registered and the seed_cmd wiring imports clean.
    """
    repo_root = Path(__file__).resolve().parent.parent
    import os as _os

    env = {**_os.environ, "HOME": str(tmp_path)}
    # Preserve user site-packages: overriding HOME loses ~/.local/lib/pythonX.Y
    # where deps like croniter/cron_descriptor live when not system-installed.
    real_home = _os.environ.get("HOME", "")
    if real_home:
        import site

        user_site = site.getusersitepackages()
        if isinstance(user_site, str) and _os.path.isdir(user_site):
            existing_pp = env.get("PYTHONPATH", "")
            if existing_pp:
                env["PYTHONPATH"] = user_site + _os.pathsep + existing_pp
            else:
                env["PYTHONPATH"] = user_site
    # Guard against trailing separator when PYTHONPATH is unset — a trailing
    # ":" on POSIX adds CWD to sys.path, which would import unexpected
    # modules depending on where pytest runs.
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo_root / "src") + (
        _os.pathsep + existing_pypath if existing_pypath else ""
    )
    env["PERSONALCLAW_PROJECT_DIR"] = str(repo_root)

    result = subprocess.run(
        [sys.executable, "-m", "personalclaw", "gateway", "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"expected exit 0 from --help, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The flag must be registered and documented.
    assert "--seed" in result.stdout
    assert "FIXTURE" in result.stdout


# ------------------------------------------------------------------
# SEL audit regression tests — pin the emission contract so a future
# refactor can't silently remove the ``sel().log_api_access(...)``
# call or swap the ``outcome`` enum values. Pattern matches
# ``test/test_enterprise.py::test_allowlist_add_emits_audit`` and
# ``test/test_token_auth.py::test_refresh_emits_denied_audit``.
# ------------------------------------------------------------------


@patch("personalclaw.seed.sel")
def test_seed_cmd_emits_sel_audit_on_success(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path emits ``outcome="allowed"`` with fixture name + target_set flag."""
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    args = type("Args", (), {"seed": "empty"})()
    assert seed_mod.seed_cmd(args) == seed_mod.EXIT_OK

    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["caller"] == "cli"
    assert kw["operation"] == "seed"
    assert kw["outcome"] == "allowed"
    assert kw["source"] == "cli"
    assert "fixture='empty'" in kw["resources"]
    # ``target_set=True`` proves we log the presence-flag, not the raw
    # ``$PERSONALCLAW_HOME`` value, to avoid leaking it into the audit stream.
    assert "target_set=True" in kw["resources"]
    # Regression guard: the raw target path must NEVER appear in the
    # audit resources string. A future refactor adding
    # ``f"... target={target!r}"`` would silently leak the path.
    assert str(target) not in kw["resources"]


@patch("personalclaw.seed.sel")
def test_seed_cmd_emits_sel_audit_on_rail_denied(
    mock_sel: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset $PERSONALCLAW_HOME rail emits ``outcome="denied"`` with ``rail`` tag."""
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)

    args = type("Args", (), {"seed": "empty"})()
    assert seed_mod.seed_cmd(args) == seed_mod.EXIT_RAIL

    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["outcome"] == "denied"
    assert "fixture='empty'" in kw["resources"]
    # Rail-tag discipline: audit stream uses short code-controlled
    # identifiers, not the exception message (which embeds user-influenced
    # paths). ``rail=unset_home`` is the constant for this denial.
    assert f"rail={seed_mod.SeedError.RAIL_UNSET_HOME}" in kw["resources"]


@patch("personalclaw.seed.sel")
def test_seed_cmd_emits_sel_audit_on_path_traversal_denied(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path-traversal fixture name emits ``outcome="denied"`` with the bad name."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))

    args = type("Args", (), {"seed": "../../.ssh"})()
    assert seed_mod.seed_cmd(args) == seed_mod.EXIT_RAIL

    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["outcome"] == "denied"
    # The full adversarial input must appear verbatim in the audit log so
    # an SOC reviewing SEL events can reconstruct the attack attempt.
    assert "fixture='../../.ssh'" in kw["resources"]
    # Path-traversal hits the bad-name rail.
    assert f"rail={seed_mod.SeedError.RAIL_BAD_NAME}" in kw["resources"]


@patch("personalclaw.seed.sel", side_effect=OSError("read-only HOME"))
def test_seed_cmd_safe_audit_swallows_sel_init_failure(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``sel()`` itself raises (e.g. read-only $HOME in sandboxed CI),
    ``_safe_audit`` must swallow the exception so the CLI's exit-code
    contract is preserved. ``SecurityEventLog.__init__`` does filesystem I/O
    which can fail, so the audit call must never break the command."""
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    args = type("Args", (), {"seed": "empty"})()
    # Must NOT raise — the audit failure is swallowed by ``_safe_audit``.
    rc = seed_mod.seed_cmd(args)
    assert rc == seed_mod.EXIT_OK
    # And the actual seed still ran: target was populated.
    assert (target / "fixture.yaml").is_file()


@patch("personalclaw.seed.sel")
def test_seed_cmd_emits_sel_audit_on_copytree_oserror(
    mock_sel: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``shutil.copytree`` raising ``OSError`` emits ``outcome="error"``
    and maps to ``EXIT_IO_ERROR``.

    Pins two behaviors:
    1. An ``OSError`` from ``copytree`` must surface through the
       ``seed: error:`` ASCII-prefix contract and emit a SEL audit, not
       propagate as a raw traceback.
    2. The ``outcome="denied" if code == EXIT_RAIL else "error"`` ternary's
       ``error`` branch is exercised.

    Triggers the failure by patching ``shutil.copytree`` to raise a disk-
    full-style ``OSError`` (pre-creating ``dst`` no longer triggers it, since
    empty-dir targets are
    accepted — and populated targets hit the non-empty rail (``denied``,
    not ``error``).
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    def _raise(*_a, **_kw):
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(seed_mod.shutil, "copytree", _raise)

    args = type("Args", (), {"seed": "empty"})()
    rc = seed_mod.seed_cmd(args)

    assert rc == seed_mod.EXIT_IO_ERROR
    # ``seed: error:`` prefix contract: plain-ASCII, never a traceback.
    err = capsys.readouterr().err
    assert err.startswith("seed: error:"), f"expected ASCII error prefix, got: {err!r}"
    # SEL audit event fires with outcome="error".
    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["outcome"] == "error"
    assert "fixture='empty'" in kw["resources"]
    # Error type is named in the audit so operators can triage without
    # re-running the failure (OSError vs PermissionError vs FileExistsError etc.).
    assert "OSError" in kw["resources"]


@patch("personalclaw.seed.sel", side_effect=OSError("read-only HOME"))
def test_seed_cmd_safe_audit_logs_warning_on_swallowed_failure(
    mock_sel: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_safe_audit`` must log ``.warning`` (not ``.debug``) on failures.

    The ``seed`` subcommand short-circuits in ``cli.py`` BEFORE
    ``logging.basicConfig()`` runs, so a ``.debug`` record is swallowed by
    Python's last-resort handler (WARNING+ only); ``.warning`` survives it and
    reaches stderr, keeping audit failures observable. This asserts a
    WARNING-level record (with ``exc_info=True``) is emitted from
    ``personalclaw.seed`` whenever ``sel()`` raises.
    """
    import logging

    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    args = type("Args", (), {"seed": "empty"})()
    with caplog.at_level(logging.WARNING, logger="personalclaw.seed"):
        rc = seed_mod.seed_cmd(args)

    assert rc == seed_mod.EXIT_OK
    # Exactly one WARNING record from our audit handler.
    audit_records = [
        r for r in caplog.records if r.name == "personalclaw.seed" and r.levelno == logging.WARNING
    ]
    assert len(audit_records) == 1, (
        f"expected exactly one WARNING log from _safe_audit on sel() failure; "
        f"got {len(audit_records)}: {[r.message for r in audit_records]!r}"
    )
    rec = audit_records[0]
    assert "SEL audit emit failed" in rec.message
    # ``exc_info=True`` preserves the traceback so callers see the OSError.
    assert rec.exc_info is not None
    assert rec.exc_info[0] is OSError


# ------------------------------------------------------------------
# Safety rails + --seed-replace.
# ------------------------------------------------------------------


def test_seed_main_home_rail_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``$PERSONALCLAW_HOME=~/.personalclaw`` exits 2 with 'refusing to seed main
    gateway home' message, even when the path doesn't exist yet.

    Monkeypatches ``$HOME`` so ``Path.home() /
    '.personalclaw'`` points into ``tmp_path`` — catching developers who set
    ``PERSONALCLAW_HOME`` to their real main home would be the worst possible
    test failure mode.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    target = fake_home / ".personalclaw"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty")

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert "refusing to seed main gateway home" in str(excinfo.value)
    # Target dir must not be created as a side-effect.
    assert not target.exists()


def test_seed_main_home_rail_refuses_even_with_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--seed-replace`` does NOT override the main-home rail.

    Rail precedence: ``--seed-replace`` on ``$PERSONALCLAW_HOME=~/.personalclaw``
    would silently ``rmtree`` the user's live gateway state.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    target = fake_home / ".personalclaw"
    target.mkdir()
    (target / "real_user_data.txt").write_text("don't delete me")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty", replace=True)

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert "refusing to seed main gateway home" in str(excinfo.value)
    # The main-home rail MUST fire before rmtree runs.
    assert (
        target / "real_user_data.txt"
    ).exists(), "CRITICAL: --seed-replace wiped main gateway home despite rail"
    assert (target / "real_user_data.txt").read_text() == "don't delete me"


def test_seed_main_home_rail_catches_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symlinked ``$PERSONALCLAW_HOME`` resolving to ``~/.personalclaw`` is caught.

    Developer might symlink ``~/dev-home -> ~/.personalclaw``
    and point ``$PERSONALCLAW_HOME`` at the symlink; the resolved comparison
    must still hit the main-home rail.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    real_main = fake_home / ".personalclaw"
    real_main.mkdir()
    (real_main / "user_data.txt").write_text("preserved")
    symlinked_target = fake_home / "dev-home"
    symlinked_target.symlink_to(real_main)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(symlinked_target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty", replace=True)

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert "refusing to seed main gateway home" in str(excinfo.value)
    assert (real_main / "user_data.txt").read_text() == "preserved"


def test_seed_non_empty_rail_refuses_without_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-empty target exits 2 with hint to pass --seed-replace.

    Note the error wording should guide
    the user to the remedy — the mechanical solution is ``--seed-replace`` and
    it's cheap to mention.
    """
    target = tmp_path / "existing"
    target.mkdir()
    (target / "stale.txt").write_text("old content")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty")

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    msg = str(excinfo.value)
    assert "not empty" in msg
    assert "--seed-replace" in msg
    # Pre-existing content must be untouched on the refusal path.
    assert (target / "stale.txt").read_text() == "old content"


def test_seed_non_empty_rail_succeeds_with_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--seed-replace`` on a non-empty target wipes + re-seeds successfully.

    Old content gone, fixture present.
    """
    target = tmp_path / "existing"
    target.mkdir()
    (target / "stale.txt").write_text("old content")
    (target / "subdir").mkdir()
    (target / "subdir" / "deep.txt").write_text("also stale")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    seed_mod.seed("empty", replace=True)

    # Pre-existing content gone.
    assert not (target / "stale.txt").exists()
    assert not (target / "subdir").exists()
    # Fixture content present.
    assert (target / "fixture.yaml").is_file()
    assert (target / "fixture.yaml").read_text().strip() == ("schema-version: 2026-04-28")


def test_seed_replace_refuses_symlinked_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--seed-replace`` on a symlinked $PERSONALCLAW_HOME refuses, preventing
    rmtree from following the link and deleting the link target.

    Symlinks already resolve in the main-home rail path; this guards the
    case where $PERSONALCLAW_HOME symlinks to a non-main-home directory.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "precious.txt").write_text("must survive")
    link = tmp_path / "link"
    link.symlink_to(real_dir)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(link))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty", replace=True)

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert "symlinked" in str(excinfo.value)
    # Link target must be untouched.
    assert (real_dir / "precious.txt").read_text() == "must survive"


def test_seed_empty_existing_dir_succeeds_without_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty pre-existing $PERSONALCLAW_HOME is accepted (no --seed-replace needed).

    An empty-directory target is accepted (only non-empty targets require
    ``--seed-replace``); this catches a future change that tightens the check
    back to "must not exist".
    """
    target = tmp_path / "empty_preexisting"
    target.mkdir()  # exists, but empty
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    seed_mod.seed("empty")

    assert (target / "fixture.yaml").is_file()


@patch("personalclaw.seed.sel")
def test_seed_cmd_replace_flag_threaded(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``args.seed_replace`` is forwarded to ``seed()`` and logged in audit.

    Pin the CLI wiring so a future refactor can't silently drop the flag.
    """
    target = tmp_path / "existing"
    target.mkdir()
    (target / "junk").write_text("stale")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    args = type("Args", (), {"seed": "empty", "seed_replace": True})()
    assert seed_mod.seed_cmd(args) == seed_mod.EXIT_OK

    # Audit event records replace=True so an SOC reviewing SEL events can
    # see whether a dev tool invocation wiped data.
    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["outcome"] == "allowed"
    assert "replace=True" in kw["resources"]
    # Actual seed happened.
    assert (target / "fixture.yaml").is_file()
    assert not (target / "junk").exists()


@patch("personalclaw.seed.sel")
def test_seed_cmd_missing_seed_replace_attr_defaults_false(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``seed_cmd`` tolerates an args Namespace without ``seed_replace``.

    Defensive: ``getattr(args, "seed_replace", False)`` means old callers that
    don't wire the flag still work. Regression guard in case someone
    refactors to ``args.seed_replace`` direct-access.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    # Intentionally omit ``seed_replace`` from the namespace.
    args = type("Args", (), {"seed": "empty"})()
    assert seed_mod.seed_cmd(args) == seed_mod.EXIT_OK
    assert (target / "fixture.yaml").is_file()
    kw = mock_sel().log_api_access.call_args.kwargs
    assert "replace=False" in kw["resources"]


# ------------------------------------------------------------------
# ------------------------------------------------------------------


def test_seed_resolve_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve()`` raising ``OSError`` in the main-home check denies, not allows.

    A failed resolve must not fall back to the unresolved path (which would
    bypass the main-home rail and, with ``--seed-replace``, risk wiping live
    gateway state on a broken-symlink ``$PERSONALCLAW_HOME``). The test patches
    ``Path.resolve`` to raise inside the main-home check; it must surface as
    ``SeedError(rail=resolve_failed)``.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    original_resolve = Path.resolve

    def _selective_raise(self, *args, **kwargs):
        # Only fail on the for_main_home_check resolve — leave other
        # resolves alone (fixtures-root resolution, etc.) so the test
        # isolates the exact branch under regression.
        if str(self) == str(target):
            raise OSError("[Errno 40] Too many levels of symbolic links")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _selective_raise)

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty", replace=True)

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert excinfo.value.rail == seed_mod.SeedError.RAIL_RESOLVE_FAILED
    assert "cannot resolve $PERSONALCLAW_HOME" in str(excinfo.value)
    # Critical: target must NOT have been wiped or created by the bypass.
    assert not target.exists()


def test_seed_empty_string_fixture_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--seed ""`` (empty fixture name) rejected with RAIL_BAD_NAME.

    The CLI must dispatch on ``args.seed is not None`` (not truthiness, which is
    falsy for ``""``) so ``--seed ""`` routes to seeding and is then rejected by
    the empty-name rail in ``_resolve_fixture`` rather than silently starting the
    gateway unseeded. This pins both halves.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("")

    assert excinfo.value.code == seed_mod.EXIT_RAIL
    assert excinfo.value.rail == seed_mod.SeedError.RAIL_BAD_NAME


@patch("personalclaw.seed.sel")
def test_seed_cmd_empty_seed_routes_to_seed_cmd(
    mock_sel: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``seed_cmd`` with ``args.seed=""`` returns EXIT_RAIL via the empty-name
    rail, proving the CLI dispatch path reaches it instead of silently
    short-circuiting on truthiness.

    This exercises the end-to-end empty-name path.
    """
    target = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    args = type("Args", (), {"seed": ""})()
    rc = seed_mod.seed_cmd(args)

    assert rc == seed_mod.EXIT_RAIL
    # Audit stream records the bad-name rail, not a swallowed no-op.
    kw = mock_sel().log_api_access.call_args.kwargs
    assert kw["outcome"] == "denied"
    assert f"rail={seed_mod.SeedError.RAIL_BAD_NAME}" in kw["resources"]


@pytest.mark.parametrize(
    "setup,expected_rail,replace",
    [
        # (setup_fn, expected rail, replace flag)
        ("main_home", "main_home", False),
        ("non_empty", "non_empty", False),
        ("symlinked_target", "symlink_replace", True),
    ],
)
def test_seed_audit_uses_rail_tag_not_raw_path(
    setup: str,
    expected_rail: str,
    replace: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rail-tagged ``SeedError`` audit path must log ``rail=<constant>``
    instead of the exception message (which embeds resolved paths).

    Logging ``reason={exc}`` would leak ``$HOME``-derived paths into the SEL
    stream on every denial, contradicting the presence-only design of the
    success/error paths. Asserts BOTH: (a) ``rail=<short-constant>`` is present,
    (b) the resolved target path is NOT present in the audit resources string.
    """
    # Patch ``sel`` LOCALLY per test so parametrize doesn't collide with
    # the @patch decorator's call-count tracking.
    with patch("personalclaw.seed.sel") as mock_sel:
        if setup == "main_home":
            fake_home = tmp_path / "fake_home"
            fake_home.mkdir()
            target = fake_home / ".personalclaw"
            target.mkdir()
            monkeypatch.setenv("HOME", str(fake_home))
            monkeypatch.setenv("PERSONALCLAW_HOME", str(target))
        elif setup == "non_empty":
            target = tmp_path / "stuffed"
            target.mkdir()
            (target / "stale.txt").write_text("old")
            monkeypatch.setenv("PERSONALCLAW_HOME", str(target))
        else:  # symlinked_target
            real = tmp_path / "real"
            real.mkdir()
            (real / "precious.txt").write_text("keep")
            target = tmp_path / "link"
            target.symlink_to(real)
            monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

        args = type(
            "Args",
            (),
            {"seed": "empty", "seed_replace": replace},
        )()
        assert seed_mod.seed_cmd(args) == seed_mod.EXIT_RAIL

        kw = mock_sel().log_api_access.call_args.kwargs
        # Rail constant present.
        assert (
            f"rail={expected_rail}" in kw["resources"]
        ), f"expected rail={expected_rail} in audit, got: {kw['resources']!r}"
        # Resolved target path must NOT leak into the audit stream —
        # that's the point of the rail-tag refactor.
        resolved = (
            target.resolve(strict=False) if target.exists() or target.is_symlink() else target
        )
        assert (
            str(resolved) not in kw["resources"]
        ), f"audit leaked resolved path {resolved!r}: {kw['resources']!r}"
