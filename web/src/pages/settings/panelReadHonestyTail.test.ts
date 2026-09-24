import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The last three settings panels whose failed read said nothing ─────────────────────────────
//
// Cycle 124 fixed the `personalclawConfig` family and left ~51 substituting readers with the rule: only
// worth a cycle where a substituted value decides what a CONTROL claims. Re-censused the 53 with that
// exact test — does the panel WRITE (patch/save/PUT), does it render editable controls, and does it read
// `error`? — and three came back positive. Driven at 500 with an intercept COUNTER, because a
// non-matching route pattern looks exactly like a clean result (it cost two wrong readings first: the
// real path is `/api/models/use-cases/<x>/settings`, not `/api/use-case-settings`):
//
//   panel                    intercepted   before                              after
//   #/settings/notifications      4        0 controls · 1 `aria-busy` skeleton  the alert + Retry
//                                          · **shimmering forever**, silent
//   #/settings/updates            2        same                                the alert + Retry
//   #/settings/voice              6        **2 switches + 1 input** from a      the alert + Retry
//                                          fabricated `{}`, silent
//
// 🔑 TWO SHAPES, ONE FAMILY. A substituted `null` is indistinguishable from "still loading" to a
// `if (!data) return <FormSkeleton/>` gate, so the panel shimmers forever (cycle 117's inbox shape); a
// substituted `{}` passes the gate and renders the form from fallbacks (cycle 124's shape). Same cause —
// the rejection never reached the hook — and the same fix: let it reach, branch on it first.
//
// 🔑 EVERY VOICE CONTROL PUTs ON CHANGE, so its version is the integrity one: a user "correcting" a switch
// that was never loaded writes the opposite of what they believe is stored.
//
// 🔑 WHAT KEEPS ITS FALLBACK, AND WHY — the decorating reads. `notificationRules` (a per-kind policy table
// BELOW the settings), `changelog` (a section further down), `modelsActive` (a readiness chip). Losing one
// degrades a section; losing the gating read fabricates the panel.

const SETTINGS = join(process.cwd(), 'src', 'pages', 'settings')
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')
const codeOf = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** [panel, the gating call that must be bare, the `what` its LoadError names] */
const PANELS: [string, string, string][] = [
  ['NotificationsPanel.tsx', 'api.notificationSettings()', 'notification settings'],
  ['UpdatesPanel.tsx', 'api.updateCheck()', 'update status'],
  ['VoicePanel.tsx', "api.useCaseSettings('stt')", 'speech settings'],
]

describe('a settings panel whose gating read fails says so', () => {
  for (const [panel, call, what] of PANELS) {
    it(`${panel} lets the rejection reach the hook`, () => {
      const code = codeOf(panel)
      expect(code, `${panel} must still make the call`).toContain(call)
      // 🪤 SAME LINE, not a character window: cycle 124's version reached the NEXT element of the same
      // `Promise.all` and blamed its legitimate fallback on this read.
      const line = code.split('\n').find((l) => l.includes(call)) ?? ''
      expect(line, 'a `.catch` chained onto the gating read fabricates the panel').not.toMatch(/\.catch\(\(\)\s*=>/)
    })

    it(`${panel} renders the failure instead of a forever-skeleton`, () => {
      const code = codeOf(panel)
      expect(code).toContain(`<LoadError what="${what}" error={loadErr} onRetry={refresh} />`)
      const errAt = code.search(/<LoadError\b/)
      const skelAt = code.search(/<FormSkeleton\b/)
      expect(errAt, 'the error branch must precede the skeleton or it never runs').toBeLessThan(skelAt)
    })
  }

  it("VoicePanel's second control-feeding read is bare too", () => {
    // Both use-case reads feed controls; fixing one would leave the other fabricating.
    const line = codeOf('VoicePanel.tsx').split('\n').find((l) => l.includes("api.useCaseSettings('tts')")) ?? ''
    expect(line).not.toMatch(/\.catch\(\(\)\s*=>/)
  })

  it('the decorating reads KEEP their fallbacks — this is not a no-catch sweep', () => {
    // Pinned, because a future "finish the job" pass would blank a panel that could have rendered.
    expect(codeOf('NotificationsPanel.tsx'), 'the rules matrix decorates').toMatch(/api\.notificationRules\(\)\.catch\(\(\) => null\)/)
    expect(codeOf('UpdatesPanel.tsx'), 'the changelog decorates').toMatch(/api\.changelog\(\)\.catch\(\(\) => ''\)/)
    expect(codeOf('VoicePanel.tsx'), 'the readiness chip decorates').toMatch(/api\.modelsActive\(\)\.catch\(\(\) =>/)
  })

  it('the census that found exactly these three is reproducible', () => {
    // The test that picked them out of 53: writes + renders controls + does NOT read `error`. All three
    // now read `error`, so re-running the census returns none — which is the assertion.
    for (const [panel] of PANELS) {
      const code = codeOf(panel)
      const writes = /\bpatch\(|api\.patchConfig|api\.save\w+|api\.set\w+/.test(code)
      const controls = /<Toggle\b|<NumberField\b|<TextInput\b|<SegToggle\b|<Segmented\b/.test(code)
      const readsError = /error:\s*loadErr/.test(code)
      expect(writes, `${panel} writes`).toBe(true)
      expect(controls, `${panel} renders controls`).toBe(true)
      expect(readsError, `${panel} must now read the error`).toBe(true)
    }
  })
})
