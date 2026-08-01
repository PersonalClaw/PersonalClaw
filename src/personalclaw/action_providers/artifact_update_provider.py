"""``artifact-update`` action provider — refresh a live artifact from a workflow (WF2-R15).

The pattern this exists for: a dashboard-style template generates an HTML (or markdown) skeleton
ONCE, then refreshes it on every run by re-binding `{{nodes.x.output}}` slots through a pure
transform. Without this provider the only way to update the artifact would be a `stage` — a whole
subagent session spawned to paste text into a file, which costs a model call and a lane slot for
work that is pure substitution.

So this is a **zero-token** node. It takes content the engine already resolved and writes it.

Three decisions worth stating:

**Upsert, not create-or-fail.** A recurring workflow's first run has no artifact and its
hundredth does. Making the template branch on that would put a `branch` node in every dashboard
spec for a distinction the provider can just absorb — so an unknown slug is created and a known
one is updated.

**`snapshot` is opt-in.** A dashboard refreshed every five minutes would otherwise accumulate a
version per refresh and bury the versions a human actually cares about. A template that wants
history asks for it.

**The slug is validated, not trusted.** It reaches here from a template's config, which a model
may have authored, and it becomes a directory name in the artifact store. The store's own writer
guards traversal too, but failing here gives the author the error at the node that named it.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

logger = logging.getLogger(__name__)

#: Artifact slugs are lowercase kebab, like every other user-facing id in the platform. Anchored
#: so a `../` or an absolute path cannot pass — the slug becomes a directory name.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Kinds the artifact store renders. `widget` is the dashboard case this provider was added for.
_KINDS = ("widget", "document", "report", "data")


class ArtifactUpdateActionProvider(ActionProvider):
    """Write resolved content into an artifact. Zero tokens, upsert semantics.

    ``action_config`` shape::

        {
            "slug": "run-dashboard",        # required; lowercase kebab
            "content": "<html>…",           # required; already-resolved text
            "name": "Run dashboard",        # optional; used when CREATING
            "kind": "widget",               # optional; widget|document|report|data
            "description": "…",             # optional
            "tags": ["workflow"],           # optional
            "snapshot": false,              # optional; keep a version of the prior body
            "collection": "…"               # optional
        }
    """

    @property
    def name(self) -> str:
        return "artifact-update"

    @property
    def display_name(self) -> str:
        return "Update Artifact"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()

        slug = str(action_config.get("slug", "") or "").strip()
        if not slug:
            return ActionResult(
                success=False,
                error="artifact-update is missing 'slug' — name the artifact to write",
            )
        if not _SLUG.match(slug):
            return ActionResult(
                success=False,
                error=(
                    f"artifact-update slug {slug!r} is not a valid id — use lowercase letters, "
                    "digits and hyphens (it becomes a directory name)"
                ),
            )

        # `is None`, not falsy: writing an empty artifact is a legitimate result (a dashboard with
        # nothing to report yet), and treating "" as missing would silently skip that write.
        raw = action_config.get("content")
        if raw is None:
            return ActionResult(
                success=False,
                error="artifact-update is missing 'content' — bind it to a node's output",
            )
        content = raw if isinstance(raw, str) else _stringify(raw)

        kind = str(action_config.get("kind", "widget") or "widget")
        if kind not in _KINDS:
            return ActionResult(
                success=False,
                error=f"artifact-update kind {kind!r} must be one of: {', '.join(_KINDS)}",
            )

        try:
            from personalclaw.artifacts.registry import get_provider

            store = get_provider()
        except Exception as exc:  # pragma: no cover — an import failure is environmental
            return ActionResult(success=False, error=f"artifact store unavailable: {exc}")
        if store is None:
            return ActionResult(success=False, error="no artifact provider is registered")

        tags = [str(t) for t in (action_config.get("tags") or [])]
        snapshot = bool(action_config.get("snapshot", False))
        collection = str(action_config.get("collection", "") or "")

        try:
            existing = store.get(slug)
        except Exception:
            # A read failure is not a reason to refuse the write: treat it as absent and let the
            # write path report the real problem.
            logger.debug("artifact-update: could not read %s", slug, exc_info=True)
            existing = None

        try:
            if existing is None:
                art = store.create(
                    name=str(action_config.get("name", "") or slug),
                    content=content,
                    kind=kind,
                    source="workflow",
                    slug=slug,
                    description=str(action_config.get("description", "") or ""),
                    tags=tags,
                    actor="workflow",
                    collection=collection,
                )
                created = True
            else:
                art = store.update(  # type: ignore[assignment]
                    slug,
                    content=content,
                    snapshot=snapshot,
                    # `iterated`, from the store's fixed ALLOWED_EVENT_TYPES — a workflow refresh
                    # IS another iteration of the same artifact. An invented type like
                    # "workflow_refresh" is rejected by the store's own validation, which is how
                    # this was caught: the create succeeded and every update failed.
                    event_type="iterated",
                    actor="workflow",
                    # Name/description/tags are NOT re-sent on update: a user who renamed the
                    # artifact in the UI should keep their name, and a template that reasserted
                    # its own on every refresh would silently revert them.
                    collection=collection or None,
                )
                created = False
        except Exception as exc:
            return ActionResult(
                success=False,
                error=f"artifact-update could not write {slug!r}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if art is None:
            return ActionResult(
                success=False,
                error=f"artifact-update: the store did not write {slug!r}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        version = getattr(art, "version", 0)
        return ActionResult(
            success=True,
            # The slug and version go to STDOUT because a downstream node binds to them — a
            # dashboard's "last updated v7" line reads this rather than re-fetching.
            stdout=f'{{"slug": "{slug}", "version": {version}, "created": {str(created).lower()}}}',
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _stringify(value: Any) -> str:
    """Render non-string content deterministically.

    A transform binding often yields a dict or list; writing `str(dict)` would put Python repr
    (single quotes, `True`) into an artifact a browser may parse as JSON. Sorted keys so two
    identical refreshes produce byte-identical bodies and the store does not record a version for
    a no-op.
    """
    import json

    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
