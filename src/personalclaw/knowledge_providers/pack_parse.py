"""Run a connector pack's PARSE-ONLY script (WATCHED-SOURCES §7.1).

A connector pack contributes *parsing*, never *fetching*. The engine performs the HTTP
request through the one guarded seam (``net.fetch`` under the ``SOURCE`` egress policy),
hands the response body to a pack script on **stdin**, and reads ``SourceItem`` JSON lines
back on **stdout**. The script never owns a socket, which is the entire reason an untrusted
third-party parser is safe to run at all: the dangerous capability is not held by the code
that came from a stranger.

**What actually stops the socket.** ``sandbox.wrap_argv`` — the shared OS path sandbox this
module also uses — denies *file reads* of credential paths; its Seatbelt profile is
``(allow default)`` plus deny-read rules and its Linux launcher unshares only
``CLONE_NEWUSER``/``CLONE_NEWNS``. Neither denies network, and on a host where
``detect_backend()`` answers ``none`` (this project's own macOS 26 dev machine, where
``sandbox_apply`` is refused for third-party callers) it is not applied at all. So the OS
sandbox is a real control over the *filesystem* and no control whatsoever over *egress*, and
pretending otherwise would leave the property this atom exists for resting on nothing.

The live rail is therefore in-process, installed by :data:`_PARSE_HARNESS_SRC` before the pack
script is executed. It is **three mechanisms plus a verification**, and the division of labour
between them is MEASURED, not assumed: under ``python -I`` exactly three denied names are
already in ``sys.modules`` at startup (``os``, ``os.path``, ``posix``) and no others.

1. A finder at ``sys.meta_path[0]`` refuses :data:`DENIED_MODULES`. This is what stops
   ``socket``, ``ssl``, ``ctypes``, ``subprocess``, ``urllib.request`` and ``importlib``:
   none is pre-imported, so every route to them — an ``import`` statement, a
   ``from … import``, ``importlib.import_module`` — reaches a finder, and ours runs first.
2. The three pre-imported names are EVICTED from ``sys.modules``. Without this ``import os``
   is served from the startup cache and never reaches a finder at all, so the finder alone
   would leave the spawn-a-child route wide open.
3. The process-spawning callables on those same live module objects are NEUTERED. Eviction
   removes the *name*, not the *object*: ``object.__subclasses__()`` finds ``os._wrap_close``,
   whose ``__init__.__globals__`` IS the os module dict, so ``os.system`` stays reachable with
   no import at all. This child parses one body and is discarded, so wrecking its copy of the
   stdlib costs nothing.
4. After the script returns the harness verifies 1 and 2 survived and reports
   ``fence: tampered`` otherwise — and the parent discards the whole batch. A script that
   removes the fence gets zero items.

A fourth mechanism was built and then DELETED: wrapping ``builtins.__import__`` to check the
denylist before delegating. Removing it reded nothing, and the measurement above is why — with
the three pre-imported names evicted, every denied import already reaches the finder, so the
wrapper was a second path to the same refusal. Two mechanisms where one suffices is the
dual-path this project does not keep, and an untested layer is decoration, not defence.

:data:`DENIED_MODULES` is a **denylist, deliberately**, and it is closed *for this property*
rather than being a general jail. Every way CPython reaches a network in-process bottoms out
at one of three things: ``_socket`` (which ``socket``/``ssl``/``http``/``urllib``/
``asyncio``/every third-party client is built on), ``_ctypes`` (raw ``libc`` calls), or a
spawned child (``os``/``subprocess``/``_posixsubprocess``/``multiprocessing``). Denying those
roots denies the set. An allowlist of *importable* modules was tried first and rejected: the
stdlib's own lazy imports (``csv`` → ``_csv``, ``datetime`` → ``_datetime``, ``re`` →
``re._compiler``) make an allowlist break legitimate parsers for reasons an author cannot
predict, and a fence that fails on correct code gets removed.

**Fail closed on output, by construction.** The harness emits a nonce-tagged terminator line
after the script returns; the nonce is delivered in a config file the harness unlinks on
read, so the script cannot forge it. The parent REQUIRES that line. A script that crashes
mid-write, is killed on timeout, blows the output cap, or emits one malformed line yields
**zero** items and a typed :class:`ParseFailure` — never a half-ingested batch, because a
partially-ingested feed is indistinguishable from a feed that shrank.

Bounds reuse core's own ceilings rather than a bare ``subprocess.run``: the ``tool`` resource
profile via ``spawn_shim_argv`` (NOFILE/NPROC/RSS + OOM bias), the allowlisted child
environment via ``build_child_env``, a wall-clock timeout, an input cap and an output cap.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Wall clock a parse script gets. A parse of an already-fetched body is CPU-bound string
#: work over at most :data:`MAX_BODY_BYTES`; anything slower is a loop, not a parser.
DEFAULT_TIMEOUT_SECS = 15

#: Most bytes handed to a script on stdin. Matches the ``SOURCE`` egress policy's own
#: ``max_bytes`` ceiling — the body cannot be larger, so this is a floor-consistency check
#: rather than a second, smaller cap the fetch would already have refused.
MAX_BODY_BYTES = 10_000_000

#: Most bytes read back from a script. Exceeding it fails the whole batch closed: a script
#: whose output we truncated would hand us a torn final line, and "the feed ends here" is
#: precisely the lie this module refuses to tell.
MAX_OUTPUT_BYTES = 4_000_000

#: Most items one parse may emit. The engine caps again per poll (``max_items_per_poll``);
#: this bound is about not building a million-element list in this process.
MAX_ITEMS = 1000

#: The import roots a parse-only script may not reach. See the module docstring for why this
#: is a denylist and why it is closed for the no-socket property. Interpolated into
#: :data:`_PARSE_HARNESS_SRC` as data, so this frozenset is the ONE live definition —
#: there is no second copy inside the harness string to drift from (or to mutate by
#: accident when falsifying the fence).
DENIED_MODULES: frozenset[str] = frozenset(
    {
        # The one C socket module every network client in CPython is built on.
        "_socket",
        "socket",
        "ssl",
        "_ssl",
        # Everything that wraps it and would otherwise be a shorter path to the same call.
        "http",
        "urllib.request",
        "urllib.error",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "nntplib",
        "telnetlib",
        "socketserver",
        "xmlrpc",
        "webbrowser",
        "asyncio",
        "select",
        "selectors",
        # Raw FFI: a libc `socket()` call needs no Python-level socket module.
        "ctypes",
        "_ctypes",
        # Spawning a child that networks on the script's behalf.
        "os",
        "posix",
        "nt",
        "subprocess",
        "_posixsubprocess",
        "multiprocessing",
        "pty",
        "signal",
        # Re-entry points that would hand back a fresh, unfenced import machinery.
        "importlib",
        "runpy",
    }
)

#: Header names whose value is a credential by definition. A pack manifest must reference a
#: ``{{secret:KEY}}`` for these rather than inline a literal (``apps/manifest.py`` enforces
#: it); named here too because this module is what renders them.
SECRET_HEADERS: frozenset[str] = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key"}
)

#: Prefix of the harness's terminator line. The nonce follows it.
_END_PREFIX = "__PC_PACK_END__"


class ParseFailure(Exception):
    """A pack parse produced no usable items, and the reason is machine-nameable.

    Raised (never swallowed into a partial result) for every failure shape: the script
    refused an import, crashed, timed out, blew a cap, tampered with the fence, or emitted a
    line that is not one JSON object. ``code`` is the stable reason a UI branches on;
    ``detail`` is the human sentence.
    """

    #: The script tried to import something a parse-only script may not have.
    IMPORT_REFUSED = "pack_import_refused"
    #: The script removed or replaced part of the fence.
    FENCE_TAMPERED = "pack_fence_tampered"
    #: The script exited without the harness's terminator (crash, ``os._exit``, kill).
    INCOMPLETE = "pack_output_incomplete"
    #: A line on stdout is not a single JSON object.
    MALFORMED = "pack_output_malformed"
    #: An emitted object is missing the fields a ``SourceItem`` needs.
    BAD_SHAPE = "pack_item_bad_shape"
    #: The script exceeded its wall clock.
    TIMEOUT = "pack_timeout"
    #: The script wrote more than :data:`MAX_OUTPUT_BYTES`, or emitted too many items.
    TOO_LARGE = "pack_output_too_large"
    #: The script could not be run at all (missing file, unusable path).
    UNRUNNABLE = "pack_script_unrunnable"

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class ParseResult:
    """The outcome of one parse: the emitted rows plus what it cost.

    ``rows`` are raw dicts, not ``SourceItem`` instances — this module owns the untrusted
    boundary and the *shape* check, and :mod:`connector_pack` owns the mapping onto the
    contract. Keeping the two apart means the shape check has exactly one home.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    stderr: str = ""
    output_bytes: int = 0


# The harness runs INSIDE the spawn (and inside the OS sandbox when one is available). It
# reads its config from a JSON file named in argv[1] and unlinks it immediately, so the nonce
# it will terminate with is not readable by the script it is about to execute — the
# schedule_script.py idiom, for the same reason: a secret a child can re-read is not a secret.
#
# `__PC_DENIED_MODULES__` is the ONLY interpolation, so DENIED_MODULES stays the single
# definition and a falsification that empties it empties the rail that actually runs.
_PARSE_HARNESS_SRC = r'''
# Everything the harness itself needs is imported HERE, before the fence exists, so no
# lazy stdlib import (traceback -> linecache -> os) can be refused by our own rail and turn
# a script's ordinary bug into an unexplained "no terminator".
import builtins
import json
import linecache  # noqa: F401 — pre-imported so traceback never imports it under the fence
import sys
import traceback
import types

import os as _os

_END_PREFIX = "__PC_PACK_END__"
_DENIED = frozenset(__PC_DENIED_MODULES__)

_cfg_path = sys.argv[1]
with open(_cfg_path, "r", encoding="utf-8") as _fh:
    _CFG = json.load(_fh)
# Unlink before the script runs: the config carries the terminator nonce, and a nonce a
# script can re-read is a nonce a script can forge.
try:
    _os.unlink(_cfg_path)
except OSError:
    pass

_NONCE = _CFG["nonce"]
_SCRIPT = _CFG["script_path"]
_ARGS_JSON = _CFG["args_json"]

def _emit_end(status, detail=""):
    sys.stdout.flush()
    sys.stdout.write(
        "\n" + _END_PREFIX + _NONCE + json.dumps({"fence": status, "detail": detail}) + "\n"
    )
    sys.stdout.flush()


# Read the script BEFORE the fence goes up: builtins.open is left working on purpose (a
# parse script may legitimately read a fixture beside itself), but doing our own read first
# means the harness never depends on anything the fence touches.
try:
    with open(_SCRIPT, "r", encoding="utf-8") as _fh:
        _CODE = _fh.read()
except OSError as _exc:
    _emit_end("unrunnable", str(_exc)[:400])
    raise SystemExit(0)


def _refuse(name):
    raise ImportError(
        "connector-pack scripts are parse-only: import of %r is refused. The engine "
        "fetches through net.fetch and hands you the body on stdin; a pack script never "
        "opens a socket, spawns a process, or calls libc." % (name,)
    )


def _denied(fullname):
    parts = str(fullname).split(".")
    for i in range(len(parts)):
        if ".".join(parts[: i + 1]) in _DENIED:
            return True
    return False


class _Fence:
    """A meta_path finder refusing denied names.

    Sufficient on its own for every denied name that is NOT pre-imported (measured: only
    `os`/`os.path`/`posix` are), because an absent name always reaches a finder however it is
    reached — an `import` statement, a `from … import`, or `importlib.import_module`.
    """

    def find_spec(self, fullname, path=None, target=None):
        if _denied(fullname):
            _refuse(fullname)
        return None


def _neutered(name):
    def _blocked(*a, **k):
        _refuse(name)

    return _blocked


# The `os`/`posix` names that START A PROCESS. Only these are neutered on the process
# modules, not every callable: `os.stat`/`os.getcwd`/`os.listdir` are what `linecache` uses
# to format a traceback, so blanket-neutering `os` would make an ordinary parser bug die
# inside our own error handler and surface as "no terminator" instead of its real message.
# Filesystem reach is a different axis, owned by `wrap_argv`'s path sandbox.
_OS_EXEC_ATTRS = frozenset(
    {
        "system", "popen", "fork", "forkpty", "openpty", "kill", "killpg", "abort",
        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
        "fexecve", "posix_spawn", "posix_spawnp", "startfile",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    }
)
_PROCESS_MODULES = frozenset({"os", "posix", "nt"})


def _neuter(module, name):
    """Replace the escape-bearing callables on an already-loaded denied module.

    Eviction from sys.modules is not enough on its own: a live module object stays reachable
    through ordinary Python reflection (`object.__subclasses__()` finds `os._wrap_close`,
    whose `__init__.__globals__` IS the os module dict), so an import fence alone leaves
    `os.system` one gadget away. This child exists only to parse one body and is thrown
    away, so wrecking its copy of the stdlib costs nothing and closes that route.
    """
    process_mod = name in _PROCESS_MODULES
    for attr in list(vars(module)):
        if attr.startswith("__"):
            continue
        if process_mod and attr not in _OS_EXEC_ATTRS:
            continue
        try:
            value = getattr(module, attr)
        except Exception:
            continue
        if callable(value):
            try:
                setattr(module, attr, _neutered("%s.%s" % (name, attr)))
            except Exception:
                pass


_fence = _Fence()
sys.meta_path.insert(0, _fence)
for _name in list(sys.modules):
    if _denied(_name):
        _mod = sys.modules.pop(_name)
        if isinstance(_mod, types.ModuleType):
            _neuter(_mod, _name)

# The harness's own globals hold the real module objects, and `sys.modules["__main__"]` is
# how a script would reach them without importing anything. Swap in an empty shell.
sys.modules["__main__"] = types.ModuleType("__main__")


def _fence_intact():
    return sys.meta_path[:1] == [_fence] and not any(_denied(n) for n in sys.modules)


_globals = {"__name__": "__main__", "__file__": _SCRIPT, "__builtins__": builtins}
sys.argv = [_SCRIPT, _ARGS_JSON]
try:
    exec(compile(_CODE, _SCRIPT, "exec"), _globals)
except BaseException as _exc:  # noqa: BLE001 — every fault becomes ONE closed report
    _detail = "".join(traceback.format_exception_only(type(_exc), _exc)).strip()
    _emit_end("intact" if _fence_intact() else "tampered", _detail[:400])
    raise SystemExit(0)

_emit_end("intact" if _fence_intact() else "tampered")
'''


def harness_source() -> str:
    """The in-spawn harness, with :data:`DENIED_MODULES` interpolated as data.

    Public so a test can read the exact source that will run — an assertion about a string
    this function builds is an assertion about the shipped rail, whereas an assertion about a
    hand-copied denylist would pass while the real one was empty.
    """
    denied = "[" + ", ".join(repr(name) for name in sorted(DENIED_MODULES)) + "]"
    return _PARSE_HARNESS_SRC.replace("__PC_DENIED_MODULES__", denied)


def _require_script(script: Path) -> None:
    if script.suffix != ".py":
        raise ParseFailure(ParseFailure.UNRUNNABLE, f"parse script must be a .py file: {script}")
    if not script.is_file():
        raise ParseFailure(ParseFailure.UNRUNNABLE, f"parse script not found: {script}")


def _classify_stderr(stderr: str) -> str | None:
    """The failure code implied by a traceback on stderr, or ``None``.

    Only the fence's own refusal is classified. Everything else is a script bug and keeps the
    generic code — misreporting a parser's ``KeyError`` as a security refusal would send the
    reader looking in the wrong place.
    """
    if "parse-only: import of" in stderr:
        return ParseFailure.IMPORT_REFUSED
    return None


def _split_output(text: str, nonce: str) -> tuple[list[str], str, str]:
    """``(item_lines, fence_status, fence_detail)``; raises if the terminator is absent.

    The terminator is what makes truncation, a kill and an ``os._exit`` all fail closed: the
    harness writes it only after the script returned, and the nonce means the script cannot
    write it itself.
    """
    marker = _END_PREFIX + nonce
    idx = text.rfind(marker)
    if idx < 0:
        raise ParseFailure(
            ParseFailure.INCOMPLETE,
            "the parse script did not run to completion (no terminator emitted), so its "
            "output is a partial batch and is discarded whole",
        )
    tail = text[idx + len(marker) :].strip().splitlines()
    try:
        end = json.loads(tail[0]) if tail else {}
    except (TypeError, ValueError, IndexError):
        end = {}
    status = str(end.get("fence") or "")
    detail = str(end.get("detail") or "")
    return text[:idx].splitlines(), status, detail


def _rows_from(lines: list[str]) -> list[dict[str, Any]]:
    """Every non-blank line as a JSON object, or a hard failure.

    Deliberately NOT tolerant. Skipping a bad line would ingest the good ones and report
    success, which is the "half-ingested batch" this contract forbids: the user would see a
    feed silently missing whichever rows the parser got wrong.
    """
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise ParseFailure(
                ParseFailure.MALFORMED,
                f"stdout line {i + 1} is not JSON ({exc}); a pack script emits one JSON "
                f"object per line and nothing else (no prints, no progress output)",
            ) from exc
        if not isinstance(parsed, dict):
            raise ParseFailure(
                ParseFailure.MALFORMED,
                f"stdout line {i + 1} is a {type(parsed).__name__}, not a JSON object",
            )
        rows.append(parsed)
        if len(rows) > MAX_ITEMS:
            raise ParseFailure(
                ParseFailure.TOO_LARGE,
                f"the parse script emitted more than {MAX_ITEMS} items",
            )
    return rows


def run_parse_script(
    script: Path,
    body: bytes,
    args: dict[str, Any] | None = None,
    *,
    timeout_secs: int = DEFAULT_TIMEOUT_SECS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> ParseResult:
    """Parse ``body`` with the pack's ``script``; return its rows or raise :class:`ParseFailure`.

    ``args`` reach the script as ``sys.argv[1]``, a JSON object (the argv/JSON-stdout
    contract). The body reaches it on stdin. Nothing else is handed over: no network, no
    credential, no gateway secret, no inherited environment beyond
    :func:`~personalclaw.sandbox.build_child_env`'s allowlist.
    """
    from personalclaw.sandbox import PROFILE_TOOL, build_child_env, spawn_shim_argv, wrap_argv

    script = Path(script)
    _require_script(script)
    if len(body) > MAX_BODY_BYTES:
        raise ParseFailure(
            ParseFailure.TOO_LARGE,
            f"fetched body is {len(body)} bytes, over the {MAX_BODY_BYTES}-byte parse cap",
        )

    nonce = secrets.token_hex(12)
    cfg = {
        "script_path": str(script),
        "nonce": nonce,
        "args_json": json.dumps(args or {}, sort_keys=True),
    }
    cfg_fd, cfg_path = tempfile.mkstemp(prefix="pc-pack-cfg-", suffix=".json")
    harness_fd, harness_path = tempfile.mkstemp(prefix="pc-pack-run-", suffix=".py")
    out_fd, out_path = tempfile.mkstemp(prefix="pc-pack-out-", suffix=".jsonl")
    os.close(out_fd)
    cleanup: str | None = None
    try:
        with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        os.chmod(cfg_path, 0o600)
        with os.fdopen(harness_fd, "w", encoding="utf-8") as fh:
            fh.write(harness_source())

        # -I: isolated mode. Ignores PYTHONPATH and the user site dir, so neither an
        # inherited path entry nor a `sitecustomize.py` can pre-import a denied module (or
        # anything else) into the interpreter before the fence is installed.
        argv = [sys.executable, "-I", harness_path, cfg_path]
        wrapped, cleanup = wrap_argv(argv, mode="strict")
        # The ceiling shim goes OUTSIDE the sandbox wrap for the reason schedule_script.py
        # documents: rlimits inherit through exec, and inside the wrap the shim's own import
        # would have to survive the profile.
        wrapped = spawn_shim_argv(wrapped, PROFILE_TOOL)
        env = build_child_env(site="connector-pack-parse")

        with open(out_path, "wb") as out_fh:
            try:
                proc = subprocess.run(  # noqa: S603 — argv list, no shell
                    wrapped,
                    input=body,
                    stdout=out_fh,
                    stderr=subprocess.PIPE,
                    timeout=max(1, int(timeout_secs)),
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ParseFailure(
                    ParseFailure.TIMEOUT,
                    f"the parse script did not finish within {timeout_secs}s",
                ) from exc

        size = os.path.getsize(out_path)
        if size > max_output_bytes:
            raise ParseFailure(
                ParseFailure.TOO_LARGE,
                f"the parse script wrote {size} bytes, over the {max_output_bytes}-byte cap",
            )
        with open(out_path, "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

        lines, fence_status, fence_detail = _split_output(text, nonce)
        if fence_status == "tampered":
            raise ParseFailure(
                ParseFailure.FENCE_TAMPERED,
                "the parse script altered the import fence; its whole batch is discarded "
                f"({fence_detail or 'no detail'})",
            )
        if fence_status == "unrunnable":
            raise ParseFailure(ParseFailure.UNRUNNABLE, fence_detail or "script unreadable")
        if fence_detail:
            raise ParseFailure(
                _classify_stderr(fence_detail) or ParseFailure.MALFORMED,
                f"the parse script raised: {fence_detail}",
            )
        rows = _rows_from(lines)
        return ParseResult(rows=rows, stderr=stderr[:2000], output_bytes=size)
    finally:
        for path in (cfg_path, harness_path, out_path, cleanup):
            if not path:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass


#: Keys a pack row may set. Anything else is dropped rather than refused: an unknown key is
#: a pack written against a newer contract, and a source that stops working because its
#: parser learned a new field is a worse failure than one extra ignored key.
ROW_KEYS = ("guid", "title", "content", "url", "published_at", "also_seen_in", "metadata")

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def normalize_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    """Coerce one emitted row to the ``SourceItem`` field shape, or raise :class:`ParseFailure`.

    The shape floor is deliberately low — ONE of ``guid``/``url``/``title`` must be present,
    because ``compose_guid`` can key an item from any of the three — but it is a floor, not a
    suggestion: a row that names nothing cannot be de-duplicated, so admitting it would write
    a fresh item on every poll forever.
    """
    if not any(str(row.get(k) or "").strip() for k in ("guid", "url", "title")):
        raise ParseFailure(
            ParseFailure.BAD_SHAPE,
            f"item {index + 1} has no guid, url or title, so it cannot be keyed for dedupe",
        )
    url = str(row.get("url") or "").strip()
    if url and not _URL_SCHEME_RE.match(url):
        raise ParseFailure(
            ParseFailure.BAD_SHAPE,
            f"item {index + 1} has a non-http(s) url {url[:80]!r}",
        )
    also = row.get("also_seen_in") or []
    if isinstance(also, str):
        also = [also]
    meta = row.get("metadata")
    return {
        "guid": str(row.get("guid") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "content": str(row.get("content") or ""),
        "url": url,
        "published_at": str(row.get("published_at") or "").strip(),
        "also_seen_in": [str(x).strip() for x in also if str(x).strip()],
        "metadata": dict(meta) if isinstance(meta, dict) else {},
    }
