import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A menu nobody could open, and two promises with nothing behind them ───────────────────────
//
// `ui/WidthPill` opened its options **only** on `onMouseEnter`. Driven on `#/dashboard`: focus the
// trigger, press Enter → `aria-expanded` stayed **false** and nothing appeared; the four options were
// never in the DOM, so there was no keyboard route to the content-width control at all. WCAG 2.1.1.
// Its options were always fine — real buttons with `aria-label` + `aria-pressed`; they were simply
// unreachable.
//
// 🪤 AND TWO TRIGGERS PROMISED A MENU THEY NEVER DELIVERED. Measured with each popover open:
//
//   ui/WidthPill         aria-haspopup="menu" → **0** role=menu, **0** menuitems
//   ui/NotificationBell  aria-haspopup="menu" → **0** role=menu, **0** menuitems (it opens a panel of
//                        notification ROWS, each with its own hit target and two actions)
//
// A screen-reader user told "menu" and then handed a list of buttons has been misdirected. Both
// popovers keep their `aria-expanded`, which honestly describes a disclosure; the false `menu` claim
// is gone. 🔑 The rows/options were NOT converted into menuitems: they carry `aria-pressed`, which is
// this app's recorded idiom for "one of N is chosen", and rewriting working controls to satisfy an
// attribute would be the wrong way round.
//
// The honest four were verified the same way and left alone: `ProjectPicker` and the collapsed
// `Segmented` both open a real `role="listbox"` (named, `aria-selected` correct, focus landing on the
// selected option, Escape returning focus to the trigger), and `HeaderActions` delivers `role="menu"`.
//
// 🔑 WHY THIS WAS INVISIBLE UNTIL NOW: every one of these findings needs a popover to be OPEN. The
// audit visits a surface in its default state, so it never saw any of them — the same blind spot that
// hid the terminal tab strip, the dropzone, the bell's action names and both progress indicators.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })
const files = () => walk(SRC).map((abs) => ({ file: abs.slice(SRC.length + 1), src: readFileSync(abs, 'utf8') }))

describe('an aria-haspopup trigger delivers what it promises', () => {
  /** `MarkdownInput` sets the attribute through a props object and documents, in its own words, that
   *  it is "`aria-activedescendant` + `aria-controls` + `aria-haspopup` only — deliberately NOT
   *  `role=combobox`"; its listbox lives in the menus it drives. Named, not silently skipped. */
  const EXEMPT = new Set(['ui/composer/MarkdownInput.tsx'])

  const declarers = () => files()
    .map((f) => ({ ...f, kinds: [...f.src.matchAll(/aria-haspopup=["{]*['"]?(menu|listbox)['"]?/g)].map((m) => m[1]) }))
    .filter((f) => f.kinds.length && !EXEMPT.has(f.file))

  it('finds the population (not vacuously green)', () => {
    expect(declarers().length, 'the aria-haspopup census must not go empty').toBeGreaterThanOrEqual(3)
  })

  it('each declared popup type is actually rendered in the same file', () => {
    const broken: string[] = []
    for (const { file, src, kinds } of declarers()) {
      for (const kind of new Set(kinds)) {
        if (!new RegExp(`role="${kind}"`).test(src)) broken.push(`${file} promises ${kind} and renders none`)
      }
    }
    expect(broken, `a trigger promises a popup it does not deliver:\n${broken.join('\n')}`).toEqual([])
  })

  it('the two corrected triggers claim only a disclosure', () => {
    for (const rel of ['ui/WidthPill.tsx', 'ui/NotificationBell.tsx']) {
      const code = readFileSync(join(SRC, rel), 'utf8').replace(/\/\/.*$/gm, '')
      expect(code, `${rel} must not re-add a false menu promise`).not.toMatch(/aria-haspopup/)
      expect(code, `${rel} still reports open/closed`).toMatch(/aria-expanded=\{open\}/)
    }
  })
})

describe('a disclosure is never hover-only', () => {
  it('anything that opens on hover also opens on click', () => {
    // 🪤 Scoped to hover-to-OPEN. Hover-to-HIGHLIGHT inside an already-open menu (`SlashMenu`,
    // `MentionMenu`, `Combobox`, `CommandPalette`, `MemoryGraph`) sets a selection index, not
    // visibility, and is correct — a rail that flagged those would be noise.
    const offenders: string[] = []
    for (const { file, src } of files()) {
      const opensOnHover = /onMouseEnter=\{\(\) => set(Open|Shown|Visible)\(true\)\}/.test(src)
      if (!opensOnHover) continue
      const opensOnClick = /onClick=\{\(\) => set(Open|Shown|Visible)\(\(?\w*\)? =>|onClick=\{\(\) => set(Open|Shown|Visible)\(/.test(src)
      if (!opensOnClick) offenders.push(file)
    }
    expect(offenders, `hover-only disclosure — unreachable without a pointer:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the width pill closes on Escape and returns focus to its trigger', () => {
    const src = readFileSync(join(SRC, 'ui/WidthPill.tsx'), 'utf8')
    expect(src).toMatch(/e\.key === 'Escape'/)
    expect(src).toMatch(/btnRef\.current\?\.focus\(\)/)
    expect(src, 'and closes on an outside click').toMatch(/wrapRef\.current && !wrapRef\.current\.contains/)
  })
})
