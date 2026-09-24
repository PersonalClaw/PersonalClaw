"""App manifest + storage primitives shared by the provider/extension system.

The third-party "app platform" (install/enable/UI-serving/backend) was retired;
what remains is the manifest schema (:class:`AppManifest` / ``ProviderConfig``) the
provider-adapter system parses, plus the app-storage path helpers it reads.
"""

from personalclaw.apps.manager import (
    InstalledApp,
    app_data_dir,
    app_dir,
    apps_dir,
    list_apps,
)
from personalclaw.apps.manifest import AppManifest

__all__ = [
    "AppManifest",
    "InstalledApp",
    "app_data_dir",
    "app_dir",
    "apps_dir",
    "list_apps",
]
