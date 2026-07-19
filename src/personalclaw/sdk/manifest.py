"""SDK: the app-manifest schema types (type-only surface).

Re-export of the ``AppManifest`` schema an app's ``app.json`` conforms to, plus the
``ProviderConfig`` an app-contributed provider is described by. An app rarely needs to
construct these (the loader parses app.json for it), but they're the typed contract for
tooling that validates or generates a manifest.
"""

from personalclaw.apps.manifest import (  # noqa: F401
    AppManifest,
    BackendConfig,
    Permissions,
    ProviderConfig,
    SetupConfig,
)

__all__ = ["AppManifest", "ProviderConfig", "Permissions", "BackendConfig", "SetupConfig"]
