# Plan: Execution Isolation & Runner Substrate — Where and Through What Agents Execute

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog). Not started —
verified 2026-08-04: no `SandboxProvider`/`AgentRunner` seam, no `sandbox/` package (only the
pre-existing single-file `sandbox.py`, unchanged since v0.1.0), no lima/docker tier.
⚠️ **The rev-14 amendment's D0 row was marked "land first" and has NOT landed.** It is docs-only and
dependency-free, and the amendment's own words are that "an inaccurate security claim is a live
defect": `docs/architecture/security.md` still lacks the what-the-sandbox-does-and-does-not-do
section, and `personalclaw.dev`'s security page still says "Bounded capabilities" with no
credential-hiding-vs-confinement qualification. D1/D2/D3 (app env inheritance, the declaration-only
`network` permission's advisory marking, the shared-venv pip install) are also untouched.

---

## Research Integration (2026-07-13)

- **NEW-14** (execution isolation & runner substrate: ephemeral workspace leases, per-run worktree isolation + diff panel, durable tmux sessions, BYO AgentRunner registry with second-opinion handoff, remote BYOI far edge) → §1-§5, honoring the overlap note: WORK-R3 (workspace-provisioning block), WORK-R20 (container mode), WORK-R8 (claim leases) are **approved in WORKFLOWS-V2-WORK-CONTAINERS** — this plan scopes to the REMAINDER (the pluggable provider seam those approved mechanisms sit on, and the runner substrate no approved plan owns). The in-cockpit diff/review panel is already approved there (§4.1 durable-branch reintegration verbs) — not re-planned here. The localhost web preview (exposing a dev server running inside a run's worktree to the cockpit) is specced in §6.2 below as a lightweight port-forward surface.
- **NEW-14 amendments (batch 2)** — (a) Lima VM tier (shell/file ops only, LLM host-side, cached availability probe, host↔guest path translation, explicit degradation) → §2; (b) pluggable SandboxProvider registry (bind-mount/isolated taxonomy + 6-method handle, none+docker built-ins, consumed by subagents/code loops/terminal/apps sandbox) → §1; (c) BYO runner as data catalog (agent_metadata schema, PATH-probe health, pinned+checksummed adapter bundles, capability gating from parsed initialize, idle-release + lease + transparent reconnect) → §3; (d) ProposerBackend 4-method contract → §4.2; (e) turn-bound two-phase file checkpointing + /rewind-to-turn → §6; (f) sandbox-internal loopback-only tool gateway → §5.
- **NEW-14 amendments (batch 3)** — reviewer-comment triage primitive (line-anchored diff comments → human-accepted subset → auto-dispatched follow-ups to the originating session) → §7; Memoh's container-direction confirmation + hosting external ACP agents behind a controlled tools proxy → §3/§5 (per-stage snapshot checkpoints stay with WORK-R20, not duplicated).
- **NEW-27** (per-project secrets store), honoring the overlap note: WORK-R19 (keychain-backed per-project secrets, secret-filtered leaf env) + WF2-R14/AUTO-R14 (`{{secret:KEY}}` templating) are **approved** — the REMAINDER is the standalone vault UX (§8): the settings surface, inherit-from-host value-omitted entries as a first-class row type, and cross-consumer presence views.
- **Deferred out of this plan:** the remote BYOI provisioner (own home server/VM). It is the far edge of the same SandboxProvider seam — §1's isolated-kind contract is written so a future `byoi` provider (emdash's provision/terminate JSON-on-stdout contract) slots in without registry changes, but no remote execution ships here (local-first soul; a remote substrate is its own plan when the user has the hardware itch).

---

## Overview

PersonalClaw executes everything **directly on the host today**. Verified starting points (2026-07-12 recon + code read 2026-07-13):

- `src/personalclaw/sandbox.py` — the ONE existing isolation mechanism: an OS seatbelt (macOS `sandbox-exec` Seatbelt profile / Linux user-namespace bind-mounts) that *hides credential dirs* from agent child processes (`wrap_argv`, sandbox.py:606). It is a **path-visibility filter, not an execution environment** — no filesystem isolation for writes, no resource limits, no disposability.
- `subagent.py` spawns run on the host with `validate_cwd` against `agent.subagent_cwd_allowed_roots` (subagent.py:209) — a path allowlist, again not isolation. The destructive-test incident that deleted the user's real bound L6 model is the proven failure class.
- `loop/worktree.py` gives code loops git-worktree separation (branch isolation, NOT process/fs isolation), and `schedule_script.py:run_script_sandboxed` (:236) wraps cron scripts in the seatbelt.
- The apps-platform sandbox (deferred task #71 — L3 permissions are declared in manifests but unenforced) has no substrate to enforce onto.
- External agent CLIs are already first-class: PClaw speaks **3 ACP dialects** via provider apps (`apps/claude-code-agent`, `apps/codex-agent`, `apps/kiro-cli-agent`), with vendor-neutral machinery in core: `acp/dialect.py` (ACPDialect strategy — core never names a CLI), `acp/cli_resolve.py` (`resolve_acp_cli` — env-var override → `shutil.which` → nvm/mise/asdf/volta/fnm roots → `npx -y` fallback; `provision_acp_adapter`), `acp/connection_pool.py` (one warmed connection per ready runtime, claim + background re-warm), and `acp_bundles/_register.py` (a bundle = launch argv + dialect id + spawn env). What's missing is not protocol plumbing — it's the **catalog, health evidence, capability gating, and lifecycle discipline** around these runners.
- Durable sessions have a beachhead: terminal P25 already wraps PTYs in tmux (opt-in `dashboard.terminal.persist`, dedicated socket `-L personalclaw`, session names `pclaw-<id>`, orphan-reaper kills the *client* never the session — dashboard/handlers/terminal.py:56-99). It covers ONLY the dashboard terminal; agent worker sessions are still reaped on restart.
- WORKFLOWS-V2.md §scope-violation (line ~366) explicitly reserves the third enforcement layer: *"OS seatbelt (a future sandbox provider receives `allowed_write_paths` as policy)."* **This plan is that future sandbox provider.**

Two fused backlog items, one substrate: **NEW-14** builds the pluggable answer to "where does agent work execute and through which agent binary," and **NEW-27**'s remainder gives the secrets that isolated execution needs a real UX. The structural payoff: subagents, code loops, workflow stage nodes, the terminal, and the apps sandbox all consume ONE seam, so `none → docker → lima` is a per-run knob instead of five bespoke integrations.

**Soul guardrail:** your machine, your installed CLIs, your Docker/Lima if you have them — graceful degradation to `none` when you don't. No fleet, no k8s, no remote deploy. The runner catalog is a folder of markdown+JSON files, not a service registry. Learning stays propose-don't-write (§7's triage queue proposes; the human accepts).

---

## 1. Pluggable SandboxProvider Registry

### 1.1 The contract (two kinds, one 6-method handle)

Sandcastle's proven minimal seam, adopted verbatim as a Python protocol in a new `src/personalclaw/sandbox_providers/` package (sibling of `action_providers/`, same registry shape):

```python
# sandbox_providers/base.py
class SandboxProvider(ABC):
    name: str                      # "none" | "docker" | "lima" | app-contributed
    display_name: str
    kind: str                      # "bind_mount" | "isolated"
    def is_available(self) -> tuple[bool, str]: ...      # cached probe (§2.2)
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

@dataclass(frozen=True)
class SandboxSpec:
    workspace_dir: str             # the WORK-R3 workspace block's resolved dir
    allowed_write_paths: tuple[str, ...]   # WORKFLOWS-V2 scope policy, enforced here
    egress_tier: str               # off|listed|registry|all — AUTONOMY-GUARDRAILS §4.2
    env: dict[str, str]            # already secret-filtered by WORK-R19 (§8)
    safety_profile: str            # AUTONOMY-GUARDRAILS §3 profile name

class SandboxHandle(ABC):          # the 6-method contract
    async def exec(self, argv, *, cwd=None, on_line=None) -> ExecResult: ...  # non-zero RETURNED, never raised
    async def close(self) -> CloseResult: ...   # dirty-preservation: returns preserved_workspace_path
    @property
    def worktree_path(self) -> str: ...          # host-visible path (bind_mount) or guest path (isolated)
    async def copy_file_in(self, host, guest): ...
    async def copy_file_out(self, guest, host): ...
    async def copy_in(self, host_dir, guest_dir): ...    # isolated-kind bulk sync
```

- **`bind_mount` kind:** host owns the workspace (a WORK-R3 worktree/scratch dir); the provider mounts it in. No sync. `docker` is this kind.
- **`isolated` kind:** provider owns the filesystem; code synced in/out via `copy_in`/`copy_file_out`. Reserved for Lima's stricter mode and a future `byoi`. Sync-out for git workspaces uses sandcastle's `format-patch` + `git am --3way` with a sandbox-owned base ref (`refs/personalclaw/sync-base`) so repeated syncs don't poison the base (ADR 0017 mechanism, adopted as-is).
- **`none` built-in:** today's behavior, formalized — composes the existing `sandbox.wrap_argv` seatbelt (credential-dir hiding stays) and executes on the host. It is the universal fallback and the default; every consumer works with zero new dependencies installed.
- **`docker` built-in:** bind-mount worktree, UID-aligned image via `--build-arg AGENT_UID=$(id -u)` at build time (NEVER runtime `chown -R` — sandcastle's permissions taxonomy: pre-created parent dirs for single-file mounts, SELinux `:z` labels on Linux, `--userns=keep-id` for rootless Podman). `is_available` probes the docker socket.
- **Failure honesty:** `exec` returns exit codes; `create` failures carry a WHAT/WHY/FIX-shaped error. A consumer that requested `docker` on a machine without Docker gets a typed refusal + the `none` fallback ONLY if its safety profile permits downgrade (unattended code runs do NOT silently downgrade — they park needs-input, per the AUTONOMY-GUARDRAILS pause-into-needs-input pattern).

### 1.2 Where it plugs in (provider fidelity)

- **New provider type `sandbox`:** added to `PROVIDER_TYPES` (apps/manifest.py:453) AND a new `SandboxTypeHandler` in `providers/registry.py` wired in `get_provider_registry()` — the two MUST land in the same commit or `test_manifest_types_match_handlers` fails (the #47 bug class). The handler `create()`s via the standard `providers/loader.py:load_factory` path and registers into `sandbox_providers/registry.py:register_sandbox_provider` (module-level flat dict, the `action_providers/registry.py` shape). Apps can therefore contribute sandbox providers (`podman`, a future `byoi`) exactly like `apps/webhook-action` contributes an action provider.
- **Built-ins register at boot** like channel transports' webui: a `register_default_sandbox_providers()` call in `dashboard/server.py` startup registers `none` + `docker` — they are core-native, not apps. `lima` ships as a **first-party app** (`apps/lima-sandbox`, §2) because it carries a real external dependency.
- **SDK:** `sdk/sandbox.py` re-exports `SandboxProvider`/`SandboxHandle`/`SandboxSpec` (the `sdk.net`/`sdk.security` facade precedent) so contributed providers import only the SDK.
- **NOT an action provider** — nothing here touches `ALLOWED_HOOK_PROVIDERS` (validation.py:555) for §1 (§4 and §7 DO add action providers; see there).

### 1.3 The four consumers (uniform adoption)

1. **Subagents:** `SubagentManager.spawn` gains `sandbox: str = "none"`; the spawn path resolves the provider and runs the worker's process tree through `handle.exec`. `validate_cwd` + the existing approval ladder are unchanged — sandboxing is an *additional* layer, not a replacement for the allowed-roots check. Read-only research-class subagents (AUTONOMY-GUARDRAILS §4.1) pair naturally: capability class picks tools, sandbox picks filesystem blast radius.
2. **Code loops / workflow stage nodes:** the WORK-R3 workspace block gains `sandbox: none|docker|lima` beside `mode:` — the workspace block resolves the DIR, the sandbox provider resolves the EXECUTION BOUNDARY around it. WORK-R20's `mode: container` is re-expressed as workspace `mode: worktree` + `sandbox: docker` when it lands (one substrate, and WORK-R20's snapshot checkpoints become a `docker` provider capability rather than an engine special case). The engine hands `allowed_write_paths` into `SandboxSpec` — closing the loop WORKFLOWS-V2 left for "a future sandbox provider."
3. **Terminal:** a per-terminal-session sandbox picker (default `none`) so "open a shell inside the run's sandbox" is one dropdown; composes with P25 tmux persistence (§5) — tmux runs INSIDE the sandbox for isolated kinds.
4. **Apps sandbox (task #71, un-deferred):** app backend subprocesses (`apps/backend_runtime.py`) launch through the provider named by a new manifest field `backend.sandbox` (default `none`, warning-badged in the store UI when a `permissions.storage/network`-heavy app declares none). The manifest `permissions` block finally gets teeth: `permissions.network` maps to `egress_tier`, `permissions.storage` to `allowed_write_paths`.

**What §1 is NOT:** it does not own worktrees (WORK-R3), leases (WORK-R8 — a leased leaf *additionally* executes through a provider), or run retention. It is the execution boundary only.

---

## 2. Lima VM Tier (shell/file ops only; LLM stays host-side)

A first-party app `apps/lima-sandbox` contributing a `sandbox`-type provider (`kind: isolated` with a bind-mount fast path via Lima's built-in mounts):

- **Split-brain by design:** ONLY shell/file operations route into the VM (`limactl shell <instance> -- <argv>` as the `exec` transport). The LLM conversation, provider resolution, credentials, and tool-approval logic stay host-side — the VM never holds an API key (§5 completes this: tool calls tunnel out, credentials never tunnel in).
- **Cached availability probing:** `is_available()` = `limactl` on PATH AND the named instance running; result cached with a short TTL (the connection-pool health-check cadence precedent, acp/connection_pool.py) so per-exec probes don't add latency. The extensions-list availability hook (`providers/loader.py` module-level `availability()`) surfaces "Lima not installed / instance stopped" as a greyed-out provider with the reason — the existing UX for unusable providers.
- **Host↔guest path translation:** an explicit `translate_path(host_path) -> guest_path` on the provider (Lima mounts `~` at a predictable guest prefix); `worktree_path` returns the guest path for `exec` cwd while `copy_file_out` addresses host paths. Translation failures are typed errors, never silent identity mapping.
- **Explicit degradation dialog:** when a consumer requests `lima` and the probe fails, interactive surfaces show a one-question dialog: *"Lima unavailable (reason) — run with path-guard-only (`none` + seatbelt) or cancel?"* Unattended surfaces park needs-input (§1.1 downgrade rule). "Path-guard-only" is honest naming for what `none` actually provides.
- **NO GUI/desktop/browser-in-VM** (Memoh runs Xvnc in containers; that is fleet-platform machinery — out of soul).

---

## 3. BYO AgentRunner Registry (data catalog over the existing ACP seam)

### 3.1 Runners stay provider apps; the catalog is new

Runner *registration* already has the right shape — `apps/claude-code-agent` etc. register through `acp_bundles/_register.py` (argv + dialect + env) into the model registry. This plan does NOT invent a second registration path. It adds:

1. **Gemini CLI runner:** a new first-party app `apps/gemini-cli-agent` (bundle: bin names + env-var override + npm adapter package + its dialect subclass) — the third-party matrix becomes Claude Code / Codex / Gemini CLI / Kiro, all via `resolve_acp_cli`'s existing 4-step PATH auto-detection (env override → which → version-manager roots → npx).
2. **The runner data catalog:** `agent_metadata.py` today stores free-form per-agent `.md` files (`~/.personalclaw/agent-metadata/<name>.md`). It gains a structured JSON sidecar per runner — `agent-metadata/<name>.runner.json` (atomic_write, same `_SAFE_NAME_RE` validation):

   ```json
   {"command": "claude", "resolved_command": ["/Users/x/.nvm/.../claude"],
    "args": [], "env": {"ANTHROPIC_..." : "<presence-flag>"},
    "behavior_policy": {"permission_mode": "auto", "effort": "medium"},
    "last_check": {"ok": true, "checked_at": "...", "probe": "initialize",
                   "version": "1.2.3", "latency_ms": 840, "error": null},
    "capabilities": {"resume": true, "fork": true, "plan_mode": true,
                     "permission_modes": ["default","acceptEdits","bypassPermissions"],
                     "efforts": ["low","medium","high"]}}
   ```

   `last_check` is the **health-evidence column set**: written by a PATH-probe + handshake health check (reuse the connection pool's warm attempt — a successful `initialize` + `session/new` IS the health check; no separate probe process). The Settings → Agents surface renders catalog rows with evidence ("healthy 2m ago, v1.2.3, 840ms handshake"), replacing today's binary ready/not_found.
3. **Capability gating from parsed initialize:** `ACPDialect.normalize_discovery` already normalizes models/modes/efforts per CLI — the catalog persists that `DiscoveryResult` into `capabilities`, and consumers **branch on the matrix instead of assuming uniformity** (emdash's 33-CLI lesson): a runner without `resume` gets fresh-session retry only; one without `plan_mode` gets the injected-policy-file plan mode (UNIVERSAL-PLANNING's synthesized fallback); the §4 handoff refuses runners whose health evidence is stale.
4. **Pinned + checksummed adapter bundles:** `provision_acp_adapter` (acp/cli_resolve.py:274) currently installs adapter npm packages unpinned. It gains a pin table (exact versions per adapter package, shipped in each runner app's bundle) + sha256 verification of the installed package tarball against a checksum recorded in the app's manifest — the `install_guarded` posture (`.pclaw-lock.json` per-file sha256 precedent, skills/marketplace.py:193) applied to runner adapters. `npx -y` fallback (which cannot pin-and-verify) is demoted to interactive-only with a warning; unattended spawns require a provisioned, verified adapter.
5. **Idle-release + lease + transparent reconnect:** the connection pool (acp/connection_pool.py) already does claim-and-rewarm. Extended: (a) **idle-release** — a claimed runner connection idle past a TTL is released back (Memoh's 30min-bound/5min-unbound shape; config `agents.runner_idle_release_secs`); (b) **lease** — a claimed connection carries a WORK-R8-style lease record (holder session_key, expires_at) so the Settings surface and co-tenant sessions can see who holds which runner — this REUSES the approved WORK-R8 lease convention (flock files under `~/.personalclaw/locks/`), not a second locking scheme; (c) **transparent reconnect** — on gateway restart or connection death mid-session, the session layer re-claims a warm connection and resumes via the runner's native session storage (the capability matrix says whether resume is possible; without it, the turn fails visibly instead of silently rewinding).

### 3.2 Config wiring (the four points)

New `AgentConfig` fields (`agents.runner_idle_release_secs`, `agents.runner_health_check_secs`, `agents.unattended_requires_verified_adapter`) wired through ALL FOUR points: dataclass `_meta(label, help)` → `AppConfig.load()` explicit mapping (loader.py:1638+, omission = silent drop) → `to_dict()` → `_EDITABLE_CONFIG` (dashboard/handlers/core.py:363) + FE for the runtime-editable subset.

---

## 4. Second-Opinion Handoff (stalled node → different runner, fire-wait-verify)

### 4.1 The action provider

A new core-native action provider `second-opinion` (registered in `action_providers/registry.py:_ensure_default_providers_registered` AND **added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)** — a new action provider that skips this is rejected by hook create/update even though the UI offers it). It packages a stalled node/loop/session's state into a **one-shot handoff brief** (agentsystem's `/handoff-codex` packet, generalized):

- Brief contents: original goal; what was tried with **verbatim errors**; where stuck; files touched; a **FRESH `git status` + `git diff`** taken at brief-build time (stale diffs are worse than none); the concrete ask. Written to a unique file under the run/loop dir, fenced where it embeds transcript excerpts (`fence_untrusted`, security.py:672).
- Target selection: a DIFFERENT runner from the catalog (§3) than the one that stalled — filtered by health evidence + required capabilities; the user's binding order breaks ties.
- **Fire-wait-verify:** invoke the target runner headless (single turn, hard timeout, `sandbox:` per the consumer's spec — a second-opinion run gets the same isolation as the stalled one); then **verify by re-diffing disk** — the runner's final message describes intent, not what landed; claimed-but-absent edits = failed handoff, recorded honestly.
- Consumers: loop watchdog (a `stagnant` loop offers "second opinion" beside nudge/stop), workflow gate nodes (an `on_stall: second_opinion` policy), and manual — a button on the run cockpit / loop cockpit stalled banner.

### 4.2 ProposerBackend contract

The handoff's runner-invocation half is factored as a 4-method contract so anything that wants "ask an external brain one question" reuses it:

```python
class ProposerBackend(Protocol):
    name: str
    async def prepare(self, brief: HandoffBrief) -> PreparedInvocation: ...   # per-runner instruction rendering
    async def invoke(self, prepared: PreparedInvocation) -> InvocationRef: ...
    async def collect(self, ref: InvocationRef) -> ProposerResult: ...        # normalized {ok, summary, diff_verified, artifacts, raw_ref}
```

`prepare` renders runner-specific instructions (Claude Code wants different framing than Gemini CLI — the dialect knows); `collect` normalizes into one result record consumed identically by loops/gates/UI. Backends: one per cataloged runner (built from the §3 catalog), plus a `subagent` backend (a fresh PClaw subagent as the second brain — zero external dependencies, the degradation path when only one runner is installed).

---

## 5. Durable tmux-Backed Sessions + Sandbox-Internal Tool Gateway

### 5.1 Durable sessions (extend P25, don't fork it)

Terminal P25 proved the mechanism (dedicated socket `-L personalclaw`, detached sessions, reaper kills clients never sessions). Extended to **agent worker sessions**:

- **Deterministic names** derived from identity, not randomness: `pclaw-<project_id>-<run_or_loop_id>-<session_slug>` (same `_tmux_session_name` sanitization, terminal.py:96) — so a restarted gateway *recomputes* the name and **reattaches instead of reaping** (emdash's exact recipe). Applies to: long-lived ACP runner processes (§3), sandbox-interior shells (the tmux session lives INSIDE the sandbox handle for isolated kinds — survives gateway death because the sandbox owns it), and P25 terminals (unchanged).
- **Reattach-not-reap boot order:** the existing orphan recoveries (`reap_orphaned_loops`, subagent `_reconcile_orphans`) gain a pre-step — before tombstoning, probe `tmux -L personalclaw has-session -t <recomputed-name>`; alive → reattach + resume streaming (the run flips to WORK-R7's `suspended`→resumed path, not `aborted`), dead → today's tombstone path. This is the substrate-liveness check WORK-CONTAINERS §5.2 specifies, given its concrete mechanism.
- Opt-in via the same config family (`dashboard.terminal.persist` precedent; new `agents.durable_sessions` flag through the four wiring points). tmux missing → feature silently off, behavior identical to today.

### 5.2 Sandbox-internal loopback-only tool gateway (zero listening ports, zero credentials inside)

Isolated sandboxes (docker/lima) still need tools (memory recall, `knowledge_search`, notify). Memoh's answer is an in-container HTTP proxy on 127.0.0.1 — ours is stricter:

- **Zero listening ports inside the sandbox.** The transport is the sandbox handle's own `exec` channel: the host injects a tiny shim (`pclaw-tool` — a single static script copied in via `copy_file_in`) that the agent calls like a CLI; the shim writes the JSON-RPC request to stdout of an exec-owned pipe pair the HOST initiated, and the host-side gateway executes the tool and returns the result over the same channel. Nothing inside the sandbox can be connected TO; there is no socket to scan, no port-forward to misconfigure.
- **Zero credentials inside.** The host authorizes calls by construction (it spawned the exec channel — the same trust basis as `X-Internal-Secret` internal HTTP, messaging.py:75, but with no secret to leak because there is no network hop). Tool results entering the sandbox are just data; secrets referenced by tools resolve host-side (§8 / WORK-R19) and never serialize into the channel — the RedactingSink (`security.redact()`) wraps the channel writer as defense in depth.
- **Policy at the host end:** the shim's tool surface is the sandbox spec's safety profile (AUTONOMY-GUARDRAILS §3) — a research-class sandbox gets read-only tools; every call SEL-audited under the owning session key. This is ALSO how hosted external ACP runners (§3) inside sandboxes reach PClaw tools (Memoh's "controlled MCP tools proxy," minus the port).

---

## 6. Turn-Bound Two-Phase File Checkpointing + /rewind-to-turn

The interactive-tier complement to WORKFLOWS-V2's journal checkpoints (run-scoped) and WORK-R20's container snapshots (stage-scoped). Scope: **chat sessions and their tool-driven file edits on the host** — where today a wrong `Edit` is simply gone.

- **Two-phase:** (1) at turn start, snapshot the *identity set* — paths+mtime+size of files under the session's cwd scope (cheap manifest, no copies); (2) **pre-edit backup** — the file-writing tool handlers (edit/write in `mcp_core`'s file tools) copy the target file into `~/.personalclaw/checkpoints/<session_slug>/<turn>/<path-hash>` *before* the first mutation of that file in that turn (content-addressed, deduped; `atomic_write_bytes`). Only touched files cost bytes.
- **/rewind-to-turn:** a session affordance (chat `>` menu + `POST /api/sessions/{key}/rewind {turn}`) restoring every file backed up in turns > N, with a **preview diff first** (files, sizes, current-vs-restored) and explicit confirm — never a blind restore. Restores are SEL-audited. Conversation history is NOT rewound (the transcript is the record); the affordance is filesystem-only and says so.
- **Bounds (personal-scale):** per-session cap (default 200MB / 50 turns, config via the four wiring points), pruned with the session; binary files over a size threshold recorded as manifest-only (restore warns "not captured"). Explicitly NOT git — it works in non-repos and never touches the user's index; inside a WORK-R3 worktree run the durable-branch mechanism is the better tool and the engine prefers it.

### 6.2 Localhost Web Preview

When a run's worktree (or sandbox) is running a dev server, the cockpit/widget can preview it:

- **Port discovery:** the run's workspace process tree is scanned for listening ports (lsof/ss on the sandbox handle; for docker, the exposed port mapping); discovered ports registered as `preview_urls` on the run record.
- **Surface:** the cockpit renders an "Open Preview" affordance linking to `localhost:<port>` (local-only, no tunneling — single-user, same machine). For docker/lima sandboxes: the port is mapped to the host at sandbox creation (`SandboxSpec.expose_ports: [int]`).
- **Lifecycle:** preview URLs are ephemeral — live while the sandbox/worktree process is alive; removed from the run record on sandbox teardown. No authentication layer needed (localhost, single user).
- **Scope guard:** this is NOT a general-purpose tunnel or public share. It serves the "see what my code loop built" use case entirely within the local machine.

---

## 7. Reviewer-Comment Triage Primitive

Air's productized loop, built once as a shared primitive (not per-surface):

- **The record:** review-producing agents (workflow review/gate stages, loop judges, the §4 second-opinion, inbox draft reviewers) emit **line-anchored diff comments** conforming to the WORKFLOWS-V2 Canonical Finding record (`{severity: Critical|Major|Minor|Nit, location(file:line), problem, why, recommended_fix, status}` — WORKFLOWS-V2.md §Canonical-Finding) plus agentsystem's `auto_fixable: bool` flag ("a mechanical, context-free edit appliable without judgment; when in doubt, false"). One contract, already approved engine-side — this plan adds the *triage surface and dispatch*, not a second schema.
- **Triage:** a diff-anchored review panel (extends the WORK-CONTAINERS cockpit diff panel — comments pinned to lines) where the human accepts/rejects each finding; comments validated against the ACTUAL diff before render (sandcastle's reviewer post-filter — a finding anchored to a line that doesn't exist is flagged, not shown as truth).
- **Dispatch:** the accepted subset auto-dispatches as **follow-up instructions to the ORIGINATING session** (the worker that produced the diff — resumed via its runner's native resume per the §3 capability matrix; no resume capability → fresh session with the handoff brief). `auto_fixable: true` findings below a severity threshold may be batch-applied mechanically (opt-in per surface). Rejected findings are recorded with the rejection — feeding LEARNING-FLYWHEEL's calibration (a reviewer whose findings are always rejected is a fake gate), propose-don't-write throughout.
- **Reusers:** workflow gate nodes, loop judge feedback (the LOOPS-EVOLUTION migration checklist's missing feed-back-accepted-comments step), and inbox drafts (accept-edits-on-a-draft is the same shape).

---

## 8. Secrets Vault UX (NEW-27 remainder over WORK-R19)

The store, keychain backing, spawn-time resolution, secret-filtered leaf env, and `{{secret:KEY}}` templating are ALL approved (WORK-R19 in WORK-CONTAINERS §4.3; WF2-R14/AUTO-R14). This section builds ONLY the standalone UX those mechanisms lack:

- **Settings → Secrets vault:** one surface listing secrets across scopes — global (the existing `.env` via `save_credential`, loader.py:255) and per-project (the WORK-R19 keychain namespaces) — with per-row: name, scope, **presence-only value display** (never readable back; re-enter to rotate), created/last-used stamps (last-used fed by SEL entries at resolution sites), and consumer links ("used by: run-template X, trigger Y" — computed by grepping `{{secret:KEY}}` references across workflow defs + triggers, the same referrers pattern as `workflows/composition.py:referrers`).
- **Inherit-from-host rows as a first-class type:** an entry that names a key but omits the value inherits from the host environment at spawn (Air's pattern, already specified mechanically in WORK-R19) — the vault UX renders these distinctly ("inherited: set in your shell, never stored") so the user can see which secrets PClaw holds vs merely passes through. Reserved vars (HOME, PATH, XDG_*) are rejected at the form.
- **Project hub Context tab** shows the project's secret **presence flags** (approved in WORK-CONTAINERS §6.1) — this plan links them to the vault surface for editing; no values ever render there either.
- **Sandbox integration:** the §1 `SandboxSpec.env` is populated ONLY from explicit vault grants (per WORK-R19's secret-filtered leaf env); the vault UI's per-secret "grant to sandboxed runs" toggle is the consent surface. `docker`/`lima` providers never see ungranted keys, and §5.2 guarantees granted tools don't leak them back in transcripts.
- **API:** `GET/POST/DELETE /api/secrets` (names + scopes + presence only; values write-only), registered in `dashboard/handlers/`. Export/portability: secrets NEVER travel (the existing `EXPORT_EXCLUDE` posture, portability.py:38 — the vault adds nothing to exports beyond presence-flag metadata in project exports, per WORK-R15).

---

## 9. Provider-Fidelity Wiring Summary (where each piece plugs in)

| Piece | Plugs in via |
|---|---|
| SandboxProvider registry | NEW provider type `sandbox`: `PROVIDER_TYPES` (manifest.py:453) + new `SandboxTypeHandler` (providers/registry.py) in the SAME commit (#47 parity test); domain registry `sandbox_providers/registry.py`; built-ins (`none`, `docker`) boot-registered like `register_default_transports()` |
| Lima provider | first-party app `apps/lima-sandbox` (`provider: {type: "sandbox", implementation: "provider:create_provider"}`), module-level `availability()` hook for greyed-out UX |
| SDK surface | `sdk/sandbox.py` facade (SandboxProvider/Handle/Spec) — the `sdk.net`/`sdk.security` precedent |
| Gemini runner | first-party app `apps/gemini-cli-agent` via `acp_bundles/_register.py` (argv + dialect + env) — the existing claude-code/codex/kiro path, no new registration mechanism |
| Runner catalog | `agent_metadata.py` extension (`<name>.runner.json` sidecar, atomic_write); health evidence written by the connection pool's warm attempts |
| `second-opinion` action provider | `action_providers/registry.py:_ensure_default_providers_registered` + **`ALLOWED_HOOK_PROVIDERS` (validation.py:555)** — mandatory, or hook create/update rejects it |
| Triage dispatch | consumes the approved WORKFLOWS-V2 Canonical Finding record; panel extends the WORK-CONTAINERS cockpit diff panel; new SSE events added to `useRunStream.ts RUN_LIFECYCLE` (EventSource drops unregistered types) |
| Config fields | ALL new fields (agents.runner_*, agents.durable_sessions, sandbox defaults, checkpoint caps) through the FOUR points: dataclass `_meta` → `AppConfig.load()` mapping → `to_dict()` → `_EDITABLE_CONFIG` + FE |
| Secrets vault | UX over the approved WORK-R19 store + existing `save_credential`; SEL-audited resolution; presence-only API |
| Audit | every sandbox create/close, adapter provision+verify, handoff fire/verify, rewind restore, and secret resolution logs to `sel.py` — same as egress/skill-install guards |

**Memory vs Knowledge boundary:** this plan touches neither store. Sandboxes, runner catalogs, checkpoints, and the vault are harness mechanics (files under `~/.personalclaw/` + OS keychain), not memory entries and not knowledge items. The §7 rejection records feed LEARNING-FLYWHEEL's propose queue (harness-side); nothing here writes `memory.db` or `knowledge.db`.

---

## 10. Disposition & Dependency Notes

| Item | Disposition |
|---|---|
| WORK-R3 workspace block / WORK-R20 container mode / WORK-R8 leases / WORK-R19 secrets store | **APPROVED elsewhere — consumed, not rebuilt.** §1.3 adds `sandbox:` beside the workspace block; WORK-R20 re-expresses as `sandbox: docker` capability; §3.1(5) reuses the R8 lease convention; §8 is UX-only over R19 |
| In-cockpit diff panel + reintegration verbs + localhost preview | approved in WORK-CONTAINERS §4.1 — §7's triage panel *extends* it |
| WORKFLOWS-V2 `allowed_write_paths` third layer ("future sandbox provider") | **fulfilled by §1** — the engine's scope policy becomes `SandboxSpec.allowed_write_paths` |
| existing `sandbox.py` seatbelt | **KEPT** — composed into the `none` provider; renamed nothing, broke nothing |
| `acp/` machinery (dialects, cli_resolve, connection_pool) | **KEPT + extended** (pin table, health persistence, idle-release/lease/reconnect) |
| terminal P25 tmux | **KEPT + generalized** to agent sessions (§5.1) |
| Remote BYOI provisioner | **DEFERRED to its own future plan** — §1's isolated-kind contract is its landing slot |
| Memoh per-stage snapshot checkpoints | stays with WORK-R20 (container capability), not duplicated here |
| apps sandbox task #71 | **un-deferred** — becomes §1.3(4), consuming the same substrate |

---

## 11. Implementation Effort

**~7 sessions.**

1. **SandboxProvider seam + `none`:** package, registry, `SandboxTypeHandler` + `PROVIDER_TYPES` (same commit), SDK facade, `none` provider composing `wrap_argv`, SubagentManager `sandbox:` param, config wiring.
2. **`docker` provider + workspace-block integration:** bind-mount provider with the UID/permissions checklist, `allowed_write_paths`/egress-tier/profile threading, code-loop + stage-node adoption, downgrade/park semantics.
3. **Lima app + apps-sandbox (#71):** `apps/lima-sandbox` (probe cache, path translation, degradation dialog), `backend.sandbox` manifest field + permission mapping, terminal sandbox picker.
4. **Runner catalog + Gemini:** `apps/gemini-cli-agent`, `.runner.json` sidecar schema + health-evidence writes from pool warms, capability persistence from `normalize_discovery`, adapter pin table + sha256 verify, Settings → Agents evidence surface.
5. **Runner lifecycle + durable sessions:** idle-release, lease records, transparent reconnect; deterministic tmux names + reattach-not-reap boot pre-step wired into both orphan recoveries; `agents.durable_sessions` flag.
6. **Second-opinion + tool gateway:** handoff brief builder + fire-wait-verify, `second-opinion` action provider + `ALLOWED_HOOK_PROVIDERS`, ProposerBackend + subagent fallback backend, cockpit stalled-banner affordance; `pclaw-tool` shim + exec-channel gateway with profile-scoped tool surface.
7. **Checkpointing, triage, vault:** two-phase file checkpoints + `/rewind-to-turn` (preview + confirm), triage panel + accepted-subset dispatch + auto-fixable batch apply, secrets vault surface + presence API + grant toggles; as-a-user validation sweep across all seven mechanisms.

Sessions 1-2 are the load-bearing pair; 4-5 (runner substrate) and 6-7 are independently shippable behind them.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Docker/Lima absent on the user's machine → dead feature surface | `none` is the default everywhere and fully functional; availability hooks grey out unusable providers with reasons; nothing REQUIRES a container runtime |
| Silent isolation downgrade defeats the point | typed refusal + profile-gated downgrade: interactive asks, unattended parks needs-input; downgrades SEL-audited |
| Exec-channel tool gateway becomes a bottleneck for chatty tool use | per-call overhead is one exec on an already-live sandbox; the shim batches; measured before generalizing — worst case falls back to fewer, larger tool calls (the agent is told the cost) |
| tmux reattach adopts a session whose work is stale/diverged | reattach only when the recomputed name matches AND the run record says the substrate should be alive (WORK-R7 substrate-liveness pairing); adopted sessions flagged `resumed` in the journal, never silently continuous |
| Adapter pin table rots (upstream ACP adapters move fast) | pins live in each runner app's manifest — updated via the normal app-update path; a stale pin fails loudly at provision with the WHAT/WHY/FIX envelope, npx interactive fallback still exists |
| Checkpoint store grows unbounded / captures secrets | per-session caps + prune-with-session; backups run through `security.redact()`-aware exclusion for known secret paths (`.env*` never checkpointed — restore warns); binary threshold manifests-only |
| Second-opinion runner returns confident garbage | fire-wait-VERIFY: disk re-diff is the acceptance test, not the runner's message; result carries `diff_verified: false` when claims don't land; consumers treat unverified results as failed |
| Triage comments anchored to phantom lines erode trust | validate every finding against the actual diff before render (sandcastle's post-filter); phantom-anchored findings shown in a quarantine group, counted against the reviewer's calibration |
| Two lease systems drift (runner leases vs WORK-R8 task leases) | one convention: both are flock files under `~/.personalclaw/locks/` with the same record shape; runner leases are documented as an R8 application, and the Work board renders both through one component |
| #47 parity break on the new provider type | `PROVIDER_TYPES` + handler land in one commit; `test_manifest_types_match_handlers` is the tripwire |

---

## Success Criteria

1. A code-loop stage declared `sandbox: docker` executes its whole process tree inside a UID-aligned bind-mount container over its WORK-R3 worktree; a write outside `allowed_write_paths` is blocked by the boundary (not merely flagged post-hoc); on a machine without Docker the same run parks needs-input with a typed reason instead of silently running on the host.
2. The destructive-test bug class is structurally closed at a second layer: a subagent spawned with `sandbox: docker` and no grant to `~/.personalclaw/models` CANNOT delete a real local model even if its prompt tells it to (verified by attempting exactly the historical incident inside the sandbox).
3. `apps/lima-sandbox` installed + instance running → a terminal opened "inside the run's sandbox" executes in the VM with correct path translation; stopping the instance flips the provider to greyed-out-with-reason within one probe TTL, and an interactive request gets the path-guard-only dialog.
4. Settings → Agents shows Claude Code, Codex, Gemini CLI, and Kiro as catalog rows with health evidence (last handshake time, version, latency) and per-runner capability chips; a runner uninstalled from PATH flips to unhealthy with the probe error verbatim; an unattended spawn against an unverified adapter is refused when `agents.unattended_requires_verified_adapter` is on.
5. Killing the gateway mid-run with durable sessions on: on reboot the recovery sweep reattaches to the still-alive tmux-backed worker (run resumes as `suspended`→running, journal flags `resumed`), and only genuinely dead sessions are tombstoned — zero work discarded for the alive case.
6. A stalled loop's "second opinion" button produces a brief with a fresh diff, fires a DIFFERENT cataloged runner one-shot inside the same sandbox class, and the result is accepted only when the disk re-diff confirms the claimed edits; the whole exchange is SEL-audited and visible on the cockpit.
7. Inside a docker/lima sandbox, `pclaw-tool memory_recall ...` succeeds through the exec-channel gateway while (a) `ss`/`netstat` inside the sandbox shows zero listening sockets, (b) no credential material exists anywhere in the sandbox filesystem or environment, and (c) a research-profile sandbox is refused write-class tools at the host end.
8. After a chat turn where the agent mangled three files, `/rewind-to-turn N` previews exactly those files with diffs and restores them byte-identical on confirm; `.env` files were never captured; the checkpoint store respects its cap and disappears with the session.
9. A workflow review stage emits line-anchored findings; the cockpit triage panel validates anchors against the real diff, the user accepts 2 of 5, and the accepted pair auto-dispatches to the originating worker session which applies them — rejected findings land in the flywheel's calibration record, and nothing was auto-written without acceptance.
10. The secrets vault lists global + per-project secrets with presence-only values, inherit-from-host rows rendered distinctly, and consumer links; a secret granted to sandboxed runs reaches a `docker` leaf's env while an ungranted sibling does not; no secret value is readable back through any API, and project export ZIPs contain presence flags only.

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

> 🔴 **SUPERSEDED IN MECHANISM (2026-08-04) — do not execute `EI-A1`/`EI-A2`/`EI-A3` as written.**
> The *intent* below (ceiling everything, audit the bypass) stands and is unchanged. The
> **delivery mechanism is unsafe**, and its stated justification is factually wrong: `EI-A1`
> claims "`preexec_fn` is fork-safe here because every seam spawns from the
> single-threaded-at-fork asyncio path." Verified 2026-08-04 — core has **67 thread-creation
> sites**; `apps/backend_runtime.py:288`'s watchdog thread **respawns app backends**, and
> `action_providers/bash_provider.py:216` spawns on the **event loop thread**. A `preexec_fn`
> forces CPython off `posix_spawn` onto a `fork()` of the whole multi-threaded gateway and runs
> bytecode in the child pre-`exec`; a lock held by another thread at fork time cannot be released
> there, so the child can wedge before `exec` — and `Popen._execute_child` then blocks in an
> **unbounded, un-awaitable `os.read` on the event loop thread** (hanging the gateway) while the
> wedged child holds duplicates of every inherited fd, `gateway.lock` and the listening socket
> included. Executing these rows would *introduce* that hazard; we have zero `preexec_fn` uses
> today. **Corrected mechanism (post-`exec` shim + a four-profile split + a `preexec_fn` AST
> tripwire), rewritten task rows, and the evidence live in
> [SIBLING-HARDENING-KIROCREW](SIBLING-HARDENING-KIROCREW.md) §1 (`SH1.1`-`SH1.5`).** Execute
> those instead; `EI-A1`-`EI-A3` are retained below only as the superseded record.

**What & why.** The sibling "ceiling-everything posture": every agent-influenced child process gets OS resource ceilings, plus a **spawn-audit test** that makes bypass a CI failure. Recon: `sandbox.py` is a path-visibility seatbelt with **zero resource-limit machinery** (no `resource`/`setrlimit`/`preexec_fn` anywhere), and the agent-influenced spawn seams are concrete and enumerable: native bash tool (`agents/native/builtin_tools.py:1456` — already funnels through `wrap_argv`), app backends (`apps/backend_runtime.py:134`), ACP CLIs (`acp/transport.py:347`), MCP stdio servers (`mcp_client.py:301-321` via `StdioServerParameters` + `mcp_discovery.py:577`), cron scripts (`schedule_script.py:270`), loop verify/worktree (`loop/gates.py:48`, `loop/worktree.py:72`), and the bash action provider (`action_providers/bash_provider.py:133`). This slots UNDER §1: `SandboxProvider.kind=none` currently means "seatbelt only" — ceilings give `none` a real floor, and `SandboxSpec` gains the limits field that `docker`/`lima` translate to their native knobs.

**Design (contract level).**
- New `sandbox_providers/ceilings.py`: `ResourceCeilings{nofile: int = 1024, max_pids: int | None, max_rss_bytes: int | None}` + `ceiling_kwargs(ceilings) -> dict` returning `{"preexec_fn": ...}` (POSIX: `resource.setrlimit(RLIMIT_NOFILE, ...)` in the child; `preexec_fn` is fork-safe here because every seam spawns from the single-threaded-at-fork asyncio path — documented per-seam) and, where a raw argv is the seam, `wrap_ceilings(argv) -> argv`. `SandboxSpec` gains `ceilings: ResourceCeilings` (§1.1); the `none` provider applies them via preexec_fn, `docker` maps to `--pids-limit/--memory`, `lima` applies guest-side.
- **cgroup v2 tier (Linux, opt-in `sandbox.cgroup_scopes: bool`):** wrap the child in a `systemd-run --user --scope -p TasksMax=<pids> -p MemoryMax=<rss>` transient scope (pids.max = fork-bomb ceiling, memory.max = RSS ceiling). Availability probed once (cgroup v2 unified hierarchy + systemd user session); unavailable (macOS, non-systemd, containers) → **loud one-time doctor/log warning** naming what is NOT enforced, RLIMIT_NOFILE still applies everywhere. macOS gets no pids/RSS ceiling — stated honestly, not simulated.
- **Spawn-audit test** `tests/test_spawn_ceiling_audit.py`: AST-walks `src/` for every `create_subprocess_exec/_shell`, `subprocess.Popen/run`, and `StdioServerParameters` construction; each hit must be in an explicit allowlist tagged either `ceiling-wrapped` (call site passes `preexec_fn=`/`wrap_ceilings`/goes through a SandboxHandle) or `operator-exempt` (gateway self-management: `service/*`, `frontend.py`, `cli_server.py`, `dashboard/handlers/updates.py` — user-initiated, not agent-influenced). A NEW spawn site not in the map fails CI with the file:line — the same honesty-ratchet shape as `test_resilience_degraded_lint.py`.

**Lands in:** Session 1 (ceilings module + `SandboxSpec.ceilings` + `none` provider + the native-bash/action-provider/subagent seams + the audit test) and Session 2 (docker/lima mapping + cgroup tier); remaining seams (apps backend, MCP, ACP, cron script, loop) adopt in Session 3. Count 7 → **8 sessions** (the extra scope is roughly one honest session spread over 1–3; recorded as 8).

| ID | Task | Files | Done when |
|---|---|---|---|
| EI-A1 | `ResourceCeilings` + `ceiling_kwargs`/`wrap_ceilings`; `SandboxSpec.ceilings`; apply in the `none` provider + native bash + bash action provider + subagent spawn; config knobs (`sandbox.nofile`, `sandbox.max_pids`, `sandbox.max_rss_mb`) 4-point wired | `sandbox_providers/ceilings.py`, `sandbox_providers/base.py`, `agents/native/builtin_tools.py`, `action_providers/bash_provider.py`, `subagent.py`, `config/loader.py` | a child running `ulimit -n` reports the ceiling; config round-trips; existing behavior unchanged when ceilings are defaults |
| EI-A2 | cgroup v2 transient-scope tier (Linux opt-in): systemd-run scope with TasksMax/MemoryMax; availability probe; loud warning where unavailable (macOS/doctor line) | `sandbox_providers/ceilings.py`, `resilience/doctor.py` probe line, docs | Linux fixture: fork-bomb child hits pids.max and dies contained; macOS: one-time warning states pids/RSS not enforced; probe never raises |
| EI-A3 | Spawn-audit CI test: AST enumeration of every spawn site vs the ceiling-wrapped/operator-exempt map; remaining agent-influenced seams (apps backend, mcp_client/mcp_discovery, acp/transport, schedule_script, loop gates/worktree) wrapped to satisfy it | `tests/test_spawn_ceiling_audit.py`, the six seam files | audit green with every agent-influenced seam wrapped; adding an unmapped `Popen` on a branch fails CI naming the site |

---

## Amendment (2026-07-29 — owner-approved: close the confinement gap, and stop overstating it)

**Why this amendment exists.** A capability audit (2026-07-28) found that PersonalClaw's sandbox is, precisely stated, a **credential-hiding sandbox, not a confinement sandbox** — and that the public website's boundary diagram implies more than exists. This plan already designs the confinement seam (§1's `SandboxProvider` registry, `docker`/`lima` tiers, `allowed_write_paths`, egress tiers). What it does **not** cover is (a) three app-side compounders that let an installed app bypass the sandbox entirely, and (b) the documentation correction, which should not wait for the engineering.

**What the sandbox actually is today (verified — cite these, don't re-derive).** `sandbox.py` (669 LOC) auto-detects three modes with real OS mechanisms: Linux `unshare(CLONE_NEWUSER)` + a separate `unshare(CLONE_NEWNS)` + bind-mounting empty dirs over sensitive paths; macOS Seatbelt via `sandbox-exec`; and `"none"`, logged once as "No OS-level sandbox available — app-level checks only." Env scrubbing (`_SENSITIVE_ENV_PREFIXES`: `AWS_SECRET`, `AWS_SESSION`, `SSH_AUTH_SOCK`, `GNUPGHOME`, `GIT_ASKPASS`) applies in all modes. Six real call sites (the native `bash` tool at `builtin_tools.py:1463`, ACP spawn at `acp/transport.py:317`, `action_providers/bash_provider.py:129`, `schedule_script.py:266`, `sdk/tts.py`, and the `sdk.util.sandbox_wrap_argv` re-export).

**The honest characterization, which the docs must match:** the macOS Seatbelt profile is `(version 1)\n(allow default)\n{deny_rules}` — **allow-by-default** with targeted denies on `~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`, `~/.docker`, `~/.kube`, `.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, `.personalclaw/.env`, plus `~/.ssh` in `strict`. The Linux path is equivalent (bind-mount empty dirs over credential paths). **There is no network restriction, no process restriction, no write confinement outside `~/.ssh`, and no filesystem jail.** It raises the cost of credential theft; it does not stop an agent from doing anything else. §1's `docker`/`lima` tiers are what turn this into real confinement — which is exactly why this plan matters and why the docs must not claim its outcome in advance.

### (a) The three app-side compounders — a `docker` tier does not fix these

These sit **outside** the sandbox seam entirely: they are how an installed app's *backend* runs, not how an agent's shell command runs. A confinement tier for agent commands leaves all three untouched, so they need naming as in-scope work rather than being assumed covered.

1. **App backends inherit the full gateway environment.** `apps/backend_runtime.py:125` — `env = dict(os.environ)`, then `PORT`/`PERSONALCLAW_APP_NAME`/`PERSONALCLAW_APP_DATA_DIR` are layered on. So **every secret present in the gateway process environment is visible to every app backend**, including apps that declared no credential access. Note the near-miss: the code carefully withholds `PERSONALCLAW_APP_DATA_DIR` when `storage` isn't declared (`backend_runtime.py:127-131`) — the permission discipline is there, but the environment it starts from is unfiltered. **Fix:** build the child environment by **allowlist**, not by copy — a minimal base (`PATH`, locale, `HOME`-equivalent, the three PersonalClaw vars) plus only what the app declared. The same `_SENSITIVE_ENV_PREFIXES` scrubbing `sandbox.py` already performs is the obvious floor, but an allowlist is the correct shape here because an app backend is long-lived and network-capable.
2. **App Python dependencies install into the shared core venv.** `apps/app_manager.py:154` documents it in its own docstring: "Pip-install an app's declared `pythonDependencies` into the shared core [venv]". So an app can **shadow a core dependency** for the whole gateway. **Fix:** a per-app target (venv or `--target` dir) with the app's backend launched against it. This is a real packaging change and may warrant its own session; the amendment's requirement is that it stop being invisible.
3. **`permissions.network` is declaration-only, and the code says so.** `apps/permissions.py` states: "a `network:false` app can still reach the internet through its own subprocess… treat `network: true` as an honest declaration, not a security boundary." That honesty is correct and should stay — but it means the Store's install-consent surface shows a permission that isn't enforced. **Fix (choose one, deliberately):** either enforce it for app backends via the same egress rail core uses (`net/` guard + policy) — which is coherent because the gateway launches the process — or keep it advisory and **mark it as advisory in the consent UI**, so the user isn't misled. Silent non-enforcement of a displayed permission is the one option that is not acceptable.

Related, already documented and NOT re-litigated here: `setup.onInstall` runs arbitrary shell post-scan (RCE-by-design, gated by the static scanner). The scanner's non-overridable DANGEROUS floor is a genuine strength and stays; it is simply not a sandbox, and `supply_chain.py` says so itself ("static inspection only").

### (b) The documentation correction — do this NOW, independent of the engineering

The website's "Bounded capabilities / untrusted zone" boundary diagram implies confinement that does not exist. `docs/architecture/security.md` is notably accurate (it names its own limitations, including the scanner's "not a sandbox" and the ACP/YOLO prompt-framing tradeoff) — the public projection is what drifted. **This is a docs fix with no engineering dependency and should not wait for §1.** Two further claims found overstated in the same audit, worth correcting in the same pass:
- **"Autonomous work bounded by deterministic guardrails"** is true only for *unattended* work — `guardrails/model_call.py` guards `reasoning|background|loops|orchestration`, and interactive chat is explicitly out of scope by design. Notably the in-product Settings copy is *more* precise than the website here ("Interactive chat is never affected by these"), so the correct wording already exists in the product.
- **"Desktop shell" as a platform** overstates distribution: the Electron app and PyInstaller spec are real, but nothing in `.github/` builds, tests, signs, notarizes, or attaches a DMG (verified: one dependabot comment is the only match). macOS-arm64 only, no auto-update channel.

### Amendment task table (extends this plan; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| D0 | **Docs-only, no dependencies — land first.** Correct the website boundary diagram + copy to describe credential-hiding vs confinement honestly; qualify the guardrails claim to unattended work (reuse the Settings panel's existing precise wording); qualify the desktop-platform claim. Add a short "what the sandbox does and does not do" section to `docs/architecture/security.md` if not already explicit | `personalclaw.dev` content, `docs/architecture/security.md` | no public surface claims confinement the code does not provide; the guardrails claim names its scope; the desktop claim matches CI reality |
| D1 | App-backend environment allowlist: construct the child env from a minimal base + declared needs instead of `dict(os.environ)`; apply the existing sensitive-prefix scrubbing as a floor; a regression test asserts a planted secret in the gateway env does NOT reach an app backend | `src/personalclaw/apps/backend_runtime.py:125`, tests | a secret in the gateway environment is absent from the backend's environment (test proves it); every shipped first-party app still boots (run the full app-boot path, not just unit tests) |
| D2 | `permissions.network` decision + implementation: EITHER enforce it for app backends through the existing egress rail, OR mark it advisory in the Store consent UI and the manifest reference. **Not both, and not neither** | `apps/permissions.py`, the Store consent surface, `docs/reference/`, tests | the consent UI's claim matches enforcement reality; the manifest reference documents the chosen posture |
| D3 | Per-app Python dependency isolation: install an app's `pythonDependencies` to an app-scoped target and launch its backend against it, so an app cannot shadow a core dependency | `src/personalclaw/apps/app_manager.py:154`, `backend_runtime.py`, tests | an app declaring a conflicting version of a core dependency does not affect the gateway (test with a real conflicting pin); existing installed apps continue to work |
| VD | Validation as a user: install a first-party app; confirm from its own process environment that gateway secrets are absent; confirm a network-declaring and a non-declaring app behave per the D2 decision; install an app pinning a conflicting dependency and confirm the gateway is unaffected; re-read the corrected website copy against `sandbox.py` and confirm every claim is true | — | holds |

**Independently re-derived 2026-08-04** by the KiroCrew comparison, which reached `D1` from the
other direction (the sibling explicitly "strips sensitive environment variables"). `D1`'s shape is
**already correct and better than a scrub** — an allowlist, because an app backend is long-lived
and network-capable — so it needs no redesign. Two additions, both in
[SIBLING-HARDENING-KIROCREW](SIBLING-HARDENING-KIROCREW.md): §2.5 extends the same allowlist shape
to the spawn sites `D1` does not name (`hooks.py`, `schedule_script.py`, and confirming
`bash_provider.py:65`'s `_scrub_env` is the allowlist shape); and §2.1 closes the **other** half of
this trust boundary, which no plan owned — the app-backend port has no *inbound* authentication, so
a local process reaching `127.0.0.1:<port>` directly bypasses the gateway proxy, session auth, and
`app_permission_middleware` entirely. `D1` and §2.1 are complementary: `D1` limits what a backend
*receives*, §2.1 establishes *who may call it*.

### Sequencing note
D0 is documentation and should land immediately — an inaccurate security claim is a live defect, not a backlog item. D1 is small, high-value, and independent. D2 is a decision before it is code. D3 is the largest and can follow §1's provider work. **None of these are blocked by the `docker`/`lima` tiers**, and none of them substitute for that work: the tiers confine *agent commands*, these tasks confine *app backends*, and the product needs both.
