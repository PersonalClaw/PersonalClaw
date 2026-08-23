# DESKTOP-COMPUTER-USE — atomic plans

**Source plan:** [`DESKTOP-COMPUTER-USE`](../plans/DESKTOP-COMPUTER-USE.md)  
**Code:** `DCU`  
**Source status:** todo

Delivers native desktop GUI automation that lets the agent read and drive the operator's own desktop applications through the OS accessibility layer — enumerate on-screen apps, walk a window into an indexed accessibility tree, then act on an element by index (press, type, set value, scroll, named action), with a coordinate path reserved for canvas/custom-drawn UI. macOS ships first (AX API) with honest typed refusals on Windows/Linux until their drivers land. The entire safety floor — an out-of-band keystone enable, element-index default so the real cursor never moves by accident, a target-app allowlist, SEL audit, and the approval ladder — lands before any capability is usable.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DCU-1` | ✅ | Keystone out-of-band enable-state | — | With the enable file absent, every computer-use tool refuses with a WHAT/WHY/FIX message pointing to the out-of-band enable step; no tool or config path can flip the state. |
| `DCU-2` | ⬜ | Target policy, input-target screen, SEL-audit gate | `EXT:AUTONOMY-GUARDRAILS:SEL audit + safety profile` | Driving a non-allowlisted app refuses; typing or set-value into a secure/password field refuses; every attempt, allowed or refused, produces a SEL record. |
| `DCU-3` | ⬜ | macOS accessibility driver (indexed AX tree) | `DCU-1`, `EXT:SECURITY-HARDENING:OS-input-layer class-B/S review` | With the enable on, snapshotting a TextEdit window then AXPress-ing a button by index and typing into a field succeeds without the pointer moving; a stale index (past TTL or changed fingerprint) refuses and forces a re-snapshot. |
| `DCU-4` | ⬜ | Thin stdio shim, in-gateway dispatch, tool surface + ceilinged spawn | `DCU-1`, `DCU-2`, `DCU-3`, `EXT:PLATFORM-HARDENING-FLOORS:ceilinged driver subprocess`, `EXT:AUTONOMY-GUARDRAILS:approval ladder in dispatch` | The agent lists apps and clicks an element by index end-to-end; the shim holds no OS handles; the driver spawn carries the resource ceiling; V1 holds and is recorded — a real app driven by element index with the pointer staying put, a secure-field refusal, SEL records present, and an absent enable file blocking everything. |
| `DCU-5` | ⬜ | Approval-ladder integration for desktop drive | `DCU-4`, `EXT:AUTONOMY-GUARDRAILS:approval ladder + unattended-profile grant` | An unattended run without the grant refuses and notifies; an interactive run prompts; validated. |
| `DCU-6` | ⬜ | Windows/Linux honest typed refusals | `DCU-4` | On non-macOS, every computer-use tool returns a typed refusal naming the platform; no silent no-op; validated. |
| `DCU-7` | ⬜ | Human-facing live-view + cursor-motion overlay | `DCU-4` | The views render; neither adds any agent capability — asserted by confirming the tool surface is unchanged with the views on; validated. |

## Atom scopes

### `DCU-1` — Keystone out-of-band enable-state

**Status:** done

New computer_use package with enable_state.py (§3.1): an out-of-band enable file that the agent process can neither read nor write (not a config field the agent can PATCH), plus enable_state.is_enabled() as the first check in the dispatch chain. No prompt, tool, or chat instruction can flip it — it is the single hardest gate and the whole capability is OFF until the operator turns it on out-of-band.

**Done when:** With the enable file absent, every computer-use tool refuses with a WHAT/WHY/FIX message pointing to the out-of-band enable step; no tool or config path can flip the state.

**DONE.** `src/personalclaw/computer_use/enable_state.py` owns the keystone, built on the
`guardrails/ceiling.py` trust model rather than a new one: the enable document lives at
`$PERSONALCLAW_HOME/governance/computer_use.enable.json` (the directory the ceiling already owns,
so the two share one trust root and one denylist entry), overridable to an absolute path by
`PERSONALCLAW_COMPUTER_USE_ENABLE_FILE` so an operator can put it on a root-owned `0444` file
outside the agent's home. It must contain exactly `{"version": 1, "enabled": true, "apps": ["TextEdit"]}`. A
touch-a-marker file was rejected on measurement: as a marker, an empty file and a half-flushed
write both arm the machine — the `empty-file` and `half-flushed-write` cases in the fail-closed
corpus are that decision made testable. Fourteen malformed shapes (non-JSON, wrong root type,
unknown version, unknown key, `"true"`, `1`, `false`, missing flag) all resolve to OFF, and
`enabled` is compared with `is True` rather than for truthiness. An unenforced key is REFUSED
rather than ignored: `{"enabled": true, "only_apps": ["Mail"]}` means *on, narrowed*, so honouring
the flag while dropping the scope would grant strictly more than was asked.

`require_enabled(tool)` raises `ComputerUseDisabled` carrying a typed `AgentError`
(`ERR_COMPUTER_USE_DISABLED`, registered in `errors.ERROR_CODES`) whose FIX names the resolved
path, the exact bytes, the restart and the env override — never a silent no-op, because a no-op
reads to a model as "the click landed". The state is read once and cached, so neither a tamper nor
a legitimate enable changes the reach of the running process; arming costs a restart the operator
performs themselves. `gateway.py`'s `run()` calls `ensure_computer_use_boot()` immediately after
`ensure_governance_boot()`, SEL-auditing the resolved source + digest + outcome once per run —
that is the live call site and the tamper evidence, and unlike governance it never aborts, because
OFF is the normal state.

⚠️ **The tool population is EMPTY today, and this atom is scoped honestly around that.** `DCU-4`
ships the tool surface, the stdio shim and the in-gateway dispatch chain; `DCU-3` ships the macOS
driver. So "every computer-use tool refuses" is proven over a *fixture* tool that routes through
the real guard, plus two rails that arm the clause for the population that does not exist yet:
rail A asserts by AST that every module-level `computer_*` function under `computer_use/` calls the
guard as its **first** statement (proven to detect — the scanner is run against an unguarded twin
and against a guard-placed-after-the-work variant), and rail B pins every public function/class in
the package, closing rail A's naming gap so a dispatch surface that abandons the convention still
trips something. Both carry an explicit vacuity marker
(`test_the_computer_use_tool_population_is_currently_empty`) that states the size is 0 out loud and
reds the moment `DCU-4` adds the first tool.

No config field was added — a field the agent can PATCH is exactly what §3 floor 1 forbids — and
that is proven three ways: absent from `_EDITABLE_CONFIG`, absent from `AppConfig` entirely, and a
census showing `enable_state.py` is the **only** module in the shipped package that even mentions
the filename or the env var (so no handler, CLI or provider can write it). 42 tests in
`tests/test_computer_use_enable_state.py`; 8 mutations were run and every one reded the intended
rail. One found a real defect: `require_enabled` originally read `state.enabled` directly, so
forcing `is_enabled()` to return True left every refusal test GREEN — two readers of one flag. It
now reads the decision through `is_enabled()`, the one check the plan names, and the same mutation
reds 20 tests.

### `DCU-2` — Target policy, input-target screen, SEL-audit gate

**Status:** todo

policy.py (§3.3-3.4): a self-plus-operator target-app allowlist via check_app, and a secure/password-field and sensitive-text screen via check_input_target run before any type/set-value; plus gate.py where require_computer_use records every attempt to the security event log (records, does not decide).

**Done when:** Driving a non-allowlisted app refuses; typing or set-value into a secure/password field refuses; every attempt, allowed or refused, produces a SEL record.

### `DCU-3` — macOS accessibility driver (indexed AX tree)

**Status:** todo

macOS accessibility driver over ctypes FFI (macos_driver.py, macos_ffi.py, types.py) per §2/§3.2: element walk into an indexed AX tree with TTL + fingerprint; AXPress activation by element index (no pointer involved); type/set-value/scroll/named-action; a located coordinate path that posts to the target process (CGEventPostToPid) for canvas/custom-drawn UI; and the explicit real-cursor global warp behind its own named method and a distinct SEL tool_kind that auto never resolves onto. Element-targeted, non-pointer input is the default and the only thing on by default.

**Done when:** With the enable on, snapshotting a TextEdit window then AXPress-ing a button by index and typing into a field succeeds without the pointer moving; a stale index (past TTL or changed fingerprint) refuses and forces a re-snapshot.

### `DCU-4` — Thin stdio shim, in-gateway dispatch, tool surface + ceilinged spawn

**Status:** todo

The stdio MCP shim (cli.py) that resolves session identity and forwards while holding no OS handles; in-gateway dispatch running the full chain — enable / policy.check_app / index freshness+fingerprint / check_input_target / SEL gate / platform driver / re-snapshot+redact (service.py, §2); the tool surface registered in tools.py (computer_list_apps, computer_snapshot, computer_click, computer_type, computer_set_value, computer_scroll, computer_perform_action) with MCP registration; and the driver running as a ceilinged subprocess so a wedged/looping driver is kernel-bounded (§3.5). Includes end-to-end validation V1.

**Done when:** The agent lists apps and clicks an element by index end-to-end; the shim holds no OS handles; the driver spawn carries the resource ceiling; V1 holds and is recorded — a real app driven by element index with the pointer staying put, a secure-field refusal, SEL records present, and an absent enable file blocking everything.

### `DCU-5` — Approval-ladder integration for desktop drive

**Status:** todo

Wire the approval ladder into policy (§3.4) so an unattended profile may drive the desktop only with a creation-time grant, while interactive sessions receive the approval prompt.

**Done when:** An unattended run without the grant refuses and notifies; an interactive run prompts; validated.

### `DCU-6` — Windows/Linux honest typed refusals

**Status:** todo

Windows (UIA) and Linux (AT-SPI) driver stubs (windows_driver.py, linux_driver.py, §3.6) that return a typed 'not yet on this platform' refusal for every tool — an honest refusal, never a silent no-op or a simulated success. Real Windows/Linux drivers are a deliberate later split, sequenced behind the platform-reach ordering when the operator has those platforms to validate on.

**Done when:** On non-macOS, every computer-use tool returns a typed refusal naming the platform; no silent no-op; validated.

### `DCU-7` — Human-facing live-view + cursor-motion overlay

**Status:** todo

Optional live-view (PiP) mirroring screenshots the model already read, and an optional cursor-motion overlay that draws a fake cursor (invisible to screen capture) so a watching human sees where a click will land, plus a dashboard view (§3.7). Both are observation-only and grant the agent nothing.

**Done when:** The views render; neither adds any agent capability — asserted by confirming the tool surface is unchanged with the views on; validated.

