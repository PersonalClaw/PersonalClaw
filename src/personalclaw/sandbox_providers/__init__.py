"""Sandbox providers — the pluggable isolation seam (EXECUTION-ISOLATION EI-1).

A sandbox provider composes the OS-level path sandbox (``sandbox.wrap_argv``) and the post-exec
resource ceilings (``sandbox.create_subprocess_limited``) behind a two-phase ``wrap`` → ``exec``
contract, so a stronger container/VM tier can slot in as an installable ``sandbox`` app without
touching any spawn site. The in-core ``none`` provider is the default and adds no new isolation.

The ``none`` builtin self-registers on first import so the registry is never empty.
"""

from __future__ import annotations

from personalclaw.sandbox_providers.base import (
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
    SandboxUnavailableError,
)
from personalclaw.sandbox_providers.registry import (
    get_provider,
    list_providers,
    register_builtin_providers,
    register_provider,
    resolve_provider,
    unregister_provider,
)

# Register the always-present ``none`` provider + the core-native ``docker`` tier on import
# (idempotent). Installed ``sandbox`` apps are added later by SandboxTypeHandler on enable.
register_builtin_providers()

__all__ = [
    "SandboxProvider",
    "SandboxHandle",
    "SandboxSpec",
    "SandboxUnavailableError",
    "register_provider",
    "unregister_provider",
    "get_provider",
    "list_providers",
    "register_builtin_providers",
    "resolve_provider",
]
