"""Diff-aware required-check selection (§1.4).

Maps touched file areas to profiles that a change MUST satisfy regardless of what its task
spec declares. The rule (plan §1.2): the spec author can ADD requirements; the diff can
only add more, never remove. So touching a sensitive area (chat/SSE emission, a config
dataclass, an action provider) forces the profile that guards it even if the task spec
forgot to list it.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class ForceRule:
    """If any changed file matches ``glob``, force ``profile``. ``reason`` is shown to the
    executor so the forcing is legible ("forced replay because you touched chat stream")."""

    glob: str
    profile: str
    reason: str


# Ordered; a file can match several rules and force several profiles. Globs are matched
# against repo-relative POSIX paths. These encode the plan's named couplings plus the
# obvious "touching web/ forces the web gate" and "touching Python forces fast tests".
_FORCE_RULES: tuple[ForceRule, ...] = (
    ForceRule(
        "web/src/pages/chat/*",
        "replay",
        "chat stream touched — replay guards the K42/K44/K45 coalescer bug class",
    ),
    ForceRule(
        "web/src/pages/loops/*",
        "replay",
        "run stream touched — replay guards the run-fold state machine",
    ),
    ForceRule(
        "src/personalclaw/dashboard/sse.py",
        "replay",
        "SSE registry touched — replay guards journal→widget-stream fidelity",
    ),
    ForceRule("web/*", "web", "frontend touched — the web gate (typecheck + vitest) applies"),
    ForceRule("web/**/*", "web", "frontend touched — the web gate (typecheck + vitest) applies"),
    ForceRule(
        "src/personalclaw/config/loader.py",
        "scan",
        "config dataclass touched — the config-four-points scanner check applies",
    ),
    ForceRule(
        "src/personalclaw/action_providers/*",
        "scan",
        "action provider touched — the hook-provider-parity scanner check applies",
    ),
    ForceRule("src/personalclaw/*", "scan", "core Python touched — run the boundary scanner"),
    ForceRule("src/personalclaw/**/*", "scan", "core Python touched — run the boundary scanner"),
    ForceRule("apps/**/*", "scan", "app code touched — the app-sdk-boundary scanner check applies"),
)


@dataclass
class Forced:
    """A forced profile and the reasons it was forced (may be several)."""

    profile: str
    reasons: list[str]


def forced_profiles(changed_files: list[str]) -> list[Forced]:
    """Return the profiles forced by the changed file set, each with its reason(s).

    Deterministic order (first force wins for ordering); reasons accumulate so the
    executor sees every trigger. Matches both ``*`` (one segment) and ``**`` (recursive)
    globs — we include both forms so a single-level and nested match are covered.
    """
    forced: dict[str, list[str]] = {}
    order: list[str] = []
    for rule in _FORCE_RULES:
        for f in changed_files:
            if _matches(f, rule.glob):
                if rule.profile not in forced:
                    forced[rule.profile] = []
                    order.append(rule.profile)
                if rule.reason not in forced[rule.profile]:
                    forced[rule.profile].append(rule.reason)
                break
    return [Forced(profile=p, reasons=forced[p]) for p in order]


def _matches(path: str, glob: str) -> bool:
    """fnmatch with ``**`` treated as recursive. ``a/**/*`` matches any depth under ``a/``;
    ``a/*`` matches only direct children."""
    if "**" in glob:
        prefix = glob.split("**", 1)[0].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatch(path, glob)
