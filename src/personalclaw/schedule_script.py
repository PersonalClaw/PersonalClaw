"""Zero-token Schedule execution modes — Python ``script`` and shell ``command``.

These are LLM-free execution strategies of the Schedule entity: a job may run a
Python callable (``script``) or a shell string (``command``) deterministically
in the shared sandbox (``sandbox.wrap_argv``) at zero token cost — no model
invocation, no ACP turn.

Script authors get a small control-flow API via exceptions — ``Skip`` (do
nothing this tick), ``Done(msg)`` (deliver and remove the job), ``Report(msg)``
(deliver and keep the job) — and a ``ScriptContext`` with ``.notify(...)`` and
``.call_tool(...)``. Crucially, ``call_tool`` routes through PersonalClaw's
**Tool entity** (the ``POST /api/tools/invoke`` gateway route → the tool
provider registry), NOT a bespoke MCP client — so a script gets the same
MCP+native tool surface the agent has.

Layering: the parent process (gateway) calls :func:`run_script_sandboxed`,
which writes a launcher and runs it through
``wrap_argv``. The launcher (its source is :data:`_LAUNCHER_SRC`) runs *inside*
the sandbox: it defines the author API, execs the user script, and emits a JSON
status line. The gateway internal secret is handed to the launcher via an
unlinked temp file (never an env var the user script can read).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from personalclaw.config.loader import DASHBOARD_PORT, config_dir
from personalclaw.hooks import validate_file_path
from personalclaw.mcp_core import _internal_secret
from personalclaw.sandbox import wrap_argv

logger = logging.getLogger(__name__)

_DEFAULT_SCRIPT_TIMEOUT = 30


def _crons_dir() -> Path:
    """The directory script jobs must live under (``~/.personalclaw/crons/``)."""
    return config_dir() / "crons"


# ── Author-facing control-flow exceptions (re-exported into the launcher) ──


class Skip(Exception):
    """Raise to do nothing this tick (silent — no delivery, job retained)."""


class Done(Exception):
    """Raise to deliver ``message`` and remove the job (one-shot complete)."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class Report(Exception):
    """Raise to deliver ``message`` and keep the job scheduled."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


# ── resolve_script_path ───────────────────────────────────────────────


def resolve_script_path(spec: str) -> tuple[Path, str]:
    """Validate a ``file.py:func`` script spec and return ``(abs_path, func)``.

    Enforces: ``:func`` present and a valid identifier; ``.py`` file; the path
    is non-sensitive (``validate_file_path``); and the file resolves **under**
    ``~/.personalclaw/crons/`` (no escape). Raises ``ValueError`` otherwise.
    """
    if ":" not in spec:
        raise ValueError("script must be 'path/to/file.py:function'")
    raw_path, _, func = spec.rpartition(":")
    if not raw_path or not func.isidentifier():
        raise ValueError("invalid script spec — expected 'file.py:function'")
    if not raw_path.endswith(".py"):
        raise ValueError("script file must be a .py file")
    validated = validate_file_path(raw_path)
    if validated is None:
        raise ValueError("script path is not allowed (sensitive or invalid)")
    resolved = Path(validated).resolve()
    crons = _crons_dir().resolve()
    if not (resolved == crons or crons in resolved.parents):
        raise ValueError(f"script must live under {crons}")
    if not resolved.is_file():
        raise ValueError(f"script file not found: {resolved}")
    return resolved, func


# ── The in-sandbox launcher ────────────────────────────────────────────
# Runs as a standalone subprocess inside wrap_argv. Reads its config from a
# JSON temp file (path in argv[1]); that file also carries the internal secret,
# and is unlinked by the launcher immediately after read so the user script
# can never re-read it. Emits exactly one JSON status line on stdout prefixed
# with the sentinel so the parent can find it amid any user prints.

_RESULT_SENTINEL = "__PC_SCRIPT_RESULT__"

_LAUNCHER_SRC = r'''
import importlib.util
import json
import os
import sys
import urllib.request

_SENTINEL = "__PC_SCRIPT_RESULT__"

def _load_config():
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    # Unlink the config file immediately — it carries the internal secret.
    try:
        os.unlink(sys.argv[1])
    except OSError:
        pass
    return cfg

_CFG = _load_config()
_SECRET = _CFG.get("secret", "")
_PORT = _CFG.get("port", 0)
_SESSION_KEY = _CFG.get("session_key", "")


class Skip(Exception):
    pass

class Done(Exception):
    def __init__(self, message=""):
        super().__init__(message); self.message = message

class Report(Exception):
    def __init__(self, message=""):
        super().__init__(message); self.message = message


def _post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (_PORT, path),
        data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Internal-Secret": _SECRET,
                 "X-Session-Key": _SESSION_KEY},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ScriptContext:
    """Passed to the user's run(ctx). message = the job's message field."""
    def __init__(self, message):
        self.message = message

    def notify(self, text, **kwargs):
        """Deliver a message to the cron's channel/dashboard."""
        body = {"text": text}
        body.update(kwargs)
        return _post("/api/send-message", body)

    def call_tool(self, tool, arguments=None, provider=""):
        """Invoke a tool through PersonalClaw's Tool entity. Returns the result dict."""
        return _post("/api/tools/invoke",
                     {"tool": tool, "arguments": arguments or {}, "provider": provider})


def _emit(d):
    sys.stdout.write("\n" + _SENTINEL + json.dumps(d) + "\n")
    sys.stdout.flush()


def _install_author_api():
    """Make Skip/Done/Report/ScriptContext available to the user script with
    or without `personalclaw` on sys.path: injected as builtins AND exposed via
    a stub `personalclaw.schedule_script` module for explicit imports."""
    import builtins
    import types as _types
    for _name, _obj in (("Skip", Skip), ("Done", Done), ("Report", Report),
                        ("ScriptContext", ScriptContext)):
        setattr(builtins, _name, _obj)
    if "personalclaw.schedule_script" not in sys.modules:
        _stub = _types.ModuleType("personalclaw.schedule_script")
        _stub.Skip = Skip; _stub.Done = Done; _stub.Report = Report
        _stub.ScriptContext = ScriptContext
        sys.modules.setdefault("personalclaw", _types.ModuleType("personalclaw"))
        sys.modules["personalclaw.schedule_script"] = _stub


def _main():
    _install_author_api()
    spec = importlib.util.spec_from_file_location("_pc_cron_script", _CFG["script_path"])
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        fn = getattr(module, _CFG["func"], None)
        if fn is None or not callable(fn):
            _emit({"status": "error", "error": "function %r not found" % _CFG["func"]}); return
        ctx = ScriptContext(_CFG.get("message", ""))
        result = fn(ctx)
        _emit({"status": "ok", "message": "" if result is None else str(result)[:4000]})
    except Skip:
        _emit({"status": "skip"})
    except Done as e:
        _emit({"status": "done", "message": e.message})
    except Report as e:
        _emit({"status": "report", "message": e.message})
    except Exception as e:
        import traceback
        _emit({"status": "error", "error": (str(e) + "\n" + traceback.format_exc())[:4000]})


_main()
'''


def _parse_launcher_output(stdout: str) -> dict:
    """Extract the sentinel-prefixed JSON status line from launcher stdout."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(_RESULT_SENTINEL):
            try:
                return json.loads(line[len(_RESULT_SENTINEL) :])
            except json.JSONDecodeError:
                break
    return {"status": "error", "error": "no result emitted by script"}


def run_script_sandboxed(
    script_spec: str, job_id: str, job_message: str, timeout: int = 0, *, session_key: str = ""
) -> dict:
    """Run a ``file.py:func`` script in the sandbox; return a status dict.

    Returns ``{"status": "ok"|"skip"|"done"|"report"|"error", "message"|"error"}``.
    The internal secret + port are passed to the launcher via an unlinked temp
    file, so the user script process never sees them in its environment.
    """
    resolved, func = resolve_script_path(script_spec)
    timeout = timeout if timeout and timeout > 0 else _DEFAULT_SCRIPT_TIMEOUT

    cfg = {
        "script_path": str(resolved),
        "func": func,
        "message": job_message,
        "secret": _internal_secret(),
        "port": DASHBOARD_PORT,
        "session_key": f"cron:{job_id}",
    }
    cfg_fd, cfg_path = tempfile.mkstemp(prefix="pc-cron-cfg-", suffix=".json")
    launcher_fd, launcher_path = tempfile.mkstemp(prefix="pc-cron-run-", suffix=".py")
    try:
        with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        os.chmod(cfg_path, 0o600)
        with os.fdopen(launcher_fd, "w", encoding="utf-8") as fh:
            fh.write(_LAUNCHER_SRC)

        argv = ["python3", launcher_path, cfg_path]
        wrapped, cleanup = wrap_argv(argv, mode="standard")
        # Clean env — the secret travels via the (unlinked-on-read) cfg file only.
        env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_SECRET")}
        try:
            proc = subprocess.run(
                wrapped,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"script timed out after {timeout}s"}
        finally:
            if cleanup:
                try:
                    os.unlink(cleanup)
                except OSError:
                    pass
        result = _parse_launcher_output(proc.stdout)
        if result.get("status") == "error" and proc.returncode != 0 and not result.get("error"):
            result["error"] = (proc.stderr or "script failed")[:4000]
        return result
    finally:
        # cfg_path is normally unlinked by the launcher; remove if it survived
        # (e.g. the subprocess never started).
        for p in (cfg_path, launcher_path):
            try:
                os.unlink(p)
            except OSError:
                pass
