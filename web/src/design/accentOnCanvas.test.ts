import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Accent TEXT on the canvas uses the emphasis shade ────────────────────────────────────────────
//
// The shell paints `--color-canvas` behind every route (`background: var(--color-canvas)`), and accent
// text lands on it wherever a panel does not paint its own surface. Measured in light by axe AND
// `ux-audit`, agreeing: `--color-primary` on `--color-canvas` is **4.37:1** where 4.5 is required.
//
// 🔑 THE GUARANTEE WAS MEASURED AGAINST THE WRONG BACKDROP. `schemeContrast.test.ts` asserted accent
// text against **white**, where every scheme passes (4.83-11.37). Computed across the curated set for
// the backdrop the app actually paints:
//
//     primary → canvas            FAILS in **7 of 12** schemes (4.37-4.41)
//     primary-emphasis → canvas   passes in **all 12**          (worst 4.82, coral 6.0)
//
// So this is a pairing the design system already ships, not a new colour — the same shape as cycle
// 146's `accentChip`. That rail now measures the canvas dimension per scheme; this one pins the call
// sites, because a token rail cannot see which ink a component chose (reverting the Studio tab to the
// failing colour left `schemeContrast` green — measured).
//
// 🔑 SCOPE, MEASURED RATHER THAN GUESSED. A runtime census over 20 routes at BOTH viewports found
// exactly **two** small accent texts painted on the canvas: the Memory Studio tab (both viewports) and
// the inbox-settings link (phone only, where the panel goes full-width). The other 43 small-coral-text
// sites sit on `--color-surface`/`surface-container` (white in light), where the same ink passes at
// 4.83 — so this change does NOT pre-empt the owner's standing "coral as accent text" decision; it
// fixes the two places where the measurement fails today.
//
// 🪤 AND THE HELPER THAT READS THE TOKEN NEEDED FIXING FIRST. Slicing tokens.css from
// `indexOf('.light')` landed in a COMMENT that says ".light mode" 380 characters in, so it read the
// DARK canvas (#0f0f0f) and made the new assertion red at 2.89:1 against a backdrop no light-mode user
// ever sees. Match the rule block, not the first occurrence of the selector's name.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

// ── Cycle 155: THE THIRD GROUND — a small accent CHIP sits on `--color-surface-high` ─────────────
//
// axe on `#/knowledge` → Intents (a tab that had never been driven): the "proposes skill" chip measured
// **3.79 dark / 3.22 light** against the 4.5 floor. It was `text-primary/80`, and the alpha is only part
// of it — computed across the curated set on that ground:
//
//     primary/80 → surface-high         3.79 / 3.22   (the shipped value)
//     primary    → surface-high         worst **4.26** light — fails in 10 of 12 schemes
//     primary-emphasis → surface-high   worst **4.70** light, 7.01 dark — passes all 12
//
// So dropping the alpha would NOT have fixed it. Same answer as the canvas, third ground, and
// `ui/Markdown`'s inline-code chip had already reached for `text-primary-emphasis` on its own — the
// precedent was in the tree before this cycle named the rule.
//
// Nine sites where accent ink and `bg-surface-high` are on the SAME element now use the emphasis token.
// One is axe-measured; the rest are token-decidable at their rendered size (the markdown body is 15px,
// so `text-[0.8em]`/`[0.85em]`/`[0.92em]` are 12-13.8px — all under the 18.66px large-text threshold):
//
//   knowledge/KnowledgeListPage  the "proposes skill", in-progress and enriching chips   ← axe-measured
//   ui/Markdown                  the memory-citation chip (12px) and file-path chip (12.75px)
//   chat/PasteChip               the two file chips (13.8px, 12.75px)
//   loops/LoopPlanReview         two buttons whose ground BECOMES surface-high on hover
//
// 🪤 DELIBERATELY NOT INCLUDED, with reasons, so the next pass does not "finish the job":
//   ui/Markdown inline code       already `text-primary-emphasis` — the precedent
//   code/CodeCockpitPage          its accent ink pairs with `bg-primary/15`, the accent-CHIP family
//                                 (cycle 146), not this ground
//   knowledge/KnowledgeDetail     an ICON at `text-primary/70`; non-text carries a 3:1 floor, and it
//                                 measures 3.79 — passing
//
// 🪤 AND THE SIZE HAD TO BE MEASURED, NOT INFERRED FROM THE CLASS. An `em` size says nothing on its own;
// the same class is a failure at a 15px base and compliant large text at 24px. Read the computed
// `fontSize` off the rendered node.

describe('the two canvas-painted accent texts use the emphasis shade', () => {
  it("the Memory Studio tab's active ink is the emphasis token", () => {
    const code = read('pages/settings/MemoryPanel.tsx')
    expect(code).toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary-emphasis\)'/)
    expect(code, 'the border may stay on the base accent — it is not text')
      .toMatch(/borderColor: 'var\(--color-primary\)'/)
  })

  it('the inbox-settings link uses the emphasis ink, in both copies of the panel', () => {
    // The sites now speak `ui/TextLink` (the adoption rail converged the idiom), so the emphasis
    // guarantee lives in two halves: the call site asks for the emphasis ink…
    for (const rel of ['pages/inbox/InboxSettingsPanel.tsx', 'pages/settings/InboxSettingsPanel.tsx']) {
      expect(read(rel), `${rel} link ink`).toMatch(/<TextLink[^>]*ink="emphasis"[^>]*>Open notification rules/)
    }
    // …and the primitive maps that ink to the measured class. Both halves pinned, so neither a
    // call-site downgrade to `ink="primary"` nor a primitive remap can silently reopen the 4.37:1 gap.
    expect(read('ui/TextLink.tsx'), 'TextLink emphasis ink mapping')
      .toMatch(/emphasis:\s*'text-primary-emphasis'/)
  })

  it('neither site carries the old failing ink any more', () => {
    expect(read('pages/inbox/InboxSettingsPanel.tsx'))
      .not.toMatch(/className="text-primary text-\[0\.8125rem\] hover:underline">Open notification rules/)
    expect(read('pages/settings/MemoryPanel.tsx'))
      .not.toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary\)'/)
  })

  it('the call sites carry the measurement, not just the token', () => {
    // A bare token swap reads like a style preference; the number is what stops it being reverted.
    expect(read('pages/settings/MemoryPanel.tsx')).toMatch(/4\.37:1/)
  })

  it('the scheme rail now measures the canvas, which is what makes this rule enforceable', () => {
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on the CANVAS/)
    expect(rail, 'and it reads the LIGHT block, not the first mention of .light')
      .toMatch(/\\.light\\s\*\\\{/)
  })

  it('the emphasis token exists in light for every scheme', () => {
    // The pairing only works because each scheme defines it; `schemes.ts` maps [dark, light].
    const schemes = read('design/schemes.ts')
    const defs = [...schemes.matchAll(/primaryEmphasis:\s*\['#[0-9a-fA-F]{6}',\s*'#[0-9a-fA-F]{6}'\]/g)]
    expect(defs.length, 'schemes defining a light emphasis shade').toBeGreaterThanOrEqual(12)
  })
})

// ── Cycle 158: THE FOURTH GROUND — a dashboard ROW, and the ledger's oldest carried contrast item ──
//
// `#/dashboard` in LIGHT reported **8 blocking** contrast findings (2 at phone): the Action Centre's
// `Reply` actions at **4.46:1** against a 4.5 floor, 15px/400. axe agreed [serious]. This is the
// "dashboard Reply contrast" the ledger has carried since cycle ~139 — bundled under the owner's
// standing "coral as accent TEXT" question, and **no longer an aesthetic question at all**: cycles 146,
// 147 and 155 established the token for accent text that must clear AA, and this is its fourth ground.
//
// 🪤 THE GROUND IS NOT A TOKEN I WOULD HAVE GUESSED. Read off the rendered row: **rgb(244,246,249)** —
// which is `--color-surface-low` (#f4f6f9), matching neither `surface` (#ffffff), `surface-high`
// (#eef1f5), `canvas` (#f0f4f8) nor `surface-highest`. Computed against `surface-container` (white) the
// same ink measures **4.83 and passes**; against the real ground it is 4.46 and fails. Cycle 147's
// lesson, third time: **read the backdrop off the node, not out of the token you assume.**
//
//     primary → surface-low            worst **4.46** light — fails in **6 of 12** schemes
//     primary-emphasis → surface-low   worst **4.92** light, 8.38 dark — passes all 12
//
// 🔑 AND ONLY THE `primary` TONE WAS BROKEN. Every other `RowAction` tone measures 5.59-10.11 on that
// ground (ok, danger, warn, info, on-surface-var, on-surface-low). `--color-primary` is the token tuned
// for brand presence; the emphasis shade is the legible sibling, in both modes — it is DARKER in light
// (#c8452e → #a33922) and LIGHTER in dark (#ff6b5b → #ff9a86), i.e. further from the ground either way.
//
// One line in `dashboard/widgets/kit.tsx` fixes all four `RowAction tone="primary"` call sites (Reply,
// Answer, Send, Apply update). After: **0 blocking at all four theme × viewport combinations**, and the
// live row measures 6.13 light / 8.38 dark. Pixel cost: **0.1348% light / 0.1368% dark**, bounding box
// `684,402 57×375` — the column of `Reply` labels and nothing else; phone captures are 0%.
//
// ⚠️ DARK MOVED WITHOUT AN AA REASON, and that is stated rather than hidden: dark was already passing at
// 6.16. The token is applied unconditionally because it is mode-aware by construction and because
// forking it per mode would invent an idiom the three earlier grounds do not use.
//
// 🔑 `MetaPill`'s primary tone was checked and left alone — it already uses `accentChip`
// (primary-container / on-primary-container), the cycle-146 pairing.

describe('accent chips on surface-high use the emphasis shade', () => {
  const SITES: [string, RegExp][] = [
    ['pages/knowledge/KnowledgeListPage.tsx', /bg-surface-high px-1\.5 text-primary-emphasis text-\[0\.75rem\]/],
    ['ui/Markdown.tsx', /bg-surface-high px-1\.5 align-baseline text-\[0\.8em\] text-primary-emphasis/],
    ['ui/Markdown.tsx', /text-\[0\.85em\] font-mono text-primary-emphasis underline/],
    ['pages/chat/PasteChip.tsx', /text-\[0\.92em\] text-primary-emphasis/],
    ['pages/chat/PasteChip.tsx', /text-\[0\.85em\] text-primary-emphasis/],
    ['pages/loops/LoopPlanReview.tsx', /text-\[0\.75rem\] text-primary-emphasis hover:bg-surface-high/],
    ['pages/loops/LoopPlanReview.tsx', /text-\[0\.8125rem\] text-primary-emphasis hover:bg-surface-high/],
  ]
  for (const [rel, re] of SITES) {
    it(`${rel} pairs surface-high with the emphasis token (${re.source.slice(0, 34)}…)`, () => {
      expect(read(rel)).toMatch(re)
    })
  }

  it('no site pairs surface-high with the plain or alpha accent on the same element', () => {
    // THE RATCHET, and the reason it is written from the other side: a new chip that reaches for
    // `text-primary` on this ground cannot pass AA in 10 of 12 schemes, so it fails here first.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
        for (const m of line.matchAll(/className=(?:\{`|")([^"`]*)(?:`\}|")/g)) {
          const cls = m[1]
          if (!/(?:^|\s)bg-surface-high(?:\s|$)/.test(cls)) continue
          if (/(?:^|\s)text-primary(?:\/\d+)?(?:\s|$)/.test(cls)) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
        }
      })
    }
    expect(offenders, `these cannot reach AA on this ground:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the scheme rail carries the number for every scheme, not just the default', () => {
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on SURFACE-HIGH/)
    expect(rail, 'both modes').toMatch(/contrast\(emphasis\.dark, HIGH_DARK\)/)
  })
})


describe('a dashboard row action uses the emphasis shade', () => {
  it("RowAction's primary tone is the emphasis ink", () => {
    const code = read('pages/dashboard/widgets/kit.tsx')
    expect(code).toMatch(/primary: 'text-primary-emphasis hover:bg-primary-container\/40'/)
    expect(code, 'the failing ink must be gone').not.toMatch(/primary: 'text-primary hover:bg-primary-container/)
  })

  it('its sibling tones are untouched, because they already pass on that ground', () => {
    // Measured on `--color-surface-low`: ok 5.89/6.87, danger 5.95/5.95, on-surface-var 5.59/10.11.
    // Changing them would be a redesign, not a fix.
    const code = read('pages/dashboard/widgets/kit.tsx')
    expect(code).toMatch(/ok: 'text-ok hover:bg-ok\/15'/)
    expect(code).toMatch(/danger: 'text-danger hover:bg-danger\/15'/)
    expect(code).toMatch(/default: 'text-on-surface-var hover:bg-surface-highest hover:text-on-surface'/)
  })

  it('the call sites it reaches are the four dashboard row actions', () => {
    // If a fifth appears it inherits the fix; this pins the population the measurement covered.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const sites = walk(join(SRC, 'pages/dashboard')).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/<RowAction tone="primary"/g)].map(() => abs.slice(SRC.length + 1)))
    expect(sites.length, 'RowAction tone="primary" call sites on the dashboard').toBeGreaterThanOrEqual(4)
  })

  it('the scheme rail carries the fourth ground for every scheme', () => {
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on SURFACE-LOW/)
    expect(rail, 'both modes').toMatch(/contrast\(emphasis\.dark, LOW_DARK\)/)
  })
})

// ── Cycle 172: THE FIRST GROUND AGAIN, reached through a TONE REGISTRY instead of a class list ──
//
// The canvas ground was settled in cycle 147, and this rail pinned the two sites it had measured —
// both of which write their ink **literally** (`text-primary-emphasis`, or an inline
// `color: 'var(--color-primary-emphasis)'`). A third site was invisible to that shape: the Knowledge
// breadcrumb's type segment picks its ink from `knowledgeMeta`'s `tone` field, so no literal accent
// token appears at the call site at all. Re-running the ground's measurement over the CURRENT surface
// inventory found it — 51 surfaces in light, the inventory having grown from the 20 cycle 147 swept.
//
// 🔑 THE WHOLE-INVENTORY CENSUS, so the population is stated rather than implied. Nine contrast
// failures in light across 51 surfaces, in exactly THREE families, and only one is this ground:
//
//   knowledge-detail   "Note" breadcrumb segment   **4.37**  primary on CANVAS          ← this cycle
//   triggers           "runs on its own" chip ×8   3.97      primary on its OWN 14% tint
//   inbox-proposals    "Proposals 35" tab          3.3       on an absolute sibling pill
//
// The other two are DELIBERATELY NOT FIXED HERE. The triggers chip paints `primary/14` behind its own
// text — that is the accent-CHIP family (cycle 146), the same call cycle 155 made when it left
// `CodeCockpitPage`'s `bg-primary/15` alone; 3.97 is precisely `primary` over its own 14% tint, so it
// is one measurement, not a coincidence. The inbox tab's ground is a positioned sibling, a fourth
// shape again. One family per change.
//
// 🔑 ONLY `primary` IS BROKEN ON THIS GROUND — recomputed independently here, and it agrees with the
// three earlier grounds: on `--color-canvas` in light, info 5.74, ok 5.77, warn 5.71, danger 5.83 all
// pass; `primary` alone lands at 4.37. So the remap is one tone wide, not a sweep of the registry.
//
// 🪤 AND THE REGISTRY IS THE WRONG PLACE TO FIX IT — the trap this cycle had to avoid. `knowledgeMeta`'s
// `tone` is consumed by EIGHT other call sites (`ArtifactCard` ×4, `ArtifactViewer`, `KnowledgeDetail`
// ×2, `NotificationBell`), and every one of them inks an ICON, which carries a 3:1 non-text floor and
// already passes at 4.37. Cycle 155 examined that very icon and left it. Editing the shared registry to
// fix one text label would have moved coral on five surfaces for no AA reason — so the fix is a
// ground-named helper at the call site, which is what cycle 158 did with `RowAction`'s tone map.
//
// Measured after, on the live surface: **6.0:1** light, 9.33 dark, and knowledge-detail reports 0
// blocking contrast findings at both themes. The validation home holds 26 knowledge items of which
// **4 are primary-toned** (3 note + 1 fleeting), so the fix is visible on 4 of 26 detail routes and the
// other 22 (bookmark/gist, info tone) are unchanged — which is why the pixel diff is small by design.

describe('a tone-registry ink painted on the canvas uses the emphasis shade', () => {
  it('the breadcrumb type segment inks through the canvas helper, not the raw tone', () => {
    const code = read('pages/knowledge/KnowledgeDetailPage.tsx')
    expect(code).toMatch(/style=\{\{ color: canvasInk\(tm\.tone\) \}\}/)
    expect(code, 'the failing ink must be gone from the segment')
      .not.toMatch(/whitespace-nowrap" style=\{\{ color: tm\.tone \}\}/)
  })

  it('the helper remaps the one failing tone and is the identity for the rest', () => {
    // Behavioural, not textual: the point is that `info`/`ok`/`warn`/`danger` keep their own ink.
    const code = read('pages/knowledge/KnowledgeDetailPage.tsx')
    const m = code.match(/const canvasInk = \(tone: string\) => \((.+)\)\n/)
    expect(m, 'canvasInk must exist as a single-expression helper').toBeTruthy()
    const canvasInk = new Function('tone', `return (${m![1]})`) as (t: string) => string
    expect(canvasInk('var(--color-primary)')).toBe('var(--color-primary-emphasis)')
    for (const t of ['var(--color-info)', 'var(--color-ok)', 'var(--color-warn)', 'var(--color-danger)'])
      expect(canvasInk(t), `${t} passes on the canvas and must be left alone`).toBe(t)
  })

  it('the mapping is not vacuous — the registry still declares the tone it remaps', () => {
    // THE VACUITY FLOOR. If `knowledgeMeta` ever stops using `--color-primary`, `canvasInk` becomes
    // dead code that still reads as an enforced rule. Then this rail is the thing that must change,
    // deliberately, rather than quietly matching nothing.
    const meta = read('pages/knowledge/knowledgeMeta.ts')
    const primaryKinds = [...meta.matchAll(/key: '(\w+)'[^}]*tone: 'var\(--color-primary\)'/g)].map((x) => x[1])
    expect(primaryKinds, 'kinds whose tone this helper remaps').toEqual(['note', 'fleeting', 'journal'])
  })

  it('the shared registry is untouched, so the icons keep the base accent', () => {
    // The other eight consumers ink icons at a 3:1 floor. A future cycle that "finishes the job" by
    // moving the registry would move coral on five surfaces for no accessibility reason.
    expect(read('pages/knowledge/knowledgeMeta.ts'))
      .toMatch(/key: 'note', label: 'Note', icon: StickyNote, tone: 'var\(--color-primary\)'/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(read('pages/knowledge/KnowledgeDetailPage.tsx')).toMatch(/4\.37:1/)
  })

  it('this ground is already scheme-covered, which is what makes the remap safe in all 12', () => {
    // No new scheme assertion is needed: the canvas is the ground cycle 147 added to the scheme rail,
    // and `primary-emphasis` passes there in every scheme (worst 4.82, coral 6.0).
    expect(read('design/schemeContrast.test.ts')).toMatch(/primary-emphasis as accent text on the CANVAS/)
  })
})

// ── Cycle 614: THE STATE THE CENSUS COULD NOT SEE — the first-run overlay ─────────────────────────
//
// Every ground above was found by a runtime census over 20 routes at both viewports. That census could
// not observe the onboarding overlay at all, because it only renders on an **unconfigured home** and
// every sweep ran against a configured one. So the only screens a brand-new user sees had never been
// contrast-measured — and the overlay renders over EVERY route, so its one failure was the single
// blocking contrast defect on all of them.
//
// Driven on an empty home, walking the flow, measuring the backdrop off each node:
//
//   step 0  "Skip setup — start as Operator…"   13px/400  rgb(200,69,46) on rgb(240,244,248)  4.37 ✗
//   step 1  "Skip setup and go to the dashboard" 13px/400  same ink, same canvas               4.37 ✗
//   step 1  "Show all 15 model provider apps"    16px      on rgb(255,255,255)                 4.83 ✓
//   step 1  "Show all 7 web search apps"         16px      on rgb(255,255,255)                 4.83 ✓
//   step 1  "Set up later"                       16px      on rgb(255,255,255)                 4.83 ✓
//
// 🔑 THE PASSING SIBLINGS ARE THE POINT. Three TextLinks with the IDENTICAL ink pass at 4.83 because a
// card paints `--color-surface` behind them; the two that fail are the same element on the canvas,
// outside the card. Same component, same token, opposite verdicts — so the fix belongs at the call
// site, not in the primitive's default. Re-inking all ~16 TextLinks would change three compliant links
// and pre-empt the owner's standing "coral as accent text" decision.
//
// 🔑 SO THE PRIMITIVE GAINED AN `ink` PROP RATHER THAN A NEW DEFAULT, and it cannot be done through
// `className`: two colour utilities on one element resolve by **stylesheet order**, not by the order
// they are written, so `className="text-primary-emphasis"` would work or not depending on Tailwind's
// output. The prop makes the ground an explicit decision.
//
// 🪤 DARK WAS ALREADY FINE (6.85 on the canvas), so this is a light-only defect — but the class swap
// moves both modes, which the PR's AFTER captures show at both.
//
// The second site is token-decidable rather than driven, and says so: `Pointer` declares
// `bg-surface-high` on the link's own parent, the worst ground in the set (coral 4.26, failing in 10 of
// 12 schemes; emphasis 5.86). The `ready` step needs a completed flow to reach, and the same
// computation reproduces the driven 4.37 on the canvas exactly — so the arithmetic is validated on a
// measured site before being trusted on an unmeasured one.

describe('the first-run overlay inks its links by their ground', () => {
  const LINK = read('ui/TextLink.tsx')
  const ONB = read('app/Onboarding.tsx')

  it('TextLink exposes the ink as a prop, mapped to the shipped emphasis token', () => {
    expect(LINK).toMatch(/type Ink = 'primary' \| 'emphasis'/)
    expect(LINK).toMatch(/primary: 'text-primary',/)
    expect(LINK).toMatch(/emphasis: 'text-primary-emphasis',/)
    expect(LINK, 'and the class comes from the map, not a hardcoded ink').toMatch(/const cls = cx\(\s*INK\[ink\],/)
  })

  it('the default stays `primary`, so no compliant link moved', () => {
    // The blast-radius floor. If the default ever flips, ~16 links change colour app-wide and this
    // cycle's reasoning (the ground decides, not the component) no longer holds.
    expect(LINK).toMatch(/ink = 'primary'/)
  })

  it('the canvas-painted skip link takes the emphasis ink', () => {
    expect(ONB).toMatch(/<TextLink size="sm" ink="emphasis" onClick=\{skipSetup\}>/)
  })

  it('the surface-high Pointer link takes it too', () => {
    expect(ONB).toMatch(/<TextLink size="sm" ink="emphasis" onClick=\{\(\) => onExitTo\('inbox'\)\}>Open the Inbox instead<\/TextLink>/)
  })

  it('neither onboarding link carries the failing default any more', () => {
    expect(ONB).not.toMatch(/<TextLink size="sm" onClick=\{skipSetup\}>/)
    expect(ONB).not.toMatch(/<TextLink size="sm" onClick=\{\(\) => onExitTo\('inbox'\)\}>/)
  })

  it('the links INSIDE the step card keep the base ink — they measured 4.83 and pass', () => {
    // The vacuity floor for "the ground decides". These three are the evidence that the primitive's
    // default is correct; if they ever gain `ink="emphasis"`, this cycle's finding was mis-scoped and
    // the reasoning above needs rewriting rather than silently passing.
    const essentials = read('app/onboarding/EssentialsStep.tsx')
    const plain = [...essentials.matchAll(/<TextLink(?![^>]*\bink=)/g)]
    expect(plain.length, 'EssentialsStep links still on the default ink').toBeGreaterThanOrEqual(3)
  })

  it('both call sites carry the measurement, not just the token', () => {
    // The family's standing rule: a bare token swap reads like a style preference and gets reverted.
    expect(ONB).toMatch(/4\.37:1/)
    expect(ONB).toMatch(/4\.26:1/)
  })

  it('the emphasis shade exists in light for every scheme, on BOTH grounds this cycle touched', () => {
    // canvas is already scheme-covered (cycle 147). surface-high is the cycle-155 ground, also covered.
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on the CANVAS/)
    const schemes = read('design/schemes.ts')
    const defs = [...schemes.matchAll(/primaryEmphasis:\s*\['#[0-9a-fA-F]{6}',\s*'#[0-9a-fA-F]{6}'\]/g)]
    expect(defs.length, 'schemes defining a light emphasis shade').toBeGreaterThanOrEqual(12)
  })
})

// ── Cycle 615: THE FAMILY, CLOSED FOR RENDERED LINKS — an exhaustive census ───────────────────────
//
// Every ground above was found by a scan for elements where the ink and the background are declared on
// the SAME element. A link inside a tinted container is invisible to that shape, because the ground is
// on an ancestor — and a static grep cannot settle it either: 16 files contain both a `TextLink` and a
// `bg-surface-high`/`bg-canvas` class, which is co-occurrence, not containment.
//
// So every RENDERED accent link was measured instead: all 55 surfaces, on a **populated** home (an
// empty one never renders the data-dependent links), fresh browser context per route, backdrop read
// off each node. 25 links, 0 route errors:
//
//     ground                 links   failing
//     --color-surface          20        0     4.83 — the base ink is correct here
//     --color-canvas            4        4     4.37 — all four are `VoicePanel`'s ManageLink
//     --color-canvas (emph)     1        0     already converged by an earlier cycle
//
// Light only; the same sweep in dark reports 0 of 25, because the dark canvas gives 6.85.
//
// 🔑 FOUR RENDERED LINKS, TWO CALL SITES. `ManageLink` is one small component mounted twice — once for
// STT, once for TTS — so two `ink="emphasis"` props fix all four. The row is a bare `<div>` with no
// background of its own, which is exactly how an accent link ends up on the canvas without anything in
// its own JSX naming the ground.
//
// 🔑 THIS CLOSES THE FAMILY FOR RENDERED LINKS, and the census is the proof rather than the claim: the
// 20 passing links are what make "the ground decides, so the default stays `primary`" measured instead
// of asserted. What it does NOT cover: links that only render in states the sweep cannot reach (the
// first-run overlay was cycle 614's finding, for exactly this reason) and non-link accent text, which
// the four sections above own.

describe('the voice panel manage-links are inked for the canvas', () => {
  const VOICE = read('pages/settings/VoicePanel.tsx')

  it('both manage-links take the emphasis ink', () => {
    const emph = [...VOICE.matchAll(/<TextLink onClick=\{\(\) => go\('(models|providers)'\)\}[^>]*ink="emphasis"/g)]
    expect(emph.length, 'manage-links on the emphasis shade').toBe(2)
  })

  it('neither carries the failing default any more', () => {
    expect(VOICE).not.toMatch(/<TextLink onClick=\{\(\) => go\('models'\)\} icon=\{ArrowRight\} iconPosition="trailing" size="xs">/)
    expect(VOICE).not.toMatch(/<TextLink onClick=\{\(\) => go\('providers'\)\} icon=\{ArrowRight\} iconPosition="trailing" size="xs">/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(VOICE).toMatch(/4\.37:1/)
  })

  it("the panel's OTHER link keeps the base ink — it is on a surface and passes", () => {
    // The vacuity floor for "the ground decides". `Reset to default` measured 4.83 on
    // `--color-surface` in the same census. If it ever gains the emphasis ink, this cycle's scope was
    // wrong and the reasoning above needs rewriting rather than silently passing.
    expect(VOICE).toMatch(/<TextLink size="xs" onClick=\{async \(\) => \{/)
  })

  it('the primitive default is still `primary`, so the 20 passing links did not move', () => {
    expect(read('ui/TextLink.tsx')).toMatch(/ink = 'primary'/)
  })
})
