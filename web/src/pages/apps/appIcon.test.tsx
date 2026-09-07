import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'
import * as Lucide from 'lucide-react'
import { Blocks, icons as lucideIconRegistry } from 'lucide-react'
import { resolveAppIcon } from './appIcon'

/** The manifest `icon` field is untrusted app-supplied input, and `resolveAppIcon` is the only
 *  door between it and a React element. Six call sites render through it, one of them the sidebar
 *  nav in `App.tsx` — where an invalid element type throws during the app shell's own render, so
 *  a single bad manifest word took down the whole dashboard rather than one app's card.
 *
 *  These tests are therefore a CORPUS rail, not examples: every letter-starting export of the
 *  installed lucide is rendered, because the defect was one export in six thousand and no
 *  hand-picked list would have found it. Both vacuity floors below are load-bearing. */
describe('resolveAppIcon', () => {
  const letterStarting = Object.keys(Lucide).filter((n) => /^[A-Za-z]/.test(n))

  it('resolves every lucide export to something React can actually render', () => {
    // FLOOR 1 (corpus is real): without this, a lucide upgrade that changed the module shape could
    // leave `letterStarting` empty and the assertion below would pass by iterating nothing.
    expect(letterStarting.length).toBeGreaterThan(5000)

    const crashed: Array<[string, string]> = []
    for (const name of letterStarting) {
      try {
        renderToString(createElement(resolveAppIcon(name), { size: 18 }))
      } catch (e) {
        crashed.push([name, (e as Error).message.slice(0, 80)])
      }
    }
    expect(crashed).toEqual([])
  })

  it('still resolves real icons rather than falling everything back to Blocks', () => {
    // FLOOR 2 (the fix is not a blanket fallback): `return Blocks` unconditionally would satisfy
    // the corpus test above perfectly. This is the counter-assertion that catches it — and the
    // reason two floors are needed rather than one.
    const resolved = letterStarting.filter((n) => resolveAppIcon(n) !== Blocks)
    expect(resolved.length).toBeGreaterThan(5000)
  })

  it('rejects the container exports that are truthy but not components', () => {
    // The original defect. `icons` is lucide's own registry object: truthy, so `?? Blocks` never
    // fired, and not a component, so React threw "Element type is invalid".
    expect(resolveAppIcon('icons')).toBe(Blocks)
    expect(resolveAppIcon('default')).toBe(Blocks)
  })

  it('rejects exports that are VALID components but still throw when rendered', () => {
    // These are why a `typeof === 'function' || '$$typeof' in v` guard is insufficient: `Icon` is
    // a genuine forwardRef and `createLucideIcon` a genuine function, so a shape check accepts
    // both, and each then throws inside its own render for want of required arguments.
    expect(resolveAppIcon('Icon')).toBe(Blocks)
    expect(resolveAppIcon('createLucideIcon')).toBe(Blocks)
    expect(resolveAppIcon('useLucideContext')).toBe(Blocks)
  })

  it('keeps ALIAS names working, not just the registry’s canonical keys', () => {
    // The regression guard for the tempting one-liner `lucideIconRegistry[name] ?? Blocks`, which
    // would silently drop every alias — 4367 of the 6166 working names. An alias is the same
    // component object as its canonical name, which is why identity matching keeps them.
    const canonical = Object.keys(lucideIconRegistry)
    for (const alias of ['SquareTerminalIcon', 'LucideSquareTerminal', 'AlarmCheck']) {
      expect(canonical, `${alias} must be an ALIAS for this test to mean anything`).not.toContain(alias)
      expect(resolveAppIcon(alias), `${alias} is a valid lucide name and must resolve`).not.toBe(Blocks)
    }
    // and the canonical name it aliases resolves to the very same component
    expect(resolveAppIcon('SquareTerminalIcon')).toBe(resolveAppIcon('SquareTerminal'))
  })

  it('falls back for absent, non-letter and unknown names', () => {
    expect(resolveAppIcon(undefined)).toBe(Blocks)
    expect(resolveAppIcon('')).toBe(Blocks)
    expect(resolveAppIcon('\u{1F389}')).toBe(Blocks)
    expect(resolveAppIcon('NotArealIcon')).toBe(Blocks)
  })
})
