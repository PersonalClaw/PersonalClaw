"""The KEYSTONE enable for desktop computer use (DESKTOP-COMPUTER-USE §3 floor 1, `DCU-1`).

Driving the desktop is the largest physical-world blast radius in this codebase: a process
that can press buttons and type into the operator's own applications can, in principle, do
anything the operator can. So the capability ships OFF, and the only thing that turns it on
is a file a human writes by hand. There is deliberately **no in-band path** — not a config
field, not a dashboard PATCH, not a tool, not a prompt, not a chat instruction — because
every one of those is reachable by the thing being restrained. :func:`is_enabled` is the
first check in the dispatch chain, before target policy, before the driver, before anything
that could touch an OS input API.

**The file, and why it is a document rather than a marker.** Default
``$PERSONALCLAW_HOME/governance/computer_use.enable.json``, overridable to an absolute path
with ``PERSONALCLAW_COMPUTER_USE_ENABLE_FILE`` (the option that gives a real trust root: put
it on a root-owned ``0444`` file outside the agent's home). It must contain exactly
``{"version": 1, "enabled": true}``. A bare touch-a-marker file was rejected: an empty or
half-flushed file is indistinguishable from a deliberate one, so a truncated write — or any
stray process that creates the path — would read as ENABLED. A document with a required
positive shape fails the other way, which is the only acceptable direction here.

**Fail closed, in every direction.** Absent file, unreadable file, non-JSON bytes, wrong
root type, unknown version, unknown key, ``enabled`` that is not the literal ``true`` → the
capability is OFF and :func:`require_enabled` refuses with WHAT/WHY/FIX. Nothing in this
module can raise its way into an enabled state, and a parse problem is never reported as
"probably fine". Unknown keys are refused rather than ignored for a specific reason: an
operator writing ``{"enabled": true, "apps": ["Mail"]}`` means *"on, for Mail"*, and a build
that honoured the flag while dropping the scope would grant strictly more than was asked.

**Where this sits relative to the guardrails ceiling.** It is the same shape as
:mod:`personalclaw.guardrails.ceiling` and deliberately reuses that module's operator-owned
directory, so the two share one trust root and one denylist entry. What the layer DOES buy,
each verified by a test in ``tests/test_computer_use_enable_state.py``:

* **No API write surface.** It is not config: absent from the dashboard's ``_EDITABLE_CONFIG``
  PATCH allowlist, with no PUT/POST of its own, so nothing the agent reaches over HTTP edits
  it.
* **Agent write paths refuse it.** ``governance/`` is in the built-in sensitive-path denylist,
  so ``security.is_sensitive_path`` — consulted by the action denylist, the files area and the
  bash read/write hooks — refuses reads and writes of the enable file.
* **No mid-run flip.** Read once and cached, so neither a tamper nor a legitimate enable
  changes the reach of the process already running. Turning the capability on costs a restart
  the operator performs themselves, which is the whole point of a keystone.
* **Tamper evidence.** Boot SEL-audits the resolved source, digest and outcome, so a machine
  that was armed — or that had its keystone flipped between runs — is attributable after the
  fact.

What it does NOT buy, stated plainly: OS-level immutability against a process running as the
operator. On a single-user machine the agent runs as the user, and no in-process check can
make a file unwritable by that uid. The denylist closes the write paths that go *through*
PersonalClaw's own tools; it cannot close a raw ``open(..., "w")`` from arbitrary code
running as the same user. The only real trust root is ``PERSONALCLAW_COMPUTER_USE_ENABLE_FILE``
pointing at a file owned by another uid. A further honest limit: the denylist entry names the
default home (``~/.personalclaw/governance``), so a relocated ``PERSONALCLAW_HOME`` keeps the
no-mid-run-flip and no-API-surface properties but loses the tool-path refusal — another reason
the env override is the recommended posture.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from personalclaw.errors import AgentError
from personalclaw.guardrails.ceiling import GOVERNANCE_DIRNAME

logger = logging.getLogger(__name__)

#: The env var that repoints the keystone at an operator-owned path outside the agent's
#: home — the only way to get a switch the agent's own uid cannot rewrite.
ENABLE_PATH_ENV = "PERSONALCLAW_COMPUTER_USE_ENABLE_FILE"

#: The file, inside the operator-owned directory the governance ceiling already owns.
#: ``GOVERNANCE_DIRNAME`` is imported rather than re-spelled so the directory has ONE
#: source of truth shared with ``security._SENSITIVE_HOME_DIRS`` — a renamed constant
#: that stranded the denylist entry would silently un-protect this file.
ENABLE_FILENAME = "computer_use.enable.json"

#: The schema this build reads. A different version is refused rather than best-effort
#: parsed: a future document may carry scoping keys this build cannot enforce.
SCHEMA_VERSION = 1

#: The exact document an operator writes. Quoted verbatim in the refusal's FIX line so the
#: message a model reads and the bytes this module accepts can never drift apart.
ENABLE_DOCUMENT = '{"version": 1, "enabled": true}'

_ALLOWED_KEYS = ("version", "enabled")

#: The stable code every computer-use refusal carries, for callers that branch on the code
#: rather than the prose.
ERR_DISABLED = "ERR_COMPUTER_USE_DISABLED"


@dataclass(frozen=True)
class EnableState:
    """The keystone as resolved for this process. ``enabled`` is the only decision.

    ``detail`` is the human-readable reason the state is what it is ("no enable file at
    …", "malformed", "enabled is not true"). It exists so a refusal can say WHICH failure
    the operator hit — "off" and "off because your JSON has a typo" are very different
    problems to be handed, and collapsing them is how an operator concludes the feature is
    broken rather than mis-typed.
    """

    enabled: bool = False
    source: str = ""
    digest: str = ""
    detail: str = ""


class ComputerUseDisabled(Exception):
    """Desktop computer use is not enabled, so this tool call does nothing — fail CLOSED.

    Carries an :class:`AgentError` so every surface (tool result, exception text, HTTP
    envelope) renders the same WHAT/WHY/FIX lines. Raised in preference to returning a
    falsy result because a computer-use tool must never look like it succeeded: a silent
    no-op reads to a model as "the click landed", and it would then reason forward from a
    desktop state that never changed.
    """

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.render())
        self.error = error


def enable_file_path() -> Path:
    """Where the keystone is read from. NEVER a module constant — a frozen ``config_dir()``
    would bind the real home at import time and no test fixture could reach it."""
    override = os.environ.get(ENABLE_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / GOVERNANCE_DIRNAME / ENABLE_FILENAME


def parse_enable_document(raw: str, *, source: str) -> EnableState:
    """Resolve raw file bytes to a state. NEVER raises: every problem resolves to OFF.

    A raising parser would put the caller in charge of failing closed, and a caller that
    forgot the try/except would surface a 500 instead of a refusal — the failure mode this
    whole module exists to make impossible.
    """
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return EnableState(
            source=source,
            digest=digest,
            detail=f"the enable file is not valid JSON (line {exc.lineno})",
        )
    if not isinstance(data, dict):
        return EnableState(
            source=source, digest=digest, detail="the enable file is not a JSON object"
        )
    extra = sorted(k for k in data if k not in _ALLOWED_KEYS)
    if extra:
        return EnableState(
            source=source,
            digest=digest,
            detail=(
                f"the enable file carries key(s) this build does not enforce: "
                f"{', '.join(extra)}"
            ),
        )
    version = data.get("version")
    if version != SCHEMA_VERSION:
        return EnableState(
            source=source,
            digest=digest,
            detail=f"the enable file declares version {version!r}; this build reads "
            f"{SCHEMA_VERSION}",
        )
    flag = data.get("enabled")
    # `is True`, not truthiness: the string "false", 1, and [] are all things an operator
    # or a stray writer can produce, and none of them is a human saying yes.
    if flag is not True:
        return EnableState(
            source=source,
            digest=digest,
            detail=f'the enable file\'s "enabled" is {flag!r}, not the literal true',
        )
    return EnableState(
        enabled=True, source=source, digest=digest, detail=f"enabled out-of-band by {source}"
    )


def load_enable_state(path: Path | None = None) -> EnableState:
    """Read + resolve the keystone from disk. Absent or unreadable → OFF.

    Note the deliberate asymmetry with the governance ceiling, which ABORTS boot on an
    unreadable file: there, "no bound" would be a widening, so it must stop the process.
    Here the fail-closed answer is simply OFF, which is a state the system already runs in
    and can report cleanly — so an unreadable keystone must not take the gateway down.
    """
    target = Path(path) if path is not None else enable_file_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return EnableState(source=str(target), detail=f"no enable file at {target}")
    except OSError as exc:
        return EnableState(
            source=str(target),
            detail=f"the enable file at {target} exists but could not be read ({exc})",
        )
    return parse_enable_document(raw, source=str(target))


_ACTIVE: EnableState | None = None


def active_enable_state() -> EnableState:
    """The keystone in force for this process — read once, cached, never reloaded.

    Caching IS the no-mid-run-flip property, and here it is load-bearing in both
    directions: a tamper cannot arm a running gateway, and an operator's legitimate enable
    does not take effect until they restart — a restart they perform themselves and can
    see. Unlike the ceiling, a resolution failure is cached, because here failure is not an
    error to retry: it is the narrow answer (OFF), and re-reading it every call would let a
    mid-run write flip the process.
    """
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_enable_state()
    return _ACTIVE


def reset_enable_state() -> None:
    """Drop the cached keystone (tests + a deliberate reload). Process-global by design."""
    global _ACTIVE
    _ACTIVE = None


def is_enabled() -> bool:
    """Is desktop computer use armed for this process? The FIRST check in every dispatch."""
    return active_enable_state().enabled


def disabled_error(tool: str, state: EnableState | None = None) -> AgentError:
    """The WHAT/WHY/FIX a computer-use refusal carries, naming the out-of-band enable step.

    Public because a surface that renders an error envelope rather than raising (an HTTP
    handler, a tool card) needs the same three lines the exception carries — two spellings
    of this message is how a refusal ends up telling an operator to edit a setting that
    does not exist.
    """
    resolved = state if state is not None else active_enable_state()
    path = enable_file_path()
    return AgentError(
        code=ERR_DISABLED,
        what=(
            f"{tool} refused: desktop computer use is OFF on this machine — "
            f"{resolved.detail or 'no enable file'}."
        ),
        why=(
            "Driving the desktop posts real input into the operator's own applications, so "
            "the capability is keystone-gated: it stays off until a human arms it in a file "
            "outside every write path this process has. No prompt, tool call, chat "
            "instruction or settings change can flip it — including this one. That is the "
            "point of the gate, not a bug in it."
        ),
        fix=(
            f"A human must write {path} containing exactly {ENABLE_DOCUMENT}, then restart "
            "PersonalClaw so the keystone is re-read at boot. To keep the switch outside the "
            f"agent's home entirely, put the file anywhere and point {ENABLE_PATH_ENV} at it "
            "(e.g. a root-owned 0444 file) — that is the only version this process genuinely "
            "cannot rewrite."
        ),
    )


def require_enabled(tool: str) -> EnableState:
    """Gate a computer-use entry point. Returns the state when armed; raises when not.

    Every dispatchable computer-use tool calls this as its first statement, and
    ``tests/test_computer_use_enable_state.py`` is the ratchet that says so — the guard is
    only worth anything if it cannot be skipped by the next tool somebody adds.

    Reads the decision through :func:`is_enabled` rather than off ``state.enabled``, so the
    check the plan names is the ONE decision point rather than a convenience alias beside
    it. Measured, not assumed: with an earlier version that read the field directly, forcing
    ``is_enabled`` to return True left every refusal test GREEN — two independent readers of
    one flag is exactly how one of them ends up answering differently from the other.
    """
    state = active_enable_state()
    if not is_enabled():
        raise ComputerUseDisabled(disabled_error(tool, state))
    return state


def ensure_computer_use_boot() -> EnableState:
    """Resolve + record the keystone at gateway boot. Never raises; OFF is a normal state.

    Called from the gateway's start-up right after governance so the resolved source,
    digest and outcome land in the SEL once per run. That record is the tamper evidence: a
    machine that was armed, or whose keystone changed between runs, is attributable after
    the fact even though the file itself lives outside anything this process controls.
    """
    state = active_enable_state()
    if state.enabled:
        # WARNING, not INFO: an armed keystone is the loudest posture this process can be
        # in, and it should be legible in a log an operator skims rather than greps.
        logger.warning(
            "computer_use: desktop drive is ENABLED by %s (digest %s)",
            state.source,
            state.digest or "none",
        )
    else:
        logger.info("computer_use: desktop drive is off — %s", state.detail)
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="gateway",
            operation="computer_use.enable_boot",
            outcome="enabled" if state.enabled else "disabled",
            source="computer_use",
            resources=(
                f"{state.source or enable_file_path()} digest={state.digest or 'none'} "
                f"detail={state.detail}"
            ),
        )
    except Exception:
        logger.debug("computer-use keystone boot SEL audit failed", exc_info=True)
    return state
