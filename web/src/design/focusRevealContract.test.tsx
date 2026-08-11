import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A hover-revealed control must also reveal on FOCUS ──────────────────────────
//
// `opacity: 0` does NOT remove focusability — unlike `display:none`, `visibility:hidden` or `inert`.
// So a row action that reveals only on `group-hover` still receives keyboard focus while invisible:
// Tab lands on a button nobody can see, and in two cases here that button was DELETE.
//
// The app's convention already existed — 9 sites paired `group-hover:opacity-100` with
// `focus-within:opacity-100`, and 11 more used `focus-visible:opacity-100` on the button itself. The
// outliers were drift against a working sibling, not a missing convention.
//
// Measured on the live DOM, Tab-walking three real lists and reading each focused control's
// EFFECTIVE opacity (ancestors multiplied, since an ancestor at 0 hides its child):
//
//                    BEFORE                        AFTER
//   #/projects       0 invisible                   0   (already had focus-visible)
//   #/notifications  4 invisible                   0   Investigate / Mark read / Delete / Mark unread
//   #/loops/history  1 invisible                   0   Stop
//
// `focus-within` is the right single token for BOTH shapes — verified in a real browser, not assumed:
// `:focus-within` matches an element that is focused OR contains focus, so it works on a wrapper AND
// on a bare button. That is why one uniform edit was safe across 13 files.
//
// 🪤 THREE MEASUREMENT TRAPS, all hit before the numbers above were trustworthy. Each produced a
// CONFIDENT WRONG ANSWER, which is worse than a crash:
//
//  1. **`transition-opacity` means the reveal is not instant.** Reading computed opacity in the same
//     tick as the Tab press returns `0` on a control that is about to be fully visible. This alone
//     manufactured SIX false defects — including on `#/projects`, which was already correct. The probe
//     now waits 260ms after each Tab.
//  2. **`focus-visible` does not match a programmatic `.focus()`** — only keyboard-initiated focus. An
//     `el.focus()` probe reports a false `opacity: 0` on a control that is perfectly visible to a real
//     keyboard user. Drive real `Tab` presses.
//  3. **`getByRole('button', { name })` can resolve the ROW, not the action.** These rows are
//     `cursor-pointer` clickables, so the accessible-name lookup matched the row and I measured the
//     wrong element's opacity. Query the actual `button[aria-label=…]`.
//
// The scanner also missed 4 sites its first time out, because named Tailwind groups
// (`group-hover/code:`, `group-hover/q:`, `group-hover/bl:`) do not match a bare `group-hover:`
// pattern. Counted: 31 hover-revealed focusable containers tree-wide, 0 without a focus reveal.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const REVEALS_ON_FOCUS =
  /focus-within:opacity-100|group-focus-within:opacity-100|focus-visible:opacity-100|group-focus:opacity-100/

interface Hit { file: string; line: number; cls: string; ok: boolean }

/** Every className that hides with opacity-0 and reveals on hover, and whether it also reveals on
 *  focus. Named groups (`group-hover/code:`) are included — omitting them missed 4 real sites. */
function hoverRevealed(src: string, rel: string): Hit[] {
  const out: Hit[] = []
  for (const m of src.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    const cls = m[1] ?? m[2] ?? ''
    if (!/\bopacity-0\b/.test(cls)) continue
    if (!/group-hover(?:\/[\w-]+)?:opacity-100/.test(cls)) continue
    // A decorative overlay cannot take focus, so it owes nothing here.
    if (/pointer-events-none/.test(cls)) continue
    out.push({
      file: rel,
      line: src.slice(0, m.index).split('\n').length,
      cls: cls.replace(/\s+/g, ' '),
      ok: REVEALS_ON_FOCUS.test(cls),
    })
  }
  return out
}

const all = walk(SRC).flatMap((abs) => hoverRevealed(strip(readFileSync(abs, 'utf8')), abs.slice(SRC.length + 1)))

describe('the rail: hover-revealed means focus-revealed', () => {
  it('every hover-revealed focusable container also reveals on focus', () => {
    const offenders = all.filter((h) => !h.ok).map((h) => `${h.file}:${h.line}  ${h.cls.slice(0, 90)}`)
    expect(
      offenders,
      `opacity-0 does NOT remove focusability, so Tab lands on an invisible control:\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the hover-revealed containers', () => {
    // A rail that matches nothing passes forever; `toEqual([])` cannot tell "nothing is broken" from
    // "my matcher is broken". Pin a floor and the two shapes it must recognise.
    expect(all.length, 'the scanner must find the tree\'s hover-revealed controls').toBeGreaterThan(20)

    // Named groups must be in scope — omitting them missed 4 sites on the first pass.
    const named = all.filter((h) => /group-hover\/[\w-]+:opacity-100/.test(h.cls))
    expect(named.length, 'named Tailwind groups must be scanned').toBeGreaterThan(0)

    // And the check must still FLAG the shape.
    const bad = hoverRevealed('<div className="opacity-0 group-hover:opacity-100" />', 'x.tsx')
    expect(bad.length).toBe(1)
    expect(bad[0].ok).toBe(false)
    // A decorative, unfocusable overlay must NOT be flagged.
    expect(hoverRevealed('<div className="pointer-events-none opacity-0 group-hover:opacity-100" />', 'x.tsx').length).toBe(0)
  })

  it('both accepted forms count as a focus reveal', () => {
    // `focus-within` works on a wrapper AND on the focused element itself (verified in a browser);
    // `focus-visible` is the pre-existing form on bare buttons. Either satisfies the contract.
    for (const cls of [
      'opacity-0 group-hover:opacity-100 focus-within:opacity-100',
      'opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
    ]) {
      expect(hoverRevealed(`<div className="${cls}" />`, 'x.tsx')[0].ok, cls).toBe(true)
    }
  })
})
