import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SegToggle } from './bento'

// ── Eight pills at 22px, adjacent, on the app's most-visited hub ─────────────────────────────
//
// Cycle 125 finished the `#/settings/*` sweep and left three shapes behind, measured. This is the first:
// `SegToggle`'s pills — the compact choice strip inside a bento tile.
//
//   #/settings   Mode: Light 43.70×22 · Mode: Dark 41.30×22 · Mode: Auto 42.48×22
//                Density ×3, Min severity ×2 (below the fold — reached by scrolling the hub's own
//                internal scroller, since this app's page height never grows)
//                → **8 pills across 3 groups, 8 of 8 under 24px**
//
// 🔑 THE SPACING EXCEPTION CANNOT APPLY HERE, and the measurement says why: the gap between adjacent
// pills is **0** — they sit shoulder to shoulder inside the group's `p-0.5`. SC 2.5.8's undersized-target
// exception needs a 24px circle to clear every other target, and a neighbour 0px away never can. Width
// is irrelevant; the 22px height is the whole failure.
//
// Fixed on the shared component, so all four adopters (Mode, Density, Min severity, and the dashboard
// tile's strip) come along: `h-6` with `-my-px` — 24px of target, 2px handed straight back. The shape
// cycle 113 established for `RowAction` and cycle 116 restated as the invariant: **grow the hit box,
// leave the layout alone.**
//
// Driven on `#/settings`, parent worktree vs this one (`grep -c 'h-\[22px\]'` = 2 there, 0 here):
//
//                        before            after
//   pills                 8                 8
//   under 24px            **8**             **0**
//   pill box              43.7×22           **43.7×24**   (widths identical)
//   GROUP box             131.5×26 @375.0   **131.5×26 @375.0**  ← unchanged, to the tenth of a px
//
// Evidence is a CROP of the pill group, not the page: 2.15% dark / 2.00% light inside a 131×26 group,
// bounding box 40×24 — the selected pill's tint is 2px taller and nothing else moved. The page-level diff
// is 0.018%/0.017%; per cycle 125 a page capture cannot be trusted to contain a defect in a fixed-shell
// app, so the crop is the real evidence and the page number is context.

describe('SegToggle pills are 24px targets', () => {
  const OPTS = [{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }, { key: 'auto', label: 'Auto' }]

  it('is 24px tall', () => {
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Light' }).className).toMatch(/\bh-6\b/)
  })

  it('hands the 2px back, so the group keeps its height', () => {
    // Measured: group 131.5×26 at y=375.0 on both trees. Without this the group grows and every tile
    // below it in the masonry column shifts.
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Dark' }).className).toMatch(/-my-px/)
  })

  it('no longer carries the off-ramp height', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/bento.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the 22px pill height must be gone from the component').not.toMatch(/h-\[22px\][^"]*text-\[0\.75rem\] transition-colors/)
  })

  it('keeps its exclusive-choice semantics', () => {
    // The pills are a single choice, not three toggles: each announces its own name and pressed state.
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Dark' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Mode: Light' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('still ignores a click on the already-selected option', async () => {
    const onPick = vi.fn()
    render(<SegToggle value="dark" options={OPTS} onPick={onPick} ariaLabel="Mode" />)
    // `await act(async …)` rather than a bare click: the handler awaits `onPick` and then clears its
    // own `busy` state, so the update lands after the event and warns if it is not wrapped.
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Mode: Dark' })) })
    expect(onPick, 'picking the current value is a no-op').not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Mode: Auto' })) })
    expect(onPick).toHaveBeenCalledWith('auto')
  })

  it('has enough adopters that fixing the component was the right move', () => {
    const widgets = readFileSync(join(process.cwd(), 'src/pages/settings/settingsWidgets.tsx'), 'utf8')
    const uses = (widgets.match(/<SegToggle\b/g) ?? []).length
    expect(uses, 'Mode, Density, Min severity, …').toBeGreaterThanOrEqual(3)
  })
})
