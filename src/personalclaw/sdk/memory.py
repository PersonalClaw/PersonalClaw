"""SDK: the memory-provider ABC + data types.

Stable re-export of ``personalclaw.memory_providers.base`` — an app imports these, not the core module
directly, so the core path can move without breaking installed apps.
"""

from personalclaw.memory_providers.base import (  # noqa: F401
    MemoryProvider,
)

__all__ = ['MemoryProvider']
