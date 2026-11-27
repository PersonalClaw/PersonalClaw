"""``selfqa-evidence`` action provider — seal the proof bundle, deterministically.

An LLM ``stage`` evidence node is told to "compute the digests; do not estimate them"
— an instruction a model can quietly ignore. This provider replaces that trust with code: it
derives the contact-sheet and GIF from the recording (ffmpeg as a local subprocess, degrading
typed when ffmpeg is absent), computes the SHA256 manifest from the bytes on disk, registers the
bundle as a **single** Artifact, and runs the required-kinds completion gate — and it opens the
optional fix branch on a confirmed failure. Everything a self-report could get wrong is measured
instead.

The node reads its bundle from the **run workspace** — the same directory the ``execute`` stage
wrote ``screenshots/`` and ``recording.mp4`` into — or from ``bundle_subdir`` inside it, the
scenario's own folder (``scenarios/<sha>`` in the bundled template), so each scenario of a run
seals only what its own drive captured. A ``bundle_subdir`` that leads out of the run's folder is
refused. The engine threads that path into the action
payload as ``workspace`` (the artifact gate at the same dispatch seam already receives it), so the
provider needs no coupling to the run store to find the files. Only a step's dispatch carries it
(``base.run_identity``): an automation's fire or a lifecycle hook is no run's, whatever folder its
payload names, so it seals nothing and writes nothing.

Output (one JSON object, so the template binds ``{{nodes.evidence.output.*}}``)::

    {
        "evidence_ref": "artifact:<slug>",   # the single Artifact
        "complete": true|false,               # the required-kinds gate
        "present": ["screenshot", …],
        "missing": ["recording", …],          # the gate's missing-kinds list
        "degraded": [{"kind": "gif", "reason": "…"}],
        "fix_branch": "pclaw/selfqa-<sha8>"    # "" unless a failure + fix_branch_enabled
    }

A missing required kind returns ``success=False`` with the missing list, which marks the node —
and so the run — incomplete: a run cannot pass while its declared proof is absent, independent of
what the driving agent claimed. No new provider TYPE is introduced — this is an action provider
added to ``ALLOWED_HOOK_PROVIDERS`` exactly as every action provider must be, the QA run still
fires through ``run-workflow``, and no new inbox source or task provider is introduced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
    is_workflow_step,
    run_identity,
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

    @property
    def internal(self) -> bool:
        # A step of the Self-QA loop, configured by that loop — not an action to pick by hand.
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.selfqa import evidence as ev
        from personalclaw.selfqa.fix_branch import create_fix_branch

        if not is_workflow_step(ctx):
            # The bundle is a run's. A dispatch that is not a step's names no run, and a folder its
            # payload carries is event data, so nothing is sealed or written anywhere.
            return ActionResult(
                success=False,
                error=(
                    "selfqa-evidence seals a workflow run's folder, so it runs only as a step of a "
                    "run"
                ),
                failure_class="user",
            )
        workspace = run_identity(ctx, "workspace").strip()
        if not workspace:
            # 🔴 A run works in a folder of its own only when its workflow declares one, or when
            # it runs in a project. The bundled self-qa declares a scratch folder, so this is a
            # workflow edited to drop it, run outside a project: say so, and how to fix it.
            return ActionResult(
                success=False,
                error=(
                    "selfqa-evidence has no folder of this run's to seal: the run works in no "
                    'folder of its own. Declare one in its workflow ("workspace": {"mode": '
                    '"scratch"}), or run it in a project.'
                ),
                failure_class="user",
            )

        subdir = str(action_config.get("bundle_subdir", "") or "").strip()
        bundle_dir = _bundle_dir(workspace, subdir)
        if bundle_dir is None:
            # The seal reads, hashes and registers every file of the folder it is given, so only a
            # folder of this run's: a subdir that leads out of it (`..`, an absolute path, a link)
            # would seal another run's captures, or any folder at all, as this scenario's proof.
            return ActionResult(
                success=False,
                error=(
                    f"selfqa-evidence seals only one scenario's folder inside this run's: its "
                    f"bundle_subdir {subdir!r} leads out of it, or names every scenario's, as "
                    "the run's own folder does once it holds scenarios/ (a scenario's folder is "
                    "scenarios/<its commit's sha>)"
                ),
                failure_class="user",
            )
        if subdir:
            # A scenario's own folder, which its drive step may have left uncaptured: sealed empty,
            # the gate below names what is missing, as it does for an empty run folder.
            bundle_dir.mkdir(parents=True, exist_ok=True)
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

        project_id = run_identity(ctx, "project_id")

        def seal():
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
                project_id=project_id,
            )
            return manifest, registered, ev.check_required_kinds(manifest, required_kinds)

        try:
            # Sealing runs ffmpeg over the recording and reads, hashes and copies every file of the
            # bundle, so in a worker thread: the gateway answers everything else meanwhile.
            manifest, registered, gate = await asyncio.to_thread(seal)
        except Exception as exc:  # noqa: BLE001 - error result, never raise past the dispatch seam
            return ActionResult(success=False, error=f"selfqa-evidence failed: {exc}")

        # The fix branch is opened only on a CONFIRMED failure (the scenario did not pass) and only
        # when the flag is on. `create_fix_branch` never pushes; a disabled flag or a bad ref just
        # leaves `fix_branch` empty, and the Task then links nothing rather than a phantom branch.
        fix_branch = ""
        if not passed and fix_branch_enabled and repo:
            fbr = await asyncio.to_thread(create_fix_branch, repo, sha, enabled=True)
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
            # is missing. The evidence_ref is still in the output, so the partial
            # bundle that WAS produced is not lost.
            return ActionResult(
                success=False,
                error="required artifacts missing: " + ", ".join(gate.missing),
                stdout=json.dumps(output),
            )

        return ActionResult(success=True, stdout=json.dumps(output))


#: The folder every scenario's own folder of a self-qa run sits in, one for each commit's sha.
SCENARIOS_ROOT = "scenarios"


def _bundle_dir(workspace: str, subdir: str) -> Path | None:
    """The folder the seal reads, resolved: the run's folder *workspace*, or *subdir* inside it
    (a scenario's own, `scenarios/<sha>`); None when *subdir* leads out of it, as written or
    through a link, or names the folder of every scenario (`scenarios/`, a sha left empty, an
    absolute path to it, a link to it), which would seal them all as one. The run's folder itself
    is sealed only while it holds no scenarios' folder: it would hold every scenario's captures.
    Compared resolved, so no spelling names another folder than the one read, and a link put in
    its place after this check is not the folder read."""
    root = Path(os.path.realpath(workspace))
    every = Path(os.path.realpath(root / SCENARIOS_ROOT))
    real = Path(os.path.realpath(Path(workspace) / subdir)) if subdir else root
    if real == root:
        return None if every.is_dir() else root
    if real == every or (real != root and root not in real.parents):  # `..`, absolute, a link
        return None
    return real


def create_provider(config: dict[str, Any] | None = None) -> "SelfQaEvidenceActionProvider":
    return SelfQaEvidenceActionProvider()
