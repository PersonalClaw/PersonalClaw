"""``personalclaw seed`` — copy a hand-authored fixture into ``$PERSONALCLAW_HOME``.

Fixtures ship as package data at ``personalclaw/tests_fixtures/<name>/``.
Each fixture is a valid ``PERSONALCLAW_HOME`` tree with a ``fixture.yaml``
declaring ``schema-version``. Shipping inside the package is what lets
``personalclaw seed`` work after the package is installed into
site-packages (a source-tree-relative path walk would not find
``<repo>/tests/fixtures/`` from there).
"""

import logging
import os
import shutil
import sys
from pathlib import Path

try:
    from importlib.resources import files as _resource_files
except ImportError:  # pragma: no cover — PersonalClaw targets py3.12+.
    _resource_files = None  # type: ignore[assignment]

# ``personalclaw.sel`` is a first-party module shipped in the same package, so
# we import it at top level like every other caller (``subagent.py``, ...).
# An ImportError here would mean the PersonalClaw
# install itself is broken — there's no scenario where it's optional.
# ``_safe_audit`` still handles *runtime* SEL failures (read-only
# ``$HOME``, HMAC-key write failure) via its broad except.
from personalclaw.sel import sel

# Exit codes: 0 success, 1 I/O error, 2 rail violation (rejected input).
EXIT_OK = 0
EXIT_IO_ERROR = 1
EXIT_RAIL = 2

# Name of the package-data directory that holds the shipped fixtures.
_FIXTURES_PKG = "tests_fixtures"


class SeedError(Exception):
    """Rail violation or lookup failure. ``code`` is the intended exit code.

    ``rail`` is a short code-controlled discriminator used in SEL audit
    events (see ``_safe_audit`` in ``seed_cmd``). Keeping it separate from
    the human-readable message keeps the audit stream path-free AND
    SOC-meaningful — ``type(exc).__name__`` would always be ``"SeedError"``
    and the message contains resolved filesystem paths we must NOT leak.
    """

    # Rail constants. Used in audit ``resources=... reason=<rail>`` strings,
    # so any change here has an audit-schema impact — prefer additive.
    RAIL_UNSET_HOME = "unset_home"
    RAIL_MAIN_HOME = "main_home"
    RAIL_NON_EMPTY = "non_empty"
    RAIL_SYMLINK_REPLACE = "symlink_replace"
    RAIL_BAD_NAME = "bad_name"
    RAIL_UNKNOWN_FIXTURE = "unknown_fixture"
    RAIL_ROOT_ESCAPE = "root_escape"
    RAIL_RESOLVE_FAILED = "resolve_failed"

    def __init__(
        self,
        message: str,
        *,
        code: int = EXIT_RAIL,
        rail: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.rail = rail


def _fixtures_root() -> Path:
    """Locate the checked-in fixtures tree.

    Uses ``importlib.resources`` so the path works for source-checkout
    workspaces (``personalclaw/tests_fixtures/``) and for installed wheels
    where the package lives under ``site-packages/personalclaw/``. ``files()``
    returns a ``Traversable`` which, for the filesystem loader PersonalClaw
    uses, is a real ``PosixPath`` — no ``as_file`` context-manager
    gymnastics needed.
    """
    if _resource_files is None:  # pragma: no cover — see import guard above.
        raise SeedError(
            "importlib.resources unavailable — need Python >= 3.9",
            rail=SeedError.RAIL_RESOLVE_FAILED,
        )
    return Path(str(_resource_files("personalclaw") / _FIXTURES_PKG))


def _resolve_fixture(name: str) -> Path:
    """Return the path to fixture ``name``, or raise ``SeedError``.

    Rejects path-traversal attempts (``--fixture ../../.ssh``) before
    ``shutil.copytree`` ever sees the path. Multiple gates are needed because
    each alone is bypassable:

    - Empty or ``.``-only names resolve to the fixtures root itself — would
      copy the whole ``tests_fixtures/`` tree into ``$PERSONALCLAW_HOME``.
    - Path-separator / ``..`` character checks catch the obvious traversal
      cases (``../../.ssh``, ``foo/bar``).
    - A final ``relative_to`` containment check catches symlinks inside the
      fixtures tree that resolve outside it (e.g.
      ``tests_fixtures/foo -> ../../.ssh``).
    - A post-resolve ``candidate != root`` check catches the edge case
      where all character-level gates pass but the resolved path still
      picks the root itself — reachable only through a self-symlink inside
      ``tests_fixtures/`` (e.g. ``tests_fixtures/self -> .``); strings
      like ``"./"`` are caught earlier at the empty-or-root gate.
    - NUL-byte / control-char rejection at the empty-or-root gate: a name
      like ``"foo\x00bar"`` would otherwise let ``(root / name).resolve()``
      raise a bare ``ValueError``, bypassing both the plain-ASCII
      ``seed: error:`` contract AND the SEL audit event in ``seed_cmd``.
    """
    if name in ("", ".", "./") or any(ord(c) < 0x20 for c in name):
        raise SeedError(
            f"fixture name is empty or refers to the root: {name!r}",
            rail=SeedError.RAIL_BAD_NAME,
        )
    if "/" in name or "\\" in name or ".." in Path(name).parts:
        raise SeedError(
            f"fixture name has path separators or '..': {name!r}",
            rail=SeedError.RAIL_BAD_NAME,
        )
    root = _fixtures_root()
    candidate = (root / name).resolve()
    if not candidate.is_dir():
        # List the available fixtures so the user doesn't have to go read
        # ``tests_fixtures/``. Sorted for stable test assertions and a
        # predictable display order.
        available = sorted(
            p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        available_str = ", ".join(available) if available else "(none)"
        raise SeedError(
            f"unknown fixture: {name!r}. Available fixtures: {available_str}.",
            rail=SeedError.RAIL_UNKNOWN_FIXTURE,
        )
    resolved_root = root.resolve()
    if candidate == resolved_root:
        raise SeedError(
            f"fixture name resolves to fixtures root: {name!r}",
            rail=SeedError.RAIL_BAD_NAME,
        )
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise SeedError(
            f"fixture name escapes fixtures root: {name!r}",
            rail=SeedError.RAIL_ROOT_ESCAPE,
        ) from exc
    return candidate


def _main_home() -> Path:
    """Return the resolved path to the main gateway home (``~/.personalclaw``).

    ``Path.home() / ".personalclaw"``, then ``expanduser().resolve()`` to
    collapse any symlinks along the way. This is the path we refuse to
    seed into under any circumstance — even with ``--seed-replace`` — because
    clobbering the dev's live gateway state is the single most destructive
    outcome this tool could produce.

    Extracted as a helper so tests can monkeypatch ``Path.home()`` via
    ``$HOME`` and exercise the rail on synthetic ``~/.personalclaw`` paths.
    """
    return (Path.home() / ".personalclaw").expanduser().resolve()


def _resolve_target(*, for_main_home_check: bool = False) -> Path:
    """Return ``$PERSONALCLAW_HOME`` as a ``Path``, or raise ``SeedError``.

    ``expanduser()`` is applied so ``PERSONALCLAW_HOME=~/dev`` works. Pass
    ``for_main_home_check=True`` to additionally ``resolve()`` the path so
    a symlinked ``PERSONALCLAW_HOME`` pointing at ``~/.personalclaw`` is caught by
    the main-home rail. The unresolved form is used for ``copytree`` /
    ``rmtree`` because a non-existent target is valid input to those.
    """
    raw = os.environ.get("PERSONALCLAW_HOME")
    if not raw:
        raise SeedError(
            "$PERSONALCLAW_HOME is not set. Point it at a dev directory "
            "(e.g. PERSONALCLAW_HOME=~/.personalclaw-dev personalclaw gateway --seed empty).",
            rail=SeedError.RAIL_UNSET_HOME,
        )
    target = Path(raw).expanduser()
    if for_main_home_check:
        # ``resolve(strict=False)`` tolerates non-existent targets while
        # still collapsing any symlinks that DO exist along the way. That
        # catches ``$PERSONALCLAW_HOME -> ~/.personalclaw`` even when the symlink
        # target doesn't exist yet on some platforms.
        #
        # On resolution failure (broken symlink chain, permission error,
        # exotic cross-mount issues) we MUST fail closed — falling back
        # to the unresolved path would silently bypass the main-home
        # rail: if the unresolved path is actually a symlink to
        # ``~/.personalclaw`` that ``resolve()`` couldn't evaluate, the
        # rail comparison won't match, and ``--seed-replace`` would
        # then ``rmtree`` the dev's live gateway home. That's the one
        # outcome this tool must never produce.
        try:
            target = target.resolve(strict=False)
        except OSError as exc:
            raise SeedError(
                f"cannot resolve $PERSONALCLAW_HOME ({raw!r}) for main-home "
                f"safety check — refusing to proceed: {exc}",
                rail=SeedError.RAIL_RESOLVE_FAILED,
            ) from exc
    return target


def seed(fixture_name: str, *, replace: bool = False) -> None:
    """Copy fixture ``fixture_name`` into ``$PERSONALCLAW_HOME``.

    Raises ``SeedError`` on rail violations. Enforces, in order:

    1. **Main-home rail** — refuses when ``$PERSONALCLAW_HOME`` resolves to
       ``~/.personalclaw`` (the dev's live gateway home). This rail is
       ABSOLUTE: ``replace=True`` does NOT override it. Clobbering the
       main gateway is the one outcome we never want to enable.
    2. **Non-empty rail** — refuses when the target exists and contains
       anything, unless ``replace=True``. With ``replace=True``, the
       shutil.rmtree the target first, then copytree. The sequence is not
       atomic — documented in this docstring — but that's acceptable for a
       dev tool. The caller can re-run with ``--seed-replace`` to clean up.

    ``symlinks`` is left at the ``shutil.copytree`` default (``False``) so
    symlinks inside fixtures are followed. No shipped fixtures contain
    symlinks.
    """
    src = _resolve_fixture(fixture_name)
    # Resolve twice: once symlink-collapsed for the main-home comparison,
    # once raw for the actual copy/rmtree operations (those tolerate
    # non-existent targets and we want to pass the user-provided path).
    dst_resolved = _resolve_target(for_main_home_check=True)
    if dst_resolved == _main_home():
        raise SeedError(
            f"refusing to seed main gateway home: {dst_resolved}. "
            "Point $PERSONALCLAW_HOME at a separate dev directory "
            "(e.g. ~/.personalclaw-dev).",
            rail=SeedError.RAIL_MAIN_HOME,
        )
    dst = _resolve_target()

    if dst.exists() and any(dst.iterdir()):
        if not replace:
            raise SeedError(
                f"$PERSONALCLAW_HOME is not empty: {dst}. "
                "Pass --seed-replace to wipe it and re-seed.",
                rail=SeedError.RAIL_NON_EMPTY,
            )
        # ``rmtree`` on a symlink would follow the link and delete its target.
        # Guard against that — if the user passed a symlinked target, refuse
        # --seed-replace and ask for a real directory.
        if dst.is_symlink():
            raise SeedError(
                f"refusing to --seed-replace a symlinked $PERSONALCLAW_HOME: {dst}. "
                "Point it at a real directory.",
                rail=SeedError.RAIL_SYMLINK_REPLACE,
            )
        shutil.rmtree(dst)
    elif dst.exists() and not dst.is_symlink():
        # Empty-but-existing dst is accepted (only non-empty needs
        # --seed-replace). ``shutil.copytree`` refuses ANY pre-existing dst, so
        # we rmdir first. Symlinked-but-empty-target case falls through to
        # the copytree below which will follow the link for the check and
        # then copytree will raise FileExistsError — caught as EXIT_IO_ERROR.
        dst.rmdir()

    shutil.copytree(src, dst)


def seed_cmd(args) -> int:  # noqa: ANN001 — argparse.Namespace at call site
    """CLI entry point — catches ``SeedError`` and maps to exit codes.

    Error prefix is plain ASCII ``seed: error:`` so CI terminals without
    a UTF-8 locale don't swallow the message.

    Emits a SEL audit event on every invocation (allowed / denied / error)
    per the repo's security-controls guideline — ``seed`` writes an entire
    directory tree into ``$PERSONALCLAW_HOME`` and ``--seed-replace`` will
    ``rmtree`` first, so an audit trail is required.
    """
    fixture = args.seed  # required when gateway --seed was passed
    # ``--seed-replace`` on the CLI becomes ``args.seed_replace`` via argparse's
    # dash-to-underscore conversion. Kept as plain ``replace=`` inside the
    # module because the kwarg is module-internal (no flag collision risk
    # at the Python call site).
    replace = bool(getattr(args, "seed_replace", False))
    # Capture invocation-time env state BEFORE ``seed()`` runs — otherwise
    # this flag is always ``True`` on the success path (if ``PERSONALCLAW_HOME``
    # were unset, ``_resolve_target()`` would have raised ``SeedError`` and
    # we'd be in the ``except SeedError`` branch, not here).  Capturing up
    # front keeps the flag meaningful.
    target_set = "PERSONALCLAW_HOME" in os.environ
    try:
        seed(fixture, replace=replace)
    except SeedError as exc:
        print(f"seed: error: {exc}", file=sys.stderr)
        # EXIT_RAIL = user-input rejection ("denied"). SeedError with any
        # other code maps to "error".
        # Log ``exc.rail`` (short code-controlled constant) rather than
        # ``{exc}`` — the message text can contain resolved filesystem
        # paths we must NOT leak to the SEL stream. ``target_set`` is
        # still presence-only. See class docstring on SeedError for the
        # full list of rail values an SOC analyst might see.
        _safe_audit(
            outcome="denied" if exc.code == EXIT_RAIL else "error",
            resources=f"fixture={fixture!r} replace={replace} rail={exc.rail}",
        )
        return exc.code
    except OSError as exc:
        # ``shutil.copytree`` raises ``FileExistsError`` when ``dst`` exists
        # (without --seed-replace; with --seed-replace we rmtree first so this
        # path is reached for disk-full / permission / similar OSErrors only).
        # Map the raw OSError to EXIT_IO_ERROR so the ``seed: error:`` prefix
        # + SEL audit contracts are preserved instead of leaking a traceback.
        #
        # Log only the exception TYPE in the audit stream (not ``{exc}``):
        # ``FileExistsError``'s string representation includes the full
        # target path (e.g. ``[Errno 17] File exists: '/home/user/dev'``)
        # which would defeat the ``target_set`` presence-only design below.
        # The user still sees the full detail on stderr for debuggability.
        print(f"seed: error: {exc}", file=sys.stderr)
        _safe_audit(
            outcome="error",
            resources=f"fixture={fixture!r} replace={replace} reason={type(exc).__name__}",
        )
        return EXIT_IO_ERROR
    _safe_audit(
        outcome="allowed",
        # ``{fixture!r}`` escapes control chars so a crafted name like
        # ``"foo\n<forged-event>"`` can't inject fake rows into the SEL
        # JSONL log (and ``sel.py`` json.dumps-escapes again downstream).
        # ``target_set`` records presence-only (captured pre-``seed()``) —
        # never the raw path, which would leak ``$HOME``-derived info.
        # ``replace`` records whether the rmtree path was taken.
        resources=(
            f"fixture={fixture!r} target_set={target_set} replace={replace}"
        ),
    )
    return EXIT_OK


def _safe_audit(*, outcome: str, resources: str) -> None:
    """Emit a SEL audit event; swallow any failure.

    SEL singleton init (``SecurityEventLog.__init__``) calls
    ``self._dir.mkdir(...)`` and ``_load_or_create_hmac_key()`` which can
    raise ``OSError`` / ``PermissionError`` in sandboxed CI accounts or
    read-only ``$HOME`` scenarios. ``seed_cmd``'s contract is "plain
    ASCII ``seed: error:`` prefix on every failure, clean exit code" —
    audit emission must never change user-visible exit behavior.

    Pattern matches ``dashboard/chat.py``'s forward-callback handling
    (``except Exception: logger.warning(...)``). Using ``.warning`` (not
    ``.debug``) is deliberate: the ``seed`` subcommand short-circuits in
    ``cli.py`` before ``logging.basicConfig()`` runs, so a ``.debug``
    call would be silently dropped by Python's last-resort handler
    (WARNING+ only). ``.warning`` survives the last-resort handler and
    emits one line to stderr on the rare path where SEL init fails, so the
    audit failure stays observable. SEL init failures are rare (once per
    read-only $HOME install), so the stderr line is not spammy.
    """
    # ``sel`` is imported at module level.  If SEL init itself fails at call
    # time (``SecurityEventLog.__init__`` does ``mkdir`` + HMAC-key load in
    # read-only $HOME / sandboxed CI), the broad ``except`` below catches it
    # and logs a WARNING — we never let the tool crash just because the
    # audit sink is unavailable.
    try:
        sel().log_api_access(
            caller="cli",
            operation="seed",
            outcome=outcome,
            source="cli",
            resources=resources,
        )
    except Exception:  # noqa: BLE001 — audit must never fail the tool.
        logging.getLogger(__name__).warning(
            "seed: SEL audit emit failed", exc_info=True
        )
