"""The declared provider-type set must equal the runtime type-handler registry.

`apps/manifest.py`'s `PROVIDER_TYPES` carries this note, verbatim:

    NOTE: this set MUST equal the runtime type-handler registry
    (providers/registry.py register_type_handler(...) calls). ``prompt`` was a
    registered handler (PromptTypeHandler) but was missing here (#47, the split-era
    #1-'action'-rejected class) — so ProviderConfig.validate() rejected any prompt
    provider manifest, blocking reinstall/update + third-party prompt providers.
    native-prompts is native (auto-seeded, bypasses install-time validation), which
    masked it. test_manifest_types_match_handlers guards this equality going forward.

**That guard did not exist.** Measured 2026-09-01 on `origin/main`:
`git grep -l 'def test_manifest_types_match_handlers' -- tests/` returned **zero files**,
while `test_manifest_types_match_handlers` was cited **46 times across 18 files** —
`docs/roadmap/atomic/{DAS,EI,INU,TSE,WF2AUT,WS}.md`, ten plans, and **six `dag.json`
`done_when` entries**. `DAS-6`, `EI-1`, `TSE-4`, `WF2AUT-4` and `WF2AUT-8` each declare
"test_manifest_types_match_handlers green/passes" as part of what made them done. Five
atoms rested their completion claim on a test nobody had written.

**The invariant itself held when this file was added** — 19 declared, 19 registered, both
directions empty — so nothing here changes behaviour. What changes is that #47's shape can
no longer recur in silence. The failure it guards is quiet and expensive in exactly one
direction: a handler registered without being declared makes `ProviderConfig.validate()`
reject every manifest of that type, so installing or updating such an app fails while a
NATIVE provider of the same type keeps working, because native providers are auto-seeded
and bypass install-time validation. That asymmetry is what masked it for a whole release.

**Read-only by construction.** `get_provider_registry()` returns a process-global singleton
whose `_type_handlers` other tests rely on, so this file only reads it. A test that
registered a handler to prove a point would leak that handler into every later test in the
session — the shape that once made a "one def, from my fake provider" assertion see seven.
"""

from __future__ import annotations

from personalclaw.apps.manifest import PROVIDER_TYPES
from personalclaw.providers.registry import get_provider_registry

#: A floor under both sets. Not a count to maintain — a guard that the comparison below is
#: comparing two POPULATED sets. Two empty sets are equal, so a registry that failed to
#: initialise, or an import that silently produced an empty frozenset, would satisfy the
#: equality while asserting nothing at all. 15 is comfortably under the 19 present when this
#: was written and comfortably over "something went wrong".
MINIMUM_PLAUSIBLE_TYPES = 15


def _registered_types() -> set[str]:
    """The types the runtime has a handler for, read without mutating the singleton."""
    return set(get_provider_registry()._type_handlers)


def test_manifest_types_match_handlers() -> None:
    """`PROVIDER_TYPES` and the handler registry name exactly the same provider types.

    Both directions, reported separately, because they break differently:

    * **registered but not declared** is #47 itself — `ProviderConfig.validate()` rejects
      every manifest of that type, so install and update fail for it while a native
      provider of the same type keeps working and hides the fault.
    * **declared but not registered** is the inverse — a manifest validates at install
      time and then finds nothing to enable it, so the app installs and does nothing.
    """
    declared = set(PROVIDER_TYPES)
    registered = _registered_types()

    registered_not_declared = sorted(registered - declared)
    declared_not_registered = sorted(declared - registered)

    assert not registered_not_declared, (
        "these provider types have a registered handler but are missing from "
        f"apps/manifest.PROVIDER_TYPES: {registered_not_declared}. This is #47: "
        "ProviderConfig.validate() will reject every app manifest declaring one of them, so "
        "installing or updating such an app fails — while a NATIVE provider of the same type "
        "keeps working, because native providers are auto-seeded and skip install-time "
        "validation. Add the type to PROVIDER_TYPES in the same commit as its handler."
    )
    assert not declared_not_registered, (
        "these provider types are declared in apps/manifest.PROVIDER_TYPES but no handler is "
        f"registered for them: {declared_not_registered}. An app declaring one would pass "
        "install-time validation and then find nothing able to enable it. Register the "
        "handler in providers/registry.py in the same commit as the declaration."
    )


def test_the_comparison_is_not_vacuous() -> None:
    """Both sets must be populated, or the equality above proves nothing.

    Two empty sets are equal. Without this floor, an import that yielded an empty
    `frozenset` or a registry singleton that failed to run its default registrations would
    make the test above pass while guarding nothing — the same failure mode as a rail whose
    pattern matches no files and therefore reports clean.
    """
    declared = set(PROVIDER_TYPES)
    registered = _registered_types()

    assert len(declared) >= MINIMUM_PLAUSIBLE_TYPES, (
        f"PROVIDER_TYPES holds only {len(declared)} entries ({sorted(declared)}), which is "
        "below the plausible floor — the equality test above would be comparing something "
        "degenerate rather than the real set"
    )
    assert len(registered) >= MINIMUM_PLAUSIBLE_TYPES, (
        f"the runtime registered only {len(registered)} type handlers ({sorted(registered)}). "
        "The registry's default registrations probably did not run, which would make the "
        "equality test above vacuous rather than green"
    )
