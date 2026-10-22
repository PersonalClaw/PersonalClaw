// @module-tag tree-scan
/** Every binding has a keyboard e2e case, and the palette and tooltips show it (#3822's Done-when).
 *
 *  The keymap (`keymap.ts`) is the one table of the app's keys. This census holds three things to
 *  every row of it:
 *
 *  - **A key that is pressed in a browser.** Each of the row's chords is pressed in
 *    `web/e2e/keyboard.spec.ts`, or in the spec a row names in `PRESSED_ELSEWHERE` with its reason
 *    (and that spec must press it too). A row with no case at all is named in `NO_E2E_CASE` with why.
 *  - **The palette lists it, with its keys.** `keymapCommands` makes a palette command of every row,
 *    its chords in the hint.
 *  - **A tooltip shows its keys.** Some control that runs the row names it with
 *    `bindingLabel('<id>')`, or the row is in `NO_TOOLTIP` with why it has no control to tip.
 *
 *  An exemption must still be needed: one that names a row the census already finds covered, or a
 *  row the keymap no longer has, reds too, so the lists only shrink. */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { KEYMAP, bindingLabel, type CommandId } from './keymap'
import { keymapCommands } from './CommandPalette'
import { filesUnder, readSource } from '../test/sourceTree'

const WEB = process.cwd()
const SPEC = 'e2e/keyboard.spec.ts'

/** Rows whose keys another spec presses, with what it holds them to. */
const PRESSED_ELSEWHERE: Partial<Record<CommandId, { spec: string; why: string }>> = {
  'focus-mode': { spec: 'e2e/focusAndReadingModes.spec.ts', why: 'presses ⌘\\ on a chat with a turn and measures the work surface it leaves' },
  'reading-mode': { spec: 'e2e/focusAndReadingModes.spec.ts', why: 'presses ⌘⇧. to fold and unfold the composer, a draft kept across the fold' },
}

/** Rows with no e2e case of their own, and why one would add nothing. */
const NO_E2E_CASE: Partial<Record<CommandId, string>> = Object.fromEntries(
  [2, 3, 4, 5, 6, 7, 8, 9].map((n) => [`chat-${n}`, `⌘${n} runs the runner ⌘1 runs, with its number (threadNumberRoute); ⌘1's case holds the key to the sidebar's order`]),
)

/** Rows with no control of their own to carry a tooltip, and where their keys are shown instead. */
const NO_TOOLTIP: Partial<Record<CommandId, string>> = {
  'next-needs-you': 'a jump with no button of its own: Home lists what needs you, and the palette shows the key',
  'mark-all-read': 'a sweep with no button of its own: the sidebar shows each unread mark, and the palette shows the key',
  'workspace-tree': 'the working folder opens by its key or the palette, and its panel has only a Close control',
  'restore-last-prompt': "the composer's own ↑, in an empty composer: there is no control to tip",
  ...Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [`chat-${n}`, 'the sidebar row by its number: the palette lists every number with its key'])),
}

/** A Playwright key name or a keymap chord, written one way: `mod+shift+p`, `mod+\``, `arrowup`. */
function canonical(chord: string): string {
  const KEYS: Record<string, string> = { backquote: '`', backslash: '\\', period: '.', arrowup: 'arrowup', enter: 'enter', escape: 'escape' }
  const MODS: Record<string, string> = { '${mod}': 'mod', controlormeta: 'mod', meta: 'mod', control: 'mod', mod: 'mod', alt: 'alt', shift: 'shift' }
  const parts = chord.split('+')
  const key = parts.pop()!.toLowerCase()
  const mods = new Set(parts.map((m) => MODS[m.toLowerCase()] ?? m.toLowerCase()))
  return [...['mod', 'alt', 'shift'].filter((m) => mods.has(m)), KEYS[key] ?? key].join('+')
}

/** Every chord a spec presses: the argument of each `.press(…)`, a literal or a `const` it names. */
function pressedIn(spec: string): Set<string> {
  const src = readFileSync(join(WEB, spec), 'utf8')
  const consts = new Map([...src.matchAll(/const\s+([A-Z_]+)\s*=\s*['`]([^'`]+)['`]/g)].map((m) => [m[1], m[2]]))
  const out = new Set<string>()
  for (const m of src.matchAll(/\.press\(\s*(?:['`]([^'`]+)['`]|([A-Z_]+))\s*[,)]/g)) {
    const raw = m[1] ?? consts.get(m[2] ?? '')
    if (!raw) continue
    out.add(canonical(raw.replace('${MOD}', consts.get('MOD') ?? 'ControlOrMeta')))
  }
  return out
}

function sources(dir: string): string[] {
  return filesUnder(dir, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
}

/** The rows some control names in its tooltip, through `bindingLabel('<id>')`. */
function tippedRows(): Set<string> {
  const out = new Set<string>()
  for (const path of sources(join(WEB, 'src'))) {
    if (relative(WEB, path) === 'src/app/keymap.ts') continue
    for (const m of readSource(path).matchAll(/bindingLabel\(\s*(?:'([^']+)'|([^)]+))\)/g)) {
      if (m[1]) out.add(m[1])
      // A ternary of two ids (`focusMode ? 'focus-mode' : 'sidebar'`) names both.
      else for (const id of (m[2] ?? '').matchAll(/'([a-z0-9-]+)'/g)) out.add(id[1])
    }
  }
  return out
}

describe('every binding has a keyboard e2e case', () => {
  const inKeyboardSpec = pressedIn(SPEC)

  for (const row of KEYMAP) {
    it(`${row.id}: each of its chords is pressed in a browser`, () => {
      if (NO_E2E_CASE[row.id]) return
      const elsewhere = PRESSED_ELSEWHERE[row.id]
      const pressed = elsewhere ? pressedIn(elsewhere.spec) : inKeyboardSpec
      for (const chord of row.chords) {
        expect(pressed.has(canonical(chord)), `${row.id}'s ${chord} is pressed in ${elsewhere?.spec ?? SPEC}`).toBe(true)
      }
    })
  }

  it('names no exemption it does not need', () => {
    const ids = new Set(KEYMAP.map((r) => r.id))
    for (const id of [...Object.keys(NO_E2E_CASE), ...Object.keys(PRESSED_ELSEWHERE), ...Object.keys(NO_TOOLTIP)]) {
      expect(ids.has(id as CommandId), `${id} is a keymap row`).toBe(true)
    }
    for (const id of Object.keys(NO_E2E_CASE) as CommandId[]) {
      const row = KEYMAP.find((r) => r.id === id)!
      expect(row.chords.every((c) => inKeyboardSpec.has(canonical(c))), `${id} has a case now: drop its exemption`).toBe(false)
    }
    for (const id of Object.keys(PRESSED_ELSEWHERE) as CommandId[]) {
      const row = KEYMAP.find((r) => r.id === id)!
      expect(row.chords.every((c) => inKeyboardSpec.has(canonical(c))), `${id} is pressed in ${SPEC} now: drop its exemption`).toBe(false)
    }
    const tipped = tippedRows()
    for (const id of Object.keys(NO_TOOLTIP)) expect(tipped.has(id), `${id} has a tooltip now: drop its exemption`).toBe(false)
  })
})

describe('the palette and the tooltips show every binding', () => {
  it('lists every row in the palette, with its keys in the hint', () => {
    const listed = keymapCommands({})
    expect(listed.map((c) => c.id)).toEqual(KEYMAP.map((r) => `key:${r.id}`))
    for (const row of KEYMAP) {
      expect(listed.find((c) => c.id === `key:${row.id}`)!.hint, row.id).toContain(bindingLabel(row.id))
    }
  })

  const tipped = tippedRows()
  for (const row of KEYMAP) {
    it(`${row.id}: a tooltip shows its keys, or it has no control to tip`, () => {
      expect(tipped.has(row.id) || !!NO_TOOLTIP[row.id], `${row.id} is in no control's tooltip (bindingLabel('${row.id}'))`).toBe(true)
    })
  }
})
