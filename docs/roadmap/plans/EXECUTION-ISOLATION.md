# Plan: Execution Isolation & Runner Substrate — Where and Through What Agents Execute

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
**Created:** 2026-07-13
**Wave:** 2 (engine-adjacent) — consumes WORKFLOWS-V2 Slice 0-1 primitives (`__wf_depth`, run workspace block) and WORK-CONTAINERS §4 (workspace provisioning, leases, keychain secrets); everything unattended in Waves 3+ inherits its isolation floor. The SandboxProvider registry (§1) and runner catalog (§3) have no engine dependency and can front-run.
**Depends on:** WORKFLOWS-V2-WORK-CONTAINERS §4.1/§4.3 (workspace block + WORK-R19 secrets store — this plan builds the pluggable substrate *under* the workspace block and the UX *over* the secrets store). AUTONOMY-GUARDRAILS §3/§4.2 (safety profiles + egress tiers — sandbox providers carry a profile, they don't invent one).
**Scope:** one pluggable execution-isolation seam (SandboxProvider registry, two-kind taxonomy, none/docker built-ins, Lima tier) + one BYO agent-runner substrate (runner catalog, capability matrix, health evidence, second-opinion handoff, durable sessions) + the interactive safety tier (turn-bound file checkpointing, sandbox-internal tool gateway, reviewer-comment triage) + the standalone secrets-vault UX remainder of NEW-27.

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
