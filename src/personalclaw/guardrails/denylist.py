"""Path/action denylist honored by ALL action providers.

Action providers are pluggable — apps deliver them (``apps/webhook-action``) — so
enforcement CANNOT rely on provider cooperation. ``enforce_action`` is called at every
dispatch seam an action-provider execution passes through with nobody answering it (a
lifecycle hook, a stored trigger's fire, a webhook's fire, a view's refresh, a Run now an
agent or an app starts, a workflow run's action step, a dashboard tile's refresh, the
triage digest's auto-execution), so an app-contributed provider inherits the denylist
without knowing it exists. ``tests/test_action_provider_chokepoints.py`` fails a new
execution site that reaches a provider without it.

A command a path runs with nobody answering is asked the same rules (``check_command``):
a loop's check and a workflow's verify gate, a workflow's setup and teardown steps, an
effect's teardown, and the agent's bash tool in a session nobody is in. Each records a
refusal the way it records every refusal of a command it was about to run
(``command_audit``, or the tool call's own audit row), and
``tests/test_every_command_path_asks_the_denylist.py`` fails a new one that skips it. Among
those rules, such work may not delete the owner's home folder, the filesystem root or the
folder it runs in (``personalclaw.protected_folders``): a person must say yes to that, and
nobody is there to.

A refusal quotes the path or the command it refused as it was written: a dispatch that fills each
``{{secret:NAME}}`` in before the check (a trigger's fire, whoever asked for it, a workflow step,
the agent's bash tool) passes the writing too (``written``), so a refusal, the
security log and the gateway log name a secret by its reference and never hold its value.

This is defense-in-depth, not a sandbox: it composes with the always-on built-ins
(``security.is_sensitive_path``, the shell denylist ``security.denied_command``) and the OS
child sandbox, which remains the containment story. What it adds is a *path-level*
denylist for autonomous action-provider runs — the machine-readable analog of the
loop-constraints, configurable via ``security.autonomy_denylist``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from personalclaw import notification_kinds
from personalclaw.guardrails.registries import path_glob

if TYPE_CHECKING:
    from personalclaw.guardrails.self_destruct import HostEffect

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenyRule:
    """A path/action denylist rule.

    ``paths`` are globs (``~/.ssh/**``, ``**/.env*``); ``actions`` are action
    classes (``external-write``, ``delete``, ``credential-read``) — reserved for a
    future action-classification pass, matched today only when a caller tags the
    context. ``verdict`` is ``block`` (hard refuse) or ``needs_human`` (route to a
    needs-input notification with the payload attached).
    """

    paths: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    verdict: str = "block"


@dataclass
class DenyDecision:
    """The outcome of checking one action against the denylist."""

    blocked: bool = False
    verdict: str = ""  # "" (allow) | "block" | "needs_human"
    reason: str = ""
    matched: str = ""  # the rule/pattern that matched (for the SEL + user message)

    @property
    def allowed(self) -> bool:
        return not self.blocked

    def refusal(self) -> str:
        """What a refused action's run records: the rule that refused it (``matched``, its code)
        and why (``reason``, its sentence). One composition, so a workflow step and a trigger's fire
        refused by the same rule say the same thing. The sentence quotes what was refused as it was
        written (:func:`check_action`'s ``written``), a secret by its reference, never its value."""
        reason = self.reason or "blocked by a guardrail rule"
        if self.matched:
            return f"blocked by the guardrails denylist: {self.matched} — {reason}"
        return f"blocked by the guardrails denylist: {reason}"


# Keys in an action_config whose VALUES are filesystem paths worth checking. Kept
# broad but explicit — an app provider's config is free-form, so we scan the common
# path-carrying keys rather than guess every field.
_PATH_KEYS = ("path", "file", "filename", "dest", "destination", "target", "cwd", "output")
# Keys whose values are shell command strings (bash/run-script providers).
_COMMAND_KEYS = ("command", "cmd", "script", "args")


#: The opening of a ``{{secret:NAME}}`` reference, with or without the pipes a workflow step's
#: binding can add after the name.
_SECRET_REFERENCE = re.compile(r"\{\{\s*secret:")


def _as_written(value: str, written: object) -> str:
    """How a refusal quotes *value*, a path or a command the check judged: as it was *written* when
    that holds a ``{{secret:NAME}}`` reference, the reference left a reference, so no refusal, run
    record or log holds the value it was filled with; as it is otherwise. *written* is None when
    the caller filled nothing in."""
    if written is None:
        return value
    text = " ".join(str(w) for w in written) if isinstance(written, list) else str(written)
    return text if _SECRET_REFERENCE.search(text) else value


def _written_at(written: object, key: str) -> object:
    """What was written at *key* of an action config: the writing's own value there, or the whole
    writing when it is not a mapping (one binding a workflow step filled the config from)."""
    if written is None or not isinstance(written, dict):
        return written
    return written.get(key)


def _config_paths(action_config: dict, written: object = None) -> list[tuple[str, str]]:
    """Each path-carrying value, and how a refusal quotes it (:func:`_as_written`)."""
    out: list[tuple[str, str]] = []
    for key in _PATH_KEYS:
        v, w = action_config.get(key), _written_at(written, key)
        if isinstance(v, str) and v.strip():
            out.append((v, _as_written(v, w)))
        elif isinstance(v, list):
            each = w if isinstance(w, list) and len(w) == len(v) else [w] * len(v)
            out.extend((x, _as_written(x, each[i])) for i, x in enumerate(v) if isinstance(x, str))
    return out


def _config_commands(action_config: dict, written: object = None) -> list[tuple[str, str]]:
    """Each command string, and how a refusal quotes it (:func:`_as_written`)."""
    out: list[tuple[str, str]] = []
    for key in _COMMAND_KEYS:
        v, w = action_config.get(key), _written_at(written, key)
        if isinstance(v, str) and v.strip():
            out.append((v, _as_written(v, w)))
        elif isinstance(v, list):
            joined = " ".join(str(x) for x in v)
            out.append((joined, _as_written(joined, w)))
    return out


def _effect_as_written(effect: HostEffect, command: str, said: str) -> HostEffect:
    """*effect*, its operation quoting *command* as it was written (*said*): unchanged when every
    part of the command it quotes was written so, else quoting the writing whole, so a program or a
    target a secret was filled into is named by its reference."""
    quoted = re.findall(r"`([^`]*)`", effect.operation)
    if said == command or all(part in said for part in quoted):
        return effect
    return replace(effect, operation=f"`{said}`")


def _glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against a rule glob through the ENFORCER-OWNED matcher.

    One matcher, one behaviour: :func:`personalclaw.guardrails.registries.path_glob`
    normalizes only the queried item (``~``/``$VAR`` expansion then ``abspath``) and never
    runs the pattern through ``normpath``. The hand-rolled fnmatch this replaced compared
    an un-absolutized item, so a relative ``../../etc/passwd`` dodged a deny of ``/etc/**``
    by simply failing to match as a string, and ``**`` collapsed to ``*`` so ``~/.ssh/**``
    missed ``~/.ssh/sub/key``. Both were the wired-but-wrong class: the check ran every
    time and was still category-wrong.
    """
    return path_glob(path, pattern)


def _load_config_rules() -> list[DenyRule] | None:
    """The operator's path rules from ``security`` config, or ``None`` on a failed read.

    TWO outcomes, deliberately distinguishable — the same three-way discipline
    ``providers/entity_routes._load_entity_settings`` and ``AppConfig.load`` now carry, with
    "absent" folded into the empty-tuple case because an operator who declared no rules and one
    who has no config file are asking for the same thing:

    * a list — the operator's rules, as stored (possibly empty, which means "I denied nothing").
    * ``None`` — the config could not be read, so nothing is known about what they denied.

    It used to return ``([], [])`` for the second, described as "fail-open to empty ... the
    built-in checks below still apply", and that sentence is the defect: the built-ins are a
    different, narrower control (a fixed sensitive-path list and a fixed command-pattern list),
    so "the built-ins still apply" is not a reason the *operator's* rules may be dropped. A
    denylist that could not be read has not said "nothing is denied" — it has said it does not
    know, and substituting the first for the second converts an unknown into a permission.

    This loader picks no fallback, because a denylist has no expressible restrictive value: the
    restrictive answer is "everything is denied", which a list cannot say. ``check_action``
    resolves it instead, by refusing the action — a restrictive posture that invents nothing.
    """
    try:
        from personalclaw.config.loader import AppConfig

        sec = AppConfig.load().security
        rules = [
            DenyRule(
                paths=tuple(r.get("paths", []) or []),
                actions=tuple(r.get("actions", []) or []),
                verdict=str(r.get("verdict", "block")),
            )
            for r in (getattr(sec, "autonomy_denylist", []) or [])
            if isinstance(r, dict)
        ]
        return rules
    except Exception:
        logger.warning("denylist config read failed — refusing the action", exc_info=True)
        return None


def check_action(
    provider_name: str,
    action_config: dict,
    ctx: object = None,
    session_key: str = "",
    *,
    shell_denylist: bool = True,
    written: object = None,
    cwd: str = "",
) -> DenyDecision:
    """Check one action-provider execution against the composed denylist.

    Order (first match wins): built-in sensitive-path check on any path-carrying
    config value → operator ``autonomy_denylist`` path globs (unioned with the
    session's SafetyProfile ``denylist_extra``) → the host-lifecycle self-destruct
    guard on an unattended run → a delete of a protected folder on an unattended
    run → built-in + operator denied-command patterns against any command string. Returns an
    ``allowed`` decision when nothing matches.

    A command runs in the folder *cwd*, ``""`` being this process's own, where an action's command
    runs (``BashActionProvider`` starts its shell with no folder of its own): an unattended run is
    refused a delete of that folder.

    ``session_key`` identifies the run so its SafetyProfile can layer extra path
    globs onto the operator denylist. Every named profile ships
    ``denylist_extra=()``, so this is a no-op until a profile/operator sets globs.

    ``shell_denylist=False`` leaves out the last rule, the shell denylist, for a command path
    that asks it itself right after and words its refusal as every command path does
    (``security.denied_command``): the rules before it are asked as they are for an action, so
    an effect-named refusal still wins over a pattern that also catches the command.

    ``written`` is the action config as it was written, when the dispatch filled its
    ``{{secret:NAME}}`` references in before asking (``triggers.secrets.resolve_for``, a workflow
    step's bindings, the bash tool): the rules judge *action_config*, what the provider is handed,
    and each reason quotes a path or a command as it was written (:func:`_as_written`).

    FAIL-CLOSED on an unreadable ``security`` config, before any other check: if the operator's
    rules cannot be read then no composed decision below is trustworthy, so the honest answer is
    a refusal naming the reason rather than a verdict computed from half the denylist. Checked
    first so the message the user sees is *why* everything is being refused, not whichever
    built-in happened to match. Only reachable on an unexpected raise — since #3424 a corrupt
    ``config.json`` resolves fail-closed by value instead of raising.
    """
    from personalclaw.security import denied_command, is_sensitive_path

    config_rules = _load_config_rules()
    if config_rules is None:
        return DenyDecision(
            blocked=True,
            verdict="block",
            reason=(
                "the security config could not be read, so the operator's denylist is unknown — "
                "refusing rather than acting on an empty one"
            ),
            matched="config:unreadable",
        )
    paths = _config_paths(action_config, written)

    # The session's SafetyProfile can layer extra path globs (``denylist_extra``) and
    # CONFINE the run to an allow-list (the ``paths`` ceiling scope). Read lazily + only
    # when a session identity is known.
    profile_globs: tuple[str, ...] = ()
    path_allowlist: tuple[str, ...] = ()
    if session_key:
        from personalclaw.guardrails.policy import profile_for_session

        profile = profile_for_session(session_key)
        profile_globs = profile.denylist_extra
        path_allowlist = profile.path_allowlist

    # 1. Built-in sensitive-path denylist (always on) — a credential dir/file.
    for p, said in paths:
        if is_sensitive_path(p):
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=f"action targets a sensitive path: {said}",
                matched="builtin:sensitive_path",
            )

    # 1b. Confinement (the ceiling's ``paths`` allow plane): when the resolved profile
    # carries an allow-list, a path-carrying action config value that matches NONE of it
    # is refused. This is the closed stance — deny unless allowed — and it is the one
    # check here that cannot be expressed as a denylist, which is why the ceiling has an
    # allow plane at all. Empty allow-list = unconfined, so the default is unchanged.
    if path_allowlist:
        for p, said in paths:
            if not any(_glob_match(p, pattern) for pattern in path_allowlist):
                return DenyDecision(
                    blocked=True,
                    verdict="block",
                    reason=(
                        f"action path {said!r} is outside the paths this run is confined to "
                        f"({', '.join(path_allowlist)})"
                    ),
                    matched="ceiling:paths.allow",
                )

    # 2. Operator path-glob rules (verdict block | needs_human), unioned with the
    # session profile's extra globs (which block with no needs_human escalation).
    for rule in config_rules:
        for pattern in rule.paths:
            for p, said in paths:
                if _glob_match(p, pattern):
                    return DenyDecision(
                        blocked=True,
                        verdict=(
                            rule.verdict if rule.verdict in ("block", "needs_human") else "block"
                        ),
                        reason=f"action path {said!r} matches deny rule {pattern!r}",
                        matched=f"config:{pattern}",
                    )
    for pattern in profile_globs:
        for p, said in paths:
            if _glob_match(p, pattern):
                return DenyDecision(
                    blocked=True,
                    verdict="block",
                    reason=f"action path {said!r} matches profile deny glob {pattern!r}",
                    matched=f"profile:{pattern}",
                )

    commands = _config_commands(action_config, written)

    # 3. 🔴 HOST-LIFECYCLE EFFECT on an unattended run: an action that would
    # restart/stop/reinstall/update the gateway EXECUTING it. Placed BEFORE the pattern step
    # deliberately, so the legible, effect-named refusal wins over the baseline's
    # `.*personal.?claw restart.*` for the one spelling both catch — and so the shapes that
    # regex misses (`personalclaw stop`, `personalclaw service uninstall`, `PC=personalclaw;
    # $PC restart`) are caught at all. Classification is on the EFFECT, never on literal text:
    # see `guardrails/self_destruct.py` for the measurement and for why this is DISTINCT from
    # The `skip_if_active` liveness guard (`triggers/service.py:547`).
    for cmd, said in commands:
        from personalclaw.guardrails.self_destruct import unattended_host_effect

        effect = unattended_host_effect(cmd, session_key)
        if effect is not None:
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=_effect_as_written(effect, cmd, said).reason(),
                matched=f"self_destruct:{effect.kind}",
            )

    # 3b. A DELETE OF A PROTECTED FOLDER on an unattended run: the owner's home folder, the
    # filesystem root or the folder the command runs in, read on the command as its shell reads it
    # (`protected_folders`). A person must answer such a delete whatever grant stands, and nobody
    # is here to, so it is refused, in words that name the folder.
    for cmd, _said in commands:
        if (gone := unattended_protected_delete(cmd, session_key, cwd=cwd)) is not None:
            return gone

    # 4. The shell denylist, through the one check every command path asks
    # (`security.denied_command`), so action dispatch and the bash tool cannot drift apart. The
    # reason is a clause, like every reason above: each seam words the refusal around it.
    for cmd, _said in commands if shell_denylist else ():
        if (denied := denied_command(cmd)) is not None:
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=denied.why(),
                matched=f"cmd:{denied.pattern}",
            )

    return DenyDecision()


def check_command(
    command: str, *, session_key: str, cwd: str = "", written: str | None = None
) -> DenyDecision:
    """:func:`check_action` for a command a command path is about to run in the folder *cwd*
    (``""``: this process's own, where a command given no folder runs), under *session_key*, the
    identity its work is judged by: every rule before the shell denylist, which the path asks
    itself next, in the words every command path gives its refusal (``security.denied_command``).
    *written* is the command as it was written, for a path that filled its ``{{secret:NAME}}`` in
    (the bash tool). The path records a refusal as it records any other (``command_audit``, or a
    tool call's own audit row)."""
    return check_action(
        "command",
        {"command": command},
        session_key=session_key,
        shell_denylist=False,
        written=None if written is None else {"command": written},
        cwd=cwd,
    )


def unattended_protected_delete(command: str, session_key: str, *, cwd: str) -> DenyDecision | None:
    """The refusal of *command* on an UNATTENDED run when it would delete the owner's home folder,
    the filesystem root or the folder *cwd* it runs in (``protected_folders``), or a path it does
    not name; None when it may proceed, or when a person is watching, whom the approval gate puts
    such a call to (``run_bounds``).

    Unattendedness is :func:`personalclaw.guardrails.policy.is_unattended_session`, as for the
    self-destruct guard, and an EMPTY key is unattended here too: a caller that cannot say who is
    running a command has not shown that a person is there to answer for it."""
    if not (command or "").strip():
        return None
    from personalclaw import protected_folders
    from personalclaw.guardrails.policy import is_unattended_session

    if session_key.strip() and not is_unattended_session(session_key):
        return None
    found = protected_folders.protected_delete(command, cwd=cwd)
    if not found:
        return None
    kinds = ",".join(found.kinds) or "unnamed"
    return DenyDecision(
        blocked=True,
        verdict="block",
        reason=(
            f"{protected_folders.clause(found)}, and nobody is here to allow it: a delete of "
            f"{protected_folders.ALWAYS_ASKS} always asks a person"
        ),
        matched=f"protected_folder:{kinds}",
    )


def enforce_action(
    provider_name: str,
    action_config: dict,
    ctx: object = None,
    session_key: str = "",
    *,
    written: object = None,
) -> DenyDecision:
    """``check_action`` + SEL audit + (for needs_human) a needs-input notification.

    The seam wrapper every unattended dispatch point calls (the module docstring names them).
    On a block/needs_human it logs to the SEL (same as egress/skill-install guards) and, for
    ``needs_human``, fires a needs-input notification with the matched rule so the action isn't
    silently dropped. Returns the decision; the caller refuses the action when ``blocked`` is
    True, recording :meth:`DenyDecision.refusal` where its run is recorded.

    ``session_key`` is threaded to ``check_action`` so the run's SafetyProfile can
    layer extra deny globs (``denylist_extra``), and ``written`` (the config as it was written,
    its secrets' references unfilled) so the refusal and its row quote it as written.
    """
    decision = check_action(provider_name, action_config, ctx, session_key, written=written)
    if not decision.blocked:
        return decision
    try:
        from personalclaw.audit_subject import audit_text
        from personalclaw.sel import sel

        # The command it would have run, as a refused command's row keeps it (`command_audit`): as
        # it was written, a secret by its reference.
        commands = (
            [said for _cmd, said in _config_commands(action_config, written)]
            if isinstance(action_config, dict)
            else []
        )
        sel().log_api_access(
            caller=f"action:{provider_name}",
            operation="guardrails.denylist",
            outcome="blocked" if decision.verdict == "block" else "needs_human",
            source="guardrails",
            resources=f"{decision.matched} — {decision.reason}",
            metadata={"command": audit_text("; ".join(commands))} if commands else None,
        )
    except Exception:
        logger.debug("denylist SEL audit failed", exc_info=True)
    if decision.verdict == "needs_human":
        try:
            from personalclaw.action_providers.services import get_action_services

            services = get_action_services()
            if services is not None and getattr(services, "state", None) is not None:
                services.state.notify(
                    notification_kinds.WARNING,
                    "Action needs your approval",
                    f"An automated action via {provider_name!r} was held: {decision.reason}. "
                    f"Review it in Settings → Guardrails.",
                    meta={"provider": provider_name, "matched": decision.matched},
                )
        except Exception:
            logger.debug("denylist needs_human notify failed", exc_info=True)
    logger.warning(
        "action denied (%s) for provider %r: %s", decision.verdict, provider_name, decision.reason
    )
    return decision
