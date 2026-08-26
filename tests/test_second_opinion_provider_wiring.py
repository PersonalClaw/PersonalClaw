"""EI-7 — the ``second-opinion`` action provider is wired at all four points it needs.

§4.1 states the failure this guards: "a new action provider that skips this is rejected by hook
create/update even though the UI offers it". A provider registered in one set and missing from
another validates, saves, and then fails at fire time — strictly worse than not offering it. So
the four points are asserted together:

1. the dispatch registry (``action_providers.registry``) resolves the name;
2. ``validation.ALLOWED_HOOK_PROVIDERS`` accepts it, so a trigger can be created;
3. ``guardrails.rungs`` carries an autonomy declaration for it (a registered provider with no
   declaration is indistinguishable from an ungoverned action at the dispatch seams); and
4. ``triggers.screen`` classifies it write-capable, so an unattended fire needs the opt-in.

Each assertion carries a vacuity floor: the same lookup against a name that does NOT exist must
fail. Otherwise a membership test against a set that answers "yes" to everything would pass.
"""

from __future__ import annotations

import asyncio

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.registry import (
    _ensure_default_providers_registered,
    get_action_provider,
)

_NAME = "second-opinion"
_ABSENT = "second-opinion-not-a-real-provider"


def test_the_provider_is_registered_in_the_dispatch_registry() -> None:
    _ensure_default_providers_registered()
    provider = get_action_provider(_NAME)
    assert provider is not None, "second-opinion is not in the dispatch registry"
    assert provider.name == _NAME
    # Floor: the registry does not answer for an arbitrary name.
    assert get_action_provider(_ABSENT) is None


def test_the_name_is_accepted_by_hook_validation() -> None:
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    assert _NAME in ALLOWED_HOOK_PROVIDERS
    assert _ABSENT not in ALLOWED_HOOK_PROVIDERS


def test_the_provider_has_an_autonomy_declaration() -> None:
    from personalclaw.guardrails.rungs import CORE_ACTION_TYPES

    declared = {p for spec in CORE_ACTION_TYPES for p in spec.providers}
    assert _NAME in declared, "second-opinion has no autonomy declaration behind it"
    assert _ABSENT not in declared


def test_the_provider_is_classified_write_capable() -> None:
    from personalclaw.triggers.screen import provider_is_read_only

    assert not provider_is_read_only(_NAME)
    # Floor: the classifier is not a constant — a genuinely read-only provider reads as one.
    assert provider_is_read_only("notify")


def test_the_registry_and_the_hook_allowlist_agree_about_this_name() -> None:
    """The specific drift §4.1 warns about, asserted in both directions for this provider."""
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    _ensure_default_providers_registered()
    registered = get_action_provider(_NAME) is not None
    allowed = _NAME in ALLOWED_HOOK_PROVIDERS
    assert registered == allowed == True  # noqa: E712 — the point is the pair, not the value


def test_a_handoff_with_no_origin_runner_is_refused_before_anything_fires() -> None:
    """Without the exclusion key there is no "different runner" property to enforce, so the
    provider refuses rather than defaulting to an empty exclusion."""
    _ensure_default_providers_registered()
    provider = get_action_provider(_NAME)
    assert provider is not None
    result = asyncio.run(
        provider.execute(
            {"goal": "g", "stuck_at": "s"},
            ActionContext(event="loop_stalled"),
        )
    )
    assert not result.success
    assert "origin_runner" in result.error


def test_missing_goal_or_stuck_at_is_refused() -> None:
    _ensure_default_providers_registered()
    provider = get_action_provider(_NAME)
    assert provider is not None
    result = asyncio.run(
        provider.execute({"origin_runner": "codex"}, ActionContext(event="loop_stalled"))
    )
    assert not result.success
    assert "goal" in result.error
