import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ToggleRow } from './settingsUI'

// ── "This switch relaxes a safety default" has to be sayable, not just drawable ───────────────────
//
// `ToggleRow`'s `danger` prop shows a warn glyph while the switch is ON. Its own doc says why: *"Show a
// warning glyph while ON — for a switch that relaxes a safety default."* The glyph carried that with **no
// accessible name at all**, so the only part a screen-reader user got was the `Toggle` beside it — which
// announces the row's label and its on/off state, but nothing about the relaxation.
//
// Both shipped consumers are consequential, which is why this is not cosmetic:
//   · `AgentDefaultsPanel` "YOLO mode" — *"Skip every tool-approval confirmation — overrides approval
//     mode. Only inside a sandbox or for trusted automation."*
//   · `AgentDefaultsPanel` "Propose fix branches" — opens a branch carrying a diff on a confirmed failure.
//
// 🔑 `role="img"` + `aria-label` is the settled form for a glyph whose label is its only text —
// `design/ariaProhibitedAttr.test.ts` declares it, and six sites already do it (census in
// `tools/approvalShieldNamed.test.ts`). No `title`: lucide's `LucideProps` rejects it (TS2322).
//
// 🪤 THIS IS A RENDER TEST, NOT A SOURCE SCAN. `ToggleRow` is exported and cheap to mount, so the
// accessible name can be asserted where it actually resolves. A source rail would pass on markup that
// React never renders — and the sibling rail `toggleRowDedup.test.tsx` already proves the ON/OFF gating
// by rendering, so this matches its instrument.

const patchNoop = () => vi.fn()

describe('the danger glyph says what it means', () => {
  it('is named while ON', () => {
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: true }} field="f" patch={patchNoop() as never} danger />,
    )
    const glyph = container.querySelector('svg.text-warn')
    expect(glyph, 'the danger glyph must render while ON').not.toBeNull()
    expect(glyph!.getAttribute('aria-label'), 'it must say what being ON means').toMatch(/safety default/i)
    // The role is load-bearing, not decoration: `ModelsPanel` records that on a role-less element the
    // name is DISCARDED, so asserting the label alone would pass on markup that announces nothing.
    expect(glyph!.getAttribute('role'), 'role="img" is what makes the name stick').toBe('img')
  })

  it('says nothing while OFF, because there is nothing to say', () => {
    // The glyph marks an ACTIVE relaxation. `toggleRowDedup.test.tsx` owns the presence contract; this
    // asserts the NAME does not linger where the glyph does not — a stray label on an off switch would
    // announce a danger that is not in effect.
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: false }} field="f" patch={patchNoop() as never} danger />,
    )
    expect(container.querySelector('svg.text-warn')).toBeNull()
    expect(container.querySelector('[aria-label*="safety default"]')).toBeNull()
  })

  it('a non-danger row gains no name — the four plain panels are untouched', () => {
    const { container } = render(
      <ToggleRow label="Poll" cfg={{ f: true }} field="f" patch={patchNoop() as never} />,
    )
    expect(container.querySelector('svg.text-warn')).toBeNull()
    expect(container.querySelector('[aria-label*="safety default"]')).toBeNull()
  })

  it("the label is the prop's own doc sentence, so the two cannot drift", () => {
    // 🪤 The wording is not free here. If the doc says "relaxes a safety default" and the label says
    // something else, a reader has two definitions of what `danger` means — which is how the count
    // comments in `ui/forms.tsx` went stale. Both are asserted against the same phrase.
    const src = readFileSync(join(import.meta.dirname, 'settingsUI.tsx'), 'utf8')
    const at = src.indexOf('danger?: boolean')
    expect(at, 'the danger prop moved — this rail measures nothing').toBeGreaterThan(-1)
    const doc = src.slice(Math.max(0, at - 300), at)
    expect(doc, "the prop's doc must still describe a relaxed safety default").toMatch(/safety default/i)
  })
})

describe('every consumer that relaxes a safety default marks itself', () => {
  const PANELS = join(import.meta.dirname)
  const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('the danger consumers are a real, findable population (vacuity floor)', () => {
    // Two today. A floor rather than an equality: a new safety-relaxing switch should not red this, but
    // the population going EMPTY would mean the prop is dead and the glyph unreachable — at which point
    // the render assertions above are testing a path nobody takes.
    let consumers = 0
    for (const abs of walk(PANELS)) {
      for (const m of strip(readFileSync(abs, 'utf8')).matchAll(/<ToggleRow\b[\s\S]{0,400}?\/>/g)) {
        if (/\bdanger\b/.test(m[0])) consumers++
      }
    }
    expect(consumers, 'no ToggleRow passes `danger` — the glyph is unreachable').toBeGreaterThanOrEqual(2)
  })
})
