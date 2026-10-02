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

``AGENT_TIERS`` is the vocabulary ``permissions.agent`` takes, narrowest first (``text``, ``read``,
``tools``): what a tool that writes or checks a manifest offers, and what an app's own tests check
its declaration against.
"""

from personalclaw.apps.agent_tiers import AGENT_TIERS  # noqa: F401
from personalclaw.apps.core_version import CoreCompatibility  # noqa: F401
from personalclaw.apps.manifest import (  # noqa: F401
    AppManifest,
    AppSkill,
    AutonomyConfig,
    BackendConfig,
    CliConfig,
    ClientInstallConfig,
    CronEntry,
    Dependencies,
    ExternalWrite,
    LaunchedProgram,
    MarketplaceDependencies,
    PackSourceEntry,
    Permissions,
    PlatformConfig,
    Prerequisite,
    ProposalKind,
    ProviderConfig,
    QualityDeclaration,
    RouteEntry,
    SettingCondition,
    SetupConfig,
    UIConfig,
    UIPage,
    UISidebar,
)

__all__ = [
    "AGENT_TIERS",
    "AppManifest",
    "AppSkill",
    "AutonomyConfig",
    "BackendConfig",
    "CliConfig",
    "ClientInstallConfig",
    "CoreCompatibility",
    "CronEntry",
    "Dependencies",
    "ExternalWrite",
    "LaunchedProgram",
    "MarketplaceDependencies",
    "PackSourceEntry",
    "Permissions",
    "PlatformConfig",
    "Prerequisite",
    "ProposalKind",
    "ProviderConfig",
    "QualityDeclaration",
    "RouteEntry",
    "SettingCondition",
    "SetupConfig",
    "UIConfig",
    "UIPage",
    "UISidebar",
]
