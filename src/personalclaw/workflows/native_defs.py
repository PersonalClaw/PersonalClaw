"""The native filesystem definition provider — where a user's own workflows live.

`defs.py` is the registry SEAM; this is the provider that makes it usable. Without it only
an installed app could contribute definitions, so a user with no app installed could not
save a workflow at all — the API correctly reported "no writable provider", which is an
honest error and a dead end.

One directory per definition under `<config>/workflows/defs/<name>/workflow.json`, mirroring
how apps and prompts are stored. A directory rather than a flat file because a definition
will grow siblings (spec history, bundled prompt blocks), and moving to a directory later
would be a migration.

**Versioning is on every save**, and it is load-bearing: a run pins the spec version it
started from, and a mutation diffs against its predecessor. A save that reused the version
would make `expect_version` meaningless.

Reads are tolerant — a corrupt or hand-edited file is skipped in a listing rather than
breaking every other definition — because the listing is how a user finds the broken one to
fix it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows import store
from personalclaw.workflows.defs import WorkflowDefProvider
from personalclaw.workflows.models import SPEC_SEMVER, Node, WorkflowDef, valid_name

logger = logging.getLogger(__name__)

DEF_FILE = "workflow.json"


def defs_root() -> Path:
    return store.workflows_dir() / "defs"


def _def_path(name: str) -> Path:
    return defs_root() / name / DEF_FILE


class NativeWorkflowDefProvider(WorkflowDefProvider):
    """User-authored definitions on the filesystem. The only WRITABLE provider by default."""

    @property
    def name(self) -> str:
        return "native"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0) -> tuple[list[Any], int]:
        root = defs_root()
        if not root.is_dir():
            return [], 0
        names = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / DEF_FILE).is_file())
        window = names[offset : offset + max(1, limit)]
        out: list[Any] = []
        for name in window:
            loaded = _read(name)
            if loaded is not None:
                out.append(loaded)
        # `total` counts what EXISTS, not what parsed: a corrupt def is still a def the user
        # has, and hiding it from the count would make it undiscoverable.
        return out, len(names)

    async def get_def(self, name: str) -> Any | None:
        if not valid_name(name):
            return None
        return _read(name)

    async def save_def(self, **fields: Any) -> Any:
        name = str(fields.get("name", "") or "")
        if not valid_name(name):
            raise ValueError(f"{name!r} is not a valid definition name")
        root_raw = fields.get("root")
        if not isinstance(root_raw, dict):
            raise ValueError("a definition needs a `root` node object")
        # Parsed to REJECT an unknown node kind at the boundary: a def that cannot be built
        # into a Node would fail at every future run start instead of here, once.
        Node.from_dict(root_raw)

        prior = _read(name)
        payload = {
            "name": name,
            "root": root_raw,
            # A save always advances the version — a run pins the version it started from,
            # and reusing one would make `expect_version` meaningless.
            "version": (prior.version + 1) if prior is not None else 1,
            "spec_semver": str(fields.get("spec_semver", SPEC_SEMVER) or SPEC_SEMVER),
            "description": str(fields.get("description", "") or ""),
            "source": "user",
            "provenance": str(fields.get("provenance", "user") or "user"),
            "inputs": dict(fields.get("inputs") or {}),
            "tags": [str(t) for t in (fields.get("tags") or [])],
            "metadata": dict(fields.get("metadata") or {}),
            "created_at": prior.created_at if prior is not None else _now(),
            "updated_at": _now(),
        }
        if "defaults" in fields and isinstance(fields["defaults"], dict):
            payload["defaults"] = fields["defaults"]
        if "on_overlap" in fields:
            payload["on_overlap"] = str(fields["on_overlap"])

        path = _def_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))
        return WorkflowDef.from_dict(payload)

    async def delete_def(self, name: str) -> bool:
        if not valid_name(name):
            return False
        path = _def_path(name)
        if not path.is_file():
            return False
        try:
            path.unlink()
            # Remove the now-empty directory too, so a listing does not show a def with no
            # spec. Non-empty (a future spec_history) is left alone deliberately.
            parent = path.parent
            if not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            logger.warning("could not delete workflow def %s", name, exc_info=True)
            return False
        return True


def _read(name: str) -> WorkflowDef | None:
    """Load one definition, tolerating corruption.

    A hand-edited or truncated file returns None rather than raising: the listing is how a
    user finds the broken definition, and one bad file must not hide every good one.
    """
    path = _def_path(name)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("workflow def %s: unreadable JSON", name)
        return None
    if not isinstance(raw, dict):
        return None
    raw.setdefault("name", name)
    try:
        return WorkflowDef.from_dict(raw)
    except (ValueError, TypeError):
        logger.warning("workflow def %s: unusable spec", name)
        return None


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register_native_provider() -> None:
    """Register the native provider. Idempotent — safe on every boot."""
    from personalclaw.workflows.defs import get_provider, register_provider

    if get_provider("native") is None:
        register_provider(NativeWorkflowDefProvider())
