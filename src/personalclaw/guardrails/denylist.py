"""Path/action denylist honored by ALL action providers (AUTONOMY-GUARDRAILS §1.2).

Action providers are pluggable — apps deliver them (``apps/webhook-action``) — so
enforcement CANNOT rely on provider cooperation. ``check_action`` is called at the
THREE dispatch seams every action-provider execution passes through (script hooks,
scheduled jobs, memory-event triggers), so an app-contributed provider inherits the
denylist without knowing it exists.

This is defense-in-depth, not a sandbox: it composes with the always-on built-ins
(``security.is_sensitive_path``, ``baseline_denied_command_patterns``) and the OS
child sandbox, which remains the containment story. What it adds is a *path-level*
denylist for autonomous action-provider runs — the machine-readable analog of the
loop-constraints, configurable via ``security.autonomy_denylist``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from personalclaw import notification_kinds
from personalclaw.guardrails.registries import path_glob

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


# Keys in an action_config whose VALUES are filesystem paths worth checking. Kept
# broad but explicit — an app provider's config is free-form, so we scan the common
# path-carrying keys rather than guess every field.
_PATH_KEYS = ("path", "file", "filename", "dest", "destination", "target", "cwd", "output")
# Keys whose values are shell command strings (bash/run-script providers).
_COMMAND_KEYS = ("command", "cmd", "script", "args")


def _config_paths(action_config: dict) -> list[str]:
    out: list[str] = []
    for key in _PATH_KEYS:
        v = action_config.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v)
        elif isinstance(v, list):
            out.extend(str(x) for x in v if isinstance(x, str))
    return out


def _config_commands(action_config: dict) -> list[str]:
    out: list[str] = []
    for key in _COMMAND_KEYS:
        v = action_config.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v)
        elif isinstance(v, list):
            out.append(" ".join(str(x) for x in v))
    return out


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


def _load_config_rules() -> tuple[list[DenyRule], list[str]]:
    """(rules, denied_command_patterns) from ``security`` config. Fail-open to
    empty on any read error — the built-in checks below still apply."""
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
        return rules, list(getattr(sec, "denied_commands", []) or [])
    except Exception:
        logger.debug("denylist config read failed (fail-open to built-ins)", exc_info=True)
        return [], []


def check_action(
    provider_name: str, action_config: dict, ctx: object = None, session_key: str = ""
) -> DenyDecision:
    """Check one action-provider execution against the composed denylist.

    Order (first match wins): built-in sensitive-path check on any path-carrying
    config value → operator ``autonomy_denylist`` path globs (unioned with the
    session's SafetyProfile ``denylist_extra``) → the host-lifecycle self-destruct
    guard on an unattended run (WF2AUT-14) → built-in + operator denied-command
    patterns against any command string. Returns an ``allowed`` decision when
    nothing matches.

    ``session_key`` identifies the run so its SafetyProfile can layer extra path
    globs onto the operator denylist. Every named profile ships
    ``denylist_extra=()``, so this is a no-op until a profile/operator sets globs.
    """
    from personalclaw.security import baseline_denied_command_patterns, is_sensitive_path

    config_rules, denied_cmd_patterns = _load_config_rules()
    paths = _config_paths(action_config)

    # The session's SafetyProfile can layer extra path globs (§3 ``denylist_extra``) and
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
    for p in paths:
        if is_sensitive_path(p):
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=f"action targets a sensitive path: {p}",
                matched="builtin:sensitive_path",
            )

    # 1b. Confinement (the ceiling's ``paths`` allow plane): when the resolved profile
    # carries an allow-list, a path-carrying action config value that matches NONE of it
    # is refused. This is the closed stance — deny unless allowed — and it is the one
    # check here that cannot be expressed as a denylist, which is why the ceiling has an
    # allow plane at all. Empty allow-list = unconfined, so the default is unchanged.
    if path_allowlist:
        for p in paths:
            if not any(_glob_match(p, pattern) for pattern in path_allowlist):
                return DenyDecision(
                    blocked=True,
                    verdict="block",
                    reason=(
                        f"action path {p!r} is outside the paths this run is confined to "
                        f"({', '.join(path_allowlist)})"
                    ),
                    matched="ceiling:paths.allow",
                )

    # 2. Operator path-glob rules (verdict block | needs_human), unioned with the
    # session profile's extra globs (which block with no needs_human escalation).
    for rule in config_rules:
        for pattern in rule.paths:
            for p in paths:
                if _glob_match(p, pattern):
                    return DenyDecision(
                        blocked=True,
                        verdict=(
                            rule.verdict if rule.verdict in ("block", "needs_human") else "block"
                        ),
                        reason=f"action path {p!r} matches deny rule {pattern!r}",
                        matched=f"config:{pattern}",
                    )
    for pattern in profile_globs:
        for p in paths:
            if _glob_match(p, pattern):
                return DenyDecision(
                    blocked=True,
                    verdict="block",
                    reason=f"action path {p!r} matches profile deny glob {pattern!r}",
                    matched=f"profile:{pattern}",
                )

    commands = _config_commands(action_config)

    # 3. 🔴 HOST-LIFECYCLE EFFECT on an unattended run (WF2AUT-14): an action that would
    # restart/stop/reinstall/update the gateway EXECUTING it. Placed BEFORE the pattern step
    # deliberately, so the legible, effect-named refusal wins over the baseline's
    # `.*personal.?claw restart.*` for the one spelling both catch — and so the shapes that
    # regex misses (`personalclaw stop`, `personalclaw service uninstall`, `PC=personalclaw;
    # $PC restart`) are caught at all. Classification is on the EFFECT, never on literal text:
    # see `guardrails/self_destruct.py` for the measurement and for why this is DISTINCT from
    # WF2AUT-9's `skip_if_active` liveness guard (`triggers/service.py:547`).
    for cmd in commands:
        from personalclaw.guardrails.self_destruct import unattended_host_effect

        effect = unattended_host_effect(cmd, session_key)
        if effect is not None:
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=effect.reason(),
                matched=f"self_destruct:{effect.kind}",
            )

    # 4. Command patterns (built-in self-tamper/destructive + operator regexes).
    if commands:
        # Same packaged baseline the native bash screen enforces, re-asserted on read —
        # action-provider dispatch and `execute_bash` can never drift apart.
        baseline = baseline_denied_command_patterns()
        all_patterns = list(baseline) + [p for p in denied_cmd_patterns if p not in set(baseline)]
        for cmd in commands:
            low = cmd.lower()
            for pat in all_patterns:
                try:
                    if re.search(pat, low):
                        return DenyDecision(
                            blocked=True,
                            verdict="block",
                            reason=f"action command matches denied pattern {pat!r}",
                            matched=f"cmd:{pat}",
                        )
                except re.error:
                    continue

    return DenyDecision()


def enforce_action(
    provider_name: str, action_config: dict, ctx: object = None, session_key: str = ""
) -> DenyDecision:
    """``check_action`` + SEL audit + (for needs_human) a needs-input notification.

    The seam wrapper the three dispatch points call. On a block/needs_human it
    logs to the SEL (same as egress/skill-install guards) and, for ``needs_human``,
    fires a needs-input notification with the matched rule so the action isn't
    silently dropped. Returns the decision; the caller short-circuits to a blocked
    ActionResult when ``blocked`` is True.

    ``session_key`` is threaded to ``check_action`` so the run's SafetyProfile can
    layer extra deny globs (§3 ``denylist_extra``).
    """
    decision = check_action(provider_name, action_config, ctx, session_key)
    if not decision.blocked:
        return decision
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"action:{provider_name}",
            operation="guardrails.denylist",
            outcome="blocked" if decision.verdict == "block" else "needs_human",
            source="guardrails",
            resources=f"{decision.matched} — {decision.reason}",
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
