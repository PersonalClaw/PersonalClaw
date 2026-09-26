"""App-contributed CLI seams — the runners behind ``cli.setup`` / ``cli.doctor``.

Plan 32 (Provider-Boundary Completion) lets an installed app hook into the two
core CLI commands without living in core:

- ``run_app_setup_steps`` — called by ``personalclaw setup`` AFTER the core steps.
  For each installed + enabled app whose manifest declares ``cli.setup``
  (``"module:function"``), it imports the function from the app's own dir and
  calls it with a :class:`personalclaw.sdk.cli.SetupContext`. A failing step
  prints a warning and setup continues — one broken app never aborts the wizard —
  and the step is returned, so the command can exit non-zero naming it.

- ``run_app_doctor_probes`` — called by ``personalclaw doctor``. For each such
  app declaring ``cli.doctor``, it imports + calls the probe with a hard timeout
  and exception guard, expecting a ``list[DoctorLine]``, and renders a per-app
  section. A hung/raising probe becomes one ``fail`` line — doctor never hangs.

The app's module is loaded the way the gateway loads an app's provider module
(``apps.native_contract.load_bundle_module``): from its own dir, under a namespaced
module name so two apps that both ship a ``cli_setup.py`` cannot collide in
``sys.modules``, with the app's directory on ``sys.path`` while it imports AND while
the step runs, so a step that imports its own package works here exactly as it does in
the gateway. Executing an app's declared setup/doctor code at the user's explicit
request is within the existing trust model — the app already passed the install-time
supply-chain scan.
"""

import logging
import threading
from typing import Any, Callable

from personalclaw.apps.manager import app_dir, list_apps
from personalclaw.apps.native_contract import app_dir_on_path, load_bundle_module
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
    """Import ``module:function`` from the installed app's own dir.

    Raises on a malformed ref, a missing file, a module that fails to import, or a
    missing attribute — the caller turns that into a warning (setup) or a fail line
    (doctor)."""
    if ":" not in ref:
        raise ValueError(f"cli entry {ref!r} must be 'module:function'")
    module_path, _, func_name = ref.partition(":")
    module_path, func_name = module_path.strip(), func_name.strip()
    if not module_path or not func_name:
        raise ValueError(f"cli entry {ref!r} must be 'module:function'")
    module = load_bundle_module(app_dir(app_name), app_name, module_path)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise AttributeError(f"{func_name!r} not found in {module_path} for app {app_name!r}")
    return fn


def _reason(exc: BaseException) -> str:
    """``ModuleNotFoundError: No module named 'x'`` — the class is half of the reason."""
    return f"{type(exc).__name__}: {exc}"


def _scoped_delete_credential(app_name: str) -> Callable[[str], bool]:
    """``SetupContext.delete_credential`` for one app's setup step.

    A setup step needs it to clear what an earlier release of the same step saved under a
    plain, unowned name (a channel token under its bare variable name), which no uninstall can
    attribute to the app. It may not delete a key a settings record OWNS (``PCSECRET_…``):
    that key is managed through its setting, and removing it from under the record would leave
    the setting pointing at nothing — possibly another app's. Every delete is audited by name;
    no value is read.
    """
    from personalclaw.config.credentials import delete_credential, is_owned_key
    from personalclaw.sel import sel

    def _delete(key: str) -> bool:
        if is_owned_key(key):
            raise ValueError(
                f"{key} is owned by a settings record; clear that setting instead of deleting "
                "the credential"
            )
        removed = delete_credential(key)
        sel().log_api_access(
            caller="cli:setup",
            operation=f"app_cli_setup:{app_name}:delete_credential",
            outcome="removed" if removed else "absent",
            source="cli",
            resources=key,
        )
        return removed

    return _delete


def run_app_setup_steps(only_app: str = "") -> list[str]:
    """Run each installed + enabled app's ``cli.setup`` step (alphabetical).

    ``only_app`` restricts the run to that one app (``personalclaw setup --app``).
    A step that cannot be loaded or that raises prints ``⚠️ <app>: <why>`` and setup
    continues. Returns one ``"<app>: <why>"`` line per step that did not complete — and
    one for an ``only_app`` that declares no step — so the command can exit non-zero.
    """
    from personalclaw.config.credentials import get_credential, save_credential
    from personalclaw.providers.settings import ProviderSettings
    from personalclaw.sel import sel

    steps = _enabled_apps_with("setup")
    if only_app:
        steps = [(n, r) for (n, r) in steps if n == only_app]
        if not steps:
            why = f"no installed+enabled app named {only_app!r} declares a cli.setup step"
            print(f"  ⚠️  {why[0].upper()}{why[1:]}.")
            return [f"{only_app}: {why}"]

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

    failures: list[str] = []
    for app_name, ref in steps:
        base = app_dir(app_name)
        try:
            fn = _import_app_callable(app_name, ref)
        except Exception as exc:  # noqa: BLE001 — one bad app must not abort setup
            why = f"setup step unavailable — {_reason(exc)}"
            print(f"  ⚠️  {app_name}: {why}")
            failures.append(f"{app_name}: {why}")
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="error",
                source="cli",
                error=_reason(exc),
            )
            continue
        ctx = SetupContext(
            app_name=app_name,
            get_credential=get_credential,
            save_credential=save_credential,
            settings=ProviderSettings,
            input=_safe_input,
            delete_credential=_scoped_delete_credential(app_name),
        )
        try:
            with app_dir_on_path(base):
                fn(ctx)
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="completed",
                source="cli",
            )
        except Exception as exc:  # noqa: BLE001
            why = f"setup step failed — {_reason(exc)}"
            print(f"  ⚠️  {app_name}: {why}")
            failures.append(f"{app_name}: {why}")
            sel().log_api_access(
                caller="cli:setup",
                operation=f"app_cli_setup:{app_name}",
                outcome="error",
                source="cli",
                error=_reason(exc),
            )
    return failures


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
            with app_dir_on_path(app_dir(app_name)):
                lines = _run_probe_with_timeout(lambda: fn(), _DOCTOR_TIMEOUT_SECS)
        except Exception as exc:  # noqa: BLE001
            print(f"  {_STATUS_GLYPH['fail']} probe error: {_reason(exc)}")
            issues.append(f"{app_name} doctor probe error")
            continue
        if not isinstance(lines, list):
            print(
                f"  {_STATUS_GLYPH['fail']} probe returned {type(lines).__name__}, expected list[DoctorLine]"  # noqa: E501
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
