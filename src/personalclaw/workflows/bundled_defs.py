"""The bundled-template definition provider — the batteries-included library (WF2 §6).

Ships inside the package (`workflows/bundled/<name>/workflow.json`), so a fresh
`pip install personalclaw` has a working template library with no network, no API key and no
first-run download. Same reasoning, and the same layout, as `skills/bundled/`.

**Read-only, and that is a design decision rather than a limitation.** A user who wants to
change a template instantiates it — which copies the spec into their own `defs/` — and edits
that. Writing through to the package directory would put a user's edit somewhere
`pip install --upgrade` silently overwrites, and the loss would be discovered long after the
upgrade that caused it.

**Macros are expanded on READ.** A template may be authored with `judge_panel` or
`research_sweep` for the same reason a user's spec may: the pattern is the readable form. But
what leaves this provider is always core nodes, so nothing downstream — the engine, the
journal, the resume cache, the widget — ever has to know macros exist. A template whose macro
cannot expand is dropped from the listing with a warning rather than breaking every other
template, because the listing is how a user finds the working ones.

There is no mtime sync. The old bundled-SOP pattern copied files into the user's home on boot,
which then needed a "did the user edit it?" story on every upgrade. Serving them from the
package instead means an upgrade ships new templates with no reconciliation at all.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from personalclaw.workflows.defs import WorkflowDefProvider
from personalclaw.workflows.macros import MacroError, expand_spec
from personalclaw.workflows.models import SPEC_SEMVER, WorkflowDef, valid_name

logger = logging.getLogger(__name__)

_BUNDLED_PKG = "personalclaw.workflows.bundled"
DEF_FILE = "workflow.json"

PROVIDER_NAME = "bundled"


def bundled_root() -> Path:
    """The on-disk path of the bundled templates directory.

    `importlib.resources` rather than `__file__`-relative arithmetic, so the lookup works
    identically for an editable install, a wheel, and a source checkout.
    """
    return Path(str(resources.files(_BUNDLED_PKG)))


def template_names() -> list[str]:
    """Every bundled template name, sorted. A directory with no `workflow.json` is not one."""
    root = bundled_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / DEF_FILE).is_file())


class BundledWorkflowDefProvider(WorkflowDefProvider):
    """The shipped template library. Read-only by contract."""

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0) -> tuple[list[Any], int]:
        names = template_names()
        window = names[offset : offset + max(1, limit)]
        out: list[Any] = []
        for name in window:
            loaded = read_template(name)
            if loaded is not None:
                out.append(loaded)
        # `total` counts what SHIPS, not what parsed: a template broken by a bad edit is still
        # one the user has, and hiding it from the count makes it undiscoverable.
        return out, len(names)

    async def get_def(self, name: str) -> Any | None:
        if not valid_name(name):
            return None
        return read_template(name)


@lru_cache(maxsize=64)
def _read_cached(name: str, mtime_ns: int) -> WorkflowDef | None:
    """Parse one template, keyed by (name, mtime) so an edit during development is picked up.

    Cached because the templates never change within a process in production, and a listing
    re-parses every one of them: without this, opening the template picker re-reads and
    re-expands six specs on every render.
    """
    path = bundled_root() / name / DEF_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("bundled template %s: unreadable JSON", name)
        return None
    if not isinstance(raw, dict):
        return None

    raw.setdefault("name", name)
    # `source` is what the UI keys "bundled" affordances off (no delete button, "instantiate to
    # edit"). Forced rather than trusted from the file, so a hand-edited template cannot
    # present itself as user-authored.
    raw["source"] = "bundled"
    raw.setdefault("spec_semver", SPEC_SEMVER)

    try:
        expanded = expand_spec(raw)
    except MacroError as exc:
        # Dropped from the listing, not fatal: the listing is how a user finds the working
        # templates, and one bad macro must not hide the other five.
        logger.warning("bundled template %s: macro expansion failed — %s", name, exc)
        return None

    try:
        return WorkflowDef.from_dict(expanded)
    except (ValueError, TypeError) as exc:
        logger.warning("bundled template %s: unusable spec — %s", name, exc)
        return None


def read_template(name: str) -> WorkflowDef | None:
    """Load one bundled template with macros expanded, or None if it cannot be used."""
    path = bundled_root() / name / DEF_FILE
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _read_cached(name, mtime_ns)


def register_bundled_provider() -> None:
    """Register the bundled provider. Idempotent — safe on every boot."""
    from personalclaw.workflows.defs import get_provider, register_provider

    if get_provider(PROVIDER_NAME) is None:
        register_provider(BundledWorkflowDefProvider())
