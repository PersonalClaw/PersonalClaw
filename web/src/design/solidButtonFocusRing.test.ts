// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import { tokenValue } from './tokenSheet'
import { readSource } from '../test/sourceTree'

/** The focus ring on a SOLID button clears 3:1 against what is next to it, in every scheme and both
 *  themes. A solid button is any shared control filled with the accent or with danger: Button's
 *  `primary` and `danger` tiers, a `filled` IconButton (the composer's send and stop, the steer and
 *  comment sends), HeaderActions' own `primary` and `danger` tiers, and the primary header action
 *  the app frame draws for a contributed app. Each carries the same fill, so each needs the same
 *  ring; `SITES` below lists them, and every check runs on every one.
 *
 *  What is next to the ring. The ring is an outline drawn at an offset of at least 2px outside the
 *  button, so the page shows between the ring and the button's fill: on both sides the ring's
 *  neighbour is the page, never the fill. That is what the 3:1 non-text contrast rule (SC 1.4.11,
 *  and SC 2.4.11 for a focus indicator) measures, so this file asserts the ring against every page
 *  surface, and asserts the offset that keeps the fill from touching it.
 *
 *  Why a solid tier needs its own ring colour. The app-wide `:focus-visible` ring in `tokens.css` is
 *  the scheme's primary. On a primary button that is the button's own fill colour (1.00:1), and on a
 *  danger button it is within a hair of it (coral `#ff6b5b` on danger `#f66c66`), so the ring reads
 *  as part of the button and a measurement of the focused button finds no indicator at all.
 *
 *  Why that colour is a neutral. No single colour drawn outside the button can clear 3:1 against
 *  both the fill and a dark page: in dark mode the fill is a light mid-tone and the page is near
 *  black, and the best grey reaches 2.37-2.87:1 against both at once. The gap is what separates the
 *  ring from the fill. The ring itself must not be the accent (coral stays the one live accent, on
 *  the button's fill) and must not be a status colour (a focused button is not an error), so it is
 *  the ink, `--color-on-surface`: near-white in dark, near-black in light. */

const lum = (hex: string): number => {
  const h = hex.replace('#', '')
  const ch = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4)
}
const contrast = (a: string, b: string): number => {
  const la = lum(a), lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const source = (rel: string) => () => readSource(join(process.cwd(), rel))
const CSS = source('src/design/tokens.css')
const BUTTON = source('src/ui/Button.tsx')
const ICON_BUTTON = source('src/ui/IconButton.tsx')
const HEADER_ACTIONS = source('src/ui/HeaderActions.tsx')
const APP_FRAME = source('src/pages/apps/AppFrame.tsx')

/** A token in a given mode as `tokens.css` declares it, matching the `.light` RULE BLOCK (the file
 *  says ".light mode" in a comment first, the trap `focusRingContrast.test.ts` records). */
function base(mode: 'dark' | 'light', name: string): string | undefined {
  return tokenValue(`--color-${name}`, mode)
}

/** A colour token in a scheme and mode: the scheme's value where the scheme sets it, else the base. */
function token(s: (typeof SCHEMES)[number], mode: 'dark' | 'light', name: string): string {
  const v = s.colors[`--color-${name}`]?.[mode] ?? base(mode, name)
  if (!v || !v.startsWith('#')) throw new Error(`no --color-${name} for ${s.id}/${mode}`)
  return v
}

/** The page surfaces a button can sit on. */
const PAGE = ['canvas', 'surface', 'surface-low', 'surface-container', 'surface-high', 'surface-highest']

/** The accent and status tokens a ring must not be drawn in. */
const NOT_A_RING = ['primary', 'primary-emphasis', 'on-primary-tint', 'secondary', 'danger', 'error',
  'warn', 'warning', 'ok', 'success', 'info']

/** SC 1.4.11 / SC 2.4.11: a focus indicator is a UI component boundary, 3:1. */
const FLOOR = 3

/** The class list of one tier in a file's `variants` table, read from inside the table so a key of
 *  the same name elsewhere in the file cannot answer for it. */
function tier(file: string, name: 'primary' | 'danger'): string {
  const table = /const variants: Record<Variant, string> = \{([\s\S]*?)\n\}/.exec(file)?.[1]
  if (!table) throw new Error('no variants table')
  const m = table.match(new RegExp(`\\n\\s*${name}: '([^']*)'`))
  if (!m) throw new Error(`no ${name} tier in the variants table`)
  return m[1]
}

/** The class list a component picks in one branch of a conditional, the first group of `re`. Line
 *  comments are taken out first, so a note between the condition and its class list is no miss. */
function branch(file: string, re: RegExp, what: string): string {
  const m = re.exec(file.replace(/^\s*\/\/.*\n/gm, ''))
  if (!m) throw new Error(`no ${what}`)
  return m[1]
}

/** Every solid fill a shared control draws, and where its class list is written. */
const SITES: { site: string; cls: () => string }[] = [
  { site: 'Button primary', cls: () => tier(BUTTON(), 'primary') },
  { site: 'Button danger', cls: () => tier(BUTTON(), 'danger') },
  { site: 'IconButton filled', cls: () => branch(ICON_BUTTON(), /:\s*filled\s*\?\s*'([^']*)'/, "IconButton's filled branch") },
  { site: 'HeaderActions primary', cls: () => tier(HEADER_ACTIONS(), 'primary') },
  { site: 'HeaderActions danger', cls: () => tier(HEADER_ACTIONS(), 'danger') },
  { site: "the app frame's primary header action", cls: () => branch(APP_FRAME(), /variant === 'primary'\s*\?\s*'([^']*)'/, "the app frame's primary header action") },
]

/** The colour token a tier's focus ring is drawn in: its own `focus-visible:outline-<token>`, or
 *  the global rule's `var(--color-…)` when it sets none. */
function ringToken(cls: string): string {
  const own = /(?:^|\s)focus-visible:outline-([a-z-]+)(?:\s|$)/.exec(cls)?.[1]
  if (own && !/^(none|hidden|offset-.*|\d+)$/.test(own)) return own
  const global = /:focus-visible\s*\{[^}]*outline:\s*\d+px\s+solid\s+var\(--color-([a-z-]+)\)/.exec(CSS())?.[1]
  if (!global) throw new Error('no global :focus-visible outline colour in tokens.css')
  return global
}

const fillToken = (cls: string): string => {
  const m = /(?:^|\s)bg-([a-z-]+)(?:\s|$)/.exec(cls)
  if (!m) throw new Error(`no fill in "${cls}"`)
  return m[1]
}

describe('a solid button\'s focus ring clears 3:1 against what is next to it', () => {
  for (const { site, cls: classes } of SITES) {
    it(`${site}: the ring is not the accent, the button's fill or a status colour`, () => {
      const cls = classes()
      // The site read is the solid fill it names, not some other branch of the same file.
      expect(['primary', 'danger'], `${site} reads as "${cls}"`).toContain(fillToken(cls))
      const ring = ringToken(cls)
      expect(ring, `${site}'s ring would be drawn in --color-${ring}`).not.toBe(fillToken(cls))
      expect(NOT_A_RING, `${site}'s ring would be drawn in --color-${ring}`).not.toContain(ring)
    })

    it(`${site}: the ring is >= 3:1 against every page surface, in every scheme and theme`, () => {
      const ring = ringToken(classes())
      const failures: string[] = []
      let worst = Infinity
      for (const s of SCHEMES) {
        for (const mode of ['dark', 'light'] as const) {
          const r = token(s, mode, ring)
          for (const t of PAGE) {
            const c = contrast(r, token(s, mode, t))
            worst = Math.min(worst, c)
            if (c < FLOOR) failures.push(`${s.id}/${mode}/${t}: ${c.toFixed(2)}:1`)
          }
        }
      }
      expect(failures, `${site}'s focus ring nobody can see:\n${failures.join('\n')}`).toEqual([])
      expect(worst).toBeGreaterThanOrEqual(FLOOR)
    })

    it(`${site}: the ring sits at least 2px outside the button, so the fill never touches it`, () => {
      expect(classes(), 'a solid fill must not move its ring onto its fill').not.toMatch(/outline-offset|ring-inset/)
      const rule = /:focus-visible\s*\{([^}]*)\}/.exec(CSS())?.[1] ?? ''
      const offset = Number(/outline-offset:\s*(\d+)px/.exec(rule)?.[1] ?? 0)
      const width = Number(/outline:\s*(\d+)px\s+solid/.exec(rule)?.[1] ?? 0)
      expect(offset, 'the page shows in this gap, between the ring and the fill').toBeGreaterThanOrEqual(2)
      expect(width, 'SC 2.4.11 measures a 2px-thick indicator').toBeGreaterThanOrEqual(2)
    })
  }

  it('measures every curated scheme', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  it('the floor is the standard, not a local preference', () => {
    expect(FLOOR).toBe(3)
  })
})
