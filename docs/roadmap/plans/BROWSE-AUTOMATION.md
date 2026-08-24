# BROWSE-AUTOMATION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/BA.md`](../atomic/BA.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Autonomous Browse/Web-Interaction Action Provider

**Status:** PROPOSED (created 2026-07-13 from research synthesis)
**Created:** 2026-07-13
**Wave:** 2 — after AUTONOMY-GUARDRAILS (safety floor + egress chokepoint) and WORKFLOWS-V2 engine (action-node dispatch). Consumed by deep-research template and WATCHED-SOURCES headless-fetch escalation.
**Depends on:** AUTONOMY-GUARDRAILS (net.fetch egress chokepoint, denylist, safety profiles); WORKFLOWS-V2-AUTOMATION-SUBSTRATE (action-node dispatch contract). Soft dependency on WATCHED-SOURCES (headless-fetch escalation consumes this provider).
**Scope:** one app-contributed action provider (`browse`) delivering token-frugal autonomous web interaction for workflow nodes, deep-research templates, and source-monitoring escalation.

---

## Research Integration (2026-07-13)

- **NEW-19** (Autonomous Browse/Web-Interaction Action Provider: text-only page representation, sentinel action vocabulary, form-fill with outcome verification, per-page safety injection, stealth stack, credential handoff, feeds WATCHED-SOURCES + deep-research) — full plan.
- **agenticseek** (Fosowl) — text-only browser perception loop: markdownify + `is_sentence()` filter + 32KB cap; links/forms as `[name](value)` DSL; plain-text sentinel actions (navigate/GO_BACK/REQUEST_EXIT/FORM_FILLED); per-page safety injection (`inject_safety_script.js` blocking fetch/media/hardware APIs); stealth stack (undetected-chromedriver + selenium_stealth); form-fill with submission-outcome verification; notes-as-only-memory with provenance format; search_history dedup; stuck-detection.
- **open-codex-computer-use** (iFurySt) — non-intrusive desktop interaction doctrine; snapshot-freshness protocol (get_state before acting, indexes invalid across turns); budgeted observations (1200 nodes / 64 depth / 500 chars); action-batching with shared state + halt-on-error; MCP `readOnlyHint`/`destructiveHint` annotations for mechanical safety gating.
- **Security roadmap (egress chokepoint)** — `net/guard.py:evaluate` + `net/policy.py` named profiles; `web/render.py` headless-browser pre-flight evaluate (acknowledged IP-pinning bypass gap); `fence_untrusted` for output fencing.
- **Provider architecture (action providers)** — `ActionProvider` ABC, `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`), three dispatch seams (`hooks.py:494`, `gateway.py:701`, `event_triggers.py:214`), app-contributed provider pattern (`apps/webhook-action` precedent).

---

## Overview

PersonalClaw has two web-facing mechanisms today: `web/fetch.py` (text extraction from a single URL, provenance-gated, STRICT egress policy) and the interactive chrome-devtools MCP (human-steered browser automation via a DevTools Protocol connection). Neither supports **autonomous multi-page browsing** — navigating across pages, filling forms, reading dynamic content, or conducting research runs without human turn-by-turn input.

Verified starting points:
- `action_providers/base.py:ActionProvider` ABC + `action_providers/registry.py:register_action_provider` — the pluggable action provider contract.
- `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) — the frozenset gating hook/trigger creation; a new action provider MUST be added here.
- `net/guard.py:evaluate` + `net/policy.py:EgressPolicy` / `egress_policy_for` — the egress chokepoint every outbound connection must pass through.
- `web/render.py` — existing headless Playwright path with pre-flight `guard.evaluate()` (acknowledged: Playwright bypasses IP pinning, pre-flight is the only defense).
- `sdk/net.py` + `sdk/security.py` — app-facing egress + fencing re-exports.
- `security.py:fence_untrusted` — output fencing for content transiting the agent.
- chrome-devtools MCP (available tools: navigate, click, fill, screenshot, etc.) — the interactive counterpart; this plan builds the *unattended* complement that does not require a human watching.

**Soul guardrail:** this is a *personal* assistant's browser — one user's machine, their own browser profile, their own credentials. No proxy fleet, no headless farm, no multi-tenant session isolation. The agent reads the web as the user would, just faster and more methodically.

---

## 1. Text-Only Page Representation

The core insight from agenticseek + PinchTab: raw DOM is ~100K+ tokens; a markdownified, sentence-filtered page is ~800 tokens — cheap enough to fit inside a workflow node's context without compression.

### 1.1 Extraction pipeline

```
raw HTML → strip script/style/meta/noscript
         → markdownify (preserving links, headings, lists, tables)
         → sentence filter (keep lines: >=4 words with punctuation,
            or contains digits/dates, or is a heading/list-item)
         → images → [IMAGE: alt_text] placeholders
         → hard cap: 4000 chars (~800 tokens)
         → links section (top-N navigable links, deduped)
         → forms section (input DSL)
```

### 1.2 Links DSL

Navigable links rendered as a numbered list:
```
## Links
1. [Sign In](/login)
2. [Documentation](https://docs.example.com/intro)
3. [Pricing](/pricing)
...
```

Link filtering (from agenticseek, refined): reject URLs >100 chars, reject image/font/manifest extensions, reject fragment-only anchors, strip tracking query params (keep only `q=`/`s=`/`search=`/`page=`).

### 1.3 Forms DSL

```
## Forms
[form: "search"]
  [q]("") placeholder="Search..."
  [submit]("Search")

[form: "login"]
  [email]("") type=email required
  [password]("") type=password required
  [remember](unchecked) type=checkbox
  [submit]("Log in")
```

The agent interacts by writing `[field_name](value)` lines + a `SUBMIT` sentinel.

---

## 2. Sentinel Action Vocabulary

The browse action provider accepts a small, fixed vocabulary of actions (no function-calling required from the executing model — works with any model that can write structured text):

| Sentinel | Meaning | Parameters |
|---|---|---|
| `NAVIGATE <url>` | Load a new page | Full URL |
| `CLICK <link_number>` | Follow a numbered link from the Links section | Integer |
| `TYPE [field](value)` | Fill a form field | Field name + value |
| `SUBMIT` | Submit the current form | — |
| `SCROLL down\|up` | Scroll the viewport | Direction |
| `WAIT <seconds>` | Wait for dynamic content (max 10s) | Integer 1-10 |
| `GO_BACK` | Navigate back | — |
| `DONE` | Signal task completion; exit the browse loop | — |
| `NOTES <text>` | Append to the cross-page notes accumulator | Freeform |

Actions are parsed from the LLM's response text by exact sentinel matching (first match wins per line). Unknown lines are ignored. This mirrors agenticseek's proven approach: no JSON schema required from the model, works with weak local models, and the action set is small enough to fit in a system prompt.

---

## 3. Per-Page Safety Script Injection

On every navigation, inject a script that neuters dangerous page-side APIs before the agent reads the DOM:

```javascript
// browse_safety.js — injected via CDP Page.addScriptToEvaluateOnNewDocument
(function() {
  // Block outbound fetch/XHR (page cannot phone home while agent reads)
  window.fetch = () => Promise.reject(new Error('blocked'));
  XMLHttpRequest.prototype.open = () => {};
  XMLHttpRequest.prototype.send = () => {};

  // Block media playback
  HTMLMediaElement.prototype.play = () => Promise.reject(new Error('blocked'));
  HTMLAudioElement.prototype.play = () => Promise.reject(new Error('blocked'));

  // Block hardware access
  delete navigator.serial;
  delete navigator.hid;
  delete navigator.bluetooth;
  delete navigator.usb;

  // Block popups, fullscreen, pointer lock, notifications
  window.open = () => null;
  Element.prototype.requestFullscreen = () => Promise.reject();
  Element.prototype.requestPointerLock = () => {};
  Notification.requestPermission = () => Promise.resolve('denied');

  // Block prompt/confirm (anti-phishing for agent)
  window.prompt = () => null;
  window.confirm = () => false;
})();
```

This is defense-in-depth: it cannot prevent all page misbehavior (service workers, iframes with different origins), but it blocks the most common attack surface a malicious page could use against an automated reader. Complements `fence_untrusted` which fences the *extracted text* before it enters the agent's context.

---

## 4. Stealth Stack

### 4.1 Decision: CDP over undetected-chromedriver

The existing chrome-devtools MCP already maintains a CDP connection to a real Chrome instance. The browse action provider uses the same transport layer (CDP via the DevTools Protocol) rather than introducing a Selenium/WebDriver dependency:

- **Pro CDP:** no separate chromedriver binary to manage; shares the browser instance lifecycle with the interactive MCP; access to `Page.addScriptToEvaluateOnNewDocument` for safety injection; no WebDriver-detectable automation flags (`navigator.webdriver` is clean on a real Chrome instance).
- **Con CDP (acknowledged):** some anti-bot systems detect DevTools attachment via protocol-level signals. For v1, this is accepted — the use case is reading public pages and authenticated sites the user owns, not adversarial scraping.

### 4.2 Anti-detection baseline

- Launch with `--disable-blink-features=AutomationControlled`
- Randomized viewport size (within common ranges)
- Real user-agent from the installed Chrome version
- Randomized inter-action delays (0.5-2.0s) to avoid timing-based detection
- No `--headless` flag in the persistent profile (uses a real browser window, hidden or minimized for unattended runs; headful for credential handoff)

### 4.3 Escalation path

If anti-detection becomes insufficient for specific sites, the architecture supports swapping the browser backend to `undetected-chromedriver` or `playwright-stealth` as a per-site configuration — but this is deferred to a future session. The action provider's page-reading layer is transport-agnostic (it receives HTML + screenshot, it does not care how they were obtained).

---

## 5. Browser-Session Credential Handoff

The hardest problem in autonomous browsing: how does the agent authenticate to sites the user has accounts on, without credentials ever transiting the LLM?

### 5.1 Persistent browser profile per site

```
~/.personalclaw/browse/profiles/<site_slug>/
  Default/         # Chrome user-data-dir contents (cookies, localStorage, sessionStorage)
  .meta.json       # {site, last_login_at, session_valid_until (heuristic), created_at}
```

Each monitored/browsed site gets its own persistent Chrome profile directory. Session cookies survive across browse runs. The profile is app-owned data (lives under `~/.personalclaw/`), never backed up by snapshot/portability (credentials), never exported.

### 5.2 The `request_login` action

When the browse provider encounters a login wall (detected by: known login-page URL patterns, form with password field, HTTP 401/403, or explicit LLM determination), it:

1. **Parks the run** on a `needs_input` gate (the proven pattern from AUTONOMY-GUARDRAILS: pause into needs-input with a notification).
2. **Opens a headful browser window** using the site's persistent profile, navigated to the login page.
3. **Notifies the user:** "Browse run for <site> needs you to log in. A browser window is open — please authenticate, then click 'Done' in the notification."
4. **The human authenticates** in the real browser window (typing credentials, solving CAPTCHAs, completing 2FA). The agent has zero visibility into this — it is not reading the page during this phase.
5. **On user confirmation**, the browser window is hidden/closed, the session cookies are persisted to the profile directory, and the run resumes with the now-authenticated session.

**Key invariant: credentials never transit the agent.** The LLM never sees a password field's value, never receives a 2FA code, never handles an OAuth token. It only knows "I am now authenticated" by observing that the post-login page contains the expected content.

### 5.3 Session validity heuristic

Before each browse run, the provider attempts a lightweight session check (load a known authenticated-only URL, check for redirect-to-login). If the session is stale, it proactively fires `request_login` before the main task begins — avoiding mid-task interruptions.

---

## 6. Egress Chokepoint Integration

### 6.1 Every navigation passes through `net/guard.py:evaluate`

Before the CDP `Page.navigate` command fires, the target URL is evaluated against the active egress policy:

```python
decision = await evaluate(url, policy=egress_policy_for(BROWSE_POLICY), resolver=resolver)
if not decision.allowed:
    return BrowseResult(blocked=True, reason=decision.reason)
```

`BROWSE_POLICY` is a new named profile in `net/policy.py`:
```python
BROWSE = EgressPolicy(
    allow_schemes=("https", "http"),
    allow_private=False,       # no SSRF into local network
    loopback_only=False,
    max_redirects=5,
    max_bytes=10_000_000,      # 10MB page budget
    timeout=30,
    pin_resolved_ip=True,
)
```

Operator layering via `egress_policy_for(BROWSE)` inherits the user's `security.egress` allow/deny hosts.

### 6.2 Redirect re-evaluation

Every client-side redirect (detected via CDP `Page.frameNavigated` events) is re-evaluated against the policy — matching `net/client.py`'s manual redirect loop pattern. A redirect to a denied host aborts the navigation and records the block in the SEL.

### 6.3 The `web/render.py` headless bypass gap

**Acknowledged gap:** `web/render.py` already documents that Playwright bypasses the `net/client.py` pinned-IP resolver — it can only pre-flight `evaluate()` before navigation. The browse provider has the same limitation (CDP controls Chrome's own DNS resolution, which cannot be overridden to use pinned IPs). Mitigation is identical: pre-flight `evaluate` with `pin_resolved_ip=True` verifies the resolved IP is allowed; TOCTOU between evaluation and navigation is accepted as a known residual risk for browser-based paths (documented in the security roadmap as the "rebind window"). A future hardening pass could add a PAC proxy or iptables fence — deferred.

---

## 7. Browse Loop Architecture

The action provider's `execute()` method runs a loop:

```
1. Navigate to start_url (egress-checked)
2. Inject safety script
3. Extract page → text representation + links + forms
4. Fence extracted text (fence_untrusted)
5. Present to LLM: system prompt (action vocabulary) + goal + notes + page content
6. Parse LLM response for sentinel actions
7. Execute action (click/type/submit/scroll/wait/navigate)
   - Each NAVIGATE re-enters at step 1 (egress check + safety injection)
   - Each SUBMIT triggers outcome verification (§7.1)
8. Loop until DONE or max_steps (default 20) or budget exhaustion
9. Return: final notes + last page snapshot as ActionResult
```

### 7.1 Form submission outcome verification

After a SUBMIT action:
1. Wait up to 10s for navigation or DOM change (URL change OR significant content delta).
2. Re-extract the page.
3. Present to the LLM: "You submitted the form. The page now shows: <new content>. Did the submission succeed? Respond FORM_OK or FORM_FAILED with a reason."
4. On FORM_FAILED: append failure note, allow the agent to retry or navigate away.

### 7.2 Loop guards

- **Max steps:** configurable per invocation, default 20 (prevents infinite browsing).
- **Budget integration:** each LLM call within the loop charges through the model-call chokepoint (AUTONOMY-GUARDRAILS §2); budget exhaustion parks the run.
- **Stuck detection:** if the LLM produces the same action 3 times consecutively, inject a "You appear stuck. Consider a different approach or use DONE to exit." prompt.
- **Visited-URL dedup:** maintain a `visited_urls` set; warn the LLM when it attempts to revisit a page.

---

## 8. Integration Points

### 8.1 WATCHED-SOURCES headless-fetch escalation

WATCHED-SOURCES defines an escalating fetch chain: RSS → `web_fetch` (static) → headless render → **browse provider** (for JS-heavy SPAs, paginated content, login-walled sources). The browse provider is the final escalation tier, invoked when simpler methods fail to extract meaningful content. The escalation decision is made by the monitoring template based on extraction quality signals (empty content, repeated "enable JavaScript" messages, login redirects).

### 8.2 Deep-research template

The deep-research workflow template invokes the browse provider as its web-exploration action:
- Template provides a research goal + seed URLs
- Browse provider navigates, reads, accumulates NOTES
- Notes feed back into the template's synthesis step
- Multiple browse invocations (parallel across different seed URLs) are orchestrated by the workflow engine's fork/join

### 8.3 Workflow action-node dispatch

The browse provider is a standard `ActionProvider` — workflow action nodes invoke it by name (`browse`) with an `action_config` specifying `{goal, start_url, max_steps, profile_site}`. The workflow engine handles timeout, retry, and needs-input (credential handoff) through its existing mechanisms.

---

## 9. Provider-Fidelity Wiring

- **App manifest:** `apps/browse-action/app.json` — `type: "action"`, `entity: "browse"`, `implementation: "provider:create_provider"`, `permissions: {network: true, storage: true}`. Ships as a first-party app (installed via App Store, not native — can be disabled).
- **ALLOWED_HOOK_PROVIDERS:** add `"browse"` to the frozenset at `validation.py:555`. Without this, hook/trigger creation referencing the browse provider is rejected.
- **Action dispatch:** inherits denylist enforcement at the three dispatch seams (`hooks.py:494`, `gateway.py:701`, `event_triggers.py:214`) — the browse provider's execute() is called after `check_action` passes (AUTONOMY-GUARDRAILS §1.2).
- **Egress:** `BROWSE` named policy added to `net/policy.py` alongside STRICT/CONNECTOR/WEBHOOK; operator layering via `egress_policy_for`.
- **Safety profile:** unattended browse runs resolve through the `HEADLESS` safety profile (read + navigate grants; no filesystem writes, no other action providers). A trigger creating a browse automation must grant `browse` explicitly at creation time.
- **Output fencing:** all page text extracted by the provider is wrapped with `fence_untrusted(text, source=url)` before entering the LLM context — web content is attacker-controlled and must be fenced.
- **SEL:** egress blocks, credential-handoff events (login requested/completed), and stuck-detection exits are logged to `sel.py:SecurityEventLog`.
- **SDK:** the browse provider uses `sdk.net` (egress evaluation), `sdk.security` (fence_untrusted), and `sdk.action` (ActionProvider base). It does NOT re-export anything — it is a leaf consumer.
- **Config:** browse-specific settings live in the app's own `data/config.json` (per-app settings pattern via `ProviderSettings`): `{max_steps_default, inter_action_delay_range, stealth_level, profiles_dir}`. No new top-level `AppConfig` section — the browse provider is an app, not core.

---

## 10. Implementation Effort

**~4 sessions.**

- **Session 1 — page extraction + action parsing:** markdownify + sentence filter + size cap; links/forms DSL extraction; sentinel action parser; unit tests with fixture HTML pages (static, form-heavy, JS-rendered snapshots). Output: a standalone `browse/extraction.py` module that takes HTML and returns the structured page representation.
- **Session 2 — CDP integration + safety injection + egress wiring:** browser lifecycle management (launch with persistent profile, CDP connection); safety script injection via `Page.addScriptToEvaluateOnNewDocument`; navigation with egress pre-flight (`guard.evaluate`); redirect re-evaluation; screenshot capture for action verification; the `BROWSE` egress policy in `net/policy.py`.
- **Session 3 — browse loop + action provider contract:** the full browse loop (navigate, extract, prompt, parse, execute, repeat); `BrowseActionProvider` implementing `ActionProvider` ABC; `ALLOWED_HOOK_PROVIDERS` addition; form submission with outcome verification; stuck detection; visited-URL dedup; max-steps guard; budget integration via model-call chokepoint.
- **Session 4 — credential handoff + integration + validation:** persistent profile management; `request_login` needs-input flow; session validity check; WATCHED-SOURCES escalation wiring; deep-research template integration; as-a-user validation (browse a real site, fill a real form, authenticate via handoff, run a multi-page research task).

Each session ships independently; Sessions 1-2 produce a working page-reader usable by `web_fetch` as an upgraded extraction backend even without the full action loop.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Anti-bot detection blocks automated browsing on popular sites | v1 targets user-owned/authenticated sites + public pages with low anti-bot; stealth escalation (undetected-chromedriver) is a documented upgrade path; the provider reports "blocked by site" honestly rather than retrying indefinitely |
| CDP attachment detected by sophisticated anti-bot (Cloudflare, DataDome) | Accepted for v1; the user's own authenticated session (persistent profile) is the primary use case, not adversarial scraping; escalation to playwright-stealth or undetected-chromedriver is architecturally possible without changing the extraction layer |
| Page safety injection bypassed (service workers, cross-origin iframes) | Defense-in-depth, not a sandbox; the real containment is the egress chokepoint (page cannot reach denied hosts) + output fencing (malicious page content is fenced before LLM sees it); documented as a known residual |
| Credential handoff UX friction (human must act) | By design — this is the security invariant; session caching minimizes frequency; proactive validity check avoids mid-task interruptions |
| LLM misparses sentinel actions on weak models | Sentinel format is deliberately simple (one keyword per line); stuck detection catches repeated failures; max_steps prevents infinite loops; the action vocabulary is small enough to fit in a short system prompt |
| Runaway browsing consumes budget | Inherits AUTONOMY-GUARDRAILS budget ceiling (every LLM call in the loop charges through SpendMeter); max_steps hard cap; headless safety profile constrains the run |
| TOCTOU between egress evaluate and CDP navigate | Identical to the acknowledged `web/render.py` gap; pre-flight evaluate with pinned IP is the best available defense without a local proxy; documented as residual |
| Stale persistent profile cookies leak cross-site state | One profile per site (not shared); profiles are isolated directories; no cross-site cookie leakage by construction |

---

## Success Criteria

1. The browse provider extracts a readable ~800-token page representation from a JS-heavy SPA (content invisible to simple `web_fetch`) and returns it as an `ActionResult` with the full text available to the calling workflow node.
2. A multi-step browse task (navigate to site, click through 3 pages, accumulate notes, return findings) completes within 20 steps, with each page's extracted text fenced via `fence_untrusted` before entering LLM context.
3. Form fill + submit on a real login page (using the credential handoff flow) works end-to-end: the run parks on needs-input, the user authenticates in the headful window, the run resumes with the authenticated session, and subsequent browse runs reuse the persisted session without re-authentication.
4. Every navigation attempt against a denied host (per egress policy) is blocked before the CDP navigate fires, with the block recorded in the SEL.
5. The safety script injection prevents a test page from executing `fetch()`, playing media, or accessing `navigator.bluetooth` — verified by asserting the injected page's JS calls throw/return blocked.
6. A workflow action node invokes `browse` by name in a trigger/hook definition — and the definition is accepted (provider is in `ALLOWED_HOOK_PROVIDERS`), dispatched through the standard seams, and inherits the denylist/budget/profile enforcement from AUTONOMY-GUARDRAILS without browse-specific code at the dispatch layer.
7. The WATCHED-SOURCES escalation chain successfully falls through from `web_fetch` (which returns empty/garbage for a JS-rendered page) to the browse provider (which returns meaningful content) — demonstrating the escalation integration.
8. An unattended browse run that exhausts its step budget or token budget parks cleanly into needs-input (not crash, not silent failure) with accumulated notes preserved in the ActionResult.

## Amendment (2026-07-26 — gap analysis round 2, owner-approved mechanisms)

**The three make-or-break enrichments + the actuator pattern.** Design evidence: browse agents live or die on context cost (~20x reduction from compressed outlines + screenshots-as-paths; unusable without), on the user being able to *watch* an unattended browse, and on auth that never transits the agent. The plan already has the credential-handoff invariant (§5) and the ~800-token text representation (§1); this amendment names the compression layer as a first-class session, adds the live mirror and the honest auth-expiry state, and reframes long-running browse as stateless scheduled ticks. Rung-capped via AUTONOMY-GUARDRAILS' earned-autonomy ladder (round-2 amendment there): read-only browse may graduate up the ladder; any action that SUBMITS starts `draft_only`.

### Contract-level design

- **(a) Context compression layer** — an in-gateway module between the browser driver and the agent, formalizing §1 into a stable contract:

```python
# browse/compress.py (app bundle)
@dataclass(frozen=True)
class PageOutline:
    url: str
    text: str                    # §1.1 pipeline output, ≤4000 chars
    elements: list[ElementRef]   # interactive elements with STABLE refs
    screenshot_path: str         # file under the run workspace — NEVER base64 in context

@dataclass(frozen=True)
class ElementRef:
    ref: str          # stable across re-snapshots: sha1(role + accessible_name + form_id)[:8]
    role: str         # link | button | field | checkbox | select
    label: str
    state: str = ""   # value/checked for fields
```

  Sentinel vocabulary (§2) migrates from positional numbers to refs (`CLICK <ref>`, `TYPE <ref>(value)`) — a re-snapshot after dynamic DOM change no longer invalidates the agent's plan (the numbered-list TOCTOU that positional-index approaches all hit). Screenshots are captured per step for verification (§7) but enter context only as `[SCREENSHOT: <path>]` placeholders; a multimodal step may load one explicitly.
- **(b) Live browse mirror** — read-only dashboard relay of the screenshots the agent already takes: each step's screenshot path + current URL + last action broadcast via `DashboardState.broadcast_ws("browse_step", {run_id, url, action, screenshot_url, step_n})` (`dashboard/state.py:1644`); a `BrowseMirror` panel renders the stream with the incident kill switch adjacent (one click from "that looks wrong" to full stop). No debug port, no CDP exposure, no new attack surface — the mirror consumes artifacts the loop produces anyway.
- **(c) Auth handoff honestly scoped** — §5 kept, sharpened: the user completes logins once in a visible window; session state (cookies/localStorage) is captured to the per-site profile (§5.1) with the profile-encryption key held in the credential store (`save_credential("BROWSE_PROFILE_KEY_<slug>", …)`, `config/loader.py:234` — never in the profile dir); headless reuse thereafter. **Expiry is a first-class `auth_needed` state**, not a failure: the §5.3 validity check failing sets `.meta.json:{auth_state:"expired"}`, surfaces a persistent banner on the browse panel + an inbox `needs_input` item, and every dependent scheduled tick short-circuits to `ActionResult(outcome="skip")` until re-auth. The agent NEVER handles credentials (§5.2 invariant unchanged).
- **(d) Scheduled-actuator pattern** — browse runs as stateless, idempotent scheduled ticks against a persisted plan, not as one long-lived session: `browse/plans/<id>.json` `{goal, kind: watch_page|walk_flow, cursor, notes, max_steps_per_tick}` (atomic_write). "Watch this page" = one tick re-extracts + diffs against cursor; "walk this flow" = one step per tick, cursor advances only on verified success. Coordinates with WATCHED-SOURCES: its escalation chain (§8.1) invokes exactly one tick, and `SourcePollCompleted`-style accounting carries `escalated: browse`. Crash mid-tick loses at most one step.

### Session placement

(a) restructures Session 1 (extraction was already there; stable refs + screenshot-path discipline join it). (b), (c)-sharpening, and (d) are new surface + persistence work: one added **Session 5**. Honest count ~4 → **~5**.

| ID | Task | Files | Done when |
|---|---|---|---|
| A1 | Stable `ElementRef` contract + ref-based sentinels + screenshot-as-path discipline folded into the extraction module (extends Session 1) | `browse/compress.py`, `browse/extraction.py`, sentinel parser, fixture tests | a re-snapshot after DOM mutation preserves refs for unchanged elements; no base64 appears in any rendered prompt (regression-tested); a 100K-token DOM enters context as <1K tokens + a path |
| A2 | Live mirror: `browse_step` WS broadcast per loop step + FE mirror panel with kill-switch adjacency; `auth_needed` first-class state (meta flag, banner, `needs_input` inbox item, tick short-circuit) with profile key in the credential store | browse loop, `dashboard/state.py` consumer, `web/src/pages/` browse panel, `.meta.json` schema | a user watches an unattended browse live and can stop it in one click; an expired session produces a banner + inbox item and zero failed ticks, and re-auth resumes without agent involvement |
| A3 | Scheduled-actuator: persisted browse plans + idempotent one-tick execute (watch_page diff / walk_flow single-step), WATCHED-SOURCES escalation = one tick; rung caps wired (read-only browse graduates per the AUTONOMY-GUARDRAILS ladder; SUBMIT-bearing plans registered `floor=draft_only`) | `browse/plans.py`, provider `execute()`, WATCHED-SOURCES escalation seam, autonomy type registration | killing the gateway mid-flow loses ≤1 step; the same tick re-fired is a no-op at the same cursor; a form-submitting plan cannot run unattended until its type earns promotion |

---

## Amendment (2026-07-29 — owner-approved: the local-browser advantage)

**Why this amendment exists.** A design analysis (2026-07-28/29) found authenticated web access can be solved in two opposite ways, and the divergence is the single most useful finding for a self-hosted product. **One approach gives the agent its own machine** (a per-user always-on cloud VM; the user logs in through VNC and the session persists on the VM). **An alternative approach drives the user's own machine** — a browser extension that drives *the user's real Chrome*, with their existing logins, their residential IP, and their paid subscriptions. The reasoning for the extension approach: no login barriers (traffic originates from a trusted machine, so unfamiliar-login challenges, CAPTCHA interruptions, and session expiry largely stop happening), reliable access through standard anti-bot barriers, and — the crux — **access to subscriptions the user already pays for** (e.g. Crunchbase, PitchBook, SimilarWeb, Financial Times, Bloomberg, WSJ, Semrush, LinkedIn Sales Navigator).

**The point for PersonalClaw: we are already on the user's machine.** The extension approach exists to reach where the gateway already runs. This is a structural advantage the product currently does not use — verified, PersonalClaw has **no browser automation at all**: `web/render.py::render_url` (122 lines) drives headless Chromium via Playwright — an **optional** extra (`pyproject.toml:113` `js-render`) — for exactly one read-only function returning post-JS HTML. There is no click, type, navigate-flow, form-fill, screenshot, or session persistence anywhere in core.

**Scope boundary against §5 — read this before writing code.** This plan already owns a credential-handoff model, and it is a *good* one: per-site persistent Chrome profiles under `~/.personalclaw/browse/profiles/<site_slug>/` (§5.1), a `request_login` action that parks the run and opens a **headful window** for the human to authenticate in (§5.2), the invariant that credentials never transit the agent (§5.2), and a pre-run session-validity heuristic (§5.3). That is the **gateway-owned-profile** model — PersonalClaw's local equivalent of the own-machine approach. This amendment adds a **second, distinct execution target**: the user's *own everyday browser*, with its *own existing* profile, which the gateway never owns, never copies, and never persists. Both targets coexist behind one selector; neither replaces the other.

| | **Gateway profile** (§5, already designed) | **User browser** (this amendment) |
|---|---|---|
| Profile | `~/.personalclaw/browse/profiles/<slug>/`, gateway-owned | the user's real browser profile, untouched |
| Logins | user authenticates once into the gateway's profile | already there — nothing to establish |
| Paid subscriptions | only if logged into the gateway profile | **available** (the user's real sessions) |
| IP | the host's | the host's (same machine — identical here, unlike a cloud product) |
| Persistence | cookies persist in the gateway profile | **nothing persisted by us, ever** |
| Best for | unattended/scheduled monitoring | interactive, human-present work behind a login |

**Legal/posture constraint — must be written into the implementation, not just noted.** Amazon obtained a **preliminary injunction against Perplexity (2026-03-10)** over precisely this mechanism: credential-inheriting agentic browsing. The self-hosted posture is materially different — the user automates *their own* browser on *their own* machine, under per-task authorization, and no third party holds the credentials or resells the access — but the *framing* is load-bearing. Two rules follow: (1) **never describe or design this as bypassing anti-bot protections or CAPTCHAs.** This is never marketed as bypassing protections. Its purpose is "let the agent act in your browser, with your explicit per-task permission." Anti-bot avoidance may be an *emergent consequence* of legitimate traffic; it is never a feature, a selling point, or a documented capability. (2) **the per-task grant and the kill switch are the evidentiary record** that the user directed each action — they are compliance surfaces, not just UX. Nothing here runs unattended (see the rung cap below).

### Contract-level design

- **(a) A second execution target behind one selector.** The `browse` action provider gains a `target: "gateway" | "user_browser"` field (default `"gateway"` — the existing §5 path, so nothing changes for shipped behavior). `user_browser` requires the companion extension (below) to be connected; when it is absent the provider returns `ActionResult(outcome="skip")` with a typed, actionable reason — **never a silent fallback to the gateway profile**, because falling back would run a task the user scoped to their own session against a different identity.

- **(b) The connector is a browser extension talking to the local gateway.** Because the gateway is on the same machine, the extension connects over **loopback only** — the existing `LOOPBACK_INTERNAL` egress posture (`net/policy.py`, documented as inverted and "never widened by config") is the right rail, and no new network exposure is created. The extension is a **removable app-bundle-shipped artifact**, not core: it carries no vendor logic into core, and the `user_browser` target speaks to it through a typed local contract. Pairing reuses the shipped device/pairing machinery rather than inventing a second one (COMPANION-APPS §C1/C2 owns the endpoint + device-session model — consume it, do not fork it).

- **(c) Per-task authorization, and it is the security control.** Adopt a per-task authorization control model, which maps cleanly onto PersonalClaw's existing approval vocabulary:
  1. The user enables the `user_browser` target once (a connector toggle).
  2. **Every task requires a fresh, explicit grant** — one task, one authorization, routed through the existing approval gate (`agents/native/approval.py::ApprovalGate`, which is **fail-closed on a 300s timeout → REJECT**). The grant names the sites the task intends to touch, so the user reviews scope before granting.
  3. Work happens in a **dedicated tab inside a tab group named after the task** — the user can watch it live, click in to take over, and **close the tab to kill it instantly**. Closing the tab is a hard stop, not a request: the provider observes the disconnect and ends the run.
  4. **No credentials are ever read, stored, or transmitted** — the extension drives an already-authenticated browser; it never touches a password field's value, a 2FA code, or a cookie jar. This is §5.2's invariant, restated for the new target and equally non-negotiable.
  5. Every grant, navigation, action, and revocation is **SEL-audited** (§2.3 conventions; new event types lowercase snake, e.g. `browser_grant`, `browser_revoked`).

- **(d) Rung cap — interactive only, by construction.** Per AUTONOMY-GUARDRAILS' earned-autonomy ladder, the `user_browser` target is registered with a **floor that never permits unattended execution**: it requires a live human-present grant, so it cannot be selected by a scheduled tick, a cron, a loop, or any unattended run. A scheduled plan that names `user_browser` is a configuration error and is refused at registration time with a typed error, not at run time. (The gateway-profile target keeps its existing ladder treatment: read-only browse may graduate; SUBMIT-bearing plans start `draft_only`.) This is also what keeps the legal posture above coherent — there is no unattended credential-inheriting browsing in this design at all.

- **(e) Egress still applies.** Every navigation the extension performs on our instruction passes the existing guard (§6.1 `net/guard.py::evaluate`) before it is issued. Note the honest limit already documented in code: a real browser does its own DNS and connections, so it **bypasses `net.fetch`'s IP pinning** (`web/render.py:8` says exactly this) — pre-flight validation only, therefore rebind-vulnerable. That limitation is inherent to driving any real browser and must be stated in the security docs rather than implied away.

### Amendment task table (extends the plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| B1 | `target: "gateway" \| "user_browser"` on the browse action config (default `"gateway"`, byte-identical existing behavior); `user_browser` with no connector returns `outcome="skip"` + typed reason; registration-time refusal when an unattended/scheduled plan names `user_browser` | browse provider config + `execute()`, action-provider registration, tests | existing gateway-profile runs are unchanged (regression test); an unconnected `user_browser` task skips with an actionable reason and never falls back; a cron naming `user_browser` is refused at registration with a typed error |
| B2 | The extension connector: loopback-only local contract (typed messages for navigate/read-outline/click/type/close), pairing via the shipped device-session machinery (COMPANION-APPS §C1/C2 — consume, don't fork); ships as an app bundle, zero vendor logic in core | app bundle (apps repo) + the core-side typed connector seam, tests | the extension connects over loopback and is listed as a connected device; no new listening surface beyond loopback; core carries no browser-vendor string |
| B3 | Per-task grant flow: scope-naming grant through the existing fail-closed `ApprovalGate`; dedicated task-named tab group; live watch; click-in takeover; **close-tab-to-kill observed as a hard stop**; SEL events for grant/navigate/action/revoke | approval integration, browse loop, extension, `sel.py` event types, tests | a task cannot start without a fresh grant; closing the tab ends the run within one step; every step appears in the SEL; a denied/timed-out grant rejects (fail-closed) |
| B4 | Docs + posture: `docs/architecture/security.md` gains the user-browser target — the per-task grant model, the no-credential-access invariant, and **the honest IP-pinning bypass** for real browsers; the framing rule from this amendment (never "bypass anti-bot/CAPTCHA") is stated so future copy inherits it | `docs/architecture/security.md`, the browse panel copy | the docs state the real control model and the real limitation; no surface describes anti-bot avoidance as a capability |
| VB | Validation as a user: connect the extension; grant one task scoped to a site you are logged into; watch the agent work in the named tab group; click in and take over; close the tab mid-run and confirm a hard stop; confirm the SEL shows grant→actions→revoke; attempt to schedule a `user_browser` plan and confirm it is refused; confirm an unconnected run skips cleanly; full local gate | — | holds |

### Risks specific to this amendment
- **Blast radius is the user's real session.** A mis-scoped task acts as the fully-logged-in user. Mitigated procedurally (per-task grant naming sites, live watch, close-to-kill) rather than architecturally — an honest, inherent tradeoff. This is why the unattended path is refused outright rather than rung-capped.
- **Extension maintenance is a real cost** across browser versions and manifest revisions; keeping it in an app bundle (removable, versioned separately) is deliberate for exactly this reason.
- **Prompt injection reaches a privileged surface.** A page the agent reads can attempt to steer it while it holds an authenticated session. Existing controls apply (`fence_untrusted` on all page content, the §1 compression layer meaning raw DOM never enters context) and must not be weakened for this target. Worth noting: the research itself encountered two live injection attempts on vendor/affiliate pages, so this is an observed threat, not a theoretical one.
- **Open:** whether read-only `user_browser` use (extract from a page I'm logged into) should be a lighter grant than action-bearing use. Deferred — one grant model ships first; splitting it is a natural follow-up if the single grant proves heavy in practice.

- **2026-08-23 — `BA-2` COMPLETE (all four clauses, proven against a real browser). Atom stays `todo`
  only because this code is unmerged**; flip it when the PR lands.
  **Clause 1+2 — every CDP navigation pre-flighted, and the deny lands BEFORE the wire.** The ordering is
  the whole gate, so it is asserted on the transport's message list rather than on a return value:
  a denied host produces **zero** `Page.navigate` messages, and an allowed host produces exactly one (the
  vacuity partner, without which "zero" is trivially satisfiable). Falsified by moving the guard after the
  send — reds **9 of 22**, including the allowed-host count failing `assert 2 == 1`.
  **Clause 3 — the injected script blocks in-page network, media and device APIs**, and this was proven by
  EXECUTION, not by grepping the script text. `chrome-headless-shell` driven over raw CDP, injected at
  `Page.addScriptToEvaluateOnNewDocument` (the production point), with a **local `http.server` as the
  network oracle and an uninjected BASELINE run as the positive control**: baseline
  `{fetch:1, xhr:1, beacon:1, ws:1, es:1, worker:1, iframe-src:1, iframe-blank:1, iframe-srcdoc:1}` vs
  guarded `{}`. Errors are asserted by identity (`err.name === "PersonalClawBlockedError"`), because
  headless autoplay policy and DNS failure also reject — "it threw" would have proved nothing.
  **Clause 4 — client-side redirects re-evaluated per `Page.frameNavigated`**, and the enforcement is a
  teardown rather than a log line: `Page.stopLoading` **then** `Page.navigate → about:blank`, in that
  order, because `stopLoading` alone leaves the denied document and its script context alive for the agent
  to extract. Applied to the whole page even for a subframe deny, fenced against re-entrancy with a
  **closed** exempt set so `file:`/`data:` frames still go through the guard; if neither message can be
  delivered the session **quarantines**.
  **LIVE END-TO-END, against a real browser rather than the fake transport:**

      start() wire: ['Page.enable', 'Page.addScriptToEvaluateOnNewDocument']
      DENIED  http://127.0.0.1:9/blocked  -> allowed=False, Page.navigate messages: 0
      ALLOWED https://example.com/        -> allowed=True,  Page.navigate messages: 1
      in-page marker: applied = [fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon,
                                 workers, peerConnection, media, deviceApis]
      in-page fetch() -> BLOCKED:PersonalClawBlockedError

  **`pin_resolved_ip=False`, deliberately, and the reasoning is the point.** Pinning promises "the caller
  dials these exact validated IPs", which is keepable only where we own the socket. Chrome owns its
  resolver and its sockets and CDP has no connect-to-this-IP parameter, so declaring `True` would
  advertise a mitigation this path does not implement. `evaluate` still returns `pinned_ips`; for BROWSE
  those are **SEL evidence**, not enforcement, and closing the rebind window for real needs
  `--host-resolver-rules` or a mandatory proxy — not claimed here. `LOOPBACK_INTERNAL` sets it `False` for
  an analogous reason, so this is not a novel stance.
  **The pre-flight is also the SCHEME gate, which a fetch profile never has to be.** A browser understands
  schemes where `Page.navigate` is not egress at all: verified live, `file:///etc/passwd`,
  `devtools://…`, `data:text/html,…` and `view-source:https://…` are all refused by
  `allow_schemes=("http","https")` — local-file read and debugger self-attach, refused as a side effect of
  the profile rather than by a special case. Do not widen that tuple for a local-file convenience.
  **§6.3 headless bypass gap — enumerated in the module, not left implicit.** The pre-flight covers the
  top-level URL and its redirect hops. Chrome still egresses unevaluated for: subresources
  (`img`/`script`/`link`/`iframe src`, CSS `url()`, `@font-face`, form submission), cross-origin iframes,
  `window.open`, service/shared workers outliving the navigation, and WebRTC/ICE + DNS prefetch. The
  in-page script narrows the **JavaScript** half of that (`fetch`, `XHR.open`+`.send` via a WeakSet stamp,
  `WebSocket`, `EventSource`, `sendBeacon`, `Worker`/`SharedWorker`, `RTCPeerConnection`,
  `serviceWorker.register`, and 8 device keys — widened past the clause's single `bluetooth`, because
  closing bluetooth while leaving `navigator.usb` open is a control with a hole in it). **What remains open
  after both halves is the network-stack surface**, which no in-page script can reach: it needs
  `Network.setBlockedURLs`/`Fetch.enable` or an injected CSP. Guards are `writable:false,
  configurable:false` (measured: assignment silently fails, `delete` returns `false`,
  `defineProperty` throws) — which does not beat a page holding a pre-injection reference, and that is
  precisely why the injection point is `addScriptToEvaluateOnNewDocument` and not `Runtime.evaluate`.
  **An integration defect only the MERGE could see.** `test_a_missing_safety_script_module_fails_closed`
  simulated the sibling's absence with `monkeypatch.delitem(sys.modules, …)`. That worked only while
  `safety_script.py` genuinely did not exist on the CDP branch; once it landed, evicting the cache just
  re-imported it from disk and the test asserted nothing. Corrected to insert `None` into `sys.modules`
  (which makes `import` raise), and proven non-vacuous: swallowing the injection failure reds 2 of 22.
  **NEXT SLICE, recorded rather than rushed:** the session sends the guard script but never confirms it
  installed. `safety_script` exports `SAFETY_MARKER` for exactly that, and the live run above shows the
  read works (`window.__personalclawSafety.applied` lists 9 steps) — so verifying it after navigation, and
  quarantining on an absent marker or non-empty `failed`, is a small addition. Left out deliberately: it
  changes the message sequence nine tests assert on the wire, and rushing that into a security path at the
  end of a tick is how a gate acquires a hole.
  **DISCOVERY (pre-existing BA-1, outside this atom): `browse/__init__.py`'s docstring claim "No network,
  no gateway, no config" is false.** `extraction.py:39` imports `personalclaw.knowledge.connectors.base`,
  which drags in `httpx`/`urllib3`/`http.client`. The package is import-clean in intent only.

- **2026-08-24 — `BA-2` re-driven against a REAL browser. The 2026-08-23 code is all merged**
  (`browse/cdp.py`, `browse/safety_script.py`, `net/policy.py:226` `BROWSE`, both test files at
  `origin/main` 827751b9), so nothing above needed rebuilding. Prerequisite verified rather than assumed:
  `AG-1`…`AG-13` are all `done` in `dag.json`, so Autonomy-Guardrails does not gate this.
  **The done_when has FIVE clauses, not four.** The entry above enumerates pre-flight, ordering, the
  injected script and redirect re-eval; the SEL clause is a fifth. That one was in fact implemented and
  covered on both halves (`test_deny_writes_exactly_one_sel_row` plus the vacuity partner
  `test_allowed_navigation_writes_no_sel_row`, which is what stops "a row exists" from passing when only
  one path writes) — only the summary was short. Clause status now: **1, 2, 3, 4, 5 met; §4.2 of the
  atom's SCOPE not met** (below).
  **The stand-in defect had a SIBLING, and it survived the merge.** The entry above caught
  `safety_script`'s `delitem` stub — "an integration defect only the MERGE could see". The same file's
  autouse `browse_profile` fixture was the same defect: it monkeypatched `net_policy.BROWSE` with
  `EgressPolicy(name="browse", deny_hosts=("denied.example",))` and `raising=False`, which read as a
  scaffold while BROWSE lived on a sibling branch and became a **shadow** the moment it landed. All 22
  tests asserted against a two-field fake, so clause 1 — "against a new BROWSE profile in `net/policy.py`"
  — was self-fulfilling. **Measured:** with the shipped profile re-declared as `pin_resolved_ip=True,
  max_redirects=5`, the pre-fix suite is **22 of 22 green**. Fixed by deleting the stand-in, sourcing the
  deny from the operator layer (so `egress_policy_for` is on the path under test), and adding
  `test_the_guard_is_called_with_the_real_browse_profile`, which captures the policy at the `evaluate`
  call site and asserts the shipped profile's own values — 10 redirects, 50 MB, `pin_resolved_ip=False` —
  each of which STRICT contradicts.
  **`CdpTransport` had ZERO production implementors, so the redirect clause was proven against a dict the
  test wrote itself.** `handle_event` was only ever reached by `transport.listener(FRAME_NAVIGATED, …)`
  called by hand. `browse/transport.py` (`WebSocketCdpTransport`) is that implementor — one socket to one
  page target, replies matched by `id`, no process ownership — and `tests/test_browse_cdp_live.py` drives
  the gate through it against `chrome-headless-shell`, 9 tests, no skips on this machine.
  **DISCOVERY — the reader must never await the event listener, and only a live browser can show it.**
  The listener is `handle_event`, whose teardown *sends* on the same socket, so awaiting it inline from
  the frame reader parks the only task that can resolve that send's future. **Measured:** with the two
  tasks collapsed, the wire tail is `['Page.navigate', 'Page.stopLoading']` — `stopLoading` is issued and
  never answered, `about:blank` never fires, the session quarantines on the timeout, and **enforcement is
  a live no-op while every fake-transport test stays green.** Hence a reader that only routes and a single
  dispatcher that only calls the listener (which also keeps events ordered).
  **The "`stopLoading` alone leaves the denied document alive" claim is now measured, not argued.** With
  the second teardown message removed, the live page stays on `http://denied.local:PORT/secret`, fully
  loaded. That is the DOM statement no recording fake can make.
  **The ordering clause now has a network oracle.** A denied navigation leaves **zero** requests in a live
  loopback HTTP server's hit ledger, with the allowed navigation immediately after as the vacuity partner
  (its hit must appear). `--host-resolver-rules` maps `allowed.local` and `denied.local` to 127.0.0.1 and
  everything else to `~NOTFOUND`, so both names are equally reachable and only POLICY separates them — and
  a guard regression cannot dial a real host from the suite. Redirect vacuity floor: the same client-side
  `location =` redirect to an ALLOWED host is left alone (no block, no SEL row, page ends on `/landed`),
  so the teardown is caused by the deny and not by every `Page.frameNavigated`.
  **HONEST LIMIT, now asserted instead of implied:** for a client-side redirect the request has already
  left the browser when `Page.frameNavigated` reports it. `test_the_redirect_request_itself_already_left_
  the_browser` asserts that hit IS in the ledger, so a future reader cannot mistake the teardown for
  prevention. The guard's reach here is the DOM, not the socket — §6.3, restated as a test.
  **The previous entry's NEXT SLICE is half closed.** `test_the_session_really_injected_the_real_safety_
  script` reads `window.__personalclawSafety` off a live page after the SESSION's own injection, so the
  production injection path is now proven to install the real guard (applied includes `fetch`,
  `XMLHttpRequest`, `media`, `deviceApis`; `failed` is empty). What is still out, for the same reason as
  before, is the session *quarantining* on an absent marker — that changes the message sequence nine
  tests assert on the wire.
  **NOT DONE, and deliberately NOT improvised: §4.2's anti-detection baseline and browser lifecycle.**
  §10's Session-2 line also promises "browser lifecycle management (launch with persistent profile, CDP
  connection)". There is no launcher in `src/`, so §4.2's `--disable-blink-features=AutomationControlled`,
  randomized viewport, real (non-`HeadlessChrome`) user-agent and randomized inter-action delays are
  unimplemented. This is an **owner scope question, not an oversight**: §4.1 says the provider "shares the
  browser instance lifecycle with the interactive MCP", which reads as *attach*, while §5.1 gives
  *persistent per-site profiles* to **BA-4** — so whoever writes the launcher decides both, and building
  it here would pre-empt BA-4's `done_when`. `transport.py` therefore takes a `webSocketDebuggerUrl` and
  owns no process; the live test launches its own. **Recommendation:** give the launcher + §4.2 a named
  scope on BA-4 (which already owns profiles) or a new atom, rather than leaving it between two atoms
  whose `done_when` neither mentions it. BA-2's own `done_when` does not require it and is met.
  **`GatedCdpSession` still has no non-test importer** — BA-3 is its consumer by design, so clause 1 is
  a property of the seam, not yet of a running loop. Worth stating plainly so nobody reads BA-2 as
  "browsing works".
  **CORRECTION to a standing note:** `egress_policy_for`'s old bare `except Exception: return base`
  fail-open is **already fixed on main** — it now remembers the last observed `deny_hosts` at module scope
  (`_LAST_DENY_HOSTS`) and logs at WARNING, so a config-read error can no longer un-deny a host. Residual,
  unchanged and documented in that function: on a *cold* start with nothing yet observed it still returns
  the bare profile. Note also that `_LAST_DENY_HOSTS` is module state that leaks between tests in an xdist
  worker; both browse test files now reset it.
  **Gate:** `make lint` clean (black/isort/flake8, mypy 993 files); the three browse files 53 passed in
  34.7s; `gate_report.py` 6 of 6 PASS; probe sweep 16 pre-existing / 0 added. Full `make test`: run 1 was
  **25631 passed / 2 failed in 1125s**, run 2 **25633 passed, 30 skipped, 12 xfailed, 0 failed in 648s**.
  Both run-1 failures were load flakes on this box, neither in a file this change touches:
  `test_structural_baseline::test_three_simultaneous_structural_violations_report_as_three` was a bare
  `Timeout (>120s)` (it needs ~60s on its own), and `test_inbound_mcp::test_rate_cap_returns_429_with_
  retry_after` admitted 23 requests against a cap of 20 — a token bucket losing to contention. Both pass
  `-n0`, and both passed in run 2. Run 1's 1125s against run 2's 648s is the contention itself. The three
  browse files add ~4s of wall time to the suite (30.8s → 34.7s at `-n auto`), so the new browser launches
  are not a plausible cause. No CHANGELOG entry: nothing user-visible changed — `GatedCdpSession` still
  has no production consumer, so this is a seam and its proof, not a capability.

- [2026-08-24][BA-2] **Integration re-verification.** Rebased onto `origin/main` (`9e0f727b`), full gate
  re-run by the integrator: `make lint` 0 (mypy 993), 55 browse tests green with the **live** headless
  leg (`chromium_headless_shell-1234` present, 0 skips), `make test` 25646 passed / 0 failed, 6-gate
  aggregate 6/6, probe residue 0. The redirect re-eval was re-falsified against a real browser — forcing
  `handle_event` to never re-judge reds `test_a_real_client_side_redirect_to_a_denied_host_is_re_evaluated`
  **and** the DOM-level `test_the_denied_document_is_torn_down_not_merely_stopped` (*"the denied document
  is still loaded"*), while the allowed-redirect partner stays green. **Atom left `todo`:** all five
  `done_when` clauses are met and proven live, but BA-2's declared *scope* (§4 Stealth Stack + §10 browser
  lifecycle/launcher) is unbuilt and `GatedCdpSession` has no production consumer yet (BA-3's by design) —
  so whether BA-2 flips on `done_when` or waits for the launcher to move to BA-4/a new atom is an **owner
  decision**, not the integrator's.
