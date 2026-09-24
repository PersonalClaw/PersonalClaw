"""``artifact_inspect`` action provider — pull an offloaded node output on demand (WV-11).

The other half of output-offloading. When a node's output crosses `MAX_INLINE_OUTPUT_BYTES`
(or is binary), the journal writes the full body to `runs/<id>/artifacts/` and leaves a
head+tail stub inline; `{{nodes.x.artifact}}` then resolves to that artifact's ref. Bindings
carry the POINTER cheaply so a 5MB report never rides in a prompt or an SSE frame — but a
downstream node sometimes genuinely needs the body (a summarizer over a large fetch, a diff
against a big prior result). This provider is how it asks, as a zero-token action:

    {"provider": "artifact_inspect", "with": {"ref": "{{nodes.fetch.artifact}}"}}

It returns the artifact CONTENT, optionally a byte slice (`offset`/`length`) so a caller can
page through a huge body without loading all of it. The ref is confined to the run's own
`artifacts/` directory: it arrives from a template a model may have authored, so a `../`
escape, an absolute path, or a pointer into `outputs/` is refused rather than trusted —
`store.read_artifact` does the confinement and this provider never reads outside it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

logger = logging.getLogger(__name__)


class ArtifactInspectActionProvider(ActionProvider):
    """Read an offloaded node output by its `{{nodes.x.artifact}}` ref. Zero tokens.

    ``action_config`` shape::

        {
            "ref": "artifacts/…",   # required; the pointer {{nodes.x.artifact}} resolves to
            "offset": 0,            # optional; byte-ish start into a stringified body
            "length": 65536         # optional; how many chars to return from `offset`
        }

    The run id is read from the engine-supplied payload, not the config — a template cannot
    name another run's artifacts.
    """

    @property
    def name(self) -> str:
        return "artifact_inspect"

    @property
    def display_name(self) -> str:
        return "Inspect Artifact"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()

        ref = str(action_config.get("ref", "") or "").strip()
        if not ref:
            return ActionResult(
                success=False,
                error="artifact_inspect is missing 'ref' — bind it to {{nodes.x.artifact}}",
            )

        payload = getattr(ctx, "payload", None) or {}
        run_id = str(payload.get("run_id", "") or "")
        if not run_id:
            # Without the run id the confinement root is unknowable, so refuse rather than guess:
            # a provider that read from an assumed run dir would be a path-escape by another name.
            return ActionResult(
                success=False,
                error="artifact_inspect has no run context — it runs only inside a workflow run",
            )

        from personalclaw.workflows import store

        # `read_artifact` confines the ref to `runs/<id>/artifacts/`; a `../` escape, an absolute
        # path, or a pointer into `outputs/` resolves outside the root and returns None.
        content = store.read_artifact(run_id, ref)
        if content is None:
            return ActionResult(
                success=False,
                error=(
                    f"artifact_inspect could not read {ref!r} — it is not a readable artifact of "
                    "this run (a ref outside runs/<id>/artifacts/ is refused)"
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        total = len(text)
        offset, length, err = _slice_bounds(action_config, total)
        if err:
            return ActionResult(success=False, error=err)
        chunk = text[offset : offset + length]
        truncated = offset + length < total

        body = {
            "ref": ref,
            "content": chunk,
            "offset": offset,
            "length": len(chunk),
            "total": total,
            "truncated": truncated,
        }
        return ActionResult(
            success=True,
            stdout=json.dumps(body, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _slice_bounds(cfg: dict[str, Any], total: int) -> tuple[int, int, str]:
    """Resolve `offset`/`length` against a body of `total` chars.

    The default is a FULL read (offset 0, whole body): the caller asked to inspect the
    artifact, so absent an explicit window it gets all of it. `offset`/`length` are the
    on-demand partial pull — a caller paging through a huge body supplies them to avoid
    loading the whole thing. Returns `(offset, length, error)`; a non-empty error means the
    caller supplied a bad value.
    """
    try:
        offset = int(cfg.get("offset", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0, "artifact_inspect 'offset' must be an integer"
    if offset < 0:
        return 0, 0, "artifact_inspect 'offset' must be >= 0"
    offset = min(offset, total)

    raw_length = cfg.get("length")
    if raw_length is None:
        length = total - offset
    else:
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            return 0, 0, "artifact_inspect 'length' must be an integer"
        if length <= 0:
            return 0, 0, "artifact_inspect 'length' must be positive"
    return offset, length, ""
