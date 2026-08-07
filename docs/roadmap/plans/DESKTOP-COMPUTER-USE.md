# DESKTOP-COMPUTER-USE

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DCU.md`](../atomic/DCU.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Desktop Computer Use — Native GUI Automation via the Accessibility Tree

**Status:** DESIGNED — created 2026-08-05. An accessibility-tree-driven GUI automation design
(~13k LOC of new capability). This plan covers **desktop application automation**, which no existing
plan owns: BROWSE-AUTOMATION (#…) is deliberately browser-only.

**Created:** 2026-08-05
**Wave:** 3 — a large, security-heavy capability. **Hard-gated on AUTONOMY-GUARDRAILS** (the safety
floor + a keystone enable) and on PLATFORM-HARDENING-FLOORS §1 (resource ceilings on the driver
subprocess).
**Depends on:** AUTONOMY-GUARDRAILS (safety profiles, SEL audit, egress irrelevant here but the
approval ladder applies), PLATFORM-HARDENING-FLOORS §1 (the driver runs as a ceilinged spawn),
SECURITY-HARDENING (this is a class-B/S capability that touches the OS input layer).

**Scope:** let the agent **read and drive the operator's own desktop applications** through the OS
accessibility layer — enumerate on-screen apps, walk one app's window into an indexed accessibility
tree, then act on an element **by index** (press, type, set value, scroll, named action), with a
coordinate path reserved for canvas/custom-drawn UI. macOS first; Windows/Linux report a typed
refusal until their drivers land. **Soul guardrail:** element-targeted, non-pointer input is the
DEFAULT and the only thing on by default; the real cursor never moves *by accident*; the whole
capability is OFF until the operator turns it on out-of-band in a file the agent can neither read
nor write. This is a power tool with a physical-world blast radius — every default is the safe one.

---

## 1. Why this is a separate plan, and why the accessibility-tree approach

**BROWSE-AUTOMATION is browser-only** (a `browse` action provider over headless Chromium). Driving
the operator's *desktop* apps — Mail, a native IDE, a design tool — is a different substrate: it
speaks to the OS accessibility API, not a DOM. Verified: no PersonalClaw plan mentions accessibility/
AXPress/desktop-automation (grep across all 67 plans, zero hits). So this is genuinely new capability.

**The approach (validated against the open `open-codex-computer-use` MCP contract):** act on an
**indexed accessibility element**, not on screen coordinates. `computer_click` with an `element_index`
performs an `AXPress` — it activates a control with **no pointer involved at all**. This is
dramatically safer and more reliable than screenshot-and-coordinate clicking (which breaks on
theme/resolution/layout shifts and warps the physical cursor). Coordinate clicking + drag exist only
for UI that exposes no addressable element (canvases, maps), and even those post a *located* event to
the target process (`CGEventPostToPid`) rather than moving the cursor. The one path that warps the
real cursor (`click_method: "global"`) must be **named explicitly by the model** — `auto` never
resolves onto it — and emits its own SEL record.

---

## 2. Architecture (thin shim, in-gateway dispatch)

Separate the OS driver from the shim, matching PersonalClaw's MCP-tool conventions:

```
agent (ACP)
  └─ spawns  personalclaw mcp-computer          (stdio MCP server)
       │      THIN SHIM — resolves session identity strictly, forwards, returns text
       ▼
  gateway dispatch (the real work; the shim holds no OS handles)
       1. enable_state.is_enabled()          keystone primary enable (out-of-band file)
       2. policy.check_app                    target policy (self + operator allowlists)
       3. index freshness (TTL) + fingerprint re-walk
       4. policy.check_input_target           secure-field / sensitive-text screen
       5. gate.require_computer_use           SEL audit (records, does not decide)
       6. platform driver (macOS AX / Windows UIA / Linux AT-SPI)
       7. re-snapshot, redact_result
```

**Why the shim is thin and the work is in-gateway:** the OS driver needs the gateway's policy,
enable-state, and SEL — replicating those in a subprocess would fork the security surface. The shim
resolves session identity and forwards; it holds no OS handles. This is the same reasoning as
PersonalClaw's other MCP tools that dispatch back into the gateway.

**The tool surface** (element-index discipline per turn): `computer_list_apps`, `computer_snapshot`
(walk a window → indexed AX tree), `computer_click` (element_index → AXPress; or a named coordinate
method), `computer_type`, `computer_set_value`, `computer_scroll`, `computer_perform_action` (a
named AX action). Every snapshot has a TTL + fingerprint so a stale index can't act on a moved
element.

---

## 3. The security floors (non-negotiable — this touches the OS input layer)

1. **Keystone primary enable, out-of-band.** OFF until the operator writes an enable file the agent
   can neither read nor write (not a config field the agent can PATCH). No prompt, no tool, no chat
   instruction can flip it. This is the single hardest gate and it is first in the dispatch chain.
2. **Element-index default, pointer never moves by accident.** `auto` resolves to `AXPress` whenever
   an element index is present. Coordinate/global paths must be explicitly named by the model and
   each emits a distinct SEL `tool_kind`.
3. **Target policy** (`policy.check_app`): a self-plus-operator allowlist of which apps may be
   driven; everything else refused. Secure/password fields and sensitive text screened
   (`check_input_target`) before any type/set-value.
4. **Every action is SEL-audited** (`gate.require_computer_use` records, doesn't decide) and subject
   to the AUTONOMY-GUARDRAILS approval ladder — an unattended profile cannot drive the desktop unless
   the operator granted it at creation time.
5. **Runs as a ceilinged spawn** (PLATFORM-HARDENING-FLOORS §1 `tool` profile) — a wedged/looping
   driver is bounded by the kernel, not just a userspace timeout.
6. **Honest platform story:** macOS first (AX API). Windows (UIA) and Linux (AT-SPI) report a typed
   refusal until their drivers land — never a silent no-op or a simulated success.
7. **The human-facing views grant nothing:** an optional live-view (PiP) mirrors screenshots the
   model already read; an optional cursor-motion overlay draws a *fake* cursor so a watching human
   sees where a click will land — it is not the real pointer and is invisible to screen capture.

---

## 4. Sessions (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — macOS driver + the safety floor (the whole gate before any capability)

| ID | Task | Files | Done when |
|---|---|---|---|
| DCU1.1 | Keystone enable-state: an out-of-band enable file + `enable_state.is_enabled()`; the agent has no read/write path to it; a config field cannot substitute | `src/personalclaw/computer_use/enable_state.py` (new pkg) | with the file absent, every tool refuses with a WHAT/WHY/FIX pointer to the enable step; no tool/config flips it |
| DCU1.2 | `policy.py` (target-app allowlist + `check_input_target` secure-field screen) + `gate.py` (SEL audit, no decision) | `computer_use/policy.py`, `gate.py` | driving a non-allowlisted app refuses; typing into a secure field refuses; every attempt (allowed or refused) has a SEL record |
| DCU1.3 | macOS AX driver (element walk → indexed tree with TTL+fingerprint; `AXPress`; type/set/scroll/named-action; located coordinate path via `CGEventPostToPid`; the explicit `global` warp behind its own name+SEL) via ctypes FFI | `computer_use/macos_driver.py`, `macos_ffi.py`, `types.py` | on macOS with the enable on, snapshot a TextEdit window, `AXPress` a button by index, type into a field — pointer does not move; a stale index (post-TTL) refuses and forces re-snapshot |
| DCU1.4 | The thin stdio shim + in-gateway dispatch + the tool surface registered; ceilinged spawn (PLATFORM-HARDENING-FLOORS §1 `tool` profile) | `computer_use/cli.py`, `service.py`, `tools.py`, MCP registration | the agent lists apps and clicks an element end-to-end; the shim holds no OS handles; the driver spawn carries the ceiling |
| V1 | Validation (macOS, enable ON): drive a real app by element index; confirm the pointer stays put; confirm a secure-field refusal; confirm SEL records; confirm the enable file being absent blocks everything | — | holds; recorded |

### Session 2 — Human-facing views + Windows/Linux refusal + approval integration

| ID | Task | Files | Done when |
|---|---|---|---|
| DCU2.1 | Optional live-view (PiP) mirroring already-read screenshots + optional cursor-motion overlay (fake cursor, invisible to capture); both grant the agent nothing | `computer_use/overlay*.py`, `render.py`, a dashboard view | the views render; neither adds any agent capability (assert the tool surface is unchanged with views on) |
| DCU2.2 | AUTONOMY-GUARDRAILS approval-ladder integration: an unattended profile drives the desktop only with a creation-time grant; interactive sessions get the approval prompt | `computer_use/policy.py`, guardrails seam | an unattended run without the grant refuses + notifies; an interactive run prompts |
| DCU2.3 | Windows (UIA) + Linux (AT-SPI) typed refusals with a clear "not yet on this platform" message (driver stubs that refuse honestly, not silently) | `computer_use/windows_driver.py`, `linux_driver.py` | on non-macOS, every tool returns a typed refusal naming the platform; no silent no-op |
| V2 | Validation: views on/off change no capability; unattended-without-grant refuses; a non-macOS run refuses honestly | — | holds |

**Windows/Linux real drivers** are a deliberate later split (their own sessions when the owner has
the platforms to validate on) — Session 1–2 ship macOS + honest refusals, per PLATFORM-REACH ordering.

---

## Owner tasks (real world)

- **Decide the enable-file location + shape** — it must be somewhere the agent process genuinely
  cannot reach (not under a config dir the agent can PATCH). This is the load-bearing gate.
- **Confirm macOS-first is acceptable** for v1 with honest Windows/Linux refusals (matches
  PLATFORM-REACH's ordering).

## Risks & open questions

- **This is the highest physical-world blast radius in the whole roadmap.** An agent that can drive
  the desktop can, in principle, do anything the operator can. The keystone enable + element-index
  default + target allowlist + SEL + the approval ladder are all load-bearing — none is optional, and
  the plan is structured so the *entire* safety floor lands in Session 1 before any capability is
  usable.
- **Accessibility APIs are OS-permission-gated** (macOS Accessibility permission). The enable flow
  must guide the operator through granting it and refuse legibly when it's missing — never a hang.
- **Element-index staleness** is the correctness risk: acting on a moved element. The TTL+fingerprint
  re-walk is the mitigation; validate it explicitly (DCU1.3 done-when).

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-05][plan] Created. Routed here (not into BROWSE-AUTOMATION) because that plan is
  browser-only by soul; desktop GUI automation is a distinct substrate (OS accessibility tree, not a
  DOM). The accessibility-tree / element-index approach is adopted over screenshot-and-coordinate
  because it is safer (no pointer motion), more reliable (survives theme/layout shifts), and its
  safety model (keystone enable, element-default, target allowlist) is proven sound.
