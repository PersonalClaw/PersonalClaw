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
it on a root-owned ``0444`` file outside the agent's home). It must contain the document
:data:`ENABLE_DOCUMENT` quotes verbatim — a version, the literal ``true``, and the ``apps``
allowlist naming what may be driven. A bare touch-a-marker file was rejected: an empty or
half-flushed file is indistinguishable from a deliberate one, so a truncated write — or any
stray process that creates the path — would read as ENABLED. A document with a required
positive shape fails the other way, which is the only acceptable direction here.

**Fail closed, in every direction.** Absent file, unreadable file, non-JSON bytes, wrong
root type, unknown version, unknown key, ``enabled`` that is not the literal ``true``, a
malformed ``apps`` or ``unattended`` entry → the capability is OFF and :func:`require_enabled`
refuses with WHAT/WHY/FIX. Nothing in this module can raise its way into an enabled state, and a
parse
problem is never reported as "probably fine". Unknown keys are refused rather than ignored
for a specific reason: an operator writing ``{"enabled": true, "windows": ["Inbox"]}`` means
*"on, for that window"*, and a build that honoured the flag while dropping the scope would
grant strictly more than was asked. (``apps`` was that example until `DCU-2`; this build
enforces it, so it is an accepted key now and a scope key this build cannot honour has to be
spelled with something else.)

**The target allowlist lives HERE, not in config.** ``apps`` is the operator's list of
applications an armed process may drive, read only through :func:`allowed_apps`, and it is
stored in this document for exactly the reason ``enabled`` is. The allowlist is what stands
between "computer use is on" and "the agent may drive your password manager", so a
PATCH-editable home for it would hand an agent with config-write access a route to widen its
own reach — the same threat the keystone exists to close, one field over. An absent or empty
list means NO app may be driven: armed-with-no-targets is a state the system runs in
happily, and it is the only fail-closed reading available (see :func:`allowed_apps`).

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

from personalclaw.computer_use.tools import TOOL_NAMES
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

#: The document an operator writes. Quoted verbatim in the refusal's FIX line so the message
#: a model reads and the bytes this module accepts can never drift apart.
#:
#: It carries a one-app ``apps`` allowlist rather than the bare version+flag pair it was
#: before `DCU-2`, and that is not decoration. An empty allowlist drives NOTHING
#: (:func:`allowed_apps`), so quoting the old two-key document would tell an operator to arm
#: a capability that the very next check refuses — they would follow the FIX line exactly and
#: hit a second refusal, one whose own FIX names nothing further to do. That is strictly
#: worse than no FIX line, because it teaches the operator this message cannot be trusted.
#: So the quoted bytes are a document that genuinely works: one deliberately benign, real
#: target, which the operator replaces with their own. ``TextEdit`` is chosen because pasting
#: it verbatim grants the least interesting thing on the machine.
#:
#: ``tests/test_computer_use_app_allowlist.py`` parses THIS constant through
#: :func:`parse_enable_document` and asserts the resulting state can actually drive
#: something. That test is what keeps the paragraph above true rather than aspirational —
#: without it, "the message and the accepted bytes cannot drift" is a comment, not a
#: property.
ENABLE_DOCUMENT = '{"version": 1, "enabled": true, "apps": ["TextEdit"]}'

#: The keys this build enforces. Unknown keys are REFUSED, so widening this tuple is a
#: deliberate act and must arrive together with the parser branch that enforces the new key —
#: an allowed-but-unparsed key would be a scope an operator writes and this module ignores,
#: which is the exact widening the refusal exists to prevent. The vacuity floor in
#: ``test_computer_use_app_allowlist.py`` pins the membership so a fifth key cannot be added
#: quietly.
_ALLOWED_KEYS = ("version", "enabled", "apps", "unattended")

#: The key an operator adds to name the computer-use tools an UNATTENDED run may invoke
#: (`DCU-5`). Absent is the fail-closed default: an unattended run drives nothing.
#:
#: It lives in THIS document rather than in ``config.json`` or a ceiling scope for the reason
#: the module docstring gives for the keystone itself — a grant the agent's own process can
#: write is a grant the agent can give itself, and "may I drive the desktop with nobody
#: watching" is the last question that should be answerable in-band. The ceiling can only
#: narrow a profile, so it cannot express this grant either; the out-of-band document can.
UNATTENDED_KEY = "unattended"

#: The stable code every computer-use refusal carries, for callers that branch on the code
#: rather than the prose.
ERR_DISABLED = "ERR_COMPUTER_USE_DISABLED"


@dataclass(frozen=True)
class EnableState:
    """The keystone as resolved for this process.

    ``enabled`` is the only ON/OFF decision; ``apps`` narrows WHICH applications an armed
    process may drive. Two separate operator acts, deliberately: arming the capability and
    choosing its targets are different grants, and collapsing them would mean a human could
    not say "on, but only for this one thing" — which is the thing most humans actually want
    to say.

    ``detail`` is the human-readable reason the state is what it is ("no enable file at
    …", "malformed", "enabled is not true"). It exists so a refusal can say WHICH failure
    the operator hit — "off" and "off because your JSON has a typo" are very different
    problems to be handed, and collapsing them is how an operator concludes the feature is
    broken rather than mis-typed.

    ``apps`` is the operator's target allowlist, resolved from the same out-of-band document
    and read ONLY through :func:`allowed_apps`. Empty is the fail-closed default and means no
    app may be driven; it never means "all".

    ``unattended`` is the third grant (`DCU-5`): the computer-use tools a run with no human
    watching may invoke. Read ONLY through :func:`unattended_tools`, empty by default, and
    empty means no unattended run drives anything. It is a THIRD act rather than a widening of
    the other two on the same reasoning ``apps`` is separate from ``enabled``: "on", "on for
    Mail" and "on for Mail while I am asleep" are three different things a human may want to
    say, and the last one is the one that cannot be taken back by watching the screen.
    """

    enabled: bool = False
    source: str = ""
    digest: str = ""
    detail: str = ""
    apps: tuple[str, ...] = ()
    unattended: tuple[str, ...] = ()


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


def _parse_name_list(
    data: dict,
    key: str,
    *,
    noun: str,
    source: str,
    digest: str,
    known: frozenset[str] | None = None,
) -> tuple[tuple[str, ...], EnableState | None]:
    """Resolve one exact-match name list from the document, or the OFF state it refuses to.

    ONE validator for every list this document carries (``apps``, ``unattended``), because two
    copies of it is how one list grows a normalisation the other refuses — and a normalisation
    here is always a widening. Returns ``(names, None)`` on success and ``((), refusal)`` on a
    malformed list; the caller returns the refusal unchanged.

    **An absent key and an explicit ``[]`` land here identically**, by construction rather than
    by two branches: both are the empty grant. There is no third meaning to give either one —
    neither can mean "everything" without inverting the narrowest of the operator's grants into
    the widest one — so the only way to make them differ would be to make one fail open. Note
    also that ``[]`` is NOT a parse refusal: "armed, targets not chosen yet" is a coherent thing
    for a human to have written, and reporting it as a malformed document is the same collapse
    of distinct failures that :attr:`EnableState.detail` exists to prevent.

    **Entries are matched by EXACT string equality later**, so this is the one place a malformed
    entry can be caught, and it is REFUSED rather than normalised. Every normalisation available
    here widens the list past the bytes a human wrote: case-folding lets ``"textedit"`` reach a
    differently-cased app they never named (and app names are case-sensitive on the platforms
    this drives), stripping lets a padded entry reach a target, and substring or
    display-name<->bundle-id equivalence would let ``"Mail"`` reach ``"Mailbox"`` or
    ``"TextEdit"`` reach ``"com.apple.TextEdit"``. The operator writes the identifier their
    driver reports; this module refuses to guess which namespace they meant, because guessing in
    the permissive direction is the only mistake that matters. Same choice the ``enabled`` check
    makes when it rejects the string ``"true"``.

    ``known`` is the closed vocabulary a key's entries must come from, when one exists. Only
    ``unattended`` has one (the seven declared tools); an unknown name there refuses the whole
    document rather than failing closed silently, because a grant that quietly matches nothing
    is a grant an operator believes they wrote.
    """
    raw = data.get(key, [])

    def refuse(detail: str) -> tuple[tuple[str, ...], EnableState]:
        return (), EnableState(source=source, digest=digest, detail=detail)

    if not isinstance(raw, list):
        return refuse(f'the enable file\'s "{key}" is {raw!r}, not a list of {noun}')
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            return refuse(f'the enable file\'s "{key}" carries {entry!r}, which is not a string')
        if not entry.strip():
            return refuse(
                f'the enable file\'s "{key}" carries an empty name; an entry that can '
                "match nothing is a stray comma, not a target"
            )
        if entry != entry.strip():
            return refuse(
                f'the enable file\'s "{key}" carries {entry!r}, which is padded with '
                "whitespace; names are matched exactly, so write it without the padding"
            )
        if entry in names:
            return refuse(
                f'the enable file\'s "{key}" names {entry!r} twice; a duplicate means the '
                "list is not the one whoever wrote it thinks they wrote"
            )
        if known is not None and entry not in known:
            return refuse(
                f'the enable file\'s "{key}" names {entry!r}, which is not one of this '
                f"build's computer-use tools ({', '.join(sorted(known))})"
            )
        names.append(entry)
    # Sorted, so two documents naming the same targets in different orders resolve to the same
    # tuple: a grant is a set, the typing order carries no meaning, and a stable order stops a
    # caller (or a test) from passing on incidental document order.
    return tuple(sorted(names)), None


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
    allowed, refusal = _parse_name_list(
        data, "apps", noun="app names", source=source, digest=digest
    )
    if refusal is not None:
        return refusal
    # `DCU-5`'s third grant, parsed by the SAME validator as `apps` so both lists refuse the
    # same malformed shapes rather than one of them growing a normalisation the other refuses.
    # The one extra rule is `known=`: the computer-use tool surface is a CLOSED vocabulary, so
    # a name outside it can be caught here instead of silently matching nothing at the seam —
    # a typo'd grant that fails closed is safe but invisible, and an operator who wrote it is
    # entitled to be told the document is wrong rather than left wondering why their job never
    # runs. `apps` gets no such rule because no closed set of application names exists.
    unattended, refusal = _parse_name_list(
        data,
        UNATTENDED_KEY,
        noun="computer-use tool names",
        source=source,
        digest=digest,
        known=TOOL_NAMES,
    )
    if refusal is not None:
        return refusal
    return EnableState(
        enabled=True,
        source=source,
        digest=digest,
        apps=allowed,
        unattended=unattended,
        detail=(
            f"enabled out-of-band by {source} for {len(allowed)} allowlisted app(s)"
            if allowed
            else f"enabled out-of-band by {source} with an EMPTY app allowlist, so no app "
            "may be driven"
        ),
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


def allowed_apps() -> tuple[str, ...]:
    """The operator's allowlist for this process — the ONE reader of ``EnableState.apps``.

    Empty means NO app may be driven. It never means "all": treating the unset list as
    everything would turn the narrower of the operator's two grants into the widest possible
    one, which is the fail-open this whole module exists to prevent. So an armed process with
    an empty allowlist runs perfectly happily and drives nothing until a human names a
    target — the capability being on and the capability having somewhere to point are
    separate facts, and this is the one that is safe to get wrong in the strict direction.

    Single reader on purpose, and not a stylistic one: :func:`require_enabled`'s docstring
    records what happened while ``enabled`` briefly had two readers — forcing one of them to
    return True left every refusal test GREEN. A second place that reaches into
    ``state.apps`` is a second place that can default, normalise or widen it differently
    from this one, and the divergence would only be visible on the day it mattered.
    """
    return active_enable_state().apps


def unattended_tools() -> tuple[str, ...]:
    """The tools an UNATTENDED run may invoke — the ONE reader of ``EnableState.unattended``.

    Empty means no unattended run drives anything, and it never means "all", for the reason
    :func:`allowed_apps` gives at length: the unset list resolving to everything would turn the
    strictest of the three grants into the widest. An armed machine with an allowlisted app and
    no ``unattended`` entry is the ordinary posture — the agent drives that app while a human is
    watching the session, and a cron fire is refused (`DCU-5`).

    Single reader for the same measured reason ``apps`` has one: while ``enabled`` briefly had
    two, forcing one of them to return True left every refusal test GREEN.
    """
    return active_enable_state().unattended


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
            f'A human must write {path} containing {ENABLE_DOCUMENT}, with "apps" listing '
            "the applications this agent may drive (names are matched exactly, and an empty "
            "list allows none), then restart "
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
    # Through the accessor, never off ``state.apps``: see :func:`allowed_apps`.
    allowed = allowed_apps()
    if state.enabled:
        # WARNING, not INFO: an armed keystone is the loudest posture this process can be
        # in, and it should be legible in a log an operator skims rather than greps. The
        # allowlist rides along because "armed" and "armed for what" are different facts to
        # an operator reading this line, and the second one is the blast radius.
        logger.warning(
            "computer_use: desktop drive is ENABLED by %s (digest %s) for %s",
            state.source,
            state.digest or "none",
            ", ".join(allowed) if allowed else "NO apps (the allowlist is empty)",
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
                f"apps={','.join(allowed) or 'none'} detail={state.detail}"
            ),
        )
    except Exception:
        logger.debug("computer-use keystone boot SEL audit failed", exc_info=True)
    return state
