"""``selfqa-evidence`` action provider — seal the proof bundle, deterministically (SV-10 §3.3).

The plan's evidence node was an LLM ``stage`` told to "compute the digests; do not estimate them"
— an instruction a model can quietly ignore. This provider replaces that trust with code: it
derives the contact-sheet and GIF from the recording (ffmpeg as a local subprocess, degrading
typed when ffmpeg is absent), computes the SHA256 manifest from the bytes on disk, registers the
bundle as a **single** Artifact, and runs the required-kinds completion gate — and it opens the
optional fix branch on a confirmed failure. Everything a self-report could get wrong is measured
instead.

The node reads its bundle from the **run workspace** — the same directory the ``execute`` stage
wrote ``screenshots/`` and ``recording.mp4`` into. The engine threads that path into the action
payload as ``workspace`` (the artifact gate at the same dispatch seam already receives it), so the
provider needs no coupling to the run store to find the files.

Output (one JSON object, so the template binds ``{{nodes.evidence.output.*}}``)::

    {
        "evidence_ref": "artifact:<slug>",   # the single Artifact
        "complete": true|false,               # the required-kinds gate
        "present": ["screenshot", …],
        "missing": ["recording", …],          # the gate's missing-kinds list (Criterion #7)
        "degraded": [{"kind": "gif", "reason": "…"}],
        "fix_branch": "pclaw/selfqa-<sha8>"    # "" unless a failure + fix_branch_enabled
    }

A missing required kind returns ``success=False`` with the missing list, which marks the node —
and so the run — incomplete: a run cannot pass while its declared proof is absent, independent of
what the driving agent claimed. §5's "no new provider TYPE" holds — this is an action provider
added to ``ALLOWED_HOOK_PROVIDERS`` exactly as §5 requires for one, the QA run still fires through
``run-workflow``, and no new inbox source or task provider is introduced.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


def _as_bool(value: Any) -> bool:
    """Coerce a binding that may arrive as a real bool or its string spelling.

    A JSON template renders ``true``; a binding pipe can hand back the string ``"true"``. Both
    must mean True, and anything else means False — a lenient parse here is safer than a node that
    fails because ``passed`` arrived as ``"true"`` instead of ``True``.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


class SelfQaEvidenceActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-evidence"

    @property
    def display_name(self) -> str:
        return "Seal Self-QA Evidence Bundle"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.selfqa import evidence as ev
        from personalclaw.selfqa.fix_branch import create_fix_branch

        workspace = str(ctx.payload.get("workspace", "") or "").strip()
        if not workspace:
            return ActionResult(
                success=False,
                error=(
                    "selfqa-evidence has no run workspace to seal — the engine did not thread a "
                    "workspace into the action payload"
                ),
            )

        subdir = str(action_config.get("bundle_subdir", "") or "").strip()
        bundle_dir = Path(workspace) / subdir if subdir else Path(workspace)
        if not bundle_dir.is_dir():
            return ActionResult(
                success=False,
                error=f"selfqa-evidence bundle dir does not exist: {bundle_dir}",
            )

        scenario_id = str(action_config.get("scenario_id", "") or "").strip()
        sha = str(action_config.get("sha", "") or "").strip()
        passed = _as_bool(action_config.get("passed", False))
        repo = str(action_config.get("repo", "") or "").strip()
        fix_branch_enabled = _as_bool(action_config.get("fix_branch_enabled", False))

        raw_kinds = action_config.get("required_kinds")
        required_kinds = (
            tuple(str(k).strip() for k in raw_kinds if str(k).strip())
            if isinstance(raw_kinds, list) and raw_kinds
            else ev.DEFAULT_REQUIRED_KINDS
        )

        try:
            # Derive the enrichments FIRST so they are on disk when the manifest walks the dir.
            # Each returns a typed Derivation; a non-produced one carries the reason the manifest
            # records, so an ffmpeg-less host produces a screenshots-only bundle, not a crash.
            derivations = (
                ev.derive_contact_sheet(bundle_dir),
                ev.derive_gif(bundle_dir),
            )
            manifest = ev.build_manifest(
                bundle_dir,
                scenario_id=scenario_id,
                sha=sha,
                passed=passed,
                degradations=derivations,
            )
            ev.write_manifest(bundle_dir, manifest)
            registered = ev.register_bundle(
                bundle_dir,
                manifest=manifest,
                scenario_id=scenario_id,
                sha=sha,
                passed=passed,
                project_id=str(ctx.payload.get("project_id", "") or ""),
            )
            gate = ev.check_required_kinds(manifest, required_kinds)
        except Exception as exc:  # noqa: BLE001 - error result, never raise past the dispatch seam
            return ActionResult(success=False, error=f"selfqa-evidence failed: {exc}")

        # The fix branch is opened only on a CONFIRMED failure (the scenario did not pass) and only
        # when the flag is on. `create_fix_branch` never pushes; a disabled flag or a bad ref just
        # leaves `fix_branch` empty, and the Task then links nothing rather than a phantom branch.
        fix_branch = ""
        if not passed and fix_branch_enabled and repo:
            fbr = create_fix_branch(repo, sha, enabled=True)
            if fbr.created or fbr.already_existed:
                fix_branch = fbr.branch
            else:
                logger.info("selfqa-evidence: no fix branch (%s)", fbr.reason)

        output = {
            "evidence_ref": registered.ref,
            "complete": gate.complete,
            "present": sorted(gate.present),
            "missing": list(gate.missing),
            "degraded": list(manifest.degraded),
            "fix_branch": fix_branch,
            "file_count": registered.file_count,
        }

        if not gate.complete:
            # A run cannot count complete while a declared proof kind is missing. Reported as a
            # failed action so the engine marks the node — and the run — incomplete, naming what
            # is missing (Criterion #7). The evidence_ref is still in the output, so the partial
            # bundle that WAS produced is not lost.
            return ActionResult(
                success=False,
                error="required artifacts missing: " + ", ".join(gate.missing),
                stdout=json.dumps(output),
            )

        return ActionResult(success=True, stdout=json.dumps(output))


def create_provider(config: dict[str, Any] | None = None) -> "SelfQaEvidenceActionProvider":
    return SelfQaEvidenceActionProvider()
