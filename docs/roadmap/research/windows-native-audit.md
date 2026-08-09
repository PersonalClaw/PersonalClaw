# Windows-native audit (Platform-Reach rung 3)

**Status:** audit only. This document costs the work of running PersonalClaw as a
**native Windows** process (no WSL2, no Docker Desktop) and recommends whether to
do it. It writes **no implementation code** — that is the soul guardrail of this
rung, and the go/no-go below is an owner decision, not a foregone one.

Native Windows is rung 3. Rungs 1 and 2 already give Windows users a tested path:

- **Rung 1 — Docker Desktop.** Published multi-arch images run under Docker
  Desktop's own Linux VM. The user never touches a Linux shell. See
  [platforms.md](../../guides/platforms.md).
- **Rung 2 — WSL2.** PersonalClaw runs as an ordinary Linux install inside a real
  Linux kernel. Fully supported, with a per-release checklist.

So this audit is not about *whether Windows users can run PersonalClaw* — they can,
today, two ways. It is about whether a **native** `pip install personalclaw` on
Windows PowerShell, with no Linux layer underneath, is worth building.

## Summary recommendation

**No-go for now.** Ship nothing native. Keep Windows on rungs 1–2 (Docker Desktop
+ WSL2) and revisit when the demand evidence below crosses its threshold.

The blocking reason is not any single mechanism — it is that the codebase treats
POSIX process, permission, and isolation primitives as **security and lifecycle
guarantees**, not conveniences. A native Windows port that stubbed them out would
not be "PersonalClaw on Windows"; it would be a build where credential files are
not owner-only, spawned agents cannot be reliably killed as a group, and command
execution has no resource ceiling — all silently. Rungs 1–2 already run the *real*
Linux code with those guarantees intact, which is why they are the honest Windows
story and a native port is not.

The one change worth making regardless of the go/no-go is small and defensive:
today a native-Windows process fails at **gateway boot** with an unguarded
`import resource` (see §6), so even "run it and see what breaks" does not get off
the ground. Guarding that import is a rung-2.5 courtesy, not a port — noted at the
end.

## Verified mechanism list

Each section states the POSIX implementation as-built (with `file:line`), the
Windows options, their effort and risk, and a per-mechanism verdict. Costs are
t-shirt sizes (S ≈ days, M ≈ 1–2 weeks, L ≈ multiple weeks) against a single
engineer already fluent in the codebase; they are relative, not commitments.

---

### 1. Process reaping — Job Objects vs PPID/process-group reaping

**As-built (POSIX).** Every agent-influenced spawn leads a new session/process
group with `start_new_session=True` (the `setsid` equivalent) and is reaped by
signalling the whole group:

- Spawns: `acp/transport.py:366` (ACP agent), `dashboard/handlers/terminal.py:334`
  (PTY shell), `action_providers/bash_provider.py:225` (hook/cron bash),
  `cli_server.py:322` (detached gateway).
- Group kill: `os.killpg(os.getpgid(pid), …)` at `acp/transport.py:456`/`:467`,
  `subagent.py:852`, `session.py:1196`, `dashboard/handlers/terminal.py:171`,
  `action_providers/bash_provider.py:246`.
- Escaped-child sweep (children that left the group): PPID walking via
  `/proc/<pid>/task` on Linux with a `pgrep -P` / `ps -o ppid=` fallback —
  `acp/transport.py:109-208`, `session_pid.py:376-476`, `mcp_core.py:508-519`,
  `mcp_shared.py:116-124`.
- Signal handling: `loop.add_signal_handler(SIGINT/SIGTERM)` at `gateway.py:3607`
  (POSIX-only in asyncio).

None of `os.killpg`, `os.getpgid`, `loop.add_signal_handler`, `/proc`, `pgrep`, or
`ps` exists on native Windows.

**Why it matters.** This is not cosmetic. An agent spawns a bash command that
spawns a build that spawns a test runner; when the run is cancelled or times out,
the group-kill is what guarantees the whole tree dies. Lose it and cancelled runs
leave orphaned process trees holding files, ports, and CPU.

**Windows options.**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **Job Objects** (`CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assign child at spawn via `CREATE_SUSPENDED`) | The correct Windows analogue of a process group: killing the job kills the whole tree, and it survives escaped children by design | M | Med — needs `pywin32` or `ctypes` against `kernel32`; every spawn site (4) and every kill site (5) forks a Windows path; asyncio child-watching differs |
| **`CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`** | Cheaper: spawn in a new console group, kill the tree with `taskkill /PID <pid> /T` | S–M | High — `taskkill /T` walks the *current* PPID snapshot, so a child that re-parents or a fast fork escapes; this is exactly the escaped-child case `transport.py` already handles on POSIX and would regress |
| Keep asyncio `SIGTERM` handler | — | — | Not portable; Windows asyncio uses `Proactor` and does not support `add_signal_handler` for SIGTERM; needs a Windows console-control-handler path |

**Verdict.** Job Objects are the only Windows mechanism that preserves the
kill-the-whole-tree guarantee. `taskkill /T` reintroduces the escaped-child bug the
POSIX code went to lengths to close. Cost: **M**, and it touches the most
security-adjacent spawn/kill paths in the tree. High blast radius.

---

### 2. File permissions — `icacls`/ACLs vs `chmod 0o600`

**As-built (POSIX).** Owner-only file modes are an **enforced security guarantee**
for every secret, not a best-effort. The code writes secret files `0o600` and
*re-tightens* if it ever sees group/world bits (`mode & 0o077`):

- LLM API-key store — `llm/credentials.py:96` (`FILE_MODE = 0o600`), enforced
  `:217/:222`, `_enforce_perms` `:288-297`.
- Dashboard session-signing key — `dashboard/session_store.py:87/:97`,
  `_ensure_owner_only` `:112-114` ("a key that is briefly world-readable has
  already leaked").
- Owner-login records — `auth/credentials.py:136/:226`; enrollment codes
  `auth/enrollment.py:89`.
- Dashboard secret — `dashboard/server.py:206-207` (`os.open(...,0o600)` +
  `os.fchmod`).
- App secrets — `apps/app_secret.py:53-78` (`_write_0600` + re-pin).
- SEL HMAC key — `sel.py:126`.
- `.env` credentials — `config/loader.py:272/:3839-3840` (tighten on `& 0o077`).
- Shared baseline — `atomic_write.py:78` pins mode via `os.fchmod`; umask at
  `atomic_write.py:17-26`.
- Plus ~20 more DB/inbox/snapshot/upload sites.

**Why it matters.** On NTFS the POSIX mode bits are cosmetic. Python's `os.chmod`
on Windows only toggles the read-only attribute — it does **not** restrict other
users. So `_enforce_perms`' check `mode & 0o077` reads clean, and the "owner-only"
guarantee **silently does not hold**. A shared/enterprise Windows box would expose
every credential the code believes it protected. This is the single most dangerous
part of a naive port, precisely because it fails quietly.

**Windows options.**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **`icacls` / Windows ACLs** (grant only the current SID + SYSTEM, break inheritance, remove Users/Everyone) | The real owner-only guarantee on NTFS | M | Med–High — must be applied at *every* `0o600` site behind a `_secure_file()` seam; getting the ACL wrong (leaving inherited ACEs) is a silent leak identical to the one being fixed; testing needs a real Windows box, not CI mocks |
| **`pywin32` `win32security` SDDL** | Same, programmatically | M | Same as above + a new native dependency |
| **Store secrets in DPAPI / Windows Credential Manager** | Move secrets out of files entirely on Windows | L | High — a second credential backend to maintain, test, snapshot, and migrate; diverges the storage model per-OS |
| Accept degradation, warn loudly | Document "on native Windows, credential files are not OS-protected; use WSL2 for multi-user boxes" | S | High as a *product* posture — shipping a build that weakens a security guarantee, even documented, invites misuse |

**Verdict.** A correct port must replace `chmod`-based enforcement with an ACL
seam applied at every secret-writing site; a leak here is invisible until
exploited. Cost: **M** minimum, **must** be validated on real Windows (CI cannot
prove NTFS ACL semantics with mocks). This is the hardest mechanism to get right
and the most costly to get wrong.

---

### 3. Symlinks — junction/copy vs `symlink`

**As-built (POSIX).** Symlinks are *created* in exactly three places, all wiring
the built SPA `web/dist` into the served `static/dist`:

- `frontend.py:71` (`ensure_dev_dist_symlink`), `frontend.py:98`
  (`_propagate_dist` post-build), `resilience/fixes.py:137` (doctor auto-fix).

All three already degrade on failure (log + return), but with **no copy fallback** —
a failed link means a missing/stale dashboard. Many `is_symlink()`/`resolve()`
*inspection* sites exist (portability, seed, snapshot, doctor `:555`) but those are
safe reads.

**Why it matters.** On Windows, `Path.symlink_to` requires Developer Mode or admin
(`SeCreateSymbolicLinkPrivilege`) and otherwise raises `WinError 1314`. Result: the
dashboard SPA never links and the UI is broken on a default Windows install.

**Windows options.**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **Directory junction** (`mklink /J`, or `_winapi.CreateJunction`) | Junctions need no special privilege and behave like a dir symlink for reads | S | Low — only 3 create sites; junctions cover the dir case cleanly |
| **Copy fallback** | If link/junction fails, copy `web/dist` → `static/dist` | S | Low functionally; the known cost is a stale copy shadowing a rebuild (the exact hazard `resilience/doctor.py:555` already watches for) — must invalidate on build |
| Require Developer Mode | Document it as a prerequisite | S | Med — a hostile first-run experience; many users cannot enable Developer Mode on managed machines |

**Verdict.** The cheapest mechanism to port. A junction-or-copy fallback at 3 sites
is **S**. Not a blocker on its own.

---

### 4. Terminal / PTY — ConPTY/pywinpty vs disabling the terminal page

**As-built (POSIX).** The dashboard terminal panel allocates a real PTY with the
stdlib `pty` module: `dashboard/handlers/terminal.py:273` (`pty.openpty()`),
window-size via `fcntl.ioctl(TIOCSWINSZ)` `:281`/`:465`, shell spawn `:334`.
Crucially the module *imports* `fcntl`, `pty`, `termios` at load
(`terminal.py:4-12`) — all POSIX-only — so on Windows the handler fails at
**import**, regardless of the `dashboard.terminal.enabled` flag (`:99-115`,
default `True`). There is no `sys.platform` guard and no third-party PTY dependency
(`pexpect`/`ptyprocess`/`pywinpty` are absent from `pyproject.toml`; confirmed only
in planning docs).

**Windows options.**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **Disable the terminal page on Windows** | Guard the imports + feature flag so the page is absent, not broken | S | Low — but removes a feature; honest and cheap |
| **`pywinpty` (ConPTY)** | A real Windows PTY via the ConPTY API | M | Med — new native dependency (wheels exist), a whole second PTY read/write/resize path parallel to the POSIX one, and ConPTY resize/EOF semantics differ |
| **`winpty` (pre-ConPTY)** | Legacy PTY shim | M | High — deprecated, worse than ConPTY; not worth it |

**Verdict.** Two honest choices: **disable** (S, lose the feature) or **pywinpty**
(M, keep it). The import-time crash means "do nothing" is not an option — the
handler must at minimum be import-guarded. Recommend **disable** unless the terminal
page is specifically demanded, since it duplicates capability a native shell gives.

---

### 5. Background service — Windows Service / Task Scheduler vs launchd/systemd

**As-built (POSIX).** `service/common.py:63-74` dispatches on `current_platform()`
to `SYSTEMD` (Linux, `service/linux.py`, unit at `/etc/systemd/system/`) or
`LAUNCHD` (macOS, `service/macos.py`, plist in `~/Library/LaunchAgents/`), else
`UNSUPPORTED`. The `Platform` enum has no Windows member. Windows is already an
**explicit, graceful** non-support: `controller.py:15-21` prints "service
management is only supported on Linux (systemd) and macOS (launchd)… run
`personalclaw gateway` directly or wrap it in tmux/screen," returning exit code 2.
The doctor's WSL advisory (`cli_doctor.py:381-400`) already points Windows users at
Task Scheduler.

**Why it matters (less than the others).** This is the one mechanism the code
*already* degrades cleanly for. A Windows user can run the gateway foreground today;
the only missing thing is boot-persistence.

**Windows options.**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **Task Scheduler** (`schtasks /create` on login, or a PowerShell `Register-ScheduledTask`) | Start the gateway at user logon | S–M | Low–Med — simplest boot-persistence; no service account, runs in the user session (which is what a personal agent wants) |
| **Windows Service** (`sc.exe create`, or `pywin32` `win32serviceutil`) | A true background service, runs without login | M | Med–High — service accounts, session-0 isolation (a Service cannot show UI / open a browser), and the gateway's browser-open + user-session assumptions fight it |
| Keep UNSUPPORTED | Foreground / user-wrapped only | — | Already shipped; honest |

**Verdict.** If a native port happens, **Task Scheduler** (S–M) fits the
personal-agent model far better than a Windows Service, which fights the gateway's
user-session and browser-open assumptions. Add a `Platform.WINDOWS` branch in
`common.py` + `controller.py` + a new `service/windows.py`. Low architectural risk;
the seam already exists. Not a blocker — the current UNSUPPORTED message is fine
until a port is decided.

---

### 6. Sandbox / resource limits — the degradation policy (the hard one)

**As-built (POSIX).** Three layers, all POSIX:

- **OS path sandbox** — `sandbox.py:596-618` `detect_backend()`: Linux user
  namespaces (`_probe_unshare`, `:114-150`, bind-mounts empty dirs over credential
  paths) or macOS Seatbelt (`_probe_sandbox_exec`, `:153-205`), else `"none"` with
  a warning `:667` ("No OS-level sandbox available — app-level checks only").
- **Resource ceilings** — `_spawn_exec_shim.py` delivers `setrlimit` post-exec;
  `resource` is imported guarded (`:54-57`) and `_apply_limits` no-ops if absent
  (`:60-85`). The docstring already states the load-bearing degradation contract:
  *"on a platform without the `resource` module (Windows) the shim applies no
  limits and simply `execv`s the target."* OOM bias via `/proc/self/oom_score_adj`
  is Linux-only (`:106-117`).
- **Provider registry** — `sandbox_providers/` with a `none` provider
  (`none.py`, `available()` always `True`) that adds no isolation; `resolve_provider`
  falls back to it so an unavailable backend never blocks a spawn.

**Why it matters.** On native Windows every one of these resolves to "none": no
path isolation, no CPU/memory/FD ceilings, no OOM protection. An agent-run `bash`
(or `cmd`/`pwsh`) command would execute with the gateway's full ambient authority
and no resource cap. `apps/permissions.py:34` names cgroups/nftables/seccomp only
as a *future* layer — none exists today even on Linux beyond namespaces.

**The sandbox-degradation policy question, stated plainly.** *Is "no OS-level
sandbox and no resource limits" an acceptable posture for a native-Windows build
that runs agent-authored commands with real filesystem and network access?*

The honest answer for this project is **no, not silently, and not as the default
Windows story** — which is a primary reason the overall recommendation is no-go.
Rungs 1–2 do not have this problem: Docker Desktop runs inside a Linux VM
(namespace sandbox available), and WSL2 is real Linux (namespaces + rlimits). A
native Windows port is the *only* configuration that would strip both isolation and
ceilings, and it would do so for exactly the surface — agent command execution —
where they matter most.

**Windows options for isolation (if a port were pursued anyway):**

| Option | What it is | Effort | Risk |
|---|---|---|---|
| **Job Object resource limits** (`JOB_OBJECT_LIMIT_PROCESS_MEMORY` / active-process / CPU-rate) | The Windows analogue of `setrlimit`, and it composes with the §1 kill-on-close job | M | Med — ties to the §1 Job Object work; covers memory/CPU/process-count but not FD limits the same way |
| **Windows containers** (as the installable `sandbox` app tier) | Real isolation via HCS/containerd on Windows | L | High — Windows containers need Docker/containerd anyway, at which point rung-1 Docker Desktop already wins |
| **AppContainer / restricted token** | Reduced-privilege token for the child | L | High — complex, poorly-trodden from Python, easy to get subtly wrong |
| **Accept "none", document loudly, gate command execution off by default on native Windows** | Ship with agent bash execution disabled unless the user opts in with an explicit acknowledgement | S–M | The most honest degradation, but it means the native build is a *reduced* PersonalClaw — reinforcing that WSL2 is the real story |

**Verdict.** This is the decisive mechanism. Resource ceilings can be recovered
with Job Objects (M, coupled to §1). OS-level path isolation cannot be recovered
cheaply — the moment you reach for Windows containers you have re-derived rung-1
Docker Desktop. The defensible native posture is "isolation = none, ceilings via
Job Objects, agent command execution off-by-default with a loud acknowledgement" —
and that is a materially weaker product than the Linux/WSL2/Docker paths, which is
itself the argument against shipping it.

---

## Costed roll-up

| # | Mechanism | Cheapest honest option | Effort | Blocker? |
|---|---|---|---|---|
| 1 | Process reaping | Job Objects (kill-on-close) | M | Yes — lifecycle guarantee |
| 2 | File permissions | `icacls`/ACL seam at every secret site | M | **Yes — silent security guarantee loss** |
| 3 | Symlinks | Junction-or-copy fallback (3 sites) | S | No |
| 4 | Terminal/PTY | Import-guard + disable page (or pywinpty for M) | S | Import-crash must be fixed either way |
| 5 | Background service | `Platform.WINDOWS` → Task Scheduler | S–M | No — already degrades cleanly |
| 6 | Sandbox / rlimits | Job Object limits + "isolation=none" documented, exec off-by-default | M | **Yes — posture question** |

Aggregate for a *correct* native port: roughly **M + M + S + S + (S–M) + M**, i.e.
several weeks of focused work concentrated in the two highest-risk, hardest-to-test
areas (permissions §2 and sandbox §6), both of which **require a real Windows host
to validate** — CI mocks cannot prove NTFS ACL semantics or the absence of
isolation. And the best-case outcome is a build that is *weaker* than the WSL2 path
it would compete with.

## Go / no-go

**No-go.** Do not build a native-Windows port now.

Rationale: the two blocking mechanisms (§2 permissions, §6 sandbox) are the ones
where a port would silently weaken guarantees the rest of the system depends on,
they are the most expensive to validate (real Windows box, not CI), and the
finished port would still be a reduced PersonalClaw next to WSL2 — which runs the
real code with every guarantee intact. Spending weeks to ship a weaker build than
the one Windows users already have is a poor trade.

## Demand-evidence criteria (what would flip this to go)

Revisit when **all** of these hold — the point is to spend the effort only when
real users are blocked, not on speculation:

- **Issue signal:** ≥ 10 distinct GitHub issues explicitly requesting *native*
  Windows (not WSL2, not Docker) and stating why WSL2/Docker Desktop does not work
  for them (e.g. corporate policy forbids WSL2, no virtualization allowed).
- **Community signal:** the native-Windows request is a top-5 recurring theme in
  community channels over a sustained period, not a one-off spike.
- **Blocker specificity:** the requests converge on a concrete environment WSL2 and
  Docker Desktop both genuinely cannot serve — a locked-down machine where neither
  a Linux subsystem nor a hypervisor is permitted. If WSL2/Docker *can* serve them,
  the answer is documentation, not a port.
- **Posture pre-decision:** before any code, the owner ratifies the §6
  sandbox-degradation policy (isolation=none on native Windows, agent command
  execution off-by-default with an explicit acknowledgement). Without that
  ratification the port cannot ship regardless of engineering effort.

If those cross the threshold, the port sequences as: §4 import-guard first (unblocks
"run and observe"), then §3 (cheap, unblocks the UI), then §2 + §1 + §6 together
(the security/lifecycle core, validated on a real Windows host), then §5.

## Rung-2.5 courtesy (independent of the go/no-go)

One defensive change is worth doing whether or not a native port is ever built:
guard the **unguarded** `import resource` at `gateway.py:3471` (method-local, runs
at gateway boot) the way `_spawn_exec_shim.py:54-57` already guards its own import.
Today a native-Windows process cannot even reach the "see what breaks" stage — it
`ImportError`s at boot. This is a one-line-class hardening, not a port, and it makes
future rung-3 experimentation possible without shipping any Windows feature. It is
noted here as a finding; implementing it is a separate, non-audit change and is
**not** part of this rung's soul-guarded scope.
