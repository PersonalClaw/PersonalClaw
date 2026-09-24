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

One exemption, added by `EA-8`: an id whose provider ships in a first-party APP BUNDLE is
real but invisible to `list_action_providers()`, because an app is installed separately and
this suite installs none. Read as a phantom, that forces app-delivered providers to stay
UNCLASSIFIED just to keep this module green — which is how `webhook` came to sit in neither
table. So `APP_DELIVERED_PROVIDERS` names them explicitly, and two further tests keep the
exemption from becoming a hole: it may only excuse ids the tables actually carry, and it
expires by itself if core ever starts serving the name.
"""

from __future__ import annotations

from personalclaw.action_providers.registry import (
    _ensure_default_providers_registered,
    list_action_providers,
)
from personalclaw.triggers.screen import (
    APP_DELIVERED_PROVIDERS,
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
    """Every classified id names a provider the registry serves, or a declared app-delivered one.

    App-delivered ids are exempt because core's registry cannot see them — an app bundle is
    installed separately and this suite installs none — so their absence is expected rather
    than misleading. They must still be DECLARED, which is what keeps this an exemption and
    not a hole; the two tests below hold that declaration honest.
    """
    registered = _registered()
    classified = READ_ONLY_PROVIDERS | WRITE_CAPABLE_PROVIDERS
    phantom = sorted(classified - registered - APP_DELIVERED_PROVIDERS)
    assert not phantom, (
        "These ids are classified in triggers/screen.py but no provider answers to them, so "
        "the line fences nothing and misleads anyone auditing the table:\n  "
        + "\n  ".join(phantom)
        + "\n\nIf the provider ships in a first-party app bundle, add it to "
        "APP_DELIVERED_PROVIDERS instead of removing the classification."
    )


def test_every_app_delivered_id_is_actually_classified() -> None:
    """The exemption may only excuse ids the tables really carry.

    An entry naming nothing classified is dead weight that silently widens the exemption for
    whoever adds the id later — the same "line that looks like a decision and is not" this
    module exists to prevent, one level up.
    """
    assert (
        APP_DELIVERED_PROVIDERS
    ), "the exemption set is empty — delete it rather than keeping a stub"
    classified = READ_ONLY_PROVIDERS | WRITE_CAPABLE_PROVIDERS
    unused = sorted(APP_DELIVERED_PROVIDERS - classified)
    assert not unused, (
        "APP_DELIVERED_PROVIDERS names ids that neither capability table classifies, so the "
        "exemption excuses nothing and only pre-authorises a future phantom:\n  "
        + "\n  ".join(unused)
    )


def test_no_app_delivered_id_is_secretly_core_native() -> None:
    """If core starts serving the name itself, the exemption must go.

    Left in place it would mask a genuine phantom for that id forever, which is precisely the
    failure the phantom rail catches — so the exemption has to expire on its own.
    """
    still_absent = APP_DELIVERED_PROVIDERS - _registered()
    assert still_absent == APP_DELIVERED_PROVIDERS, (
        "these ids are now served by core's own registry, so they are no longer app-delivered "
        "— drop them from APP_DELIVERED_PROVIDERS so the phantom rail covers them again:\n  "
        + "\n  ".join(sorted(APP_DELIVERED_PROVIDERS - still_absent))
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
