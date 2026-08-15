import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── `role="separator"` on a resize handle is the window-splitter promise ───────────────────────
//
// The APG splitter is a role PLUS a keyboard contract: arrow keys move it, and it reports where it
// sits (`aria-valuenow` within min/max). `ui/NavRail` declared the role and implemented none of it —
// measured before the fix: `tabindex` null, no key handler, no `aria-valuenow`, no accessible name.
// So the rail could only be resized by dragging a 1px strip with a mouse (WCAG 2.1.1, and 2.5.7 for
// the drag-only gesture) while announcing itself as a control that does none of that. Same shape as
// `aria-modal` without a trap and `aria-haspopup` with no popup: a declaration nothing stands behind.
//
// 🔑 THE CANONICAL FORM WAS ALREADY IN THE TREE, TWICE. `pages/code/CodeCockpitPage.tsx` ships it for
// its panel splitter and its terminal drawer — role + orientation + `tabIndex={0}` + an arrow-key hint
// in the accessible name + valuenow/min/max + a `focus-visible` seam. NavRail converged onto that
// rather than inventing a variant.
//
// ── The rest of the family, recorded rather than silently left ─────────────────────────────────
//
// Six drag-to-resize handles exist. This rail governs the ones that CLAIM to be splitters; the others
// are pointer-only and do not lie about it, which is a smaller defect but still one:
//
//   pages/code/CodeCockpitPage  ×2   full pattern            ← canonical
//   ui/NavRail                       full pattern            ← this change
//   pages/terminal/TerminalDrawer    tabIndex + keydown, no role/valuenow
//   ui/SidePanel · ui/Composer · pages/chat/ChatFilePanel   pointer-only
//
// 🪤 THIS RAIL DELIBERATELY DOES NOT FAIL THE POINTER-ONLY THREE. Failing them today would either
// force a rushed 3-file change or invite the cheap fix of DELETING a role to go green — and the honest
// convergence is one shared handler adopted by all six, which is real work with its own cycle. What
// the rail does guarantee is that nobody can ship the NavRail defect again: claiming the role now
// obliges the contract.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const files = () => walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

describe('a declared splitter implements the splitter contract', () => {
  const claimants = () => files().filter((f) => /role="separator"/.test(f.src))

  it('finds the claimants — not vacuous', () => {
    expect(claimants().map((f) => f.rel).sort()).toEqual(['pages/code/CodeCockpitPage.tsx', 'ui/NavRail.tsx'])
  })

  it('each one is focusable, keyboard-operable and reports its value', () => {
    // THE RATCHET. Every piece is separately load-bearing: without tabIndex it cannot be reached,
    // without a key handler the role is a lie, and without valuenow a screen-reader user is moving a
    // control that never says where it got to.
    const bad: string[] = []
    for (const { rel, src } of claimants()) {
      const missing = [
        /tabIndex=\{0\}/.test(src) ? '' : 'not focusable',
        /onKeyDown/.test(src) ? '' : 'no key handler',
        /aria-valuenow/.test(src) ? '' : 'no aria-valuenow',
        /aria-valuemin/.test(src) && /aria-valuemax/.test(src) ? '' : 'no min/max',
      ].filter(Boolean)
      if (missing.length) bad.push(`${rel}: ${missing.join(', ')}`)
    }
    expect(bad, `a declared separator must be operable:\n  ${bad.join('\n  ')}`).toEqual([])
  })

  it("the name tells you it takes arrow keys — a splitter you can't guess at is unusable", () => {
    for (const { rel, src } of claimants()) {
      expect(src, `${rel} must name the interaction`).toMatch(/aria-label=\{?[`"']Resize [^`"']*arrow keys/)
    }
  })

  it('a focused splitter is visible', () => {
    // It is a 1–2px seam; without a focus style, keyboard focus lands somewhere invisible.
    for (const { rel, src } of claimants()) {
      expect(src, `${rel} needs a focus-visible seam`).toMatch(/focus-visible:bg-primary/)
    }
  })

  it('the matcher fires on the exact shape NavRail shipped', () => {
    // Sabotage, both directions: role + pointer handler only.
    const navRailBefore = '<div role="separator" aria-orientation="vertical" onMouseDown={() => {}} />'
    expect(/role="separator"/.test(navRailBefore)).toBe(true)
    expect(/tabIndex=\{0\}/.test(navRailBefore)).toBe(false)
    expect(/aria-valuenow/.test(navRailBefore)).toBe(false)
  })
})
