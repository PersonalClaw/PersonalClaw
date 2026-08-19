# EXECUTION-ISOLATION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/EI.md`](../atomic/EI.md) as 12 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Execution Isolation & Runner Substrate — Where and Through What Agents Execute

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog). Not started —
verified 2026-08-04: no `SandboxProvider`/`AgentRunner` seam, no `sandbox/` package (only the
pre-existing single-file `sandbox.py`, unchanged since v0.1.0), no lima/docker tier.
⚠️ **The rev-14 amendment's D0 row was marked "land first" and has NOT landed.** It is docs-only and
dependency-free, and the amendment's own words are that "an inaccurate security claim is a live
defect": `docs/architecture/security.md` still lacks the what-the-sandbox-does-and-does-not-do
section, and `personalclaw.dev`'s security page still says "Bounded capabilities" with no
credential-hiding-vs-confinement qualification. The amendment's other rows are DONE: **D1** (app env
inheritance) and **D2** (the declaration-only `network` permission's advisory marking) landed
2026-08-13; **D3** and **VD** landed 2026-08-16, closing atom `EI-12`. D3 shipped **re-scoped** — an
admission refusal (an app may not re-pin a dependency core owns) rather than the app-scoped isolation
the row proposed, because a census found ZERO of 44 apps have both a backend and declared
`pythonDependencies`, so the proposed mechanism had a zero-app population. Isolating the *in-process*
providers, where the real shadowing risk lives, remains the owner-scope seam decision recorded BLOCKED
on 2026-08-13. See the `## Execution log` DEVIATION.

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

## Amendment (2026-07-26 — platform gap analysis, owner greenlight)

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
> [PLATFORM-HARDENING-FLOORS](PLATFORM-HARDENING-FLOORS.md) §1 (`SH1.1`-`SH1.5`).** Execute
> those instead; `EI-A1`-`EI-A3` are retained below only as the superseded record.

**What & why.** A "ceiling-everything posture": every agent-influenced child process gets OS resource ceilings, plus a **spawn-audit test** that makes bypass a CI failure. Recon: `sandbox.py` is a path-visibility seatbelt with **zero resource-limit machinery** (no `resource`/`setrlimit`/`preexec_fn` anywhere), and the agent-influenced spawn seams are concrete and enumerable: native bash tool (`agents/native/builtin_tools.py:1456` — already funnels through `wrap_argv`), app backends (`apps/backend_runtime.py:134`), ACP CLIs (`acp/transport.py:347`), MCP stdio servers (`mcp_client.py:301-321` via `StdioServerParameters` + `mcp_discovery.py:577`), cron scripts (`schedule_script.py:270`), loop verify/worktree (`loop/gates.py:48`, `loop/worktree.py:72`), and the bash action provider (`action_providers/bash_provider.py:133`). This slots UNDER §1: `SandboxProvider.kind=none` currently means "seatbelt only" — ceilings give `none` a real floor, and `SandboxSpec` gains the limits field that `docker`/`lima` translate to their native knobs.

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

### Amendment task table (extends this plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| D0 | **Docs-only, no dependencies — land first.** Correct the website boundary diagram + copy to describe credential-hiding vs confinement honestly; qualify the guardrails claim to unattended work (reuse the Settings panel's existing precise wording); qualify the desktop-platform claim. Add a short "what the sandbox does and does not do" section to `docs/architecture/security.md` if not already explicit | `personalclaw.dev` content, `docs/architecture/security.md` | no public surface claims confinement the code does not provide; the guardrails claim names its scope; the desktop claim matches CI reality |
| D1 | App-backend environment allowlist: construct the child env from a minimal base + declared needs instead of `dict(os.environ)`; apply the existing sensitive-prefix scrubbing as a floor; a regression test asserts a planted secret in the gateway env does NOT reach an app backend | `src/personalclaw/apps/backend_runtime.py:125`, tests | a secret in the gateway environment is absent from the backend's environment (test proves it); every shipped first-party app still boots (run the full app-boot path, not just unit tests) |
| D2 | `permissions.network` decision + implementation: EITHER enforce it for app backends through the existing egress rail, OR mark it advisory in the Store consent UI and the manifest reference. **Not both, and not neither** | `apps/permissions.py`, the Store consent surface, `docs/reference/`, tests | the consent UI's claim matches enforcement reality; the manifest reference documents the chosen posture |
| D3 | Per-app Python dependency isolation: install an app's `pythonDependencies` to an app-scoped target and launch its backend against it, so an app cannot shadow a core dependency | `src/personalclaw/apps/app_manager.py:154`, `backend_runtime.py`, tests | an app declaring a conflicting version of a core dependency does not affect the gateway (test with a real conflicting pin); existing installed apps continue to work |
| VD | Validation as a user: install a first-party app; confirm from its own process environment that gateway secrets are absent; confirm a network-declaring and a non-declaring app behave per the D2 decision; install an app pinning a conflicting dependency and confirm the gateway is unaffected; re-read the corrected website copy against `sandbox.py` and confirm every claim is true | — | holds |

**Independently re-derived 2026-08-04** by a design comparison, which reached `D1` from the
other direction (that design explicitly "strips sensitive environment variables"). `D1`'s shape is
**already correct and better than a scrub** — an allowlist, because an app backend is long-lived
and network-capable — so it needs no redesign. Two additions, both in
[PLATFORM-HARDENING-FLOORS](PLATFORM-HARDENING-FLOORS.md): §2.5 extends the same allowlist shape
to the spawn sites `D1` does not name (`hooks.py`, `schedule_script.py`, and confirming
`bash_provider.py:65`'s `_scrub_env` is the allowlist shape); and §2.1 closes the **other** half of
this trust boundary, which no plan owned — the app-backend port has no *inbound* authentication, so
a local process reaching `127.0.0.1:<port>` directly bypasses the gateway proxy, session auth, and
`app_permission_middleware` entirely. `D1` and §2.1 are complementary: `D1` limits what a backend
*receives*, §2.1 establishes *who may call it*.

### Sequencing note
D0 is documentation and should land immediately — an inaccurate security claim is a live defect, not a backlog item. D1 is small, high-value, and independent. D2 is a decision before it is code. D3 is the largest and can follow §1's provider work. **None of these are blocked by the `docker`/`lima` tiers**, and none of them substitute for that work: the tiers confine *agent commands*, these tasks confine *app backends*, and the product needs both.

---

## Execution log

- [2026-08-13][EI-3] DONE. **Premise correction: 6 of the 7 seams this atom names, and the audit
  rail itself, had already shipped** — PHF-1's SH1.3/SH1.3a built
  `tests/test_spawn_ceiling_audit.py` and wrapped `apps/backend_runtime.py`, `mcp_client.py`,
  `mcp_discovery.py`, `loop/gates.py`, `loop/worktree.py`, and (via EI-1's provider handle,
  which subsumes the former `AcpProcess.spawn` site) `acp/transport.py`. The rail classifies
  **130** spawn sites across `_CEILING_WRAPPED`/`_OPERATOR_EXEMPT` and enforces coverage in both
  directions, disjointness, and a required-wrapped ratchet. Its done-when was **falsified before
  being trusted**: appending an unmapped `subprocess.Popen` to `lexicon/store.py` reds the rail
  naming the exact site — `lexicon/store.py::_phf_probe_unmapped_spawn::subprocess.Popen
  (lexicon/store.py:344)`. So the atom's CI requirement was already satisfied and was NOT
  re-implemented.
- [2026-08-13][EI-3] The one real gap was `schedule_script.run_script_sandboxed`. It had the OS
  path sandbox (`wrap_argv(argv, mode="standard")`) and, since PHF-4, the allowlisted child env —
  and its exemption reason conflated those with a ceiling ("operator: scheduled-script runner (own
  sandbox wrap + clean env)"). Neither bounds consumption, so a cron script could exhaust
  descriptors or fork-bomb where an agent `bash` call could not. Closed: the `tool` profile now
  arrives via `spawn_shim_argv` (the sync-usable prepend; no sync site used it before, so cron is
  the first), and the entry **moved from `_OPERATOR_EXEMPT` to `_CEILING_WRAPPED`** and was
  ratcheted into `test_agent_influenced_seams_are_all_ceiling_wrapped` so the exemption cannot
  return. The exemption was NOT made merely honest — the seam is genuinely agent-influenced (an
  agent authors the file under `crons/` and the job that selects it).
- [2026-08-13][EI-3] **Composition order: the shim goes OUTSIDE the `wrap_argv` sandbox** —
  `python -m personalclaw._spawn_exec_shim <policy> -- env -u … sandbox-exec -f <profile> python3
  <launcher>`. Three reasons: (1) it matches the one routed-spawn seam,
  `sandbox_providers/none.py`, where `wrap()` builds the sandbox argv and `exec()` prepends the
  ceiling — so cron and ACP compose identically; (2) rlimits inherit through `exec`, so a limit
  set before `sandbox-exec`/`unshare` covers the sandboxed target and every descendant; (3) inside
  the wrap, the shim's own `python -m` import would have to survive the seatbelt/namespace
  profile, and one denied read of the interpreter's path turns a ceiling into a dead cron job.
  Ordering hazard recorded: `wrap_argv` returns its namespace-backend cleanup path as
  `wrapped[1]`, so the prepend must follow that unpacking, never precede it.
- [2026-08-13][EI-3] PHF-4's `PYTHONPATH` allowlist entry still carries the shim through this
  path — confirmed, and it is load-bearing here: the cron child's env is `build_child_env`'s
  allowlist, not a copy, so the shim's `personalclaw._spawn_exec_shim` import resolves only
  because `PYTHONPATH` (and `PATH`, for the `env`/`sandbox-exec` hop) are in
  `CHILD_ENV_BASE_NAMES`. The failure mode is loud, not silent: a shim that could not import
  returns a non-zero child with no sentinel line, which surfaces as an `error` status. And the
  site cannot degrade to an unshimmed spawn under any config — the `tool` policy is never empty
  because it always carries the OOM bias.
- [2026-08-13][EI-3] **Containment DRIVEN, not asserted from a constructed object**
  (`tests/test_cron_script_ceiling.py`, 6 tests). A real cron script under `sandbox.nofile = 137`
  reports **137** from its own `resource.getrlimit` (host default: 1048576), and a script asking
  for **400** descriptors is stopped at **134** with `OSError` (134, not 137, because the child
  already holds its three standard streams and the launcher's own handles). The falsification leg:
  with `sandbox.nofile = 0` (cap off, same script, same spawn path, same shim) it opens all
  **400** — so the containment is attributable to the ceiling and not to the host or the OS
  sandbox. Falsifying the implementation reds all three drives with the right diagnosis (NOFILE
  1048576, 400/400 opened, no shim in argv). The same drive was run outside pytest against an
  isolated `PERSONALCLAW_HOME` under `/var/folders/.../ei3-live-*` (never the real home) and
  reproduced `134|OSError` / `400|`.
- [2026-08-13][EI-3] **Platform honesty — what is NOT enforced here.** On macOS only
  **RLIMIT_NOFILE** applies. `max_pids` (RLIMIT_NPROC) and `max_rss_mb` (RLIMIT_AS) ship **OFF**
  by default (NPROC is a *per-user* cap that would break a busy host), and `oom_score_adj` is
  Linux-only and silently skipped — so the fork-bomb and memory bounds this atom's sibling
  language implies belong to PHF-2's Linux cgroup tier, not to an rlimit. Separately, this host is
  macOS 26, where `_probe_sandbox_exec` refuses `sandbox-exec` for third-party callers, so
  `detect_backend` returns `none` and both nesting orders produce the same argv locally. Rather
  than leave the ordering unexercised, it is driven through a surrogate wrapper of the same shape
  the real macOS wrap has (`env -u NAME …`) and the ceiling still lands on the far side of it,
  plus an argv-shape assertion at the real call site.
- [2026-08-13][EI-3] Gate: `make lint` rc=0 (black/isort/flake8/mypy, 810 source files). Full suite
  **18,947 passed / 30 skipped / 12 xfailed / 0 failed** in 169s — the branch baseline of 18,941
  plus exactly the 6 new tests, no unexplained movement — with the CRE-8 real-home rail confirming
  `~/.personalclaw` unchanged (no global `PERSONALCLAW_HOME` for the pytest run; the live drive used
  a throwaway home under `/var/folders`). All four generators re-run with `PYTHONPATH` set and
  **byte-identical**: `config-baseline.json`, `docs-lint-baseline.json`,
  `inert-surface-baseline.json` and the offline agent reference. Correct — this atom adds no config
  field, no provider type, and no inert surface (the ceiling is delivered on a live call path, not
  declared). `tools/regen_dag_derived.py` re-derived the `dag` block for the status flip
  (`plan_counts` EI done 2→3, todo 10→9; EI-3 out of the ready frontier). No `web/` change — this
  seam has no frontend surface.
- [2026-08-13][EI-12 D1] DONE (task D1 only — **`EI-12` stays `todo`**). `apps/backend_runtime.py`
  now builds the backend's child environment with
  `sandbox.build_child_env(site="app-backend", extra={…})` instead of `dict(os.environ)`. PHF-4 had
  converted the hook, cron-script and bash-action sites and deliberately left this one to D1, which
  made the app backend the **widest remaining inheritance in the tree** and the least deserving of
  it: third-party code, scanned but not trusted at install, running for as long as the app is
  enabled, while `config/loader.py` seeds `.env` credentials into `os.environ` so "trusted children"
  inherit them. The four computed variables move into `extra` unchanged (`PORT`,
  `PERSONALCLAW_APP_NAME`, `PERSONALCLAW_APP_SECRET`, and `PERSONALCLAW_APP_DATA_DIR` when the
  `storage` capability is held). `spawn_shim_argv`/`PROFILE_TOOL` composition (PHF-1) was not
  touched; its duplicate local import folded into the one this change already needed.
- [2026-08-13][EI-12 D1] **MEASURED, by falsification before trusting the test.** Reverting the one
  line to `dict(os.environ)` and re-running the new suite reds 3 of its 5 tests and enumerates what
  the backend used to receive: **~130 undeclared variables**, including `SSH_AUTH_SOCK`,
  `AWS_REGION`, `AWS_SDK_UA_APP_ID`, `GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_EMAIL`, `VIRTUAL_ENV`, both
  planted secrets and the whole agent-CLI/toolbox variable population. The 2 tests that legitimately
  stay green under the revert are the sandbox-P3 storage-gate pair — they guard an orthogonal
  control, which is why they do not move.
- [2026-08-13][EI-12 D1] **Premise correction on the blast radius — it is narrower than the atom's
  wording implies, and the correction is load-bearing for the CHANGELOG.** The 9 credential names
  that are absent from `CHILD_ENV_BASE_NAMES` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`, `META_MODEL_API_KEY`, `ALIBABA_API_KEY`, `TAVILY_API_KEY`, `SLACK_BOT_TOKEN`,
  `SLACK_APP_TOKEN`, `SKILLS_SH_API_KEY`) are read **exclusively in `provider.py` modules**, which
  the gateway imports **in-process** — verified by grepping every `*.py` in `PersonalClawApps` for
  those names: every non-test hit is a `provider.py`. Of **44** first-party apps, exactly **2**
  declare a `backend.entryPoint` (`growth`, `minutes`), and the only environment reads in either
  backend tree are `PERSONALCLAW_APP_DATA_DIR` and `PORT` — both already computed at this call site.
  So **no first-party app changes behaviour**, and an operator's exported `ANTHROPIC_API_KEY` still
  reaches the anthropic-models provider, because that code never travelled through the app-backend
  env. The real user-visible break is scoped to a **third-party** app backend that read an ambient
  gateway variable; its two migrations are the credential store (the documented primary path — the
  env var is a *fallback*, `entry.credential` → `credential_store` raising `CredentialMissing` is
  primary) or an operator declaration in `sandbox.env_passthrough`.
- [2026-08-13][EI-12 D1] **`PERSONALCLAW_CC_ISOLATE` is not an app-backend variable and needed no
  home.** It is read in the sibling apps repo, in `claude-code-agent`'s `provider.py` (line 196 at
  the time of writing — a cross-repo path, deliberately not cited in `file.py:NNN` form because the
  docs linter can only resolve in-repo paths); that app's `app.json`
  declares **no** `backend`, so the read happens in the gateway process against the full
  `os.environ`. Adding it to the base or to `extra` would have widened the allowlist for a reader
  that never crossed it. No change.
- [2026-08-13][EI-12 D1] **Live validation (isolated home, real first-party apps).** Booted with
  `PERSONALCLAW_HOME=/tmp/ei12-validate-*` (never `~/.personalclaw`) and the worktree's core on
  `PYTHONPATH`, with `ANTHROPIC_API_KEY`/`TAVILY_API_KEY`/`ACME_DEPLOY_PAT` planted in the gateway
  env: `growth` and `minutes` both installed and returned `{"ok": true}` from `/health` (pids
  72797/72855, then 73059/73121). Their child environments were then read **from the OS process
  table** (`ps -Eww -p <pid>`), not from a dict we built: all four of
  `ANTHROPIC_API_KEY`/`TAVILY_API_KEY`/`ACME_DEPLOY_PAT`/`SSH_AUTH_SOCK` absent, no planted value
  present, `PORT=` and `PERSONALCLAW_APP_NAME=<app>` present as the non-vacuity check. The remaining
  42 first-party apps declare no backend, so this seam cannot affect their boot; the gateway process
  still saw both planted keys, confirming the in-process provider path is untouched.
- [2026-08-13][EI-12 D1] **The P3 storage gate is now enforced after the build, on purpose.**
  `PERSONALCLAW_APP_DATA_DIR` is not in `CHILD_ENV_BASE_NAMES`, so it cannot be inherited — but
  `sandbox.env_passthrough` accepts any non-credential-shaped name, so declaring it (with the
  gateway itself carrying the variable) would have handed **every** storage-less backend a data dir
  at once and silently undone sandbox P3. The conditional `env.pop` is kept for exactly that path
  and is driven by a test at the real call site, not against the builder.
- [2026-08-13][EI-12 D1] **DISCOVERY (recorded, not built — out of D1's scope).**
  `_declared_env_passthrough(site)` takes a `site` argument but uses it **only for logging**; the
  declared list is the single global `sandbox.env_passthrough`. So an operator who declares one name
  to unblock one app backend also exposes it to cron scripts and bash actions. That is a real
  granularity gap now that a third site consumes the seam, but building a per-site scheme is a
  config-surface change (new field, round-trip contract, frontend control) that belongs to a plan
  that owns the sandbox config surface — not to a call-site conversion. Not started here.
- [2026-08-13][EI-12 D1] **DISCOVERY.** `BackendConfig` (`apps/manifest.py:259`) has no `env` field
  and this change does not add one: an app-declared environment name would be an exfiltration
  channel (the same reason `sandbox.env_passthrough` is deliberately unreachable from a manifest or
  a trigger payload). Recorded because "let the app declare what it needs" is the obvious wrong fix
  for the break above.
- [2026-08-13][EI-12 D1] **A measured, not hardcoded, exclusion in the closed-set test.** The
  strictest new assertion — the child's env is *exactly* `CHILD_ENV_BASE_NAMES` + the four computed
  names — first red on `__CF_USER_TEXT_ENCODING`. Probing a child spawned with a literally empty
  `env={}` shows Darwin's CoreFoundation and the interpreter's own UTF-8 coercion inject
  `__CF_USER_TEXT_ENCODING` and `LC_CTYPE` **after** exec, so neither was inherited. The exclusion is
  therefore computed at run time by that same empty-env probe rather than hardcoding two macOS
  names, and the test additionally asserts the exclusion set cannot contain the planted secrets — so
  it stays exact on a platform with a different injected set instead of widening to whatever the OS
  adds. The floor was raised, not relaxed.
- [2026-08-13][EI-12 D1] **STOP POINT. `EI-12` remains `todo`; no `pr` field set.** D1 is the only
  part landed. Outstanding, untouched: **D2** (`permissions.network` — enforce via the egress rail
  OR mark advisory in the consent UI/manifest, not both/neither), **D3** (per-app
  `pythonDependencies` isolated to an app-scoped target; confirmed nothing under `apps/` manipulates
  `PYTHONPATH` today, so the isolation does not exist yet), and **VD** (the validation-as-a-user
  sweep). The atom's done-when is correspondingly part-met: "a planted secret in the gateway env is
  absent from an app backend's env (test proves it)" ✅ and "every first-party app still boots" ✅
  (44 audited, 2 backends booted healthy); the Store-consent network claim and the per-app dependency
  clauses are still open.
- [2026-08-13][EI-12 D2] DONE (task D2 only — **`EI-12` stays `todo`**). Decision: **mark advisory,
  honestly** — not enforce. `PermissionList` (`web/src/pages/apps/AppsSection.tsx`, the ONE component
  behind both consent surfaces: the Store pre-install panel and the installed-app detail panel) no
  longer lists `network` among the enforced permission bullets. It renders its own advisory row —
  "Network access: declared / not declared — advisory only. PersonalClaw does not confine an app's
  outbound traffic: this app's code can reach the network either way. The declaration is disclosure,
  not containment." — **whether or not the app declares it**. Heading changed to "Permissions the
  gateway enforces" so the bullets make a claim the platform can keep, and the empty case now reads
  "None — this app is granted no gateway capability." instead of "No special permissions" (which
  claimed an app had no special powers while it could still reach any host on the internet).
  `apps/permissions.py`'s `can_use_network` docstring, `docs/security/limitations.md` §2 (which quotes
  it verbatim) and `docs/architecture/app-platform.md`'s permission table now describe that surface
  instead of asserting a generic "surfaced honestly". `tests/test_app_permissions.py` pins the wire
  leg the advisory reads; `web/src/pages/apps/permissionConsent.test.tsx` pins the three renderings.
- [2026-08-13][EI-12 D2] **MEASURED before-state, by falsification before the fix.** Exported
  `PermissionList`, wrote the ratchet against the honest form, ran it against the UNCHANGED component:
  3 failed, and their output is the record of what the Store actually rendered. (1) A declaring app
  (`network: true`) got a bullet matching `/network/i` *inside* the "Permissions" list — "• Network
  access" — sat beside `Storage` / `Scheduled jobs` / `Run background agents`, all of which ARE
  enforced server-side, so it read as a grant the gateway polices. (2) A non-declaring app rendered
  `"Permissions• API: /api/tasks"` — **zero** network mention, so the silence read as "blocked". (3) An
  app declaring nothing rendered `"PermissionsNo special permissions"`. So the docstring's claim that
  "the Store can surface it (install consent lists 'network access: yes/no')" was half-true at best:
  the Store listed `yes` as if enforced and never said `no` at all.
- [2026-08-13][EI-12 D2] **Why DISCLOSE and not ENFORCE — the option the atom asks us to close
  deliberately.** Enforcement is out of reach at this seam, not merely expensive: an app's provider
  code is imported **in-process** by the gateway (`providers/loader.py` `importlib.import_module`), so
  an app's own `httpx`/`requests` call IS the gateway's egress and there is no per-app chokepoint to
  gate it at; and an app with a backend owns a separate OS process with its own network stack. Either
  would need an OS-level isolation layer (cgroups/nftables/seccomp) or routing all app egress through
  a guarded seam — the `backend.sandbox` work this plan's §1/task-#71 already owns, not a copy change.
  Half-enforcing was explicitly rejected: only **2 of 44** first-party apps have a backend, so an
  egress rail on backends alone would confine 2 apps, leave 42 unconfined, and show all 44 identically
  — a worse claim than the honest advisory.
- [2026-08-13][EI-12 D2] **The sharpest case, and it inverts the intuition.** Of the four first-party
  apps whose manifests mention `network`, only `mail-inbox` and `openrouter-models` declare `true`;
  `growth` and `minutes` declare **`network: false`** — and per D1's audit those two are the *only*
  first-party apps with a backend. So the two apps that actually run their own OS process with a
  completely unconfined network stack were exactly the two the consent UI rendered with no network row
  at all, i.e. as if the platform had honoured their "we don't use the network" declaration. That is
  the reading the always-rendered advisory kills. Verified through the real parse path
  (`AppManifest.from_dict(...).permissions.to_dict()`) over all 44 manifests, not by reading JSON.
- [2026-08-13][EI-12 D2] **DISCOVERY (recorded, not built).** The Store panel still gates the whole
  section on `Object.keys(item.permissions).length > 0`, and that guard is load-bearing: `CatalogEntry.
  permissions` defaults to `{}` both for an app that declares nothing AND for a registry-index pointer
  whose manifest has not been fetched (`catalog.py` `_pointer_to_entry` vs the git/dir scan, which sets
  `pointer` *and* real permissions — so `pointer` is not the discriminator either). Dropping the guard
  would make the UI tell a user "None — this app is granted no gateway capability" about an app whose
  manifest we have never read. Distinguishing the two needs a backend wire field ("manifest known"),
  which is outside D2's clause. Consequence, stated: a Store card for an app that declares *nothing*
  shows no permission section and therefore no network advisory — the UI makes no claim rather than a
  false one, and the installed-app panel (which always renders) does disclose it.
- [2026-08-13][EI-12 D2] **DISCOVERY (out of scope, unrelated to network).** `AppPermissionsWire`
  (`web/src/lib/api.ts`) has no `appMessaging` field, so the brokered app-to-app targets an app
  declares are never shown at install consent — even though `Permissions.appMessaging`'s own docstring
  calls itself "the install-consent surface for who an app can talk to, shown in the Store via
  `to_dict`". Same class of defect as this task, different permission; needs its own atom.
- [2026-08-13][EI-12 D3] **BLOCKED — owner-scope architecture decision, deliberately not attempted.**
  Per-app `pythonDependencies` are pip-installed into the **shared core venv**; `apps/manifest.py:475`
  says so outright, and nothing under `apps/` manipulates `PYTHONPATH` (confirmed in D1). Because
  provider code is imported in-process, isolating them would require either out-of-process providers or
  per-import path machinery — i.e. a change to how the platform loads app code, not a change to the
  installer. That is an E-class owner decision (it redefines the provider seam every app depends on),
  so it is recorded here rather than improvised. The next session should not re-derive it.
- [2026-08-13][EI-12 D2] **STOP POINT (supersedes the D1 stop point). `EI-12` remains `todo`; no `pr`
  field set.** Landed: **D1** (backend child env by allowlist) and **D2** (the `network` claim marked
  advisory in the consent UI + manifest docs). Outstanding: **D3** (per-app dependency isolation —
  BLOCKED above, owner decision) and **VD** (the validation-as-a-user sweep: install a first-party app,
  confirm gateway secrets are absent from its process env, confirm a network-declaring and a
  non-declaring app behave per the D2 decision *in a live browser*, install a dependency-conflicting app,
  re-read the website copy against `sandbox.py`). D2's own surface was driven by unit-level render tests
  over the real component, not a browser — the browser leg belongs to VD. Atom done-when after D2: "a
  planted secret … is absent from an app backend's env" ✅, "every first-party app still boots" ✅, "the
  Store consent UI's network claim matches enforcement reality" ✅; the per-app dependency clause and the
  VD sweep remain open.
- [2026-08-16][EI-12 D3] **DEVIATION — shipped as an admission refusal, not app-scoped isolation, and
  the census is why.** D3's row says "install an app's `pythonDependencies` to an app-scoped target and
  launch **its backend** against it". Censused all 44 first-party manifests through the real parse path
  before designing: **20 declare `pythonDependencies`, 2 declare a backend, and the two sets are
  disjoint** — `growth` and `minutes` (the only backend-having apps) declare zero deps, and all 20
  dep-declaring apps are in-process providers with no backend. So the prescribed mechanism has a
  **zero-app population**: a backend-scoped `PYTHONPATH` would have isolated nothing while the actual
  shadowing path — pip resolving into the shared venv the gateway imports from — stayed open. Building it
  would have satisfied the row and shipped an inert control. What shipped instead attacks the property
  the atom asks for at the only chokepoint that exists without redefining the provider seam:
  `app_manager._reject_core_dependency_conflicts` refuses, **before pip is spawned**, any declared
  requirement whose canonical name is a **core-declared** dependency unless the currently-installed
  version already satisfies it — so pip is never in a position to move a core dependency. That rule is
  exactly "the install moves nothing of the gateway's", and it is deliberately stricter than "stay inside
  core's declared range": a pin *above* the installed version still sits under core's ceiling but would
  move numpy under a live process, so it is refused too. The out-of-process-provider isolation half
  remains the owner-scope seam decision recorded BLOCKED on 2026-08-13; it was NOT re-litigated here.
- [2026-08-16][EI-12 D3] **Fail-closed, with the blast radius measured rather than assumed.** Two
  can't-prove-it cases deny instead of installing: an unparseable requirement specifier (and
  `AppManifest.validate()` does **not** vet these, so garbage genuinely reaches the installer), and a
  core-owned name whose installed version cannot be read (`pysqlite3-binary` is core-declared but
  linux/x86_64-marked, so it is absent elsewhere). Blast radius of the whole guard is the *collision set*
  only: extras are excluded **by `extra ==` marker**, not by a name list, and that exclusion is
  load-bearing — `openai`, `anthropic`, `boto3`, `slack-sdk`, `faster-whisper`, `sentence-transformers`,
  `piper-tts`, `huggingface-hub`, `faiss-cpu` are ALL extras in core's metadata, and 19 of the 20
  dep-declaring apps pin exactly those. Falsified: deleting the marker filter flips core from 22 to 47
  requirements, makes `openai` "core", and reds
  `test_extras_are_not_core_so_provider_apps_stay_installable` with *"openai became a CORE dep — 
  dep-declaring apps now refuse"*. The one real first-party collision is `diarization-onnx`'s
  `numpy>=1.24` vs core's `numpy>=1.21,<3`; installed 1.26.4 satisfies it, so it is ALLOWED — that
  allowed case is the rail's vacuity floor, proving the guard evaluates real input instead of matching
  nothing.
- [2026-08-16][EI-12 D3] **Falsified before trusting the test.** Removing the one guard call from
  `_install_python_deps` reds the conflict test with *"AssertionError: pip was spawned despite a refused
  pin: ['uv', 'pip', 'install', '--python', '…/.venv/bin/python', 'numpy>1.26.4']"* — i.e. the mutant's
  own output is the record of the command the installer would otherwise run against the gateway's own
  venv. D1's filter was independently re-falsified: replacing `build_child_env(site="app-backend")` with
  `dict(os.environ)` reds three tests, headline *"assert 'ACME_CLOUD_API_KEY' not in {…}"*.
- [2026-08-16][EI-12 VD] **The validation-as-a-user sweep — what is real vs test-only.** Isolated home,
  port 10155, gateway booted from the worktree (`CORE FROM:` printed to confirm) with two secrets planted
  in its OWN environment. **Real legs:** installed `minutes` via `POST /api/apps` (ok=true); read the
  backend process env from OUTSIDE with `ps eww` — **zero** hits for either planted name or value, while
  `ps eww` on the gateway PID prints both verbatim, and the same read shows the backend DOES carry
  `PORT`/`PERSONALCLAW_APP_NAME`/`PERSONALCLAW_APP_DATA_DIR`/`HOME`/`PATH` (so the absence is a real
  filter, not a failed read); the app WORKS — `/health` returns `{"ok": true}` and a list → create →
  read-back round trip through the gateway proxy `/apps/minutes/api/meetings` returns the created
  meeting; an app pinning `numpy<1.21` refused over real HTTP with the version-specific reason, `numpy`
  reporting 1.26.4 before and after. **Test-only / code-level:** the D2 consent-UI advisory was verified
  by reading the shipped component and the wire payload (`minutes` sends no `network` key, so the
  always-rendered row reads "not declared" for an app that really does own an unconfined OS process) plus
  D2's existing render tests — **not** driven in a browser this session.
- [2026-08-16][EI-12 VD] **Enforcement reality re-measured BEFORE relying on D2's copy.** App backends
  are wrapped only by `spawn_shim_argv(cmd, PROFILE_TOOL)` (the PHF-1 resource ceiling) and never by
  `sandbox.wrap_argv`; `wrap_argv`'s own seatbelt profile is `(allow default)` plus file-read/write deny
  rules with no `(deny network*)`, the Linux launcher unshares only `CLONE_NEWUSER|CLONE_NEWNS` with
  `CLONE_NEWNET` nowhere in the tree, and the child-env allowlist deliberately passes
  `HTTP_PROXY`/`HTTPS_PROXY` through so children CAN reach the network. So "PersonalClaw does not confine
  an app's outbound traffic" is literally true and needed no copy change — D2's decision holds on
  re-measurement.
- [2026-08-16][EI-12] **DISCOVERY (recorded, not built).** The benign half of the drive is itself the
  residual limitation: installing an app declaring `cowsay>=1.0` really did add `cowsay` to the shared
  venv (via `uv`, invisible to `pip show` — removed afterwards). An app still cannot *move* a core
  dependency, but it can *add* packages importable by the whole process, and pip may still move a
  transitive dependency core does not declare directly. Documented as `docs/security/limitations.md` §3
  rather than approximated; the fix is out-of-process providers.
- [2026-08-16][EI-12] **A test-quality caveat worth not overclaiming.** Under the extras-filter mutation
  the 20-app regression table still PASSED, because those packages happen to be installed at satisfying
  versions in this venv. The real ratchet for the 19-of-20 population is
  `test_extras_are_not_core_so_provider_apps_stay_installable`, not the app table.
- [2026-08-16][EI-12] **`EI-12` flipped to done.** All five done-when clauses hold: planted secret absent
  (test + live `ps eww`), consent-UI network claim matches re-measured enforcement, a conflicting core pin
  cannot affect the gateway (test + live HTTP, numpy unchanged), every first-party app still boots (all 20
  dep declarations pass the guard by test; 24 declare no deps; `minutes` booted healthy live), VD holds as
  scoped above. D0 (the website/security.md docs row) is a SEPARATE row and remains open — untouched here.
- [2026-08-17][EI-8] **PARTIAL — `EI-8` stays `todo`.** §6 landed whole; **§6.2 (localhost web preview,
  the fourth done-when clause) is NOT built**, so the atom is not flipped. Shipped:
  `src/personalclaw/turn_checkpoints.py` (the store), `dashboard/chat_file_rewind.py` (GET preview +
  POST confirm), the pre-edit hook in `agents/native/builtin_tools.py`, phase 1 in `chat_runner._run_chat`,
  the prune in `chat_handlers.api_chat_session_delete`, a `CheckpointsConfig` section wired through all
  five round-trip points incl. a Settings → Chat control, and a `turn_checkpoints` inventory entry.
  Two test files, 47 tests.
- [2026-08-17][EI-8] **PREMISE CORRECTION — the row's named interception point does not exist.** The
  atom (and §6) say the pre-edit backup lives "in `mcp_core`'s file tools". `mcp_core` has **no file
  tools**: its 15 tool definitions are `skill_invoke`, `skill_search`, `get_context`, `skill_remember`,
  `template_save_from_session`, `project_context_review`, `skill_promote`, `dashboard_tile_propose`,
  `wait`, `hook_register`, `notify`, `notify_attachment`, `loop_nudge_stop`, `suggest_template`,
  `refiner_evidence`, `propose_template_diff` — no `write_file`, no `edit_file`. The real handlers are
  `_t_write_file`/`_t_edit_file` in `agents/native/builtin_tools.py` (`_CATEGORY_OF` maps both to
  `filesystem`), and the hook went there. Better placement than the plan's, for a reason worth keeping:
  it is synchronous with the write (no ordering race against an event stream) and it covers **every**
  caller of the native file tools — chat, loops, subagents — not only the dashboard.
- [2026-08-17][EI-8] **The adjacent seam that looked like this feature and is not.**
  `chat_runner._capture_file_change` (:592) already snapshotted before/after on a `write_file`/`edit_file`
  TOOL_CALL event. It is **display-only and cannot restore**: it truncates at `_MAX_FILE_SNAPSHOT`
  (200_000 chars, appending `"\n… [truncated]"`), runs `redact_credentials` over the body, and keeps the
  result in memory on the assistant message's `meta`. So a large or credential-bearing file's "before" is
  already lossy by design. It was left exactly as it is — the checkpoint store is a second, byte-exact
  path, not a widening of the chip.
- [2026-08-17][EI-8] **The secrecy claim is asserted on the STORED BYTES, not on the exclusion list.**
  `NEVER_CAPTURE_GLOBS` is a code-level tuple with **no config field**, and `_EDITABLE_CONFIG` exposes
  only the three bounds — so no PATCH can widen what the store may hold. The test plants a real-shaped
  canary in a workspace `.env`, drives the real `write_file` tool at it, then concatenates **every file
  under the store** (blobs, manifests, `state.json`, journals) and asserts the canary's bytes are absent.
  Falsified before being trusted: deleting the three dotenv globs reds it with
  `assert b'AKIA7SDFJK23LKJ4POIU-sk_live_9f8e7d6c5b4a3928176054-EI8CANARY' not in b'API_TOKEN=AKIA7SDF…'`
  — i.e. the body really did land in a blob. **`security.is_sensitive_path` alone would NOT have caught
  it**: it is `$HOME`-anchored (`_SENSITIVE_HOME_DIRS` lists `.personalclaw/.env`, not `.env`), so a
  workspace `.env` is invisible to it. Both checks run; the globs are the load-bearing one.
- [2026-08-17][EI-8] **A skipped secret is REPORTED, not silently dropped.** The path (never the bytes) is
  recorded as `skipped="secret"`, so the preview carries `action="not_captured"` plus the warning
  "never captured (credential-shaped file) — it will NOT be restored". The alternative — omitting the row —
  would make a rewind look complete while one file stayed mangled.
- [2026-08-17][EI-8] **Two-phase RESTORE, because the unhappy path is the whole risk.** `apply_rewind`
  stages every body as a sibling `<target>.pclaw-rewind` (same filesystem → the rename is atomic), writes
  a plan journal, then commits with `os.replace`. A death mid-commit is therefore *recoverable, not
  ambiguous*: the journal survives and `resume_incomplete_rewind` replays it idempotently (the journal
  carries each body's expected sha, so an already-committed step is recognized rather than redone). Both
  endpoints call it before reporting, so a half-restored tree cannot outlive the next interaction. Driven,
  not asserted: failing `os.replace` on the SECOND of three files leaves exactly one file unrestored plus
  a journal, and the resume returns all three byte-identical. A **staging** failure is different and
  tested separately — it unwinds every temp and leaves the tree bit-for-bit unchanged
  ("staging failed, no files were modified"). The rewind also checkpoints what it overwrites into a fresh
  safety turn, so the rewind itself is rewindable (tested by rewinding back to the mangled state).
- [2026-08-17][EI-8] **A harness artifact that faked the mid-commit finding, worth recording.** Patching
  `os.replace` unscoped also breaks `atomic_write`, which is how the store lands its own manifests — so
  the first attempt failed inside `begin_turn` and reported `ok=True` for the commit. The patch is now
  scoped to renames whose `dst` is one of the three targets. Suspect the harness before the fix.
- [2026-08-17][EI-8] **The cap is tested at the BOUNDARY and by eviction, not by reading the constant.**
  Exactly 1MB under a 1MB cap keeps both turns (`_turn_numbers == [1, 2]`); one more byte evicts turn 1.
  A body larger than the whole cap is refused and recorded `skipped="over_cap"` rather than blowing the
  cap. Turn-cap eviction also GCs the orphaned blobs (`b"body 0" not in store`). Falsified: neutering
  `_make_room` and `_enforce_turn_cap` reds five tests, incl. `assert 1048577 <= (1024 * 1024)`.
- [2026-08-17][EI-8] **Live drive on an isolated home (`/private/tmp/ei8-home`, port 10299, never the
  real home).** Phase 1 fired from a REAL user turn through the real gateway — `POST /api/chat` wrote
  `checkpoints/chat-1-…/turn-000001/manifest.json` with `identity: ['.env','alpha.py','beta.txt',
  'gamma.json']` and `files: []` (a manifest, no copies), and a second message advanced
  `current_turn` to 2. Three files were then mangled through the real `write_file`/`edit_file` handlers.
  `GET …/rewind?turn=1` returned exactly those three as `restore` with unified diffs, plus the `.env` as
  `not_captured/secret`; `grep -rl` for the canary across the whole store found nothing. `POST` without
  `confirm` returned **409 `confirmation_required` carrying the preview** and left every file mangled.
  With `confirm: true`: all three came back byte-identical (sha256 matched the pre-mangle values),
  `.env` stayed rotated, and SEL logged
  `chat.session_rewind … resources=session=…,to_turn=1,restored=3,deleted=0`. All four config fields
  round-tripped through the real `PATCH /api/config/personalclaw` into `config.json` and back;
  `enabled=false` made a live capture return `disabled`. `DELETE /api/chat/sessions/{id}` emptied
  `checkpoints/` — "prunes with the session", measured. The Settings → Chat "File checkpoints" section
  rendered the values just PATCHed (11MB / 7 turns / 2MB), 1 switch + 3 number rows, zero page errors.
- [2026-08-17][EI-8] **What is test-only, stated plainly.** No model provider is configured in an
  isolated home, so the live turn errored with "no model provider resolves for use case 'chat'" *after*
  `begin_turn` ran. The three file writes were therefore driven by calling the real tool handlers
  directly rather than by a model choosing to call them. Every other leg — phase 1, the HTTP preview,
  the confirm gate, the restore, the SEL record, the config round trip, the session-delete prune, the
  Settings render — ran through the gateway a user talks to.
- [2026-08-17][EI-8] **Storage disposition, and why the inventory entry is `derived`.** New entry
  `turn_checkpoints` → `checkpoints/`, `derived=True`, listed in `test_portability`'s
  `_SNAPSHOT_COVERAGE_GAPS`. It is the one gap there that is genuinely NOT rebuildable, and still a
  deliberate omission: every manifest entry is an ABSOLUTE host path (meaningless in another home, and a
  restore would drop one machine's workspace copies into another's), and it is a per-session safety net
  capped at `checkpoints.max_mb` and pruned with the session, so travelling it would grow every snapshot
  by the size of recent edits for no recoverable benefit. Credential material is excluded at CAPTURE
  time, so this flag is not what keeps secrets out of an export.
- [2026-08-17][EI-8] **`/rewind-to-turn` is deliberately NOT `/undo`, and the module is named for it.**
  `chat_undo` says in its own docstring that side effects "are NOT reverted"; this is that other half,
  and it does not touch the transcript (which is the record of what happened). Because
  `chat_regenerate`'s `rewind: true` already means "rewind the transcript", the new module is
  `chat_file_rewind.py`, not `chat_rewind.py` — `tests/test_chat_rewind.py` already tests the other one.
- [2026-08-17][EI-8] **STOP POINT. `EI-8` remains `todo`; no `pr` field set; `dag.json` untouched.**
  Unmet: §6.2 — port discovery on the sandbox handle, `preview_urls` on the run record, and the cockpit
  "Open Preview" affordance. It is a genuinely separate seam (the run record + `SandboxSpec.expose_ports`
  + a cockpit surface, none of which the checkpoint store touches) and `EI-2`'s docker provider owns the
  mapped-port half, so it was not half-built to make a checkbox flip. The remaining §6 line item not
  implemented: binary files over the threshold are recorded manifest-only by SIZE
  (`checkpoints.max_file_mb`) rather than by binary-detection — the restore warning the plan asks for is
  present either way.
- [2026-08-18][EI-5] **DONE (partial on one leg, stated below).** Shipped: the runner data catalog
  (`src/personalclaw/agents/runners.py` + the shipped `agents/runner_catalog.json`, package-data'd),
  measured health evidence in the `agent-metadata/<id>.runner.json` sidecar, capability persistence
  from `normalize_discovery`, adapter pin/provenance verification, `GET /api/agent-runners`, the
  Settings → Agents runner rows, and `agents.unattended_requires_verified_adapter` through all five
  round-trip points. Measured on this box: Claude Code v2.1.233/58 ms, Codex v0.146.1/60 ms, Kiro
  v2.18.1/210 ms, Gemini CLI unhealthy (absent).
- [2026-08-18][EI-5] **DEVIATION — the catalog is core DATA, not a fourth runner app.** §3.1(1) asks
  for `apps/gemini-cli-agent` in the apps repo; the four rows have to exist in Settings whether or not
  any runner app is installed, and this repo cannot ship a row into the sibling apps repo. So the
  catalog is a shipped JSON data file plus a BYO overlay (`$PERSONALCLAW_HOME/runners/<id>.json`).
  Registration is untouched — `acp_bundles/_register.py` remains the only way a runner becomes a
  `ProviderEntry`. The boundary posture: core Python carries zero vendor branching; every vendor value
  is a field read from replaceable data.
- [2026-08-18][EI-5] **Gemini CLI's HEALTHY path is unproven on this machine.** `gemini` is not
  installed here, so what is measured is its unhealthy path (verbatim: `'gemini' not found on PATH
  (looked for: gemini); set GEMINI_CLI_EXECUTABLE to override`). Its declared `acp_args`
  (`--experimental-acp`) come from the vendor's documented flag, not from a handshake run here.
- [2026-08-18][EI-5] **Adapter pins ship EMPTY on purpose; provenance is trust-on-provision.** The
  plan's §3.1(4) sha256 table would have required inventing digests, which would make `verify_adapter`
  lie. Instead `provision_acp_adapter` records npm's own resolved version + SRI integrity into
  `acp-adapters/.pclaw-lock.json` and every later read re-checks it, so an adapter swapped underneath
  an install unverifies. A catalog-declared pin, when present, is checked BEFORE recording and a
  mismatch records nothing.
- [2026-08-18][EI-5] **The gate call site is `SessionManager.get_or_create`, not the provider bridge.**
  The ACP pool-claim path never reaches the bridge factory, so a gate there would have had two holes
  (claim + concurrent-open). The refusal is asserted by the provider factory NOT being called, with
  three floors: flag off proceeds, interactive proceeds, and a verified adapter proceeds.
- [2026-08-18][EI-5] **STOP POINT. `EI-5` left `todo`; `dag.json` untouched.** Not implemented from
  §3.1: (5) idle-release / lease / transparent reconnect — that is EI-6's row, not this one. Health
  evidence is written by the catalog's own `--version` probe and by discovery; wiring the connection
  pool's warm attempt as a third writer was left out deliberately (a pool warm has no catalog id at
  that seam, and inventing one would have been the second registration path §3.1 forbids).
- [2026-08-18][EI-5] **DISCOVERY + DONE — the unattended gate had nine holes: it read a kwarg, not the
  session.** `guard_unattended_spawn` was wired at the right call site, but its `unattended` argument
  came from `extra_factory_kwargs["unattended"]`, and a tree-wide sweep found exactly ONE writer of
  that key (`subagent.py:1954`). Measured against `main`: with
  `agents.unattended_requires_verified_adapter` ON and an unverified adapter, **9 of 9** unattended
  session-key families spawned the runner anyway — `cron:`, `loop-`, `loop:`, `_bg`, `subagent:`,
  `inbox:`, `side:`, `channel:` and the sessionless `unattended:` dispatch (red: `Failed: DID NOT
  RAISE UnverifiedAdapterError`, nine parametrizations). The help text, `configuration.md` row and
  Settings hint all named "cron, scheduled run, loop worker", so the control was documented as
  covering paths it did not cover. Fixed by deriving unattendedness from the session key through
  `guardrails.policy.is_unattended_session` — the classifier the guardrail layer already resolves
  safety profiles with, so there is one vocabulary and no per-caller opt-in — while still honouring
  the explicit kwarg. All three copy surfaces corrected to the enforced scope. Vacuity floors:
  `chat:`/`project:`/`web:` keys still proceed (falsified by forcing `_unattended = True`, which reds
  all four attended tests), and an unattended key with the flag OFF still proceeds.
- [2026-08-18][EI-5] **DONE — the PATH-removal clause is now driven as a TRANSITION.** The suite
  proved the absent-binary and present-binary states separately, which is not the done-when clause:
  a row that WAS healthy has to flip, and it has to flip in the sidecar the surface paints from on a
  plain load. Added a real `monkeypatch.setenv("PATH", ...)` drive (install → healthy `v4.2.0` with a
  measured latency → remove → unhealthy with the resolver's verbatim text, `version`/`latency_ms`
  back to unknown). Falsified by making `record_evidence` merge non-`None` fields instead of replacing
  `last_check`: red `assert '4.2.0' is None` — the stale healthy version would have been served
  alongside the failure. Vacuity floor: a second probe with the binary still installed must NOT flip.
- [2026-08-18][EI-5] **DONE — §3.2's second field, `agent.runner_health_check_secs`, with a reader.**
  It was absent from `loader.py` entirely, so §3.2's "four-point" wiring was one field of two. Now
  wired through all five points (dataclass + `_meta`, `load()` explicit mapping clamped to the same
  `[60, 86400]` window `_EDITABLE_CONFIG` enforces, `to_dict()` via `asdict`, the PATCH allowlist, and
  a `NumberRow` in Settings → Agent defaults) — and deliberately NOT inert: `runner_rows` now carries
  `health_stale`, and the row renders a **check overdue** chip beside (not instead of) the reading, so
  an old "healthy" is never presented as the present state. `null` is preserved as unknown — a
  never-probed runner and an unparseable timestamp are not "overdue". Falsified by hardcoding the
  interval to 3600 (red `assert False is True`: same recorded reading, two windows, two verdicts) and,
  on the frontend, by loosening the chip to `!== false` (red: an unknown-age row wore the chip).
  `config-baseline.json` regenerated in the same change.
- [2026-08-18][EI-5] **DEVIATION (unchanged, re-affirmed) — `agents.runner_idle_release_secs` is
  EI-6's, not this atom's.** §3.2 lists it alongside the other two, but §3.1(5) idle-release is EI-6's
  scope row and no connection-pool idle-release exists yet. Adding the field here would have shipped a
  knob with no reader — the exact defect the health-check field above was written to avoid.
- [2026-08-18][EI-5] **BLOCKED (scope, not doctrine) — `apps/gemini-cli-agent` cannot be created from
  this repo.** The done-when clause "Settings → Agents shows … Gemini CLI …" is satisfied by the
  shipped catalog row (`agents/runner_catalog.json`), which is why the data-catalog deviation above was
  taken. But the scope line's "Gemini first-party app via `acp_bundles/_register.py`" needs a bundle in
  `PersonalClawApps`, which has `claude-code-agent`, `codex-agent` and `kiro-cli-agent` and no Gemini
  sibling. That is a one-app change in the other repo, outside this branch's fence. Owner call needed
  on whether EI-5 closes on the catalog row alone or waits for the apps-repo bundle.
- [2026-08-18][EI-8] **§6.2 LANDED — the localhost web preview, the clause that kept `EI-8` at
  `todo`.** New `src/personalclaw/workflows/web_preview.py`, wired into `service.workspace_review`
  (so it travels on `GET /api/workflows/runs/{id}/workspace`) and rendered as a "Preview" block with
  an **Open Preview** link in `WorkspacePanel.tsx`. Port attribution is by OWNING PROCESS: two
  probes (all listening TCP sockets, then one batched cwd lookup for just the pids found), keeping a
  port only when its process's cwd resolves inside the run's workspace. Driven against two real
  `http.server` processes in sibling directories — the one under the run root was found (from a
  SUBDIRECTORY of it, which is how a dev server is actually started), the sibling was not, and
  `curl` returned **HTTP 200** on the discovered URL, so the affordance is not a dead link.
- [2026-08-18][EI-8] **DEVIATION — `preview_urls` is computed on read, NOT stamped on the run
  record.** §6.2 says "discovered ports registered as `preview_urls` on the run record". A persisted
  port list is the stale-link defect this repo has been bitten by repeatedly: the dev server exits,
  the record keeps the port, and the cockpit offers an "Open Preview" that loads nothing — or loads
  whatever process took the port next. There is exactly one writer and one reader and no cache, so
  §6.2's "removed from the run record on sandbox teardown" costs zero code and cannot go stale: a
  torn-down workspace has no processes under it, so it reports no ports and says why.
- [2026-08-18][EI-8] **DEVIATION — no `SandboxSpec.expose_ports`, and no `SandboxHandle.list_ports`.**
  §6.2 asks for both for the docker/lima tier. `EI-2`'s container provider does not exist (the only
  provider is in-core `none`), and nothing holds a `SandboxHandle` for a workflow run — handles are
  an ACP-transport spawn concern. Adding either would have been a declared field no code reads and a
  method with no caller, which is the defect class this plan's own log keeps recording. The host tier
  is built and measured; the mapped-port half stays with the provider that will own it.
- [2026-08-18][EI-8] **No new config field, deliberately.** §6.2 specifies none, and the atom's
  done-when needs none. A knob nobody would flip would have cost the five round-trip points and put
  this branch into `config-baseline.json` / `configuration.md` / the Settings pages — all shared with
  four sibling branches — for no user-visible gain.
- [2026-08-18][EI-8] **A REAL `.env` LEAK on `main`, found by census and closed here.** The secrecy
  floor matched `NEVER_CAPTURE_GLOBS` against the **literal** basename, and `read_bytes` follows a
  symlink. Measured on unmodified `main`: `ws/config.txt -> ws/.env` returned `"captured"` and the
  planted canary landed in
  `checkpoints/<slug>/blobs/552adcef…bin`. `security.is_sensitive_path` does not cover it either — it
  is `$HOME`-anchored, so a workspace `.env` is invisible to it. `is_never_captured` now runs BOTH
  checks against the literal path AND its resolved target; the same fix closes a symlink into
  `~/.aws/`. The prior session's proof was sound for a DIRECT write to `.env` and simply never asked
  what a link would do. Falsified: dropping the resolved candidate reds with
  `AssertionError: assert 'captured' == 'secret'`. The rail carries a vacuity floor — an ordinary
  file must still return `"captured"`, or a check that refused everything would pass it forever.
- [2026-08-18][EI-8] **A rewind now refuses to write outside the session's roots.** `apply_rewind`
  is the one path that writes an arbitrary RECORDED path back to disk, so it verifies the destination
  instead of trusting the manifest that named it (`session_roots` / `is_within_roots`, compared on
  RESOLVED paths so a traversal component and an out-of-tree symlink are both caught). Roots are the
  turn manifests' `cwd` plus a new per-turn `roots` list recorded at capture time — needed because
  `begin_turn(cwd=None)` is legitimate, so the manifest alone leaves a session with no root, and the
  base the WRITE was governed by is the honest root for restoring that write. Falsified twice: the
  rail reds with `assert [] == ['…/outside.txt']`, and a direct drive with the guard removed
  overwrote a file outside the root ("MUST NOT CHANGE" → "GOOD") **while reporting `ok=True`**.
- [2026-08-18][EI-8] **The confinement test caught a swallowed-write bug in the fix itself.**
  `res.restored, res.deleted, res.errors = _commit_journal(...)` **assigned** over `errors`, erasing
  the refusals recorded before staging and flipping `ok` back to `True` — a confirm path reporting
  success while a file the user asked about was never written. Now extends. This is why the refusal
  is asserted on `ok` and on the untouched file's hash, not just on the `refused` list.
- [2026-08-18][EI-8] **Two harness artifacts worth recording, both of which faked a finding.**
  (1) A stale `http.server` from an earlier probe was squatting port 18711, so the fixture's server
  never started and the scan looked broken — `a.log` held `Address already in use`. Suspect the
  harness before the fix; the test now allocates a free port per run rather than a literal.
  (2) The "Open Preview" link's port was disambiguated with an `sr-only` span, and the computed
  accessible name **did not include it** (`textContent` did; the name did not), so the
  disambiguation would have shipped inert while looking present in the markup. It is an `aria-label`
  now, which still contains the visible text so WCAG 2.5.3 holds. Measured, not assumed.
- [2026-08-18][EI-8] **Gate:** `make lint` exit 0 (black/isort/flake8 + mypy clean, 920 files);
  `pytest` 255 passed over `test_turn_checkpoints` (+2 new) / `test_rewind_to_turn_api` /
  `test_run_web_preview` (12 new) / `test_workflows_containers` / `test_workflows_api` (+1 rail) /
  `test_portability` / `test_config_roundtrip`, plus `test_workflows_provisioning` 40 passed; web
  `typecheck` + `4157 tests in 412 files` + `build` all exit 0. `~/.personalclaw` unchanged (the
  suite's own real-home rail).
- [2026-08-18][EI-8] **What is NOT done, so the next session does not re-derive it.** The `ss` (Linux)
  listener parser is asserted from a fixture string only — this box is Darwin, so its live path is
  unproven. The docker/lima mapped-port tier is unbuilt (see the deviation above). The §6 line item
  still open from the previous session is unchanged: an over-threshold file is manifest-only by SIZE
  rather than by binary detection. `dag.json` and `EI.md` untouched (driver-owned); no push, no PR.
