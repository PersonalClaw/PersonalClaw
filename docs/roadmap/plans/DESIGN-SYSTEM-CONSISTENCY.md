# Plan: Design-System Consistency — One Coherent Surface, Everywhere

**Status:** DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: UI/UX consistencies)
**Created:** 2026-07-18
**Wave:** 2 — a consistency-hardening pass that every other product-surface plan then inherits; run it early so new work lands on a clean baseline.
**Depends on:** nothing hard (audits + hardens the shipped design system). Feeds FLUID-MOTION (52 — consistent components are the substrate motion animates), APP-PLATFORM-EVOLUTION (48 — apps consume the exported primitives), and every page-touching plan (43, 49, 50).
**Scope:** the design system is mature (tokens, `tokenRegistry`, a token-lint test, 41 shell primitives, `web/PRODUCT.md` + `web/DESIGN.md`) — but 20+ pages built over time drift from it. This plan is a **consistency audit + hardening**: verify every surface uses tokens (not hardcoded values), the shell primitives (not bespoke chrome), consistent interaction patterns, WCAG AA parity, and dark/light + responsive parity — then ratchet the lint so drift can't return. **Soul guardrail:** this is *consistency*, not redesign — no new visual language, no page rewrites; bring outliers to the existing system (`DESIGN.md`/`PRODUCT.md` are authority). Where a page genuinely needs a pattern the system lacks, add it to the system *once* (a new shared primitive), never a one-off.

---

## Context (code recon, 2026-07-18)

Strong foundation: `web/src/design/` — `tokens.css` (brand/surface/content/semantic/glow/typography/layout/shape/elevation/3D/motion token groups), `tokenRegistry.ts` (typed `Token` union, 11 `GROUPS`), `tokenLint.test.ts` + `tokenLint.allowlist.json` (**a linter already exists** — the ratchet target), `schemes.ts` (light/dark), `runtime.ts` (canvas glow), `motion.ts`, `gradients.ts`. Shell primitives (41 in `web/src/ui/`): TopBar, ListScaffold, SidePanel, HeaderActions, NavRail, Modal, Surface, Button, IconButton, Toggle, Segmented, Combobox, Popover, Toaster, WorkbenchLayout, etc. `PRODUCT.md` sets the doctrine ("companion not console," earned familiarity, everything-is-a-token, WCAG AA). `web/src/ui/cx.ts` (class util).

**Where drift lives (the audit will quantify):** the two big pages (`ChatPage.tsx` 3384 LOC, `CodeCockpitPage.tsx` 3386 LOC, loop cockpits ~1000+ LOC each) predate later primitives and likely carry bespoke chrome + hardcoded values the allowlist tolerates; empty states vary (plan 43/49 add `EmptyState`); the two first-party UI apps (Minutes/Growth) don't use the system at all (plan 48 migrates them); dark/light parity and focus-visible/reduced-motion coverage are per-component, not audited system-wide.

## Design

- **S1 — The audit (measure before touching):** an automated inventory — extend the token-lint to *report* (not just fail on allowlist) every hardcoded color/spacing/radius/shadow/duration in `web/src`, grouped by file; a primitive-adoption scan (pages using bespoke chrome vs shell primitives); a WCAG scan (axe over each route) and a reduced-motion/focus-visible coverage check. Output: `docs/design/consistency-audit.md` — a ranked drift list. **No fixes in S1** — this is the map.
- **S2 — Token + primitive hardening:** fix the ranked drift — replace hardcoded values with tokens (shrinking the allowlist as you go), migrate bespoke chrome to shell primitives, consolidate near-duplicate components (e.g. multiple ad-hoc button/menu variants → the canonical primitive). Each fix shrinks the lint allowlist; the end state = allowlist near-empty, lint strict.
- **S3 — Interaction + a11y + responsive parity:** standardize interaction patterns (list selection, empty states via the shared `EmptyState`, loading skeletons, error states, confirm dialogs — one pattern each, documented in a `docs/design/patterns.md` gallery); WCAG AA parity pass (every route: contrast, focus-visible ring, keyboard nav, reduced-motion alternative); dark/light parity pass (every token used in both, no theme-only hardcodes); responsive pass (no horizontal body scroll; the phone-viewport paths for plan 44's companion). Ratchet: the lint + an a11y CI check now *block* regressions (feeds plan 33 rails).

## Contracts & Interfaces (mostly frontend hardening; the enforceable rails pinned — conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — The consistency rails (CI-enforced, mount in plan 33 `ci.yml` web/rails)
- **Token lint (strict):** `web/src/design/tokenLint.test.ts` extended — every hardcoded design value in `web/src` must be a token or an explicit, justified allowlist entry (`tokenLint.allowlist.json` with a `reason` field per entry). Ratchets down each session; end state near-empty.
- **A11y check:** axe-core over each route (headless) in web CI — no serious/critical violations; reduced-motion + focus-visible asserted.
- **Primitive-adoption check:** a lint that flags bespoke chrome elements (raw `<button>`, ad-hoc modals) outside `web/src/ui/` primitives, allowlisted with reasons.

### C2 — Pattern gallery (`docs/design/patterns.md` + a Storybook-lite route, optional)
Canonical usage of each shared primitive + each interaction pattern (selection, empty, loading, error, confirm). This is the "how to stay consistent" reference every page-touching plan cites.

### Integration points
- **Touches:** every `web/src/pages/*` surface (consistency fixes) + `web/src/ui/*` (consolidation) + `web/src/design/*` (token/lint hardening).
- **Consumed by:** 52 (motion animates consistent components), 48 (apps import the hardened primitives via UI SDK), 43/49/50 (new surfaces inherit the patterns + `EmptyState`).
- **Rails into:** plan 33 CI (`web`/`rails` jobs gain the token-lint-strict + axe checks).
- **Authority:** `web/DESIGN.md` + `web/PRODUCT.md` (unchanged — this plan conforms TO them).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — The audit (map only)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Extend token-lint into a reporter: emit every hardcoded design value in `web/src` grouped by file + type, without failing the build | `web/src/design/tokenLint.test.ts` (+ a report script) | report lists all drift with file:line; counts by category |
| T1.2 | Primitive-adoption + a11y + parity scan → the ranked audit doc | `docs/design/consistency-audit.md`, scan scripts | audit ranks the worst offenders (likely ChatPage/CodeCockpit/loop cockpits); each entry actionable |
| V1 | Validation: the audit is complete + accurate (spot-check 5 findings against the code) | — | holds; NO fixes made this session |

### Session 2 — Token + primitive hardening

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Fix the top-ranked drift: hardcoded values → tokens; shrink the allowlist (each removed entry = a real fix) | the ranked pages, `tokenLint.allowlist.json` | allowlist materially smaller; those pages token-clean; visual diff shows no regression (screenshot compare) |
| T2.2 | Migrate bespoke chrome → shell primitives; consolidate duplicate components into the canonical primitive | outlier pages, `web/src/ui/` | duplicates removed; pages use shared primitives; suite + build green |
| T2.3 | Add any genuinely-missing primitive to the system ONCE (if the audit found a real gap) with a pattern-gallery entry | `web/src/ui/`, `docs/design/patterns.md` | new primitive documented + adopted where needed; not a one-off |
| V2 | Validation: the hardened pages look identical-or-better; token-lint allowlist near-empty for them | — | holds |

### Session 3 — Interaction + a11y + responsive parity

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Standardize interaction patterns (selection/empty/loading/error/confirm) across pages via shared primitives; document each in `patterns.md` | pages, `docs/design/patterns.md` | each pattern has one implementation + one doc entry; pages conform |
| T3.2 | WCAG AA parity pass: axe over every route → fix serious/criticals; focus-visible + keyboard nav + reduced-motion alternatives everywhere | pages, `ui/` | axe clean on all routes; keyboard-only walkthrough of the app works |
| T3.3 | Dark/light + responsive parity: every token used in both themes (no theme-only hardcodes); no horizontal body scroll at phone widths | pages, tokens | theme toggle on every page shows no broken surface; phone-width pass clean |
| T3.4 | Ratchet: mount token-lint-strict + axe into plan 33 web/rails CI (block regressions) | `.github/workflows/ci.yml` (coordinate with plan 33) | a new hardcoded value or a11y regression turns CI red |
| V3 | Validation: full app walkthrough in both themes, keyboard-only, reduced-motion, and a phone viewport — consistent throughout | — | holds |

## Owner tasks (real world)
1. **Review the audit** (S1 output) and confirm the priority order — you may weight the surfaces you use most.
2. **Taste sign-off on any consolidation** where two patterns merge into one (e.g. if two menu styles become one) — that's a visual-identity call.
3. Confirm the WCAG target stays **AA** (PRODUCT.md says AA) — AAA is not proposed.

## Risks & open questions
- **"Consistency" creeping into "redesign"** — the soul guardrail forbids it; every S2/S3 change is verified by screenshot-diff to show no unintended visual change (only conformance). If a fix *requires* a visual change, that's an owner taste call (task 2), not silent scope.
- **The two mega-pages** (ChatPage/CodeCockpit) are the highest-effort, highest-risk fixes — do them in small, screenshot-verified increments; a full rewrite is explicitly out of scope (would be its own plan).
- **Open:** Storybook-lite route vs a static pattern doc — default: static `patterns.md` (zero new dep); add a live gallery route only if the team grows (ratchet).
