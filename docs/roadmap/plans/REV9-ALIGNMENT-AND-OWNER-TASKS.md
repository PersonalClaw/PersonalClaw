# Rev-9 Alignment & Owner Tasks — The Pre-Existing 30 Plans

**Created 2026-07-18 (roadmap rev 9).** The 15 new plans (31-47) carry their own deepened designs, executor-ready task tables, and `Owner tasks (real world)` sections inline. This document is the **light-touch companion for the original 30 plans (1-30)**: it (a) binds them to the standing [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md), (b) records where rev-9 decisions change an assumption in them, and (c) enumerates the real-world owner tasks each implies — without rewriting the owner-authored plans themselves.

When plans 1-30 are eventually executed, read the plan **and** its entry here.

---

## Applies to ALL plans (1-47)

- **Execution protocol is mandatory.** Every implementation session runs under [EXECUTION-PROTOCOL.md](EXECUTION-PROTOCOL.md) — scope discipline, definition-of-done (lint + targeted tests + full suite + web build), validate-as-a-user close, the `## Execution log` deviation ledger, and the six escalation triggers. A plan lacking an executor-ready task table (all of 1-30) still executes under the protocol; the *session's first task* is to derive the task table from the plan's session/step outline and record it in the Execution log before coding.
- **Lifecycle change-class (plan 31) applies retroactively.** Before executing any of 1-30, classify each change R/B/S. The known class-B/S ones are annotated below; the rest are mostly R (internal) but confirm per change.
- **Naming/org/domain (decided):** PersonalClaw everywhere; org `github.com/PersonalClaw`; primary domain personalclaw.dev; no force-pushes (worktrees + feature branches → `main`). Any plan text assuming the old `keyurgolani/` paths or squash-mirror model is superseded (PUBLICATION already amended).
- **Zero-telemetry is a feature, not an omission** — no plan may add adoption instrumentation.

---

## Per-plan alignment notes + owner tasks (plans 1-30)

Plans with **no material rev-9 change and no real-world owner action** are marked "— internal; protocol only." Everything else carries specifics.

### Pillar A — Execution Engine

- **1 WORKFLOWS-V2** — Class **B** (engine state/journal formats are persisted; Self-Verification replay gates the journal — treat format changes as Tier-S once external tools read them). *Owner tasks:* none external; this is the flagship engineering program. Sequencing per rev-9: keeps Wave 1; launch/reach plans run alongside, not after.
- **2 LOOPS-EVOLUTION** — Class **B** (loop-engine retirement in Phase 4 migrates live loop state). *Owner:* none. Annotate its Phase-4 retirement with a gate + migration when executed (plan 31).
- **3 UNIVERSAL-PLANNING** — internal; protocol only. (Its verified dead-code deletion is a clean class-R.)
- **4 TASKS-SOPS** — Class **B** (task store schema). *Owner:* none.
- **5 KNOWLEDGE-SYNTHESIS** — internal; protocol only. *Owner:* none (uses bound providers).
- **6 WORK-CONTAINERS** — Class **B** (project/container hierarchy migration of existing tasks/projects). *Owner:* none.
- **7 AUTOMATION-SUBSTRATE** — Class **B** (unifies `triggers.json`; the autonudge absorption migrates behavior). *Owner:* none. Coordinates with plan 42 (both touch the notification/attention path — sequence 42's rules engine before autonudge absorption to avoid two migrations over the same surface).
- **8 LEARNING-FLYWHEEL** — Class **B** (learning.db staging tier). *Owner tasks:* review proposals during dogfood (shared with plan 46's owner task 3 — the same review habit). Its proposal queue MUST land as inbox `kind=proposal` (plan 42 coordination) — one attention surface.
- **30 HARNESS-CRAFT** — internal; protocol only. Its fast-worktree half is *measured-bottleneck-gated* — do not build acceleration before measuring the bottleneck (same discipline as CI plan 33).

### Pillar B — Safety, Resilience & Operations

- **9 AUTONOMY-GUARDRAILS** — Class **B** (budget/incident state files). **Owner tasks:** (1) **set your spend budgets** — the ModelCallGuard meters against real dollar caps; you decide per-scope limits (daily/loop/trigger) and the "denial-of-wallet" ceiling; (2) decide the incident-kill-switch posture (what an active incident blocks); (3) approve the default safety-profile matrix (what `headless`/unattended may do without asking). These are policy calls only you can make; the plan implements the mechanism.
- **10 PLATFORM-RESILIENCE** — internal; protocol only. Its structured-crash-capture is local-only (zero-telemetry preserved). *Owner:* review the auto-fix confirm-gates (what `doctor` may fix without asking).
- **11 SELF-VERIFICATION** — internal; protocol only. Its replay harness gates plan-1's journal format (cross-plan dependency, already noted).
- **12 CONTEXT-ECONOMY** — internal; protocol only.
- **13 EXECUTION-ISOLATION** — Class **B** (secrets-vault storage; sandbox registry). **Owner tasks:** (1) install the sandbox tooling you want available (Docker and/or Lima — the plan ships none/docker built-ins + a Lima tier; Lima needs a manual install on macOS); (2) if using **BYO runners**, provide the runner hosts (SSH targets / second machines) and their credentials; (3) secrets-vault master decision (where the vault key lives — coordinate with plan 47's keychain work). Sequence note: its secret vault and plan-47 keychain should share one credential backend — don't build two.
- **28 ACP-AGENT-PARITY** — internal; protocol only. **Owner task:** have the three agent CLIs installed for the validation sweeps (claude-code, codex, kiro-cli binaries on the machine — the plan's Phase 1 assumes they're present).

### Pillar C — Intelligence & Memory

- **14 MEMORY-GRAPH-AND-VAULT** — Class **B** (memory.db data-model change + vault projection to disk). **Owner tasks:** (1) choose the vault location (Obsidian-compatible Markdown mirror — an owner-facing directory you may point Obsidian at); (2) the memory-model migration is a flagship class-B exercise (gate + migration + snapshot per plan 31). Coordinates with plan 46 (user-model "About you" doc is the self-model step — one mechanism).
- **15 WATCHED-SOURCES** — Class **B** (source registry). **Owner tasks:** for the Drive/Photos connectors — **provide OAuth credentials** (Google Cloud project + OAuth client, or the app-password/API path each connector documents); these are per-provider account setups only you can authorize.
- **16 EVALUATION-SUBSTRATE** — internal; protocol only. **Owner task:** curate benchmark task sets (shared with plan 46 owner task 1 — the learning benchmark is one such study).
- **17 MODEL-ROUTING-TELEMETRY** — internal; protocol only (local telemetry, zero external). *Owner:* review the learned router's local-vs-cloud choices before trusting them (trust-ladder).

### Pillar D — Product Surfaces & Ecosystem

- **18 LOCAL-MODEL-MANAGER-V2** — Class **B** (catalog/state). **Owner tasks:** (1) **hardware** — local inference needs the RAM/VRAM the plan's tiering assumes; you provide the machine; (2) **gated-model access** — HuggingFace tokens for gated weights (e.g. some Llama/pyannote models — pyannote diarization requires accepting HF terms + a token); (3) disk for model downloads. The plan handles download UX; the accounts/hardware are yours.
- **19 PLATFORM-LEGIBILITY** — internal; protocol only. Its "no docs portal" guardrail governs agent-facing docs — the *human* docs site is plan 36 (no conflict; both stated).
- **20 AMBIENT-SURFACES** — internal; protocol only. Menu-bar companion coordinates with plan 45 (desktop tray) — one tray, plan 45 owns the shell, plan 20 owns the tile registry it renders.
- **21 PROACTIVE-ASSISTANT** — internal; protocol only. Its morning-digest ambient slice is **pulled forward** into plan 42 S5 (rev-9); this plan keeps the triage *intelligence* + decision journal. Update its scope note when executed to reference plan 42's digest as the delivery substrate.
- **22 MULTIMODAL-IO** — Class **B** (voice_profiles entity). **Owner tasks:** (1) **record voice samples** if using clone/design voices (consent-as-provenance — the plan requires provenance, you supply the samples + consent); (2) choose/allow a cloning-capable TTS engine (some are gated/paid providers). Voice capture hardware lands on plan 45's desktop bridge. **No always-on capture** — you approve each capture surface.
- **23 BROWSE-AUTOMATION** — internal; protocol only. **Owner task:** `playwright install chromium` (the `[js-render]` extra ships playwright; the browser binary is a post-install step) — and decide the browse action's autonomy posture (it acts on live web pages; guardrails plan 9 budgets apply).
- **24 EXTERNAL-ACCESS** — Class **B** (inbound client records). Already amended (rev 9): §3 read-only MCP → plan 41 (Wave 0/1); sender-trust → plan 40 S1; remainder Wave 3, inheriting plan 41's substrate. **Owner tasks:** when this lands — decide which inbound surfaces to enable (all default-off), generate their bearer tokens, and set `external_access.public_url` only if exposing beyond loopback (a security-boundary decision).
- **25 AGENT-PACKS** — Class **S** (the `.pclaw` pack format is a stable external artifact — versioned + provenance per plan 31). *Owner:* none beyond deciding what to publish as packs.
- **26 DURABILITY-AND-SYNC** — Class **B** (backup manifest + shard formats; on-disk formats become Tier-S candidates per plan 31 open question). **Owner tasks:** (1) choose backup destinations (local dir / external drive / your own remote — no vendor cloud is imposed); (2) for multi-machine sync, provide the transport (the second machine + its reachability — pairs with plan 44's remote-access story). **Restore drills are a launch feature** (self-hosters' first question is "what happens when my disk dies") — the plan runs them; you confirm a real restore once.
- **27 PUBLICATION** — already amended inline (org, domain, no-force-push, CHANGELOG step, re-homed follow-ups). **Owner tasks:** the release sequence itself — see PUBLICATION.md's own steps + plan 36 owner tasks (register domain, create org, transfer repos) and plan 33 owner tasks (PyPI trusted publishing, GHCR settings).
- **29 TEAM-SHARED-ENTITIES** — Class **B** (attribution fields in entity schemas — the plan already "reserves optional author/contributor string fields in still-forming schemas," which is exactly plan-31 forward-compatible thinking). **Owner tasks:** none to build the harness side; *using* it needs a team-shared backend (a shared task/trigger/memory store) which is out of this harness's scope by design — you'd provide/point at one when a team scenario is real.

---

## Cross-plan sequencing deltas introduced by rev-9 (for the maintainer)

1. **Plan 42 before plan 7's autonudge absorption and before plan 8's proposal-queue surfacing** — all three touch the attention/notification path; 42 establishes the one attention store first.
2. **Plan 41 before plan 24** — 24 inherits 41's inbound substrate.
3. **Plan 40 S1 (sender trust) before plan 24 §3** — same substrate, landed early.
4. **Plan 31 before every class-B/S plan** — the doctrine + migration runner must exist before the first governed migration (42 is the first exercise).
5. **Plan 13's secret vault and plan 47's keychain share one credential backend** — build once.
6. **Plan 46's user model defers to plan 14/8's self-model** — one mechanism, reserved slot only in 46.
7. **Plan 45 (desktop) follows plan 39 (platform reach)** for non-mac targets — desktop never leads platform support.

## Owner-tasks index (everything real-world across all 47, one place)

| Category | Plans | What you do |
|---|---|---|
| **Accounts/registration** | 33, 36, 37 | PyPI + TestPyPI (trusted publishing), GitHub org + repo transfer, personalclaw.dev registration + DNS, GitHub Sponsors, Discord server |
| **Money decisions** | 44, 45, 47, 9 | Apple Developer ($99/yr), Play Console ($25), external security audit budget, your LLM spend budgets |
| **Credentials/secrets** | 15, 18, 40, 45, 47 | Google OAuth (Drive/Photos), HuggingFace tokens (gated models), bot tokens (BotFather/Discord/email app-passwords), Apple signing certs, minisign signing key |
| **Hardware/hosting** | 18, 13, 26, 39, 44 | Local inference machine, BYO runner hosts, backup destinations, ARM + Windows test machines, Tailscale |
| **Validation you must drive** | 34, 39, 40, 42, 43, 44, 45, 46 | Clean-machine installs, ARM/Windows checklists, phone-channel walkthroughs, 24h dogfood, 3 usability strangers, mobile field week, benchmark task curation |
| **Policy/copy sign-off** | 9, 35, 37, 43, 46, 47 | Spend/incident posture, security limitations wording, CoC contact + continuity, onboarding + approval copy, benchmark publish decision, scanner-corpus publish decision |
| **Name/brand (done)** | 36 | PersonalClaw + personalclaw.dev decided 2026-07-18; still-open: pursue .com/.ai or drop; optional USPTO screen |
