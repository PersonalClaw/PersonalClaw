"""SDK: the app-manifest schema types (type-only surface).

Re-export of the ``AppManifest`` schema an app's ``app.json`` conforms to, plus the
``ProviderConfig`` an app-contributed provider is described by. An app rarely needs to
construct these (the loader parses app.json for it), but they're the typed contract for
tooling that validates or generates a manifest.

Which is why the surface is the module's WHOLE public schema and not a chosen subset. It
used to publish five of the twenty-one types, and that made the promise above unkeepable
in two measurable ways: :meth:`AppManifest.core_compatibility` returns
``CoreCompatibility`` and :meth:`Permissions.proposal_kind` returns ``ProposalKind``, so
two published methods had unnameable return types; and ``AppManifest.ui`` / ``.platform``
/ ``.dependencies`` / ``.crons`` / ``.skills`` / ``.sources`` / ``.quality`` / ``.cli``
are fields whose types an app could not import. A generator cannot build a value it
cannot name, so the only way to use this module was to reach past it — which is exactly
what the boundary exists to prevent. ``tests/test_sdk_surface_is_public.py`` now asserts
the surface is CLOSED under its own signatures, so the subset cannot re-form.
"""

from personalclaw.apps.manifest import (  # noqa: F401
    AppManifest,
    AppSkill,
    AutonomyConfig,
    BackendConfig,
    CliConfig,
    ClientInstallConfig,
    CoreCompatibility,
    CronEntry,
    Dependencies,
    MarketplaceDependencies,
    PackSourceEntry,
    Permissions,
    PlatformConfig,
    ProposalKind,
    ProviderConfig,
    QualityDeclaration,
    RouteEntry,
    SetupConfig,
    UIConfig,
    UIPage,
    UISidebar,
)

__all__ = [
    "AppManifest",
    "AppSkill",
    "AutonomyConfig",
    "BackendConfig",
    "CliConfig",
    "ClientInstallConfig",
    "CoreCompatibility",
    "CronEntry",
    "Dependencies",
    "MarketplaceDependencies",
    "PackSourceEntry",
    "Permissions",
    "PlatformConfig",
    "ProposalKind",
    "ProviderConfig",
    "QualityDeclaration",
    "RouteEntry",
    "SetupConfig",
    "UIConfig",
    "UIPage",
    "UISidebar",
]
