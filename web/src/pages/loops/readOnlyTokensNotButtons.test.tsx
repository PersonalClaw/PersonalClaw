import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TokensView } from './DesignCockpitPage'

// ── A tile that is not an action, and a button that would not say why ─────────────────────
//
// Two shapes of the same root cause, both on `#/loops`, both found by asking what a natively
// `disabled` control costs a keyboard user (cycle 109's triage, one layer down into the 21 raw
// `<button disabled={…}>` sites it could not see).
//
// 1. THE READ-ONLY TOKEN TILES. `TokensView` renders each radius and font family as a
//    `<button disabled={readOnly}>` whose `title` held the token's VALUE:
//
//        title={readOnly ? `radius.${k} · ${v}` : `Override radius.${k} (now ${v})`}
//
//    In read-only mode there is no action to take — and a natively disabled button is out of the
//    tab order, so that value was reachable by hover and by nothing else. When there is nothing to
//    press, the tile is now a plain `<div>` and the value is ON SCREEN (`k · v`), where a keyboard
//    user, a screen reader and a screenshot all get it.
//
//    🔑 `disabled` is the wrong tool for "this is a read-only VIEW". It says "an action you cannot
//    take right now"; the honest markup for "not an action at all" is not a button.
//
// 2. "SUGGEST MORE" (`SuggestMoreSubGoals`, not exported — asserted at the source below). Its gate
//    is `busy || goal.trim().length < 20`: the length half is a state the user can fix, and the
//    native attribute meant they tabbed straight past it and could not learn that the goal is
//    simply too short yet. It now keeps the tab stop with `aria-disabled` + a title, while `busy`
//    stays NATIVE — an in-flight action must not be re-clickable. The dimming had to move with it:
//    `disabled:opacity-40` never matches an `aria-disabled` element, so the class list carries both.
//
// 🪤 WHAT THIS CYCLE DID **NOT** DO, and why. `#/loops` at 390px still fails axe `target-size` on
// the granularity dial. That is the ledger's ESCALATED OWNER LAYOUT CALL — at phone width the
// floating shell corner occupies x=211..390 with `pointer-events: auto`, so ~211px of header is
// usable while the four controls need ~495px even fully collapsed — and PR #1110 (open, in the
// other chain) already shipped the part that could be fixed without a layout decision. Re-fixing it
// here would duplicate an open PR and conflict with it on merge.

const FIXTURE = {
  resolved: {
    radius: { sm: '0.25rem', lg: '0.75rem', comment: 'ignored' },
    typography: { family: { sans: 'Inter, sans-serif' }, size: {}, weight: {} },
    spacing: {}, shadow: {}, color: { semantic: {}, primitive: {} },
  },
} as unknown as Parameters<typeof TokensView>[0]['tokens']

const SCHEME = 'coral' as unknown as Parameters<typeof TokensView>[0]['scheme']

describe('read-only design tokens are not pretending to be buttons', () => {
  it('renders no radius/family button when there is nothing to press', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} readOnly />)
    // The Override affordances are the only buttons these two groups produce.
    expect(screen.queryByTitle(/^Override radius\./), 'read-only must not offer an override').toBeNull()
    expect(screen.queryByTitle(/^Override typography\.family\./)).toBeNull()
  })

  it('puts the value on screen instead of hiding it in a title', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} readOnly />)
    // `radius.lg · 0.75rem` used to be a tooltip on an unreachable button.
    expect(screen.getByText('lg · 0.75rem'), 'the value must be readable without a hover').toBeTruthy()
  })

  it('is still a real button when overriding IS possible', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} onOverride={() => {}} />)
    const b = screen.getByTitle('Override radius.lg (now 0.75rem)')
    expect(b.tagName).toBe('BUTTON')
    expect((b as HTMLButtonElement).disabled, 'an available action is not disabled').toBe(false)
  })

  it('does not print the value twice in the editable view', () => {
    // The editable tile keeps its bare `{k}` label — the value lives in the override title there,
    // where it belongs, because the tile IS reachable.
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} onOverride={() => {}} />)
    expect(screen.queryByText('lg · 0.75rem')).toBeNull()
  })
})

describe('the Suggest-more gate keeps its tab stop', () => {
  // `SuggestMoreSubGoals` is a file-local component and the state it needs (a loop draft with a
  // plan) is not reachable in the dev home, so this is asserted at the source. Every line was
  // read in the browser first for the sites that COULD be driven (cycle 109's account panel).
  const src = readFileSync(join(process.cwd(), 'src/pages/loops/LoopPlanReview.tsx'), 'utf8')

  it('names the gate instead of inlining it twice', () => {
    expect(src).toMatch(/const tooShort = goal\.trim\(\)\.length < 20/)
  })

  it('keeps the native attribute for busy and only that', () => {
    expect(src).toMatch(/disabled=\{busy\}/)
    expect(src).toMatch(/aria-disabled=\{tooShort \|\| undefined\}/)
  })

  it('says why, and suppresses the click it can no longer refuse natively', () => {
    expect(src).toMatch(/title=\{tooShort \? 'Describe the goal in a bit more detail first' : undefined\}/)
    expect(src).toMatch(/onClick=\{tooShort \? undefined : suggest\}/)
  })

  it('carries the dimming on BOTH selectors, or the soft-off state looks enabled', () => {
    // `disabled:opacity-40` cannot match an element that is no longer natively disabled.
    expect(src).toMatch(/disabled:opacity-40 aria-disabled:opacity-40/)
  })
})
