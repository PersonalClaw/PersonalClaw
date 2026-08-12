import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RowAction } from './widgets/kit'

// ── Nine undersized targets on the first screen of the app ────────────────────────────────
//
// Measured on `#/dashboard` (1440×1400), every interactive box under 24px on either axis, with the
// distance to its nearest other target:
//
//   5 ×  38×22   `RowAction`            nearest target  4px
//   4 × ~250×20  the "Jump back in" chips              16px
//   → after: **0 undersized targets**
//
// SC 2.5.8 Target Size (Minimum), AA. 🪤 THE SPACING EXCEPTION RESCUES NEITHER, and that is the part
// worth measuring rather than assuming: it requires a 24px circle on each target to clear the other
// targets, so 4px between row actions and 16px between chips both fail. (Cycle 72 hit the same trap
// from the other side — switch centres 34–107px apart *looked* like ample clearance until the
// enclosing full-card nav button was accounted for.)
//
// 🔑 ONE OF THE TWO IS A PRIMITIVE, so one edit reaches every adopter: `RowAction` is the dashboard's
// row-action button, used by TasksWidget, SystemHealth (×2), ActiveWork (×3), PinnedArtifacts and
// ActionCenter. The chips are a one-off in `DashboardPage`.
//
// 🔑 THE FIX IS THE HIT BOX, NOT THE DESIGN — the shape cycle 72 established for the `sm` Toggle:
// grow the box to 24px and hand the extra height back with a negative margin, so nothing reflows.
// Measured after: chip 268×20 → 268×24, `RowAction` 38×22 → 38×24, and the surface is
// **pixel-identical at both themes and phone (0%)**. An accessibility fix with no visual cost is
// worth proving rather than claiming: the 0% IS the proof.

describe('RowAction is a reachable target', () => {
  it('has a 24px-tall hit box', () => {
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/\bmin-h-6\b/)
  })

  it('returns the 2px to the layout, so no widget row reflows', () => {
    // Without this every row holding an action grows 2px and the whole dashboard shifts.
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/-my-px/)
  })

  it('keeps its painted padding — the design is unchanged', () => {
    render(<RowAction onClick={vi.fn()} title="Mark complete">go</RowAction>)
    const cls = screen.getByRole('button').className
    expect(cls).toMatch(/px-m/)
    expect(cls).toMatch(/py-xs/)
  })

  it('still carries the tone it was asked for', () => {
    render(<RowAction tone="danger" onClick={vi.fn()} title="Open doctor">x</RowAction>)
    expect(screen.getByRole('button').className).toMatch(/text-danger/)
  })
})

describe('the fix reaches the widgets, and the chips too', () => {
  const DIR = join(process.cwd(), 'src/pages/dashboard')

  it('RowAction has enough adopters to be worth fixing once (not vacuously green)', () => {
    const count = readdirSync(join(DIR, 'widgets'))
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
      .reduce((sum, n) => sum + (readFileSync(join(DIR, 'widgets', n), 'utf8').match(/<RowAction\b/g) ?? []).length, 0)
    expect(count, 'the primitive must actually be in use across the widgets').toBeGreaterThanOrEqual(6)
  })

  it('the "Jump back in" chips grew too', () => {
    const src = readFileSync(join(DIR, 'DashboardPage.tsx'), 'utf8')
    // 20px → 24px needs 4px back, hence `-my-0.5` rather than `-my-px`.
    expect(src).toMatch(/inline-flex min-h-6 -my-0\.5 items-center gap-xs text-on-surface-var/)
  })

  it('does not silence the Reply CONTRAST finding, which is a separate owner call', () => {
    // `#/settings` light-theme audit still reports 4.46:1 on these buttons. That is deferred in the
    // ledger; a target-size fix must not be mistaken for having addressed it.
    const src = readFileSync(join(DIR, 'widgets', 'ActionCenter.tsx'), 'utf8')
    expect(src, 'the tone is untouched — only the hit box moved').toMatch(/<RowAction tone="primary"/)
  })
})
