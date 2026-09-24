"""Workflow-definition providers — the registry apps contribute template packs through.

This is the seam only, landed early on purpose. The extension registry's ``workflow``
``_TypeHandler`` has to register into *something*: ``PROVIDER_TYPES`` (the manifest
validator's allowlist) must equal the runtime type-handler set, or installing — or even
reinstalling — any app declaring a workflow provider is refused with a validation error
that names no cause. That is issue #47's bug class, and `test_manifest_types_match_handlers`
guards it. So the old registry could not simply be deleted; it had to be replaced in the
same commit.

What a v2 provider contributes is **definitions** (reusable graph specs), never runs.
Execution is engine-owned: a provider that could hand back a half-executed run would put
two writers on the journal, which is the invariant the engine's terminal-write ownership
depends on. Hence read/list are required and write is optional — a bundled template pack
is legitimately read-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WorkflowDefProvider(ABC):
    """A source of workflow definitions. Read/list required; writes optional."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider id (``"native"``, ``"research-pack"``, …)."""

    @abstractmethod
    async def list_defs(self, *, limit: int = 200, offset: int = 0) -> tuple[list[Any], int]:
        """Return ``(defs, total)``. Paginated so a large pack cannot flood a listing."""

    @abstractmethod
    async def get_def(self, name: str) -> Any | None:
        """Return one definition by name, or None. Must not raise on a miss."""

    @property
    def readonly(self) -> bool:
        """True when this provider cannot be written through (e.g. a bundled pack)."""
        return True

    async def save_def(self, **fields: Any) -> Any:
        """Persist a definition. Only called when ``readonly`` is False."""
        raise NotImplementedError(f"provider {self.name!r} is read-only")

    async def delete_def(self, name: str) -> bool:
        """Remove a definition. Only called when ``readonly`` is False."""
        raise NotImplementedError(f"provider {self.name!r} is read-only")


_providers: dict[str, WorkflowDefProvider] = {}


def register_provider(provider: WorkflowDefProvider) -> None:
    """Register a def provider (idempotent by name — a reinstall replaces in place).

    Deliberately last-write-wins rather than raising on a duplicate: app *update*
    re-registers under the same name, and rejecting that would make every update of a
    workflow-provider app fail on its second run.
    """
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    """Remove a provider. Tolerates an unknown name — uninstall must not depend on
    registration having succeeded."""
    _providers.pop(name, None)


def get_provider(name: str) -> WorkflowDefProvider | None:
    return _providers.get(name)


def list_providers() -> list[str]:
    return sorted(_providers)
