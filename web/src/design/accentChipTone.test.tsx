import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RungChip } from '../ui/RungChip'
import { RUNG_PRESENTATION } from '../lib/rungs'
import { accentChip } from './accent'

// ── The accent-chip failure, THIRD SPELLING: the tone arrives from a REGISTRY ──────────────────────
//
// `accentChip.test.ts` owns this defect and already sweeps two spellings — the style object with a
// literal token (`background: color-mix(in srgb, var(--color-primary) N%…)` + `color:
// var(--color-primary)`) and the utility pair (`bg-primary/N text-primary`). A third population is
// invisible to both, because the colour is INTERPOLATED from a meta registry:
//
//     style={{ background: `color-mix(in srgb, ${meta.tone} 14%, transparent)`, color: meta.tone }}
//
// No accent token appears on the line at all, so neither regex can fire. `ui/RungChip` was one of
// these, and `lib/rungs.ts` maps `autonomous → var(--color-primary)`, so the chip that says "runs on
// its own" drew coral ink on a 14% coral tint: **3.97:1 in light** against a 4.5 floor — measured live
// on `#/triggers` (7 chips desktop, 6 at 390px) and exactly the 14% row of cycle 146's own table.
// Dark was never affected (5.52), the asymmetry that cycle explained: a tint lifts a light backdrop
// TOWARD a dark accent until the two converge.
//
// 🔑 ONE TONE, NOT A SWEEP. Recomputed for all four rungs as ink over a 14% tint of themselves on
// `--color-surface`:
//
//     on-surface-low   7.46 light / 5.43 dark      on-surface-var  4.99 / 8.19
//     info             5.13 / 5.35                 primary         **3.97 FAIL** / 5.52
//
// Only the coral rung moves. The other three keep the tint, so the fix cannot be "route rung chips
// through the container pair" — semantic and neutral tones have no `<tone>-container` sibling, which is
// the same reason cycle 146 left 47 semantic sites alone.
//
// 🔑 AND THE CONTAINER FILL IS GROUND-INDEPENDENT, which the tint never was. `color-mix(… ,
// transparent)` composites against whatever surface the chip lands on, so the same chip measured
// differently in a trigger row than it would in a panel. `primary-container` is opaque: one number,
// 13.1:1 light / 10.43:1 dark, guaranteed for all 12 schemes by `schemeContrast`.
//
// 🪤 THE POPULATION IS 43 SITES AND IS NOT SWEPT HERE — stated so it is recorded debt, not a silent
// cap. A regex cannot decide these: whether `${x.tone}` reaches coral depends on the registry behind
// `x`, and most of them resolve to semantic tones that pass. Converging them is per-registry work.
// The measured, drivable ones, for whoever takes the next slice:
//
//   pages/notifications/NotificationsPage.tsx:162   `toneChipBg(km.tone)` + `km.tone` ink, 16%.
//                                                   13 of its kinds are coral, and this home holds 36
//                                                   `proposal` + 6 `subagent` notifications, so it is
//                                                   drivable — but only with a row OPEN, which is why
//                                                   the surface census (default state) reports clean.
//   pages/schedule/ScheduleDetail.tsx:194,195       scheduleMeta `cron` and `agent` are both coral.
//   ui/Segmented.tsx:136                            **DEFERRED ON PURPOSE**: interactive, and its coral
//                                                   branch carries a hover. Exactly why cycle 146 held
//                                                   `ui/Button` and `pages/code/CodeCockpitPage` back
//                                                   in `CLASS_TINT_ALLOWED` — a container fill has no
//                                                   hover shade in the token set, and inventing one is
//                                                   a visual-language decision, not a contrast fix.
//
// 🪤 `toneChipBg`'s ICON-ONLY consumers are NOT part of this — `ui/NotificationBell:168` and
// `NotificationsPage:256` tint a tile behind an icon, which carries a 3:1 non-text floor it clears.
// Moving those would repaint five surfaces for no accessibility reason.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

const rung = (resolved: string) =>
  ({
    key: 'test.action',
    floor: 'draft_only',
    ceiling: 'autonomous',
    leaves_machine: false,
    providers: ['p'],
    resolved_rung: resolved,
    granted_rung: resolved,
    held_by_incident: false,
    authority: 'declared floor',
    granted_at: '',
  }) as never

const chipStyle = (resolved: string) => {
  const { unmount } = render(<RungChip type={rung(resolved)} />)
  const el = screen.getByTitle(/declared floor/)
  const style = el.getAttribute('style') ?? ''
  unmount()
  return style
}

describe('a rung chip inks coral through the container pair, not a tint of itself', () => {
  it('the coral rung uses the shipped accent pair', () => {
    const style = chipStyle('autonomous')
    expect(style, 'the opaque container fill').toContain('var(--color-primary-container)')
    expect(style, 'and its guaranteed ink').toContain('var(--color-on-primary-container)')
    expect(style, 'no tint of the accent may remain — that is the 3.97:1 pairing')
      .not.toMatch(/color-mix/)
  })

  it('it is literally the shared pair, so it cannot drift from the other adopters', () => {
    const style = chipStyle('autonomous')
    expect(style).toContain(accentChip.background)
    expect(style).toContain(accentChip.color)
  })

  it('the three passing rungs keep their tint, each with its own tone', () => {
    // 4.99-7.46 in light. Routing these through the coral container would be a redesign, and they
    // have no `<tone>-container` sibling to pair with anyway.
    for (const [resolved, tone] of [
      ['draft_only', 'var(--color-on-surface-low)'],
      ['one_tap', 'var(--color-on-surface-var)'],
      ['auto_with_undo', 'var(--color-info)'],
    ] as const) {
      const style = chipStyle(resolved)
      expect(style, `${resolved} keeps the 14% tint`).toContain('color-mix')
      expect(style, `${resolved} tints with its OWN tone`).toContain(tone)
      expect(style, `${resolved} must not borrow the coral container`)
        .not.toContain('primary-container')
    }
  })

  it('the remap is not vacuous — the registry still declares exactly one coral rung', () => {
    // THE VACUITY FLOOR. If `RUNG_PRESENTATION` stopped using `--color-primary`, the branch in
    // `RungChip` would be dead code that still reads as an enforced rule; and if a SECOND rung became
    // coral, the measurement above would no longer describe the surface. Either way this must be a
    // deliberate edit, not a silent one.
    const coral = Object.entries(RUNG_PRESENTATION).filter(([, p]) => p.tone === 'var(--color-primary)')
    expect(coral.map(([k]) => k)).toEqual(['autonomous'])
  })

  it('both consumers get the fix from the primitive, so neither can be missed', () => {
    const users = walk(SRC).filter((abs) => /<RungChip\b/.test(readFileSync(abs, 'utf8')))
    expect(users.map((a) => a.slice(SRC.length + 1)).sort()).toEqual([
      'pages/settings/GuardrailsPanel.tsx',
      'pages/triggers/TriggersListPage.tsx',
    ])
  })

  it("the family's rail points here, so the third spelling is findable from it", () => {
    expect(readFileSync(join(SRC, 'design/accentChip.test.ts'), 'utf8'))
      .toMatch(/accentChipTone\.test\.tsx/)
  })
})
