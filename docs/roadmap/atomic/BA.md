# BROWSE-AUTOMATION — atomic plans

**Source plan:** [`BROWSE-AUTOMATION`](../plans/BROWSE-AUTOMATION.md)  
**Code:** `BA`  
**Source status:** proposed

BROWSE-AUTOMATION is PROPOSED with NO execution log — nothing has shipped, so all 9 atoms are todo. The plan builds one app-contributed `browse` action provider for token-frugal autonomous web interaction. It decomposes cleanly along its own session/task IDs: original S1-S4 (extraction → CDP+safety+egress → browse loop+ActionProvider → credential handoff), the 2026-07-26 amendment (A1 folds into extraction, A2 live-mirror+auth_needed, A3 scheduled-actuator), and the 2026-07-29 amendment (B1-B4+VB user-browser target via a loopback extension). Hard cross-plan gates: the whole thing is Wave-2, gated on AUTONOMY-GUARDRAILS (egress chokepoint, safety profiles, budget/model-call chokepoint, needs-input gate, earned-autonomy ladder, fail-closed ApprovalGate) and WORKFLOWS-V2-AUTOMATION-SUBSTRATE (action-node dispatch); the actuator consumes WATCHED-SOURCES' escalation seam; the extension connector consumes COMPANION-APPS' device-session/pairing (§C1/C2 — must not fork it).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `BA-1` | ✅ | Text-only page extraction + compression module (stable ElementRefs, screenshot-as-path, sentinel parser) | — | Standalone browse/extraction.py + browse/compress.py return a structured page representation (text <=4000 chars, Links DSL, Forms DSL) from fixture HTML; a re-snapshot after DOM mutation preserves ElementRefs for unchanged elements; ref-based sentinels (CLICK <ref> / TYPE <ref>(value)) parse correctly; a 100K-token DOM enters context as <1K tokens + a screenshot path with no base64 in any rendered prompt (regression-tested) — SC 1, task A1 |
| `BA-2` | ⬜ | CDP browser integration + per-page safety-script injection + BROWSE egress policy + redirect re-eval | `BA-1`, `EXT:AUTONOMY-GUARDRAILS:net/guard egress chokepoint + HEADLESS safety profile` | Every CDP navigation is pre-flighted through net/guard.py:evaluate against a new BROWSE profile in net/policy.py; a denied host is blocked before Page.navigate fires and the block is recorded in the SEL; the injected safety script makes a test page's fetch()/media.play()/navigator.bluetooth throw or return blocked; client-side redirects re-evaluated per Page.frameNavigated — SC 4, SC 5 |
| `BA-3` | ✅ | Browse loop + BrowseActionProvider contract + provider-fidelity wiring (ALLOWED_HOOK_PROVIDERS, fencing, loop guards, budget) | `BA-2`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:action-node dispatch contract`, `EXT:AUTONOMY-GUARDRAILS:model-call budget chokepoint (SpendMeter) + denylist dispatch seams` | BrowseActionProvider implements the ActionProvider ABC and 'browse' is added to ALLOWED_HOOK_PROVIDERS (src/personalclaw/validation.py); a workflow action node invokes browse by name and the definition is accepted and dispatched through the standard seams inheriting denylist/budget/profile; a multi-step task completes within max_steps (default 20) with each page fenced via fence_untrusted; SUBMIT triggers outcome verification; budget/step exhaustion parks cleanly into needs-input with notes preserved — SC 2, SC 6, SC 8 |
| `BA-4` | ⬜ | Browser-session credential handoff (persistent per-site profiles, request_login needs-input flow, session-validity check) | `BA-3`, `EXT:AUTONOMY-GUARDRAILS:needs-input gate pattern` | Form fill + submit on a real login page via credential handoff works end-to-end: the run parks on needs-input, the user authenticates in a headful window, the run resumes with the authenticated session, and a subsequent run reuses the persisted per-site profile without re-auth; the credentials-never-transit-the-agent invariant holds (LLM never sees password/2FA/token) — SC 3 |
| `BA-5` | ⬜ | Live browse mirror panel + kill switch + auth_needed first-class state | `BA-3`, `BA-4` | A user watches an unattended browse live in an FE mirror panel (browse_step WS broadcast per step: url + last action + screenshot path) and stops it in one click via the adjacent kill switch; an expired session sets .meta.json auth_state=expired, raises a persistent banner + a needs_input inbox item, and produces zero failed ticks; the per-site profile-encryption key lives in the credential store (BROWSE_PROFILE_KEY_<slug>), never in the profile dir — task A2 |
| `BA-6` | ⬜ | Scheduled-actuator: persisted idempotent browse plans + WATCHED-SOURCES escalation tick + rung caps | `BA-3`, `BA-4`, `EXT:AUTONOMY-GUARDRAILS:earned-autonomy ladder rung registration`, `EXT:WATCHED-SOURCES:escalation chain seam` | Persisted plans (browse/plans/<id>.json {goal,kind,cursor,notes}) execute as idempotent one-tick runs — killing the gateway mid-flow loses <=1 step and re-firing the same tick is a no-op at the same cursor; the WATCHED-SOURCES escalation chain falls through from web_fetch to exactly one browse tick that returns meaningful content for a JS-rendered page (SC 7); read-only plans graduate per the AUTONOMY-GUARDRAILS ladder while any SUBMIT-bearing plan registers floor=draft_only and cannot run unattended until promoted — task A3 |
| `BA-7` | ⬜ | user_browser execution-target selector on the browse action config (default gateway, no silent fallback, unattended refusal) | `BA-3`, `EXT:AUTONOMY-GUARDRAILS:earned-autonomy ladder rung floor (never-unattended)` | browse action config gains target: 'gateway'\|'user_browser' (default 'gateway', byte-identical existing behavior under regression test); an unconnected user_browser task returns outcome='skip' with a typed actionable reason and NEVER silently falls back to the gateway profile; a scheduled/unattended/cron plan naming user_browser is refused at registration time with a typed error, not at run time — task B1 |
| `BA-8` | ⬜ | Browser extension connector: loopback-only typed local contract, paired via COMPANION-APPS device-session machinery, shipped as an app bundle | `BA-7`, `EXT:COMPANION-APPS:device-session + unified pairing (§C1/C2)` | The extension connects to the gateway over loopback only (LOOPBACK_INTERNAL rail, no new listening surface) and is listed as a connected device using the shipped COMPANION-APPS device-session/pairing (§C1/C2 — consumed, not forked); it speaks a typed local contract (navigate/read-outline/click/type/close); ships as an app bundle in the apps repo with zero browser-vendor strings in core — task B2 |
| `BA-9` | ⬜ | Per-task grant flow (fail-closed ApprovalGate, task-named tab, close-to-kill, SEL audit) + security posture docs + as-a-user validation | `BA-8`, `EXT:AUTONOMY-GUARDRAILS:fail-closed ApprovalGate + rung cap` | A user_browser task cannot start without a fresh scope-naming grant routed through the fail-closed ApprovalGate (300s timeout -> REJECT); work runs in a task-named tab group the user can watch and take over; closing the tab is observed as a hard stop that ends the run within one step; grant/navigate/action/revoke are SEL-audited (browser_grant/browser_revoked); docs/architecture/security.md states the per-task grant model, the no-credential-access invariant, and the honest IP-pinning bypass, with no surface describing anti-bot/CAPTCHA avoidance as a capability; validated end-to-end as a user (grant, watch, take-over, close-to-kill, unattended refusal, unconnected skip) — tasks B3, B4, VB |

## Atom scopes

### `BA-1` — Text-only page extraction + compression module (stable ElementRefs, screenshot-as-path, sentinel parser)

**Status:** todo

§1 Text-Only Page Representation (1.1 extraction pipeline, 1.2 Links DSL, 1.3 Forms DSL); §2 Sentinel Action Vocabulary; Session 1; Amendment 2026-07-26 (a) Context compression layer + task A1

**Done when:** Standalone browse/extraction.py + browse/compress.py return a structured page representation (text <=4000 chars, Links DSL, Forms DSL) from fixture HTML; a re-snapshot after DOM mutation preserves ElementRefs for unchanged elements; ref-based sentinels (CLICK <ref> / TYPE <ref>(value)) parse correctly; a 100K-token DOM enters context as <1K tokens + a screenshot path with no base64 in any rendered prompt (regression-tested) — SC 1, task A1

### `BA-2` — CDP browser integration + per-page safety-script injection + BROWSE egress policy + redirect re-eval

**Status:** todo

§3 Per-Page Safety Script Injection; §4 Stealth Stack; §6 Egress Chokepoint Integration (6.1 BROWSE policy, 6.2 redirect re-eval, 6.3 headless bypass gap); Session 2

**Done when:** Every CDP navigation is pre-flighted through net/guard.py:evaluate against a new BROWSE profile in net/policy.py; a denied host is blocked before Page.navigate fires and the block is recorded in the SEL; the injected safety script makes a test page's fetch()/media.play()/navigator.bluetooth throw or return blocked; client-side redirects re-evaluated per Page.frameNavigated — SC 4, SC 5

### `BA-3` — Browse loop + BrowseActionProvider contract + provider-fidelity wiring (ALLOWED_HOOK_PROVIDERS, fencing, loop guards, budget)

**Status:** todo

§7 Browse Loop Architecture (7.1 form-submission verification, 7.2 loop guards); §8.3 Workflow action-node dispatch; §9 Provider-Fidelity Wiring; Session 3

**Done when:** BrowseActionProvider implements the ActionProvider ABC and 'browse' is added to ALLOWED_HOOK_PROVIDERS (src/personalclaw/validation.py); a workflow action node invokes browse by name and the definition is accepted and dispatched through the standard seams inheriting denylist/budget/profile; a multi-step task completes within max_steps (default 20) with each page fenced via fence_untrusted; SUBMIT triggers outcome verification; budget/step exhaustion parks cleanly into needs-input with notes preserved — SC 2, SC 6, SC 8

### `BA-4` — Browser-session credential handoff (persistent per-site profiles, request_login needs-input flow, session-validity check)

**Status:** todo

§5 Browser-Session Credential Handoff (5.1 persistent profile per site, 5.2 request_login action, 5.3 session validity heuristic); Session 4 credential slice

**Done when:** Form fill + submit on a real login page via credential handoff works end-to-end: the run parks on needs-input, the user authenticates in a headful window, the run resumes with the authenticated session, and a subsequent run reuses the persisted per-site profile without re-auth; the credentials-never-transit-the-agent invariant holds (LLM never sees password/2FA/token) — SC 3

### `BA-5` — Live browse mirror panel + kill switch + auth_needed first-class state

**Status:** todo

Amendment 2026-07-26 (b) Live browse mirror + (c) auth handoff honestly scoped; task A2

**Done when:** A user watches an unattended browse live in an FE mirror panel (browse_step WS broadcast per step: url + last action + screenshot path) and stops it in one click via the adjacent kill switch; an expired session sets .meta.json auth_state=expired, raises a persistent banner + a needs_input inbox item, and produces zero failed ticks; the per-site profile-encryption key lives in the credential store (BROWSE_PROFILE_KEY_<slug>), never in the profile dir — task A2

### `BA-6` — Scheduled-actuator: persisted idempotent browse plans + WATCHED-SOURCES escalation tick + rung caps

**Status:** todo

Amendment 2026-07-26 (d) Scheduled-actuator pattern; §8.1 WATCHED-SOURCES headless-fetch escalation; §8.2 deep-research template consumption; task A3

**Done when:** Persisted plans (browse/plans/<id>.json {goal,kind,cursor,notes}) execute as idempotent one-tick runs — killing the gateway mid-flow loses <=1 step and re-firing the same tick is a no-op at the same cursor; the WATCHED-SOURCES escalation chain falls through from web_fetch to exactly one browse tick that returns meaningful content for a JS-rendered page (SC 7); read-only plans graduate per the AUTONOMY-GUARDRAILS ladder while any SUBMIT-bearing plan registers floor=draft_only and cannot run unattended until promoted — task A3

### `BA-7` — user_browser execution-target selector on the browse action config (default gateway, no silent fallback, unattended refusal)

**Status:** todo

Amendment 2026-07-29 (a) A second execution target behind one selector + (d) rung cap interactive-only; task B1

**Done when:** browse action config gains target: 'gateway'|'user_browser' (default 'gateway', byte-identical existing behavior under regression test); an unconnected user_browser task returns outcome='skip' with a typed actionable reason and NEVER silently falls back to the gateway profile; a scheduled/unattended/cron plan naming user_browser is refused at registration time with a typed error, not at run time — task B1

### `BA-8` — Browser extension connector: loopback-only typed local contract, paired via COMPANION-APPS device-session machinery, shipped as an app bundle

**Status:** todo

Amendment 2026-07-29 (b) The connector is a browser extension talking to the local gateway + (e) egress still applies; task B2

**Done when:** The extension connects to the gateway over loopback only (LOOPBACK_INTERNAL rail, no new listening surface) and is listed as a connected device using the shipped COMPANION-APPS device-session/pairing (§C1/C2 — consumed, not forked); it speaks a typed local contract (navigate/read-outline/click/type/close); ships as an app bundle in the apps repo with zero browser-vendor strings in core — task B2

### `BA-9` — Per-task grant flow (fail-closed ApprovalGate, task-named tab, close-to-kill, SEL audit) + security posture docs + as-a-user validation

**Status:** todo

Amendment 2026-07-29 (c) Per-task authorization is the security control + (e) egress + B4 docs/posture; tasks B3, B4, VB

**Done when:** A user_browser task cannot start without a fresh scope-naming grant routed through the fail-closed ApprovalGate (300s timeout -> REJECT); work runs in a task-named tab group the user can watch and take over; closing the tab is observed as a hard stop that ends the run within one step; grant/navigate/action/revoke are SEL-audited (browser_grant/browser_revoked); docs/architecture/security.md states the per-task grant model, the no-credential-access invariant, and the honest IP-pinning bypass, with no surface describing anti-bot/CAPTCHA avoidance as a capability; validated end-to-end as a user (grant, watch, take-over, close-to-kill, unattended refusal, unconnected skip) — tasks B3, B4, VB

