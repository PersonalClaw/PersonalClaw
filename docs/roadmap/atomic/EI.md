# EXECUTION-ISOLATION — atomic plans

**Source plan:** [`EXECUTION-ISOLATION`](../plans/EXECUTION-ISOLATION.md)  
**Code:** `EI`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `EI-1` | ✅ | SandboxProvider seam + `none` provider + ResourceCeilings foundation | — | sandbox_providers/ package + `none` provider composing sandbox.wrap_argv exists; SubagentManager.spawn(sandbox="none") runs the worker through handle.exec; a child `ulimit -n` reports the ceiling; sandbox.nofile/max_pids/max_rss_mb round-trip via test_config_roundtrip; test_manifest_types_match_handlers passes (type+handler in one commit) |
| `EI-2` | ⬜ | `docker` provider + engine/workspace-block adoption + cgroup v2 tier | `EI-1`, `EXT:WORK-CONTAINERS:WORK-R3 workspace block call sites + WORK-R20 mode:container re-expression` | SC1: a stage `sandbox: docker` runs its process tree in a UID-aligned bind-mount container over its WORK-R3 worktree; a write outside allowed_write_paths is blocked by the boundary; a no-Docker machine parks needs-input with a typed reason (no silent host downgrade for unattended). SC2: a subagent `sandbox: docker` without a grant to ~/.personalclaw/models cannot delete a real local model. Linux fork-bomb child hits pids.max; macOS logs a one-time not-enforced warning |
| `EI-3` | ✅ (#PENDING) | Spawn-audit CI test + wrap remaining agent-influenced spawn seams | `EI-1` | tests/test_spawn_ceiling_audit.py is green with every agent-influenced seam ceiling-wrapped or explicitly operator-exempt; adding an unmapped create_subprocess_exec/Popen/StdioServerParameters on a branch fails CI naming the file:line |
| `EI-4` | ⬜ | Lima VM tier app + apps-sandbox (#71) + terminal sandbox picker | `EI-1`, `EI-2` | SC3: with apps/lima-sandbox installed + instance running, a terminal opened "inside the run's sandbox" executes in the VM with correct path translation; stopping the instance flips the provider to greyed-out-with-reason within one probe TTL; an interactive request gets the path-guard-only dialog; app backends launch through backend.sandbox |
| `EI-5` | ⬜ | BYO runner data catalog + Gemini CLI runner + adapter pin/verify + Settings→Agents evidence | — | SC4: Settings→Agents shows Claude Code, Codex, Gemini CLI, Kiro rows with health evidence (last handshake, version, latency) + capability chips; a runner removed from PATH flips unhealthy with the verbatim probe error; an unattended spawn against an unverified adapter is refused when agents.unattended_requires_verified_adapter is on |
| `EI-6` | ⬜ | Runner lifecycle (idle-release/lease/reconnect) + durable tmux-backed sessions | `EI-5`, `EXT:WORK-CONTAINERS:WORK-R8 lease convention (flock under locks/) + WORK-R7 suspended-liveness path` | SC5: killing the gateway mid-run with durable sessions on → the recovery sweep reattaches to the still-alive tmux worker (run resumes suspended→running, journal flags `resumed`), only genuinely dead sessions are tombstoned; a claimed runner idle past TTL is released and its lease holder is visible in Settings |
| `EI-7` | ⬜ | Second-opinion handoff + ProposerBackend + sandbox-internal tool gateway | `EI-1`, `EI-5` | SC6: a stalled loop's "second opinion" fires a DIFFERENT cataloged runner one-shot inside the same sandbox class and is accepted only when the disk re-diff confirms the edits, SEL-audited. SC7: inside a docker/lima sandbox `pclaw-tool memory_recall` succeeds while ss/netstat shows zero listening sockets, no credential material exists in the sandbox, and a research-profile sandbox is refused write-class tools host-side |
| `EI-8` | ✅ | Turn-bound two-phase file checkpointing + /rewind-to-turn + localhost web preview | `EI-1` | SC8: after a turn that mangled three files, /rewind-to-turn N previews exactly those files with diffs and restores them byte-identical on confirm; .env files were never captured; the checkpoint store respects its cap and prunes with the session; a run's dev server port surfaces an "Open Preview" affordance to localhost:<port> |
| `EI-9` | ⬜ | Reviewer-comment triage primitive (line-anchored findings → accepted-subset dispatch) | `EI-5`, `EXT:WORK-CONTAINERS:cockpit diff panel to extend`, `EXT:LEARNING-FLYWHEEL:calibration record for rejections` | SC9: a workflow review stage emits line-anchored findings; the triage panel validates anchors against the real diff; the user accepts 2 of 5; the accepted pair auto-dispatches to the originating worker which applies them; rejected findings land in the calibration record; nothing was auto-written without acceptance |
| `EI-10` | ⬜ | Secrets vault UX + presence-only API + grant-to-sandbox toggles | `EI-1`, `EXT:WORK-CONTAINERS:WORK-R19 secrets store + per-project keychain backend` | SC10: the vault lists global + per-project secrets with presence-only values, inherit-from-host rows rendered distinctly, and consumer links; a secret granted to sandboxed runs reaches a docker leaf's env while an ungranted sibling does not; no value is readable back through any API; project export ZIPs contain presence flags only |
| `EI-11` | ⬜ | Security docs correction — credential-hiding vs confinement (D0, land first) | — | no public surface claims confinement the code does not provide; the guardrails claim names its unattended scope (reusing the Settings panel wording); the desktop claim matches CI reality; security.md has the explicit sandbox does/doesn't section |
| `EI-12` | ✅ | App-side confinement compounders — env allowlist, network-perm decision, per-app deps (D1/D2/D3+VD) | — | a planted secret in the gateway env is absent from an app backend's env (test proves it); the Store consent UI's network claim matches enforcement reality; an app pinning a conflicting core dependency does not affect the gateway; every first-party app still boots; the VD user-validation sweep holds |

## Atom scopes

### `EI-1` — SandboxProvider seam + `none` provider + ResourceCeilings foundation

**Status:** done (PR #933)

§1.1 contract (SandboxProvider/SandboxSpec/SandboxHandle 6-method), §1.2 provider fidelity (PROVIDER_TYPES + SandboxTypeHandler same commit, sdk/sandbox.py facade, boot-register none), §1.3(1) subagents; §11 Session 1; Amendment 2026-07-26 EI-A1 (ResourceCeilings + ceiling_kwargs/wrap_ceilings applied in none + native bash + bash action provider + subagent)

**Done when:** sandbox_providers/ package + `none` provider composing sandbox.wrap_argv exists; SubagentManager.spawn(sandbox="none") runs the worker through handle.exec; a child `ulimit -n` reports the ceiling; sandbox.nofile/max_pids/max_rss_mb round-trip via test_config_roundtrip; test_manifest_types_match_handlers passes (type+handler in one commit)

### `EI-2` — `docker` provider + engine/workspace-block adoption + cgroup v2 tier

**Status:** todo

§1.1 docker built-in + failure-honesty/downgrade; §1.3(2) code loops & workflow stage nodes (allowed_write_paths/egress_tier/safety_profile threaded into SandboxSpec); §11 Session 2; Amendment 2026-07-26 EI-A2 (cgroup v2 systemd-run transient scope + doctor probe)

**Done when:** SC1: a stage `sandbox: docker` runs its process tree in a UID-aligned bind-mount container over its WORK-R3 worktree; a write outside allowed_write_paths is blocked by the boundary; a no-Docker machine parks needs-input with a typed reason (no silent host downgrade for unattended). SC2: a subagent `sandbox: docker` without a grant to ~/.personalclaw/models cannot delete a real local model. Linux fork-bomb child hits pids.max; macOS logs a one-time not-enforced warning

### `EI-3` — Spawn-audit CI test + wrap remaining agent-influenced spawn seams

**Status:** done (#PENDING)

Amendment 2026-07-26 EI-A3: AST spawn-audit vs ceiling-wrapped/operator-exempt map; wrap apps backend, mcp_client/mcp_discovery, acp/transport, schedule_script, loop gates/worktree

**Done when:** tests/test_spawn_ceiling_audit.py is green with every agent-influenced seam ceiling-wrapped or explicitly operator-exempt; adding an unmapped create_subprocess_exec/Popen/StdioServerParameters on a branch fails CI naming the file:line

### `EI-4` — Lima VM tier app + apps-sandbox (#71) + terminal sandbox picker

**Status:** todo

§2 Lima VM Tier (apps/lima-sandbox isolated kind, cached availability probe, host↔guest path translation, degradation dialog); §1.3(3) Terminal per-session sandbox picker; §1.3(4) apps sandbox un-deferred (backend.sandbox manifest field + permissions.network→egress_tier / permissions.storage→allowed_write_paths); §11 Session 3

**Done when:** SC3: with apps/lima-sandbox installed + instance running, a terminal opened "inside the run's sandbox" executes in the VM with correct path translation; stopping the instance flips the provider to greyed-out-with-reason within one probe TTL; an interactive request gets the path-guard-only dialog; app backends launch through backend.sandbox

### `EI-5` — BYO runner data catalog + Gemini CLI runner + adapter pin/verify + Settings→Agents evidence

**Status:** todo

§3.1 runners-stay-apps + <name>.runner.json sidecar (health evidence, capability persistence from normalize_discovery, pinned+sha256 adapter bundles), Gemini first-party app via acp_bundles/_register.py; §3.2 config wiring (agents.runner_* four-point); §11 Session 4

**Done when:** SC4: Settings→Agents shows Claude Code, Codex, Gemini CLI, Kiro rows with health evidence (last handshake, version, latency) + capability chips; a runner removed from PATH flips unhealthy with the verbatim probe error; an unattended spawn against an unverified adapter is refused when agents.unattended_requires_verified_adapter is on

### `EI-6` — Runner lifecycle (idle-release/lease/reconnect) + durable tmux-backed sessions

**Status:** todo

§3.1(5) idle-release + WORK-R8-style lease record + transparent reconnect; §5.1 durable sessions (deterministic pclaw-<project>-<run>-<session> names, reattach-not-reap boot pre-step in both orphan recoveries, agents.durable_sessions flag); §11 Session 5

**Done when:** SC5: killing the gateway mid-run with durable sessions on → the recovery sweep reattaches to the still-alive tmux worker (run resumes suspended→running, journal flags `resumed`), only genuinely dead sessions are tombstoned; a claimed runner idle past TTL is released and its lease holder is visible in Settings

### `EI-7` — Second-opinion handoff + ProposerBackend + sandbox-internal tool gateway

**Status:** todo

§4.1 second-opinion action provider (registered + added to ALLOWED_HOOK_PROVIDERS) with handoff brief + fire-wait-verify; §4.2 ProposerBackend 4-method contract + subagent fallback backend; §5.2 pclaw-tool shim + loopback-free exec-channel tool gateway; §11 Session 6

**Done when:** SC6: a stalled loop's "second opinion" fires a DIFFERENT cataloged runner one-shot inside the same sandbox class and is accepted only when the disk re-diff confirms the edits, SEL-audited. SC7: inside a docker/lima sandbox `pclaw-tool memory_recall` succeeds while ss/netstat shows zero listening sockets, no credential material exists in the sandbox, and a research-profile sandbox is refused write-class tools host-side

### `EI-8` — Turn-bound two-phase file checkpointing + /rewind-to-turn + localhost web preview

**Status:** todo — **PARTIAL, 2026-08-17.** §6 landed whole (checkpoint store + `/rewind-to-turn`
preview/confirm + caps + prune + secrecy floor); **§6.2 localhost web preview is NOT built**, so the
fourth done-when clause is unmet and the atom stays `todo`. See the plan's `## Execution log`.

§6 two-phase checkpoint (identity-set snapshot + pre-edit backup in mcp_core file tools) + /rewind-to-turn (preview diff + confirm, SEL-audited, filesystem-only) + per-session caps; §6.2 localhost web preview (port discovery on the sandbox handle, ephemeral preview_urls); §11 Session 7

**Premise correction (2026-08-17).** The row says the pre-edit backup goes "in mcp_core file tools".
`mcp_core` has **no file tools** — its 15 tools are skills/context/notify/hook/template ones (grep
`'"name": "'` in `mcp_core.py`). The file-writing handlers an agent actually calls are
`_t_write_file`/`_t_edit_file` in `agents/native/builtin_tools.py`, and that is where the hook went.

**Done when:** SC8: after a turn that mangled three files, /rewind-to-turn N previews exactly those files with diffs and restores them byte-identical on confirm; .env files were never captured; the checkpoint store respects its cap and prunes with the session; a run's dev server port surfaces an "Open Preview" affordance to localhost:<port>

### `EI-9` — Reviewer-comment triage primitive (line-anchored findings → accepted-subset dispatch)

**Status:** todo

§7: consume the approved WORKFLOWS-V2 Canonical Finding record + auto_fixable flag; diff-anchored triage panel with anchor validation against the actual diff; accepted subset auto-dispatched to the originating session (native resume per §3 capability matrix); rejections feed the flywheel calibration; §11 Session 7

**Done when:** SC9: a workflow review stage emits line-anchored findings; the triage panel validates anchors against the real diff; the user accepts 2 of 5; the accepted pair auto-dispatches to the originating worker which applies them; rejected findings land in the calibration record; nothing was auto-written without acceptance

### `EI-10` — Secrets vault UX + presence-only API + grant-to-sandbox toggles

**Status:** todo

§8 (NEW-27 remainder over WORK-R19): Settings→Secrets vault listing global + per-project secrets with presence-only display, inherit-from-host rows as a first-class type, consumer links; GET/POST/DELETE /api/secrets (write-only values); project hub Context tab links; per-secret grant-to-sandboxed-runs toggle populating SandboxSpec.env; §11 Session 7

**Done when:** SC10: the vault lists global + per-project secrets with presence-only values, inherit-from-host rows rendered distinctly, and consumer links; a secret granted to sandboxed runs reaches a docker leaf's env while an ungranted sibling does not; no value is readable back through any API; project export ZIPs contain presence flags only

### `EI-11` — Security docs correction — credential-hiding vs confinement (D0, land first)

**Status:** todo

Amendment 2026-07-29 (b) + task D0 — docs-only, dependency-free: correct the website boundary diagram/copy, qualify the guardrails claim to unattended work, qualify the desktop-platform claim, add a "what the sandbox does and does not do" section to docs/architecture/security.md

**Done when:** no public surface claims confinement the code does not provide; the guardrails claim names its unattended scope (reusing the Settings panel wording); the desktop claim matches CI reality; security.md has the explicit sandbox does/doesn't section

### `EI-12` — App-side confinement compounders — env allowlist, network-perm decision, per-app deps (D1/D2/D3+VD)

**Status:** ✅ done — D1+D2 landed 2026-08-13; **D3 (re-scoped) + VD landed 2026-08-16**. D3 shipped as
an **admission refusal**, not the app-scoped isolation the row proposed: measured, ZERO of 44
first-party apps have both a backend and declared `pythonDependencies`, so a backend-scoped
`PYTHONPATH` would have isolated an empty population. Isolating the in-process providers (where the
real risk lives) stays the owner-scope seam decision recorded BLOCKED in the plan — see the
`## Execution log` DEVIATION.

Amendment 2026-07-29 (a) + tasks D1 (backend env by allowlist not dict(os.environ), sensitive-prefix scrub floor), D2 (permissions.network: enforce via egress rail OR mark advisory in consent UI/manifest — not both/neither), D3 (per-app pythonDependencies isolated to app-scoped target so an app cannot shadow a core dep), VD (validation-as-a-user sweep)

**D1 (done).** `apps/backend_runtime.py` builds the child env via `sandbox.build_child_env(site="app-backend", extra={PORT, PERSONALCLAW_APP_NAME, PERSONALCLAW_APP_SECRET, +PERSONALCLAW_APP_DATA_DIR when storage is held})`. Falsified before trusting: reverting the line shows the backend used to receive ~130 undeclared variables incl. `SSH_AUTH_SOCK`, AWS and git-identity vars. Blast radius measured — the 9 credential-name fallbacks are read only in **in-process** `provider.py` modules, and of 44 first-party apps only `growth` and `minutes` declare a backend (both read just `PORT`/`PERSONALCLAW_APP_DATA_DIR`), so no first-party app changes behaviour; both booted healthy against the change. `tests/test_app_backend_child_env.py` drives the real spawn.

**D2 (done).** Resolved as **mark advisory**, not enforce: provider code is imported in-process by the
gateway, so there is no per-app egress chokepoint, and only 2 of 44 first-party apps have a backend —
an egress rail there would confine 2 and leave 42 unconfined while showing all 44 identically. The
Store's `PermissionList` (both the pre-install panel and the installed-app panel) now renders the
network claim outside the enforced-permission bullets, labelled advisory, **whether or not the app
declares it** — measured before-state: a declaring app got a "• Network access" bullet among enforced
grants, and a `network: false` app (which `growth` and `minutes`, the only two backend-having apps, both
are) got no row at all, reading as "blocked". `apps/permissions.py`, `docs/security/limitations.md` §2
and `docs/architecture/app-platform.md` now describe that surface instead of claiming it generically.

**D3 (done, re-scoped — DEVIATION).** The row proposed installing an app's `pythonDependencies` to an
app-scoped target and launching *its backend* against it. Census first: of 44 first-party manifests,
**20 declare `pythonDependencies` and 2 declare a backend, and the sets do not intersect** — `growth`
and `minutes` declare no deps, and every dep-declaring app is an in-process provider. So the proposed
mechanism had a **zero-app population**: it would have shipped as an inert control while the real
shadowing path (pip resolving into the shared venv that the gateway imports from) stayed wide open.
What shipped instead makes the done-when property true at the only chokepoint that exists without
redefining the provider seam: `app_manager._reject_core_dependency_conflicts` refuses, before pip is
spawned, any declared requirement naming a **core-declared** dependency unless the installed version
already satisfies it — so pip is never in a position to move one. Fail-closed on the two
can't-prove-it cases (unparseable specifier, core name whose installed version can't be read).
Extras are excluded **by `extra ==` marker**, which is load-bearing: `openai`, `anthropic`, `boto3`,
`slack-sdk`, `faster-whisper`, `sentence-transformers`, `piper-tts`, `huggingface-hub` and `faiss-cpu`
are all extras, and 19 of the 20 dep-declaring apps pin exactly those — treating one as core would
refuse almost every provider app in the Store. The single real collision is `diarization-onnx`'s
`numpy>=1.24` against core's `numpy>=1.21,<3`, which is satisfied by the installed 1.26.4 and is
therefore ALLOWED — the rail's vacuity floor. Out-of-process provider isolation remains the owner-scope
decision already recorded BLOCKED; it was not re-litigated. `tests/test_app_core_dep_conflicts.py`.

**VD (done).** Isolated home on port 10155, gateway booted from the worktree with two secrets planted
in **its own** environment. Installed `minutes` (a real first-party app with a backend) through
`POST /api/apps`; read the backend process's environment from OUTSIDE with `ps eww`: **zero** occurrences
of either planted name or value, while `ps eww` on the gateway PID shows both verbatim — and the same
read shows the backend DOES carry `PORT`/`PERSONALCLAW_APP_NAME`/`PERSONALCLAW_APP_DATA_DIR`/`HOME`/`PATH`,
so the absence is not a failed read. The app works: healthy on `/health`, and a list → create → read-back
round trip through the gateway proxy (`/apps/minutes/api/meetings`) returns the created record. An app
pinning `numpy<1.21` is refused over real HTTP with the version-specific reason, and `numpy` reports
1.26.4 before and after. Enforcement reality re-checked against code for the D2 copy: app backends are
wrapped only by the resource-ceiling shim, never `sandbox.wrap_argv`, and that profile is
`(allow default)` plus file-only deny rules with `CLONE_NEWNET` nowhere in the tree — so "PersonalClaw
does not confine an app's outbound traffic" is literally true.

**Done when:** a planted secret in the gateway env is absent from an app backend's env (test proves it); the Store consent UI's network claim matches enforcement reality; an app pinning a conflicting core dependency does not affect the gateway; every first-party app still boots; the VD user-validation sweep holds

