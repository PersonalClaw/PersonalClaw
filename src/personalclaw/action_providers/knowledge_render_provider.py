"""``render-report`` — a declarative report spec into a sanitized, self-contained export.

KNOWLEDGE-SYNTHESIS §6.2 (KNOW-R15), the optional terminal step of a synthesis or monitoring
template. Zero tokens: the spec is authored once (by a human or by one earlier `stage`), and every
later run re-renders it against fresh data. That is the whole reason the spec and the data are
separate arguments — a periodic synthesizer regenerates the visuals for free, with no model call.

**What is stored where.** The SPEC TEXT is the versioned record: it lands in the artifacts registry
(`artifacts/registry.py`), which is where versioning lives — knowledge items have no version
history. The rendered HTML is a DERIVED export written to a companion artifact whose body is
replaced, not accumulated, because a regenerated view is not a new authored revision. Each export
records the spec version it was rendered from, so a stale export is identifiable rather than merely
old.

**Sanitization is the point.** A spec's strings are usually LLM output over untrusted web or inbox
material, and the export is served same-origin with the dashboard. `knowledge/reports.py` admits no
caller markup at all and refuses to emit a document containing script, an event handler, or a remote
reference; this provider adds nothing to that and bypasses none of it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.knowledge import reports

logger = logging.getLogger(__name__)

#: Artifact slugs are lowercase kebab — they become directory names in the artifact store. Anchored
#: so a `../` or an absolute path cannot pass, and validated HERE as well as in the store so the
#: template author gets the error at the node that named it.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,55}$")

#: Suffix of the derived export's slug. A separate artifact rather than a second version of the same
#: one: one slug holding alternating spec/HTML bodies makes `version - 1` mean two different things,
#: and a revert would restore a rendered page as the spec.
EXPORT_SUFFIX = "-report"


class KnowledgeRenderReportActionProvider(ActionProvider):
    """Render a report spec; version the spec, export the HTML.

    ``action_config`` shape::

        {
            "slug": "market-brief",       # required; names the spec artifact
            "spec": {                     # required; object or JSON text
                "title": "Market brief",
                "blocks": [
                    {"type": "markdown", "text": "## Summary\\n- one line"},
                    {"type": "table", "dataset": "movers", "sort": {"column": "chg", "desc": true},
                     "compute": [{"column": "pct", "op": "percent", "of": ["chg", "base"]}]},
                    {"type": "xychart", "dataset": "trend", "style": "line"}
                ]
            },
            "data": {"movers": [...], "trend": {"x": [...], "series": [...]}},
            "name": "Market brief",       # optional; used when CREATING the spec artifact
            "description": "…",           # optional
            "tags": ["knowledge"],        # optional
            "collection": "…",            # optional
            "render_only": false          # optional; return the HTML without writing artifacts
        }
    """

    @property
    def name(self) -> str:
        return "render-report"

    @property
    def display_name(self) -> str:
        return "Render Report"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        raw_spec = cfg.get("spec")
        if raw_spec is None:
            return ActionResult(
                success=False,
                error="render-report is missing 'spec' — the declarative report to render",
            )
        try:
            spec = reports.parse_spec(raw_spec)
            rendered = reports.render_report(spec, _data(cfg.get("data")))
        except reports.SpecError as exc:
            # A spec error is the AUTHOR's error and must be visible as one. Rendering a partial
            # report around it would ship a page that reads complete and is not.
            return ActionResult(
                success=False,
                error=f"render-report: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        payload: dict[str, Any] = {
            "blocks": rendered.block_count,
            "bytes": len(rendered.html),
            "warnings": rendered.warnings,
        }

        if _truthy(cfg.get("render_only")):
            # The escape hatch for a template that pipes the HTML somewhere else (an email body, a
            # channel message). No artifact is written, so nothing is versioned either.
            payload["html"] = rendered.html
            return ActionResult(
                success=True,
                stdout=json.dumps(payload, ensure_ascii=False),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        slug = str(cfg.get("slug", "") or "").strip()
        if not slug:
            return ActionResult(
                success=False,
                error="render-report is missing 'slug' — name the report to write",
            )
        if not _SLUG.match(slug):
            return ActionResult(
                success=False,
                error=(
                    f"render-report slug {slug!r} is not a valid id — use lowercase letters, "
                    "digits and hyphens (it becomes a directory name)"
                ),
            )

        try:
            from personalclaw.artifacts.registry import get_provider

            store = get_provider()
        except Exception as exc:  # pragma: no cover — an import failure is environmental
            return ActionResult(success=False, error=f"artifact store unavailable: {exc}")
        if store is None:
            return ActionResult(success=False, error="no artifact provider is registered")

        try:
            spec_version = _write_spec(store, slug, spec, cfg)
            export_version = _write_export(store, slug, rendered.html, spec_version, cfg)
        except Exception as exc:
            return ActionResult(
                success=False,
                error=f"render-report could not write {slug!r}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        payload.update(
            {
                "spec_slug": slug,
                "spec_version": spec_version,
                "export_slug": f"{slug}{EXPORT_SUFFIX}",
                "export_version": export_version,
            }
        )
        return ActionResult(
            success=True,
            # Both slugs and versions go to STDOUT because a downstream node binds them — a digest
            # that links "report v4" reads this rather than re-fetching the artifact.
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _write_spec(store: Any, slug: str, spec: dict[str, Any], cfg: dict[str, Any]) -> int:
    """Upsert the spec artifact — the VERSIONED record. Returns its version.

    `snapshot=True` when the text CHANGED: the spec is the authored thing, and its history is what
    makes "why did last month's report look like that" answerable.

    An unchanged spec is not written at all. Measured: the store does NOT dedupe a no-op update, so
    a `snapshot=True` write on every periodic run minted a version per run — 24 versions a day of
    identical text, against the store's `MAX_VERSIONS = 50` FIFO prune
    (`artifacts/models.py:18`), which would evict the human-authored history inside two days. That
    is exactly the burial this atom's spec-versus-export split exists to prevent, so the
    idempotency check lives here rather than being assumed of the store. `canonical_spec_text`
    sorts keys so the comparison is not defeated by dict ordering.
    """
    text = reports.canonical_spec_text(spec)
    existing = _get(store, slug)
    if existing is not None and (existing.content or "") == text:
        return int(getattr(existing, "version", 0) or 0)
    if existing is None:
        art = store.create(
            name=str(cfg.get("name", "") or spec.get("title", "") or slug),
            content=text,
            # `json`, from the store's own ALLOWED_KINDS: the spec IS JSON, and a kind the store
            # rejects fails the create outright.
            kind="json",
            source="cron",
            slug=slug,
            description=str(cfg.get("description", "") or ""),
            tags=[str(t) for t in (cfg.get("tags") or [])],
            actor="workflow",
            collection=str(cfg.get("collection", "") or ""),
        )
    else:
        art = store.update(
            slug,
            content=text,
            snapshot=True,
            # `edited`, from ALLOWED_EVENT_TYPES — a changed spec is an edit of the authored record.
            # An invented type is rejected by the store's validation.
            event_type="edited",
            actor="workflow",
            # Name/description/tags are NOT re-sent: a user who renamed the report in the UI keeps
            # their name.
            collection=str(cfg.get("collection", "") or "") or None,
        )
    if art is None:
        raise RuntimeError(f"the store did not write the spec artifact {slug!r}")
    return int(getattr(art, "version", 0) or 0)


def _write_export(
    store: Any, slug: str, document: str, spec_version: int, cfg: dict[str, Any]
) -> int:
    """Upsert the derived HTML export. Returns its version.

    `snapshot=False`: a regenerated view is derived, not authored. Snapshotting it would put one
    version per periodic run in front of the spec history that actually matters — and the export is
    reproducible from the spec plus the data, so there is nothing to preserve.
    """
    export_slug = f"{slug}{EXPORT_SUFFIX}"
    # Recorded on the lifecycle event, not in the body: the body is a finished document, and a
    # provenance comment inside it would be one more thing the self-containment check has to trust.
    provenance = {"derived_from": slug, "spec_version": spec_version}
    existing = _get(store, export_slug)
    if existing is None:
        art = store.create(
            name=f"{cfg.get('name', '') or slug} (rendered)",
            content=document,
            kind="html",
            source="cron",
            slug=export_slug,
            description=f"Derived export of the {slug!r} report spec. Regenerated, not authored.",
            tags=[str(t) for t in (cfg.get("tags") or [])],
            actor="workflow",
            collection=str(cfg.get("collection", "") or ""),
            event_metadata=provenance,
        )
    else:
        art = store.update(
            export_slug,
            content=document,
            snapshot=False,
            # `iterated` — another regeneration of the same derived view.
            event_type="iterated",
            actor="workflow",
            collection=str(cfg.get("collection", "") or "") or None,
            event_metadata=provenance,
        )
    if art is None:
        raise RuntimeError(f"the store did not write the export artifact {export_slug!r}")
    return int(getattr(art, "version", 0) or 0)


def _get(store: Any, slug: str) -> Any:
    try:
        return store.get(slug)
    except Exception:
        # A read failure is not a reason to refuse the write: treat it as absent and let the write
        # path report the real problem.
        logger.debug("render-report: could not read %s", slug, exc_info=True)
        return None


def _data(raw: Any) -> dict[str, Any]:
    """Datasets as an object. Accepts JSON text because a `{{nodes.x.output}}` binding is a string.

    A binding that resolved to text and got dropped silently was the shape worth guarding: the
    report would render every block empty and still report success.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")
