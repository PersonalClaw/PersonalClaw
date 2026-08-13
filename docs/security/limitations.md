# Security Limitations — What We Do Not (Yet) Enforce

PersonalClaw's strongest claim is that its controls are *enforced at the point of
execution*, not merely requested in a prompt. Honesty requires naming the places
where that is not (yet) literally true. These are deliberate, documented
tradeoffs — not oversights — and each is stated here in the same terms the
internal architecture uses, without softening.

This page is referenced from the public
[threat model](threat-model.md) and from `SECURITY.md`. Verified against the
codebase at the commit that introduced this file.

## 1. ACP agents under auto-approve (YOLO) rely on system-prompt framing, not rails

Task modes (`agent` / `ask` / `plan` / `build`) decide *which tools may run*. For
the **native runtime**, this gate is hard-enforced: `task_modes.py` is enforced
in `_guard_and_invoke` **before approval is consulted, so a Trust/YOLO
auto-approve can never bypass a task-mode restriction**
(`src/personalclaw/task_modes.py`).

For **ACP agents** (external CLI agents driven over the Agent Client Protocol),
the same module is applied in the dashboard's permission handler as
"belt-and-suspenders for ACP runtimes that gate via their own protocol path"
(`task_modes.py`). But an ACP agent running under YOLO ultimately gates through
its own protocol path, and the architecture states the tradeoff plainly:

> Task-mode tool-gating postures are hard-enforced at the permission prompt for
> the native runtime; ACP agents under YOLO rely on system-prompt framing (a
> documented tradeoff — `task_modes.py`).
> — [`docs/architecture/security.md`](../architecture/security.md#trust--yolo-state-trust_modepy)

**What this means for you:** if you enable auto-approve (YOLO) *and* run an
external ACP agent, that agent's tool use is bounded by prompt framing rather than
by the same hard rail the native runtime enforces. Running a trusted native agent,
or leaving approval prompts on, keeps the hard rail in force.

## 2. The app `network` permission is declaration-only

An app manifest declares a permission scope (`api` / `events` / `mcpTools` /
`storage` / `network` / `memory` / `cron`). Most of these are enforced
server-side by the gateway. **`network` is not**, by design:

> `can_use_network` — **DECLARATION-ONLY (unenforced by design)**, and the consent
> surface says so rather than implying otherwise. There is no per-app egress
> chokepoint to enforce at: an app's provider code is imported **in-process** by the
> gateway, so its own `httpx`/`requests` calls are the gateway's egress, and an app
> with a backend owns a separate OS process with its own network stack … So the flag
> is DISCLOSURE, and the Store discloses it as such … Treat `network: true` as an
> honest declaration, not a boundary.
> — `src/personalclaw/apps/permissions.py`

The consent surface states the non-enforcement outright. The Store shows the
network claim **outside** the list of permissions the gateway enforces, labelled
advisory, with the text *"PersonalClaw does not confine an app's outbound traffic:
this app's code can reach the network either way. The declaration is disclosure, not
containment."* It is shown whether or not the app declares `network`, so an app that
declares `network: false` is not presented as one the platform has blocked. An app's
*gateway-mediated* reach is separately bounded by its `api` permission; what is
**not** bounded is the app's own outbound traffic.

**What this means for you:** treat an installed app's `network: true` as a stated
intent you are consenting to, the same way you would trust any program you choose
to run — not as a sandbox that prevents the app from talking to the network. The
supply-chain scanner (quarantine → scan → consent → install, with `dangerous`
terminal) is the control that vets what you install; the `network` flag is
disclosure, not containment.

## Why these are listed, not fixed

Per the project's lifecycle discipline, a control *gap* discovered while writing
documentation is recorded as a candidate for the security-hardening track — never
patched inline in a docs change. Both items above have a named future direction
(OS-level app isolation for #2; extending the hard rail to ACP protocol paths for
#1). This page will shrink as those land.
