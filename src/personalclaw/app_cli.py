"""App-contributed CLI seams — the runners behind ``cli.setup`` / ``cli.doctor``.

Plan 32 (Provider-Boundary Completion) lets an installed app hook into the two
core CLI commands without living in core:

- ``run_app_setup_steps`` — called by ``personalclaw setup`` AFTER the core steps.
  For each installed + enabled app whose manifest declares ``cli.setup``
  (``"module:function"``), it imports the function from the app's own dir and
  calls it with a :class:`personalclaw.sdk.cli.SetupContext`. A failing step
  prints a warning and setup continues — one broken app never aborts the wizard.

- ``run_app_doctor_probes`` — called by ``personalclaw doctor``. For each such
  app declaring ``cli.doctor``, it imports + calls the probe with a hard timeout
  and exception guard, expecting a ``list[DoctorLine]``, and renders a per-app
  section. A hung/raising probe becomes one ``fail`` line — doctor never hangs.

The app's module is loaded from its own dir under a namespaced module name
(mirroring ``providers.loader._load_ext_module``) so two apps that both ship a
``cli_setup.py`` cannot collide in ``sys.modules``. Executing an app's declared
setup/doctor code at the user's explicit request is within the existing trust
model — the app already passed the install-time supply-chain scan.
"""

import importlib.util
import logging
import threading
from typing import Any, Callable

from personalclaw.apps.manager import app_dir, list_apps
from personalclaw.sdk.cli import DoctorLine, SetupContext

logger = logging.getLogger(__name__)

_DOCTOR_TIMEOUT_SECS = 5.0

# Glyph per DoctorLine.status — the render buckets doctor shows.
_STATUS_GLYPH = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "info": "ℹ️ "}


def _enabled_apps_with(field: str) -> list[tuple[str, str]]:
    """(app_name, "module:function") for every installed + enabled app whose
    manifest declares ``cli.<field>``, sorted by app name (deterministic order)."""
    out: list[tuple[str, str]] = []
    for app in list_apps():
        if not app.get("enabled", True):
            continue
        cli = (app.get("manifest") or {}).get("cli") or {}
        ref = str(cli.get(field) or "").strip()
        if ref:
            out.append((str(app.get("name", "")), ref))
    out.sort(key=lambda t: t[0])
    return out


def _import_app_callable(app_name: str, ref: str) -> Callable[..., Any]:
    """Import ``module:function`` from the installed app's own dir under a
    namespaced module name so two apps sharing a module filename can't collide.

    Raises on a malformed ref, a missing file, or a missing attribute — the
    caller turns that into a warning (setup) or a fail line (doctor)."""
    if ":" not in ref:
        raise ValueError(f"cli entry {ref!r} must be 'module:function'")
    module_path, _, func_name = ref.partition(":")
    module_path, func_name = module_path.strip(), func_name.strip()
    if not module_path or not func_name:
        raise ValueError(f"cli entry {ref!r} must be 'module:function'")
    base = app_dir(app_name)
    file_path = base / (module_path.replace(".", "/") + ".py")
    if not file_path.is_file():
        raise FileNotFoundError(f"{file_path} not found for app {app_name!r}")
    mod_name = f"_pclaw_app_{app_name.replace('-', '_')}__{module_path.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise AttributeError(f"{func_name!r} not found in {module_path} for app {app_name!r}")
    return fn


def run_app_setup_steps(only_app: str = "") -> None:
    """Run each installed + enabled app's ``cli.setup`` step (alphabetical).

    ``only_app`` restricts the run to that one app (``personalclaw setup --app``).
    A step that raises prints ``⚠️ <app>: <err>`` and setup continues.
    """
    from personalclaw.config.loader import env_path, save_credential
    from personalclaw.providers.settings import ProviderSettings
    from personalclaw.sel import sel

    steps = _enabled_apps_with("setup")
    if only_app:
        steps = [(n, r) for (n, r) in steps if n == only_app]
        if not steps:
            print(f"  ⚠️  No installed+enabled app named {only_app!r} declares a cli.setup step.")
            return

    def _get_credential(key: str) -> str:
        ep = env_path()
        if not ep.exists():
            return ""
        for line in ep.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
        return ""

    def _safe_input(prompt: str) -> str:
        """Prompt, but return "" on a non-interactive run (closed/empty stdin)
        instead of raising EOFError — the SetupContext contract says a setup step
        must treat empty as "skip / keep", so a headless `personalclaw setup` never
        crashes an app's step."""
        try:
            return input(prompt)
        except EOFError:
            print()  # close the dangling prompt line
            return ""

    for app_name, ref in steps:
        try:
            fn = _import_app_callable(app_name, ref)
        except Exception as exc:  # noqa: BLE001 — one bad app must not abort setup
            print(f"  ⚠️  {app_name}: setup step unavailable — {exc}")
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="error",
                source="cli",
                error=str(exc),
            )
            continue
        ctx = SetupContext(
            app_name=app_name,
            get_credential=_get_credential,
            save_credential=save_credential,
            settings=ProviderSettings,
            input=_safe_input,
        )
        try:
            fn(ctx)
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="completed",
                source="cli",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  {app_name}: setup step failed — {exc}")
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="error",
                source="cli",
                error=str(exc),
            )


def _run_probe_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """Call ``fn()`` on a daemon thread, returning its result or raising
    ``TimeoutError`` after ``timeout`` seconds (a hung probe never wedges doctor).
    A thread-based timeout (not signal.alarm) works off the main thread too."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"probe exceeded {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_app_doctor_probes() -> list[str]:
    """Render a per-app doctor section for each installed + enabled app with a
    ``cli.doctor`` probe. Returns a list of issue strings (fail lines) for the
    caller's summary. A timeout/exception becomes one ``fail`` line — never hangs.
    """
    issues: list[str] = []
    for app_name, ref in _enabled_apps_with("doctor"):
        print(f"\n{app_name}")
        try:
            fn = _import_app_callable(app_name, ref)
            lines = _run_probe_with_timeout(lambda: fn(), _DOCTOR_TIMEOUT_SECS)
        except Exception as exc:  # noqa: BLE001
            print(f"  {_STATUS_GLYPH['fail']} probe error: {exc}")
            issues.append(f"{app_name} doctor probe error")
            continue
        if not isinstance(lines, list):
            print(
                f"  {_STATUS_GLYPH['fail']} probe returned {type(lines).__name__}, expected list[DoctorLine]"
            )
            issues.append(f"{app_name} doctor probe malformed")
            continue
        for ln in lines:
            if not isinstance(ln, DoctorLine):
                continue
            glyph = _STATUS_GLYPH.get(ln.status, "•")
            detail = f"  {ln.detail}" if ln.detail else ""
            print(f"  {glyph} {ln.label}{detail}")
            if ln.status == "fail":
                issues.append(f"{app_name}: {ln.label}")
    return issues
