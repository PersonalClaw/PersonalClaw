"""The test five source comments and eighteen roadmap docs already cite by name.

`apps/manifest.py` says of `PROVIDER_TYPES`:

    NOTE: this set MUST equal the runtime type-handler registry
    (providers/registry.py register_type_handler(...) calls). … `prompt` was a registered
    handler (PromptTypeHandler) but was missing here (#47) — so `ProviderConfig.validate()`
    rejected any prompt provider manifest, blocking reinstall/update + third-party prompt
    providers. native-prompts is native (auto-seeded, bypasses install-time validation), which
    masked it. test_manifest_types_match_handlers guards this equality going forward.

**It did not exist.** `providers/registry.py` cites it three times ("the bug class
`test_manifest_types_match_handlers` exists to prevent"), `workflows/defs.py` once, and eighteen
docs under `docs/` name it. A repo-wide grep found zero implementations. The 19 = 19 equality was
true today and unguarded — held by luck, with a #47 recurrence one forgotten line away.

Both directions matter, and they fail differently:

* **declared but unregistered** — the manifest accepts a type nothing can instantiate, so an app
  installs and then does nothing;
* **registered but undeclared** — the #47 shape: a working handler whose manifests are rejected at
  validation, invisible while only a native (auto-seeded) provider uses it.
"""

from __future__ import annotations

import pytest

from personalclaw.apps.manifest import PROVIDER_TYPES


@pytest.fixture
def registered(tmp_path, monkeypatch) -> set[str]:
    """The live handler registry. Isolated home — building it constructs real providers."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.providers.registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    return set(reg.get_provider_registry()._type_handlers)


def test_every_declared_type_has_a_runtime_handler(registered):
    """A manifest type nothing can instantiate installs an app that then does nothing."""
    missing = sorted(set(PROVIDER_TYPES) - registered)
    assert not missing, (
        "PROVIDER_TYPES declares types with no registered handler, so a manifest using one "
        f"validates and then cannot be instantiated: {missing}"
    )


def test_every_runtime_handler_is_a_declared_type(registered):
    """The #47 shape: a working handler whose manifests `validate()` rejects."""
    undeclared = sorted(registered - set(PROVIDER_TYPES))
    assert not undeclared, (
        "these types have a registered handler but are absent from PROVIDER_TYPES, so "
        "ProviderConfig.validate() rejects every third-party manifest using them while a native "
        f"auto-seeded provider masks it (this is exactly #47): {undeclared}"
    )


def test_the_comparison_is_not_vacuous(registered):
    """Both sides are populated. Two empty sets are equal and prove nothing."""
    assert len(PROVIDER_TYPES) >= 15, f"PROVIDER_TYPES looks truncated: {sorted(PROVIDER_TYPES)}"
    assert len(registered) >= 15, f"the handler registry looks truncated: {sorted(registered)}"


#: The three types whose handler is an `EntitySeamHandler` — enable/disable + Settings seams
#: only, whose factory returns None BY DESIGN because the entity lives elsewhere (agents in
#: `config.json agents{}`, skills in the skills store, notifications in the kinds registry).
#: Pinned so a FOURTH seam-only type cannot appear silently: a seam that nobody meant to be a
#: seam is a provider type that installs and produces nothing, which reads exactly like the
#: unregistered case above but passes it.
SEAM_ONLY_TYPES = frozenset({"agent", "notification", "skills"})


def test_the_seam_only_types_are_exactly_the_declared_three(tmp_path, monkeypatch):
    """Which types are seams is a DECISION; this is where it is written down."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.providers.registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    handlers = reg.get_provider_registry()._type_handlers
    seams = {t for t, h in handlers.items() if isinstance(h, reg.EntitySeamHandler)}
    assert seams == SEAM_ONLY_TYPES, (
        "the set of seam-only provider types changed. A new seam is fine — say so here and say "
        f"where its entity lives. expected {sorted(SEAM_ONLY_TYPES)}, found {sorted(seams)}"
    )


def test_a_seam_handler_documents_where_its_entity_lives(tmp_path, monkeypatch):
    """A seam is only defensible if it names the real source of truth.

    Otherwise "the factory returns None by design" is indistinguishable from a handler somebody
    forgot to finish — and `EntitySeamHandler`'s own comment warns that registering an instance
    would create "a second source of truth nothing reads (the Bedrock trap)".
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.providers.registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    handlers = reg.get_provider_registry()._type_handlers
    for t in sorted(SEAM_ONLY_TYPES):
        source = getattr(handlers[t], "source_of_truth", "")
        assert (
            source and len(source) > 20
        ), f"the {t!r} seam does not say where its entity actually lives: {source!r}"


def test_the_manifest_note_points_at_this_file():
    """The comment claims a guard exists. Now it does — and this keeps the claim honest.

    The note survived as a promise for the whole life of the invariant it describes; a test that
    can be renamed out from under five source citations would put it straight back.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src/personalclaw/apps/manifest.py"
    text = src.read_text(encoding="utf-8")
    assert "test_manifest_types_match_handlers" in text, (
        "manifest.py no longer names its guard — if this test is renamed, update every citation "
        "(manifest.py, providers/registry.py ×3, workflows/defs.py)"
    )
    assert Path(__file__).stem == "test_manifest_types_match_handlers", (
        "this file's name IS the citation five source comments make; renaming it silently makes "
        "all five false again"
    )
