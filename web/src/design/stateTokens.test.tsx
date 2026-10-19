/** The shared brand and state tokens, and their examples (#3809's approved update).
 *
 * One set for coral, the agent orb, the buddy and the four state families (quiet, active, waiting,
 * blocked), in layer 2 for every scheme and mode. The buddy has no colour of its own (`web/DESIGN.md`
 * §2): its dots read the orb's tokens. The examples are `ui/StateMark`, and each case the update
 * names gets its gallery test: every state with its words, forced colours, high contrast, and the
 * editor and terminal surfaces.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { SCHEMES } from './schemes'
import { tokenLayer } from './tokenLayers'
import { loadTokenSheets, tokenValue, type Mode } from './tokenSheet'
import { SYSTEM_STATE_FAMILIES, WORK_PHASES, WORK_PHASE_LOOKS, type PhaseFamily } from '../lib/workPhase'
import { StateMark } from '../ui/StateMark'

afterEach(cleanup)

const MODES: Mode[] = ['dark', 'light']
const FAMILIES: PhaseFamily[] = ['quiet', 'active', 'waiting', 'blocked']
/** The tokens the update names: coral (the brand), the orb (whose dots the buddy shares) and the four families. */
const STATE_TOKENS = ['--color-brand', '--color-orb-alive', '--color-orb-rest', ...FAMILIES.map((f) => `--color-state-${f}`)]
/** What each resolves to: the colour the orb, buddy and status docs already name, so no value is new. */
const MEANS: Record<string, string> = {
  '--color-brand': '--color-primary',
  '--color-orb-alive': '--color-primary',
  '--color-orb-rest': '--color-on-surface-var',
  '--color-state-quiet': '--color-on-surface-var',
  '--color-state-active': '--color-primary',
  '--color-state-waiting': '--color-warn',
  '--color-state-blocked': '--color-danger',
}
/** Every resting ground a state shows on: the surface ramp, the editor and the terminal. */
const GROUNDS = ['--color-canvas', '--color-surface', '--color-surface-low', '--color-surface-container', '--color-surface-high', '--color-surface-highest']
const EDITOR_GROUNDS = ['--editor-background', '--editor-line-highlight-background', '--terminal-background']

const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }

/** Every state the phase contract names, with the family and the words it reads as. */
const STATES: { state: string; family: PhaseFamily; words: string }[] = [
  ...WORK_PHASES.map((p) => ({ state: p, family: WORK_PHASE_LOOKS[p].family, words: WORK_PHASE_LOOKS[p].label })),
  ...Object.entries(SYSTEM_STATE_FAMILIES).map(([s, family]) => ({ state: `system:${s}`, family, words: s === 'idle' ? 'Idle' : s === 'restarting' ? 'Restarting' : 'Offline' })),
]

describe('state tokens: one shared set, in layer 2, for every scheme and mode', () => {
  const sheets = loadTokenSheets()

  it('each is declared once in layer 2, as an alias, and never reassigned', () => {
    for (const name of STATE_TOKENS) {
      expect(tokenLayer(name), name).toBe(2)
      expect(sheets.base.filter((d) => d.name === name), `${name} declared once`).toHaveLength(1)
      expect(sheets.light.some((d) => d.name === name), `${name} follows the mode through what it aliases`).toBe(false)
      for (const s of Object.values(sheets.schemes)) expect([...s.dark, ...s.light].some((d) => d.name === name), `${s.id} sets ${name}`).toBe(false)
    }
  })

  for (const s of SCHEMES) {
    for (const mode of MODES) {
      it(`${s.id} / ${mode}: each resolves to the colour it names, so a scheme recolours them together`, () => {
        for (const name of STATE_TOKENS) expect(tokenValue(name, mode, s.id), name).toBe(tokenValue(MEANS[name], mode, s.id))
        // Coral is the scheme's primary, never a literal coral.
        expect(tokenValue('--color-brand', mode, s.id)).toBe(s.colors['--color-primary'][mode])
      })
    }
  }

  it('the buddy has no token of its own: its dots are the orb’s', () => {
    expect(sheets.base.some((d) => /^--color-buddy/.test(d.name))).toBe(false)
  })
})

describe('state examples: every state with its words', () => {
  it('covers every phase of the contract and every system state', () => {
    expect(STATES.length).toBe(WORK_PHASES.length + 3)
    for (const f of FAMILIES) expect(STATES.some((s) => s.family === f), f).toBe(true)
  })

  for (const { state, family, words } of STATES) {
    it(`${state}: the ${family} mark, and the words "${words}"`, () => {
      const { container } = render(<StateMark family={family}>{words}</StateMark>)
      const el = container.firstElementChild as HTMLElement
      expect(el.dataset.stateFamily).toBe(family)
      expect(el.textContent).toBe(words)
      // The mark is decoration: an assistive reading gets the words, never a colour.
      const mark = el.querySelector('[aria-hidden="true"]') as HTMLElement
      expect(mark.textContent).toBe('')
      expect(mark.style.background).toBe(`var(--color-state-${family})`)
    })
  }
})

describe('state examples: forced colours', () => {
  it('with every colour removed, each state is still its words, and the mark keeps its ring', () => {
    for (const { family, words } of STATES) {
      const { container, unmount } = render(<StateMark family={family}>{words}</StateMark>)
      // What forced colours does to it: author colours are replaced, so strip them.
      for (const el of container.querySelectorAll<HTMLElement>('*')) {
        el.style.removeProperty('background')
        el.style.removeProperty('border-color')
        el.style.removeProperty('color')
      }
      const el = container.firstElementChild as HTMLElement
      expect(el.textContent, family).toBe(words)
      // A border survives forced colours in the system's text colour; a fill alone would vanish.
      expect(el.querySelector('[aria-hidden="true"]')!.className, family).toMatch(/\bborder\b/)
      unmount()
    }
  })
})

describe('state examples: high contrast', () => {
  for (const s of SCHEMES) {
    for (const mode of MODES) {
      it(`${s.id} / ${mode}: each mark clears 3:1 and the words 7:1 on every resting ground`, () => {
        for (const ground of GROUNDS) {
          const g = tokenValue(ground, mode, s.id)
          expect(ratio(tokenValue('--color-on-surface', mode, s.id), g), `words on ${ground}`).toBeGreaterThanOrEqual(7)
          for (const f of FAMILIES) {
            expect(ratio(tokenValue(`--color-state-${f}`, mode, s.id), g), `${f} mark on ${ground}`).toBeGreaterThanOrEqual(3)
          }
        }
      })
    }
  }
})

describe('state examples: the editor and terminal surfaces', () => {
  for (const s of SCHEMES) {
    for (const mode of MODES) {
      it(`${s.id} / ${mode}: each mark clears 3:1 and the words 4.5:1 inside the editor and the terminal`, () => {
        for (const ground of EDITOR_GROUNDS) {
          const g = tokenValue(ground, mode, s.id)
          expect(ratio(tokenValue('--editor-foreground', mode, s.id), g), `editor text on ${ground}`).toBeGreaterThanOrEqual(4.5)
          expect(ratio(tokenValue('--terminal-foreground', mode, s.id), g), `terminal text on ${ground}`).toBeGreaterThanOrEqual(4.5)
          for (const f of FAMILIES) {
            expect(ratio(tokenValue(`--color-state-${f}`, mode, s.id), g), `${f} mark on ${ground}`).toBeGreaterThanOrEqual(3)
          }
        }
      })
    }
  }

  it('a mark inside a mono editor surface still says its state', () => {
    const { container } = render(
      <pre className="font-mono" style={{ background: 'var(--editor-background)', color: 'var(--editor-foreground)' }}>
        <StateMark family="blocked">Stopped</StateMark>
      </pre>,
    )
    expect(container.querySelector('[data-state-family="blocked"]')!.textContent).toBe('Stopped')
  })
})
