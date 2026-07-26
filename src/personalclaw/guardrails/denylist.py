"""Path/action denylist honored by ALL action providers (AUTONOMY-GUARDRAILS §1.2).

Action providers are pluggable — apps deliver them (``apps/webhook-action``) — so
enforcement CANNOT rely on provider cooperation. ``check_action`` is called at the
THREE dispatch seams every action-provider execution passes through (script hooks,
scheduled jobs, memory-event triggers), so an app-contributed provider inherits the
denylist without knowing it exists.

This is defense-in-depth, not a sandbox: it composes with the always-on built-ins
(``security.is_sensitive_path``, ``BUILTIN_DENIED_COMMAND_PATTERNS``) and the OS
child sandbox, which remains the containment story. What it adds is a *path-level*
denylist for autonomous action-provider runs — the machine-readable analog of the
loop-constraints, configurable via ``security.autonomy_denylist``.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass

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
    """Match ``path`` against a user glob, honoring ``~`` and ``**`` loosely.

    Uses fnmatch on both the raw and the ``~``-expanded path so a rule written as
    ``~/.ssh/**`` catches an absolute ``/home/u/.ssh/id_rsa``. ``**`` is treated as
    ``*`` (fnmatch has no recursive glob) — acceptable for a defense-in-depth deny.
    """
    expanded = os.path.expanduser(os.path.expandvars(path))
    pat = pattern.replace("**", "*")
    pat_expanded = os.path.expanduser(pat)
    return any(
        fnmatch.fnmatch(candidate, p) for candidate in {path, expanded} for p in {pat, pat_expanded}
    )


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


def check_action(provider_name: str, action_config: dict, ctx: object = None) -> DenyDecision:
    """Check one action-provider execution against the composed denylist.

    Order (first match wins): built-in sensitive-path check on any path-carrying
    config value → operator ``autonomy_denylist`` path globs → built-in +
    operator denied-command patterns against any command string. Returns an
    ``allowed`` decision when nothing matches.
    """
    from personalclaw.security import BUILTIN_DENIED_COMMAND_PATTERNS, is_sensitive_path

    config_rules, denied_cmd_patterns = _load_config_rules()
    paths = _config_paths(action_config)

    # 1. Built-in sensitive-path denylist (always on) — a credential dir/file.
    for p in paths:
        if is_sensitive_path(p):
            return DenyDecision(
                blocked=True,
                verdict="block",
                reason=f"action targets a sensitive path: {p}",
                matched="builtin:sensitive_path",
            )

    # 2. Operator path-glob rules (verdict block | needs_human).
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

    # 3. Command patterns (built-in self-tamper/destructive + operator regexes).
    commands = _config_commands(action_config)
    if commands:
        all_patterns = list(BUILTIN_DENIED_COMMAND_PATTERNS) + denied_cmd_patterns
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


def enforce_action(provider_name: str, action_config: dict, ctx: object = None) -> DenyDecision:
    """``check_action`` + SEL audit + (for needs_human) a needs-input notification.

    The seam wrapper the three dispatch points call. On a block/needs_human it
    logs to the SEL (same as egress/skill-install guards) and, for ``needs_human``,
    fires a needs-input notification with the matched rule so the action isn't
    silently dropped. Returns the decision; the caller short-circuits to a blocked
    ActionResult when ``blocked`` is True.
    """
    decision = check_action(provider_name, action_config, ctx)
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
                    "warning",
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
