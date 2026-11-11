import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'
import * as Lucide from 'lucide-react'
import { Blocks, Brain, SquareTerminal, icons as lucideIconRegistry } from 'lucide-react'
import { AppIcon, resolveAppIcon } from './appIcon'

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

/** Every letter-starting lucide export that is NOT an icon, measured against the locked version.
 *  `LucideProvider` is the one the corpus sweep above cannot catch on its own: it renders without
 *  throwing, it just renders nothing — so an app declaring it got an invisible icon rather than a
 *  crash, and only an identity check rejects it. */
const NON_ICON_EXPORTS = [
  'icons', 'default', 'module.exports', 'createLucideIcon', 'useLucideContext', 'LucideProvider',
  'Icon',
] as const

describe('resolveAppIcon folds case', () => {
  it('rejects all SEVEN non-icon exports, including the one that renders empty', () => {
    // The sweep above proves nothing CRASHES; this proves the two exports that fail quietly
    // (`LucideProvider` renders nothing, `Icon` needs an `iconNode` a manifest cannot supply)
    // still fall back, so a partial fix cannot pass by being crash-free.
    for (const name of NON_ICON_EXPORTS) {
      expect(resolveAppIcon(name), `${name} must not be treated as an icon`).toBe(Blocks)
    }
  })

  it('resolves a lowercase spelling to the same component', () => {
    // 🔑 A live defect, not a hypothetical: `meta-muse-spark` ships `"brain"`, which is not an
    // export, so it rendered the generic Blocks glyph instead of the brain its author asked for —
    // with no error anywhere. `manifest.py` accepts any identifier as an icon (ICON_NAME_RE), so
    // the validator and the resolver disagreed about what a valid icon is. The fold closes that
    // gap in the direction that keeps the shipped app working.
    expect(resolveAppIcon('brain')).toBe(Brain)
    expect(resolveAppIcon('squareterminal')).toBe(SquareTerminal)
  })

  it('does NOT let a non-icon re-enter through the fold', () => {
    // The fold is built from the same identity predicate as the exact lookup, so a rejected
    // export cannot come back by being spelled differently. Without this cell the fold is a
    // second, unguarded door into the same React element.
    for (const name of NON_ICON_EXPORTS) {
      expect(resolveAppIcon(name.toLowerCase()), `${name} lower-cased`).toBe(Blocks)
      expect(resolveAppIcon(name.toUpperCase()), `${name} upper-cased`).toBe(Blocks)
    }
  })

  it('reaches the whole UNAMBIGUOUS set by lowercase spelling, and refuses the rest', () => {
    // FLOOR 3 (the fold is not two hand-picked names): `brain` and `squareterminal` above would
    // both pass a two-entry special case. This asserts the index is built from lucide's own values
    // — and it is what measured the fold's one real limit.
    const iconValues = new Set<unknown>(Object.values(lucideIconRegistry))
    const byLower = new Map<string, Set<unknown>>()
    for (const [key, value] of Object.entries(Lucide as unknown as Record<string, unknown>)) {
      if (!iconValues.has(value)) continue
      const lower = key.toLowerCase()
      if (!byLower.has(lower)) byLower.set(lower, new Set())
      byLower.get(lower)!.add(value)
    }
    const canonical = Object.keys(lucideIconRegistry)
    expect(canonical.length).toBeGreaterThan(1000)

    // Why the fold compares values rather than counting names: most case-only name collisions
    // (`Grid2x2Check`/`Grid2X2Check`, `Move3d`/`Move3D`, …) are one component under two spellings,
    // and the first version of this fix dropped on the name collision alone and silently refused 7
    // of them. A collision whose names are DIFFERENT components (lucide 1.64's `LetterS`/`Letters`
    // was the first) is genuinely ambiguous. Every such spelling group is derived here from the
    // library's own exports, so a group a later release adds is held with no edit: the fold refuses
    // it, and each of its exact spellings still resolves to its own icon.
    const exports = Lucide as unknown as Record<string, unknown>
    const spellings = new Map<string, string[]>()
    for (const [key, value] of Object.entries(exports)) {
      if (!iconValues.has(value)) continue
      const lower = key.toLowerCase()
      spellings.set(lower, [...(spellings.get(lower) ?? []), key])
    }
    const ambiguous = [...spellings].filter(([lower]) => byLower.get(lower)!.size > 1)
    expect(ambiguous.length, 'FLOOR 4: lucide ships a case-only group of different icons, so the refusal below runs').toBeGreaterThan(0)
    for (const [lower, names] of ambiguous) {
      expect(resolveAppIcon(lower), `${lower}: the fold refuses to choose`).toBe(Blocks)
      for (const n of names) expect(resolveAppIcon(n), `${n}: its exact spelling`).toBe(exports[n])
    }
    const refused = new Set(ambiguous.map(([lower]) => lower))
    expect(canonical.filter((n) => !refused.has(n.toLowerCase()) && resolveAppIcon(n.toLowerCase()) !== resolveAppIcon(n))).toEqual([])
  })

  it('refuses to choose when two lowercase-equal names are DIFFERENT components', () => {
    // The loop above drives the refusal with every such group lucide ships. This cell names the
    // first, LetterS/Letters (since lucide 1.64), so the branch keeps a real case if a release
    // drops it: the lowercase spelling falls back to Blocks, and each exact spelling resolves.
    expect(resolveAppIcon('letters')).toBe(Blocks)
    expect(resolveAppIcon('LetterS')).toBe(Lucide.LetterS)
    expect(resolveAppIcon('Letters')).toBe(Lucide.Letters)
    // and the exact spelling of every colliding name works regardless, since the fold is only
    // consulted after an exact-name miss — so a conflict can never cost a working name.
    for (const n of ['Grid2x2Check', 'Grid2X2Check', 'Move3d', 'Move3D', 'Grid3x3', 'Grid3X3']) {
      expect(resolveAppIcon(n), `${n} must resolve by its exact spelling`).not.toBe(Blocks)
    }
  })
})

describe('AppIcon', () => {
  it('paints for the crashing name instead of throwing', () => {
    // The component the shell actually mounts, not just the resolver — `App.tsx`'s sidebar nav
    // renders through this, which is why one manifest word could take the whole dashboard down.
    expect(renderToString(createElement(AppIcon, { name: 'icons' }))).toContain('<svg')
    expect(renderToString(createElement(AppIcon, { name: 'brain' }))).toContain('<svg')
  })
})
