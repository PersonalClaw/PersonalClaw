"""Every id in the capability-fence tables must name a REAL action provider.

`triggers/screen.py` splits providers into `READ_ONLY_PROVIDERS` (safe to auto-fire) and
`WRITE_CAPABLE_PROVIDERS` (needs an explicit opt-in). Its own comment says the list is
"spelled out rather than derived as 'everything else' so the security-relevant list is
greppable and reviewable in one place" — which only holds while every entry is a name the
registry actually serves.

`WRITE_CAPABLE_PROVIDERS` carried ``knowledge-maintain``, which is a MODULE name
(``knowledge_maintain_provider.py``), not a provider id. That module registers three
providers — ``knowledge-health``, ``knowledge-gaps``, ``knowledge-consolidate`` — and all
three were already classified correctly elsewhere in the tables. So the entry fenced
nothing: it was a line that looked like a security decision and was not.

It was harmless (`provider_is_read_only` fails CLOSED, so an unlisted name is treated as
write-capable anyway) but actively misleading in a table whose whole value is being
reviewable. A reader auditing "what can write?" saw a provider that does not exist, and
would not have found the three real ones by looking for it.

This test is the reason the entry cannot come back, and it also catches the more dangerous
inverse: a REAL provider that neither table classifies. That one is not merely untidy —
it silently inherits the fail-closed default, so a genuinely read-only provider added
without a line here becomes un-auto-fireable and its triggers stop working for a reason
nobody can grep.
"""

from __future__ import annotations

from personalclaw.action_providers.registry import (
    _ensure_default_providers_registered,
    list_action_providers,
)
from personalclaw.triggers.screen import (
    READ_ONLY_PROVIDERS,
    WRITE_CAPABLE_PROVIDERS,
)


def _registered() -> set[str]:
    # The registry lazily bootstraps its defaults; without this the list is empty and every
    # assertion below would pass vacuously.
    _ensure_default_providers_registered()
    return set(list_action_providers())


def test_registry_is_actually_populated() -> None:
    """Guard the guard: a silently-empty registry would make this whole module vacuous."""
    assert len(_registered()) > 10


def test_no_phantom_ids_in_the_capability_tables() -> None:
    """Every classified id names a provider the registry serves."""
    registered = _registered()
    classified = READ_ONLY_PROVIDERS | WRITE_CAPABLE_PROVIDERS
    phantom = sorted(classified - registered)
    assert not phantom, (
        "These ids are classified in triggers/screen.py but no provider answers to them, so "
        "the line fences nothing and misleads anyone auditing the table:\n  " + "\n  ".join(phantom)
    )


def test_no_provider_is_left_unclassified() -> None:
    """The inverse, and the dangerous one.

    `provider_is_read_only` fails closed, so a provider missing from BOTH tables is treated as
    write-capable — a read-only provider added without a line here quietly stops auto-firing.
    """
    registered = _registered()
    classified = READ_ONLY_PROVIDERS | WRITE_CAPABLE_PROVIDERS
    unclassified = sorted(registered - classified)
    assert not unclassified, (
        "These providers are registered but appear in neither capability table, so they inherit "
        "the fail-closed write-capable default. That is safe but silent — classify them "
        "explicitly:\n  " + "\n  ".join(unclassified)
    )


def test_the_two_tables_do_not_overlap() -> None:
    """A provider cannot be both. An id in both sets makes `provider_is_read_only` the only
    arbiter and the write-capable listing decorative — exactly the ambiguity the split exists
    to prevent."""
    both = sorted(READ_ONLY_PROVIDERS & WRITE_CAPABLE_PROVIDERS)
    assert not both, f"classified as BOTH read-only and write-capable: {both}"
