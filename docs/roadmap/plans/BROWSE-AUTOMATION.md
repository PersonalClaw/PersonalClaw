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
