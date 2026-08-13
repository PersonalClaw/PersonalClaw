import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PanelRight } from 'lucide-react'
import { HeaderControl } from './HeaderActions'
import { IconButton } from './IconButton'

// ── Three primitives modelled "this control is on" and told nobody ───────────────────────────
//
// Cycle 128 measured 48 boolean-toggling buttons and closed 14, leaving 34 to CLASSIFY (the rail's
// ceiling). Reading them turned the remainder inside out: **most are not raw buttons at all.** They are
// primitives that already take an `active` prop — and every one of them spent it on a colour:
//
//   `HeaderControl`  ui/HeaderActions.tsx   **14 call sites, 7 files**  → `active ? variants.secondary : …`
//   `FilterChip`     knowledge/KnowledgeListPage.tsx   **8 call sites** → `style={active ? selected : …}`
//   `IconButton`     ui/IconButton.tsx      **2 call sites** (composer optimize + mic)
//
// 🔑 SO THE FIX BELONGS IN THREE PRIMITIVES, NOT AT 24 CALL SITES — and the contract is already written
// down in `Button`'s own doc: *"a button acting as a SELECTED/UNSELECTED choice must announce that state,
// or a screen-reader user hears an identical label for the row they are on and the row they are not."*
// `Segmented`/`SegToggle` already ship `aria-pressed` for exclusive choice; this brings the `active`
// family onto the same contract instead of inventing one.
//
// Driven, parent worktree vs this one (`grep -c 'aria-pressed={active}'` = 0 there, 1 here per file):
//
//   surface        aria-pressed nodes   one live flip
//   #/knowledge    **0 → 9** (2 true)   the type chips are an exclusive group; clicking the active one
//                                       is a no-op, as it should be (true → true)
//   #/files        **0 → 4** (4 true)   **true → false**
//   #/chat         **0 → 6** (0 true)   **false → true**
//   #/inbox        **0 → 4** (0 true)   **false → true**
//
// 🪤 THE NODE COUNTS ARE NOT CONTROL COUNTS. `#/files` shows four nodes all named "Hide explorer" because
// `HeaderActions` renders the same control at several responsive tiers. Four nodes, one control — worth
// saying plainly rather than implying the header has four toggles.
//
// 🔑 AND `active` STAYS OPTIONAL. A header action that is not a toggle (Save, Run build, Close) passes no
// `active`, so it must emit NO `aria-pressed` at all — announcing `"false"` would tell assistive tech that
// a plain action has an off state.

describe('HeaderControl announces the state its colour already showed', () => {
  it('is pressed when active', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('is unpressed when explicitly inactive', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active={false} onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('says nothing at all when the control is not a toggle', () => {
    // The distinction that keeps this honest: no `active` prop → no state claim.
    render(<HeaderControl icon={PanelRight} label="Run build" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Run build' }).hasAttribute('aria-pressed')).toBe(false)
  })
})

describe('IconButton does the same, and keeps what it already announced', () => {
  it('is pressed when active', () => {
    render(<IconButton icon={PanelRight} label="Optimize prompt" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Optimize prompt' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<IconButton icon={PanelRight} label="Close" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Close' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('still carries its name and its disabled contract', () => {
    // Cycle 119's work must survive: the reason rides `title`, the name stays findable.
    render(<IconButton icon={PanelRight} label="Optimize prompt" disabled disabledReason="Type something first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Optimize prompt' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Optimize prompt — Type something first')
  })
})

describe('the population this reaches, so the primitives were the right place', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** 🪤 Matched to the SELF-CLOSING `/>`, never to the first `>` — `onClick={() => …}` contains one, and
   *  that mistake produced four false negatives earlier in this session. */
  const callSites = (prim: string) =>
    walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(new RegExp(`<${prim}\\b[\\s\\S]{0,400}?/>`, 'g'))]
        .filter((m) => /\bactive=/.test(m[0])))

  it('HeaderControl has 14 active call sites', () => {
    expect(callSites('HeaderControl').length).toBeGreaterThanOrEqual(14)
  })

  it('FilterChip has 8, and now announces them', () => {
    expect(callSites('FilterChip').length).toBeGreaterThanOrEqual(8)
    const src = readFileSync(join(SRC, 'pages/knowledge/KnowledgeListPage.tsx'), 'utf8')
    expect(src).toMatch(/<button type="button" onClick=\{onClick\} aria-pressed=\{active\}/)
  })

  it('IconButton has 2 — small, and one of them is a recording state', () => {
    // "Recording" vs "not recording" is precisely what a tint cannot convey.
    expect(callSites('IconButton').length).toBeGreaterThanOrEqual(2)
  })

  it('each primitive binds the attribute to its own `active` prop', () => {
    for (const rel of ['ui/HeaderActions.tsx', 'ui/IconButton.tsx']) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must bind aria-pressed to active`)
        .toMatch(/aria-pressed=\{active\}/)
    }
  })
})
