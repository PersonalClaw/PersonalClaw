"""The source registry — the one list of tools we can import from.

A source is (name, display name, env var, default root, scan function). Adding a
tool is adding one module under :mod:`~personalclaw.onboarding_import.sources` and
one row here; nothing downstream changes, which is why broader source coverage was
explicitly not a v1 bar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from personalclaw.onboarding_import.model import ScanResult
from personalclaw.onboarding_import.sources import claude_code, codex


@dataclass(frozen=True)
class ImportSource:
    name: str
    display_name: str
    env_var: str
    default_root: str
    scan: Callable[..., ScanResult]
    resolve_root: Callable[[], Path]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "env_var": self.env_var,
            "default_root": self.default_root,
        }


def _source(module) -> ImportSource:
    return ImportSource(
        name=module.NAME,
        display_name=module.DISPLAY_NAME,
        env_var=module.ENV_VAR,
        default_root=module.DEFAULT_ROOT,
        scan=module.scan,
        resolve_root=module.resolve_root,
    )


SOURCES: tuple[ImportSource, ...] = (_source(claude_code), _source(codex))

_BY_NAME: dict[str, ImportSource] = {src.name: src for src in SOURCES}


def list_sources() -> tuple[ImportSource, ...]:
    return SOURCES


def get_source(name: str) -> ImportSource:
    """Look up a source by name. Unknown names raise — never a silent no-op scan."""
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME))
        raise KeyError(f"unknown import source {name!r} (known: {known})") from None
