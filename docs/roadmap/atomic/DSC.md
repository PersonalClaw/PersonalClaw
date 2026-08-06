# DESIGN-SYSTEM-CONSISTENCY — atomic plans

**Source plan:** [`DESIGN-SYSTEM-CONSISTENCY`](../plans/DESIGN-SYSTEM-CONSISTENCY.md)  
**Code:** `DSC`  
**Source status:** done

DSC is owner-closed DONE (2026-07-24): a design-system consistency audit + hardening across S1/S2/S3, executed in ~90 cycles on branch feature-design-system-consistency (not pushed; no PR numbers in the log). 10 done atoms + 2 todo tail atoms (authenticated axe-per-route CI gate + V3 walkthrough; ErrorState primitive + CodeCockpitPage-class raw-button redesign) that the owner deferred as follow-on plans.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DSC-1` | ✅ | S1 audit: drift reporter + a11y static scan + ranked audit doc (T1.1/T1.2/V1) | — | web/src/design/consistencyAudit.report.ts (scanDrift/rankFiles + scanA11y) + consistencyAudit.test.ts emit docs/design/consistency-audit.{json,md} ranked worst-first; V1 spot-check of 5 findings holds; vitest green; no fixes made (map-only). Recorded the dominant drift is primitive-adoption (420 raw <button> + 206 raw inputs), token layer near-clean. |
| `DSC-2` | ✅ | Playwright visual-regression + axe a11y e2e harness with captured baselines (S2 prerequisite infra) | — | web/playwright.config.ts + e2e/{routes,helpers,visual,a11y}.spec.ts run 16 routes×2 themes; @playwright/test + @axe-core/playwright locked in package-lock; 32 Darwin visual baselines captured deterministic (2× zero-diff) against the tokenless dev gateway recipe; axe wcag2aa suite 32/32; blank-shell blocker root-caused (React-18 pin from main) and closed by rebase. |
| `DSC-3` | ✅ | CI consistency rails / ratchets: primitive-adoption + inline-font-weight caps + token-lint-strict (C1/T3.4) | `DSC-1` | web/src/design/primitiveAdoption.test.ts + baseline.json ratchet rawButton/rawInput/rawDialog/inlineFontWeight (may only decrease) live in the CI web vitest step alongside tokenLint-strict; a synthetic +1 raw element turns CI red and reverts clean; count-based, no browser needed. (The third C1 rail — axe-per-route — is the deferred DSC-11 tail.) |
| `DSC-4` | ✅ | Inline font-weight canonicalization: fvs()/withWeight() + .fw-* utilities + 156-site migration (T2.1) | `DSC-3` | web/src/design/fontWeight.ts + tokens.css .fw-400..650 added; every migratable inline fontVariationSettings 'wght' site (156 across cycles 18–27, byte-identical) routes through fvs()/withWeight(); inlineFontWeight ratchet driven 168→7 (the 7 liveMarkdown CodeMirror-theme sites legitimately stay); visual 64/64, no baseline moved. |
| `DSC-5` | ✅ | Canonical form-field family: ui/forms convergence + size/surface scale + NumberField (T2.3) | `DSC-1`, `DSC-2` | dead ui/TextField removed; the adopted formControls generics relocated to ui/forms.tsx (Field a11y wiring + TextInput/TextArea/DateInput/Select/ChipInput/NumberField) with 24 importers repointed; principled size×surface scale byte-identical at comfortable density (density-correct px-m); forms.test.tsx locks the scale + accessible-name logic; ratchet rawInput drawn down in lockstep. |
| `DSC-6` | ✅ | Raw-input migration: SearchField primitive + search/query/textarea input drawdown (T2.2/S2) | `DSC-5`, `DSC-3`, `DSC-2` | ui/SearchField (overlay\|inline variants, spring-pop clear) + leadingIcon/mono/type growth on TextInput; compound-search, submit-a-query, model-provider text fields, and the dense-textarea cluster migrated onto SearchField/TextInput/TextArea; rawInput ratchet 206→149; byte-identical or owner-authorized (left-3/pl-9 geometry) pixel moves, live-validated, visual 64/64. |
| `DSC-7` | ✅ | Type-scale normalization: off-ramp text-[…] sweep + ramp reconciliation + caption tier (T2.1) | `DSC-2` | ~934 off-ramp text-[Nrem/px] literals across 139 files snapped to the blessed ramp; DESIGN.md typography frontmatter rewritten as a strict superset mirroring tokens.css; caption 0.75rem role added to tokens.css; impeccable font-size detector clean (EXIT=0) on all changed files; the 2 legitimately-moved Tools baselines regenerated; rendered-document .doc scale deliberately out of scope. |
| `DSC-8` | ✅ | Raw-button consolidation + shared-primitive extraction (SquareIconButton, GraphZoomControls, FormFooter, AddItemButton, QuietButton, SelectionPill, InlineError, Centered, VariableRow) + mega-page triage (T2.2/T2.3) | `DSC-3`, `DSC-2` | raw-button count 420→~314 via byte-identical primitive extraction (SquareIconButton +danger tone, GraphZoomControls, VariableRow, FormFooter, AddItemButton, QuietButton, SelectionPill, InlineError, Centered) and per-role mega-page triage (labelled→Button, round→IconButton, dense-square→SquareIconButton; semantic rows stay raw); each ratcheted down in lockstep; visual/live-validated; pattern-gallery entries added. |
| `DSC-9` | ✅ | Owner taste-call convergence batch G1–G9 + follow-on raw-button waves (owner-authorized pixel moves) | `DSC-8` | G1–G9 executed as 9 atomic convergence cycles: *-error→*-danger token normalization, LoopCockpit back-icon + add-prerequisite, ghost Cancel/Done, retry family→Button, <TextLink> extraction (16 sites), tonal/primary convergence, full RowActionButton/remove-X family→SquareIconButton tone=danger, CapRow picker unification, InlineError fold-all + warn→danger re-tone; rawButton 314→285; each live-validated dark+light, baselines regenerated where pixels moved. |
| `DSC-10` | ✅ | S3 interaction-pattern standardization + a11y/dark-light/responsive parity + pattern gallery (T3.1/T3.2/T3.3, C2) | `DSC-2`, `DSC-8` | EmptyState/SlotEmptyState + confirm/loading/selection/error patterns converged and documented in docs/design/patterns.md; dynamic axe serious/critical violations cleared on default scheme; all 11 color schemes reach WCAG AA in both modes with permanent schemeContrast.test.ts guard (67 assertions); settings bento inert-switch bug fixed; phone-width 375px sweep shows 0 horizontal-overflow routes. |
| `DSC-11` | ⬜ | Deferred tail (environment-blocked): authenticated seeded per-route axe CI gate (T3.2/T3.4) + V3 full-app walkthrough | `DSC-2`, `DSC-3`, `EXT:CI-RELEASE-ENGINEERING:seeded authenticated per-route axe CI harness in ci.yml web/rails` | e2e/a11y.spec.ts mounted into ci.yml web/rails as a blocking gate against a seeded authenticated session, no serious/critical on any route; V3 full-app keyboard-only + reduced-motion + phone-viewport walkthrough passes in both themes — requires a seeded, authenticated, per-route CI harness not stood up in-plan. |
| `DSC-12` | ⬜ | Deferred tail (owner/design): ErrorState primitive + harness-gated CodeCockpitPage-class raw-button redesign | `DSC-8`, `DSC-10` | ErrorState primitive extracted only once a second faithful adopter exists (fold the CodeSection↔CodeCockpitPage load-error twins), and the CodeCockpitPage-class bespoke raw-buttons migrated where that is a deliberate pixel/semantics redesign — both owner/design decisions taken as follow-on plans (not manufactured sub-5-copy clusters). |

## Atom scopes

### `DSC-1` — S1 audit: drift reporter + a11y static scan + ranked audit doc (T1.1/T1.2/V1)

**Status:** done

Session 1 — The audit (map only): T1.1 token-lint reporter, T1.2 primitive-adoption/a11y/parity scan → docs/design/consistency-audit.md; V1

**Done when:** web/src/design/consistencyAudit.report.ts (scanDrift/rankFiles + scanA11y) + consistencyAudit.test.ts emit docs/design/consistency-audit.{json,md} ranked worst-first; V1 spot-check of 5 findings holds; vitest green; no fixes made (map-only). Recorded the dominant drift is primitive-adoption (420 raw <button> + 206 raw inputs), token layer near-clean.

### `DSC-2` — Playwright visual-regression + axe a11y e2e harness with captured baselines (S2 prerequisite infra)

**Status:** done

Design S1→S2 safety rail; Risks (screenshot-diff verification); Execution log cy3/cy13/cy14

**Done when:** web/playwright.config.ts + e2e/{routes,helpers,visual,a11y}.spec.ts run 16 routes×2 themes; @playwright/test + @axe-core/playwright locked in package-lock; 32 Darwin visual baselines captured deterministic (2× zero-diff) against the tokenless dev gateway recipe; axe wcag2aa suite 32/32; blank-shell blocker root-caused (React-18 pin from main) and closed by rebase.

### `DSC-3` — CI consistency rails / ratchets: primitive-adoption + inline-font-weight caps + token-lint-strict (C1/T3.4)

**Status:** done

C1 — The consistency rails (CI-enforced); T3.4 ratchet; Execution log cy6/cy11

**Done when:** web/src/design/primitiveAdoption.test.ts + baseline.json ratchet rawButton/rawInput/rawDialog/inlineFontWeight (may only decrease) live in the CI web vitest step alongside tokenLint-strict; a synthetic +1 raw element turns CI red and reverts clean; count-based, no browser needed. (The third C1 rail — axe-per-route — is the deferred DSC-11 tail.)

### `DSC-4` — Inline font-weight canonicalization: fvs()/withWeight() + .fw-* utilities + 156-site migration (T2.1)

**Status:** done

Session 2 T2.1 conformance; Execution log cy8–cy10, cy18–cy27 (INLINE FONT-WEIGHT SUB-THREAD COMPLETE)

**Done when:** web/src/design/fontWeight.ts + tokens.css .fw-400..650 added; every migratable inline fontVariationSettings 'wght' site (156 across cycles 18–27, byte-identical) routes through fvs()/withWeight(); inlineFontWeight ratchet driven 168→7 (the 7 liveMarkdown CodeMirror-theme sites legitimately stay); visual 64/64, no baseline moved.

### `DSC-5` — Canonical form-field family: ui/forms convergence + size/surface scale + NumberField (T2.3)

**Status:** done

Session 2 T2.3 (missing primitive) + Owner task 2 (two-patterns→one); Execution log cy5, cy17, cy28–cy30, cy35

**Done when:** dead ui/TextField removed; the adopted formControls generics relocated to ui/forms.tsx (Field a11y wiring + TextInput/TextArea/DateInput/Select/ChipInput/NumberField) with 24 importers repointed; principled size×surface scale byte-identical at comfortable density (density-correct px-m); forms.test.tsx locks the scale + accessible-name logic; ratchet rawInput drawn down in lockstep.

### `DSC-6` — Raw-input migration: SearchField primitive + search/query/textarea input drawdown (T2.2/S2)

**Status:** done

Session 2 T2.2/T2.3; Execution log cy31–cy32, cy34 (SearchField), cy10–cy12, cy16

**Done when:** ui/SearchField (overlay|inline variants, spring-pop clear) + leadingIcon/mono/type growth on TextInput; compound-search, submit-a-query, model-provider text fields, and the dense-textarea cluster migrated onto SearchField/TextInput/TextArea; rawInput ratchet 206→149; byte-identical or owner-authorized (left-3/pl-9 geometry) pixel moves, live-validated, visual 64/64.

### `DSC-7` — Type-scale normalization: off-ramp text-[…] sweep + ramp reconciliation + caption tier (T2.1)

**Status:** done

Session 2 T2.1 pixel-moving normalization; Execution log cy36 (owner-directed)

**Done when:** ~934 off-ramp text-[Nrem/px] literals across 139 files snapped to the blessed ramp; DESIGN.md typography frontmatter rewritten as a strict superset mirroring tokens.css; caption 0.75rem role added to tokens.css; impeccable font-size detector clean (EXIT=0) on all changed files; the 2 legitimately-moved Tools baselines regenerated; rendered-document .doc scale deliberately out of scope.

### `DSC-8` — Raw-button consolidation + shared-primitive extraction (SquareIconButton, GraphZoomControls, FormFooter, AddItemButton, QuietButton, SelectionPill, InlineError, Centered, VariableRow) + mega-page triage (T2.2/T2.3)

**Status:** done

Session 2 T2.2/T2.3; Execution log cy4, cy16, cy37–cy50 (item-1 mega-page per-role triage; migrate-the-idiom-not-the-page)

**Done when:** raw-button count 420→~314 via byte-identical primitive extraction (SquareIconButton +danger tone, GraphZoomControls, VariableRow, FormFooter, AddItemButton, QuietButton, SelectionPill, InlineError, Centered) and per-role mega-page triage (labelled→Button, round→IconButton, dense-square→SquareIconButton; semantic rows stay raw); each ratcheted down in lockstep; visual/live-validated; pattern-gallery entries added.

### `DSC-9` — Owner taste-call convergence batch G1–G9 + follow-on raw-button waves (owner-authorized pixel moves)

**Status:** done

OWNER TASTE-CALL RULINGS (cy50 batch); Execution log cy51/cy2–cy9 (G1–G9) + cy14–cy15 (RowActionButton family)

**Done when:** G1–G9 executed as 9 atomic convergence cycles: *-error→*-danger token normalization, LoopCockpit back-icon + add-prerequisite, ghost Cancel/Done, retry family→Button, <TextLink> extraction (16 sites), tonal/primary convergence, full RowActionButton/remove-X family→SquareIconButton tone=danger, CapRow picker unification, InlineError fold-all + warn→danger re-tone; rawButton 314→285; each live-validated dark+light, baselines regenerated where pixels moved.

### `DSC-10` — S3 interaction-pattern standardization + a11y/dark-light/responsive parity + pattern gallery (T3.1/T3.2/T3.3, C2)

**Status:** done

Session 3 T3.1/T3.2/T3.3 (autonomous-completable) + C2 pattern gallery; Execution log cy7, cy12, cy33, dynamic-axe (S2 cy15), cy13, cy17, cy18

**Done when:** EmptyState/SlotEmptyState + confirm/loading/selection/error patterns converged and documented in docs/design/patterns.md; dynamic axe serious/critical violations cleared on default scheme; all 11 color schemes reach WCAG AA in both modes with permanent schemeContrast.test.ts guard (67 assertions); settings bento inert-switch bug fixed; phone-width 375px sweep shows 0 horizontal-overflow routes.

### `DSC-11` — Deferred tail (environment-blocked): authenticated seeded per-route axe CI gate (T3.2/T3.4) + V3 full-app walkthrough

**Status:** todo

Session 3 T3.2/T3.4 (axe-per-route CI rail) + V3 validation; Status line 'known, honestly-recorded tail'; cy13/cy18 environment-blocked notes

**Done when:** e2e/a11y.spec.ts mounted into ci.yml web/rails as a blocking gate against a seeded authenticated session, no serious/critical on any route; V3 full-app keyboard-only + reduced-motion + phone-viewport walkthrough passes in both themes — requires a seeded, authenticated, per-route CI harness not stood up in-plan.

### `DSC-12` — Deferred tail (owner/design): ErrorState primitive + harness-gated CodeCockpitPage-class raw-button redesign

**Status:** todo

Status line deferred items; patterns.md open '[ ] Error state'; Execution log cy17/cy18 (single-adopter ErrorState; harness-gated redesign lane 1)

**Done when:** ErrorState primitive extracted only once a second faithful adopter exists (fold the CodeSection↔CodeCockpitPage load-error twins), and the CodeCockpitPage-class bespoke raw-buttons migrated where that is a deliberate pixel/semantics redesign — both owner/design decisions taken as follow-on plans (not manufactured sub-5-copy clusters).

