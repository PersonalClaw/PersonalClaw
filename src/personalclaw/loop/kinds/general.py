"""General kind — a Claude-Code-``/loop``-style generic iterative goal.

The fallback when a task isn't a known space (goal/code/design): a free-form
iterative loop in a chat session that reuses the shared nudge + watchdog. Minimal
subject-matter expertise — no specialized classification or phasing beyond an
optional verify command and a flat, optional set of self-defined phases. The point
is the generic mechanics, not domain breakdown.
"""

from __future__ import annotations

from personalclaw.loop.kinds import register
from personalclaw.loop.loop import Loop


class GeneralKind:
    kind = "general"
    label = "General"
    description = "A generic iterative goal — loop until done, no domain specialization."
    wants_workspace = False
    default_agent = "personalclaw-loop"

    def default_kind_config(self) -> dict:
        # Optional deterministic check; otherwise the loop relies on the judge /
        # budget like an open-ended goal but with no domain-specific phasing.
        return {"verify_command": ""}

    def phase_key(self, phase: dict) -> str:
        return str(phase.get("title", "")).strip()

    def validate_config(self, config: dict) -> tuple[list[str], list[str]]:
        """Screen the optional verify_command — the general kind RUNS it every cycle
        (is_done_signal), so a destructive one must be rejected pre-persist, same gate
        goal/code apply. Without this hook the shared validator never screened a
        general loop's command (run_verify_command's exec-time screen is only the
        defensive backstop; the create/edit gate is the intended one)."""
        from personalclaw.security import audit_bash_command
        cfg = config.get("kind_config") if isinstance(config.get("kind_config"), dict) else config
        cmd = str(cfg.get("verify_command") or "").strip()
        if cmd:
            danger = audit_bash_command(cmd)
            if danger:
                return [f"Verify command rejected — {danger}."], []
        return [], []

    async def is_done_signal(self, loop: Loop, findings: list[dict]) -> bool | None:
        # Generic: if a verify_command is set, the supervisor runs it (verifiable-
        # style); else defer (the loop is bounded by the judge/budget). No domain
        # phasing — the point is the generic mechanics.
        cfg = loop.kind_config or {}
        cmd = str(cfg.get("verify_command", "")).strip()
        if cmd:
            from personalclaw.loop.gates import run_verify_command
            return await run_verify_command(cmd, loop.workspace_dir or None, label="verify")
        return None

    def has_done_check(self, loop: Loop) -> bool:
        # A General loop only has a point-in-time done-check when a verify_command
        # is configured. Without one it defers to budget BY DESIGN — so a None
        # signal is normal, NOT a degraded judge (the watchdog must not flag it).
        return bool(str((loop.kind_config or {}).get("verify_command", "")).strip())

    async def classify(self, task: str, ask, *, skills=None, workflows=None, agents=None) -> dict:
        """No domain specialization — the General kind doesn't analyze a problem
        space. Return safe defaults (the loop just iterates toward the task)."""
        return {
            "title": "", "summary": "", "classified": True, "intake_rigor": "minimal",
            "execution": "solo", "roster": [], "strategy_id": "orchestrator",
            "clarifying_questions": [], "suggested_skill_ids": [], "suggested_workflow_ids": [],
            "marketplace_suggestions": [], "success_criteria": "", "plan": [],
            "kind_config": {"verify_command": ""},
        }

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        cfg = loop.kind_config or {}
        verify_command = str(cfg.get("verify_command", "")).strip()
        lines = ["# Loop Brief", "", f"**Goal:** {loop.task}", "",
                 f"**Max cycles:** {loop.max_cycles}"]
        ws = str(loop.workspace_dir or "").strip()
        if ws:
            lines += ["",
                      f"**Working directory:** `{ws}` — this is your workspace (your shell "
                      "starts here). Read and write files here; when the goal says \"the "
                      "workspace\", it means this directory, not any default."]
        if context_dir:
            lines += ["",
                      f"**Project context dir:** `{context_dir}` — read it for prior project "
                      "context at the start; write durable notes there as you learn them."]
        if verify_command:
            lines += ["",
                      f"**Verification check:** the supervisor runs `{verify_command}` each "
                      "cycle and reads the result. Drive toward making it pass; don't self-certify."]
        if loop.success_criteria:
            lines += ["", f"**Definition of Done:** {loop.success_criteria}",
                      "Make real progress toward this each cycle; a separate check decides done."]
        lines += ["",
                  "**Unattended by default:** investigate ambiguities yourself, record the "
                  "assumption in your finding, and proceed."]
        return "\n".join(lines)

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """Generic per-cycle trigger — the shared spine with no domain framing: read
        status/brief/guidance, do ONE step, MUST write a finding (the cycle's
        deliverable). The General-kind slice may enrich this; the contract is fixed."""
        return "\n".join([
            f"Run the next autonomous cycle for loop {loop.id} "
            f"(working dir for loop files: {loop_dir}). Steps: (1) check status.json — "
            "if not 'running', stop; (2) read brief.md; (3) apply + delete guidance.txt "
            "if present; (4) do ONE adaptive step toward the goal.",
            "",
            f"Before you end this turn you MUST write findings/cycle_NNN.json to {loop_dir} "
            "(next sequential N) — {cycle, summary, key_insight, files_touched, evidence}. "
            "`files_touched` is the list of workspace files you created or modified this cycle "
            "(so they surface as the loop's outputs) — [] if none. Report what you "
            "DID and the EVIDENCE; do NOT self-certify done — a separate check decides that.",
            "",
            "Actually do the work with your tools; do not just describe it. Then end the turn.",
        ])


register(GeneralKind())
