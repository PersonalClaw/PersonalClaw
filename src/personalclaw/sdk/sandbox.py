"""SDK: the sandbox-provider contract — ``SandboxProvider`` + its data types.

A sandbox app imports these from ``personalclaw.sdk.sandbox`` (never from the core module
directly) to implement a stronger isolation backend (a container/VM tier) that composes with the
host path-sandbox + resource-ceiling primitives PersonalClaw already applies. The app registers
through the ``sandbox`` provider type (``providers/registry.py::SandboxTypeHandler``).

``ResourceCeilings`` is here because ``SandboxSpec.ceilings`` is typed with it: a backend
that cannot name the ceilings it is asked to honour cannot honour them deliberately.
"""

from personalclaw.sandbox import ResourceCeilings  # noqa: F401
from personalclaw.sandbox_providers.base import (  # noqa: F401
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
)

__all__ = [
    "SandboxProvider",
    "SandboxHandle",
    "SandboxSpec",
    "ResourceCeilings",
]
