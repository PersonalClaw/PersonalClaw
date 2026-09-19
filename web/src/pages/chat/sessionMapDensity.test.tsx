/**
 * SESSION MAP — the persisted VIEW preference (SEMANTIC-SESSION-MAP §A.9, atom SSM-14).
 *
 * The clause is "round-trips through `appearance.tsx` + `tokenRegistry.ts` to localStorage;
 * test sets/reloads/reads back the value; `config.json` and `test_config_roundtrip.py`
 * untouched" — and the cheap way to pass it is a test that calls `setSelect` then reads the
 * same in-memory React state straight back. That proves a setter, not a ROUND TRIP: it would
 * pass identically on a provider that never wrote to localStorage at all.
 *
 * So the reload here is a real one. The provider is UNMOUNTED and a FRESH one is rendered,
 * which is the only thing that re-runs `appearance.tsx`'s `load()` against the stored blob.
 * Three further things are pinned, because the clause does not name them and each is a way
 * for the preference to exist and still do nothing:
 *
 *   1. the value reaches the MAP — `sessionMapDensityMarks` actually changes what renders;
 *   2. a corrupt stored value degrades to the NAMED default, not to a blank rail (the
 *      existing selects blind-cast, `DotGlow.tsx:117`, and localStorage is untrusted input);
 *   3. the preference never became a config field — the clause's "untouched" half, asserted
 *      against the Python surfaces rather than trusted.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The store fetches saved themes on mount and nothing here cares. Left PENDING deliberately
// (the convention in motionSliders/personalityDials): a promise settling after render lands a
// setState outside act() and buries the run in warnings for a fetch under no test.
vi.mock('../../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider, useAppearance } = await import('../../app/appearance')
const { TOKENS } = await import('../../design/tokenRegistry')
const {
  DEFAULT_SESSION_MAP_DENSITY,
  SESSION_MAP_DENSITIES,
  SESSION_MAP_DENSITY_VAR,
  asSessionMapDensity,
  sessionMapDensityMarks,
} = await import('./sessionMap')
type SessionMark = import('./sessionMap').SessionMark

const SRC = join(process.cwd(), 'src')
const REPO = join(process.cwd(), '..')

/** The registry entry behind the preference — resolved the same way `ChatPage` resolves it. */
const TOKEN = TOKENS.find((t) => t.kind === 'select' && t.varName === SESSION_MAP_DENSITY_VAR)

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  // jsdom has no matchMedia and the store's useIsMobile calls it unguarded.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
  localStorage.clear()
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ORIGINAL_MATCH_MEDIA,
  })
  localStorage.clear()
  document.documentElement.removeAttribute('style')
})

/** Mount the real provider and expose the one value + the one setter under test. */
function mountStore() {
  const seen: { density: string; setDensity: (v: string) => void } = {
    density: '',
    setDensity: () => {},
  }
  function Probe() {
    const { selectValue, setSelect } = useAppearance()
    seen.density = TOKEN ? selectValue(TOKEN) : ''
    seen.setDensity = (v: string) => setSelect(SESSION_MAP_DENSITY_VAR, v)
    return null
  }
  const view = render(
    <AppearanceProvider>
      <Probe />
    </AppearanceProvider>,
  )
  return { seen, view }
}

/** The stored appearance blob, read the way a reload reads it. */
const storedSelects = () =>
  JSON.parse(localStorage.getItem('appearance') ?? '{}').selects ?? {}

const mark = (markIndex: number, kind: SessionMark['kind'], visibleIndex: number): SessionMark => ({
  markIndex,
  kind,
  role: kind === 'user' ? 'user' : 'assistant',
  visibleIndex,
  ts: '',
  preview: `${kind} ${markIndex}`,
})

describe('the preference is declared once, in the registry', () => {
  it('is a select token whose options and default ARE the exported vocabulary', () => {
    // Not merely "a token exists": the registry entry must read the same constants the
    // filter does, or the control could offer a value the filter cannot honour.
    expect(TOKEN, `no select token registered for ${SESSION_MAP_DENSITY_VAR}`).toBeTruthy()
    expect(TOKEN!.kind).toBe('select')
    expect((TOKEN as { options: string[] }).options).toEqual([...SESSION_MAP_DENSITIES])
    expect((TOKEN as { value: string }).value).toBe(DEFAULT_SESSION_MAP_DENSITY)
  })

  it('renders in a DesignPanel section, so a user can actually reach it', () => {
    // A token whose `group` is in none of the four rendered lists renders in NO UI and no
    // other test catches it — the registry's own `GROUPS` export is dead.
    const schemes = readFileSync(join(SRC, 'design/schemes.ts'), 'utf8')
    const rendered = ['COLOR_GROUPS', 'BACKDROP_GROUPS', 'TYPOGRAPHY_GROUPS', 'LAYOUT_GROUPS']
      .flatMap((name) => {
        const block = schemes.split(`${name} =`)[1]?.split(']')[0] ?? ''
        return [...block.matchAll(/'([^']+)'/g)].map((m) => m[1])
      })
    expect(rendered).toContain((TOKEN as { group: string }).group)
  })

  it('names its default ONCE — the closed vocabulary is not re-spelled in the registry', () => {
    const registry = readFileSync(join(SRC, 'design/tokenRegistry.ts'), 'utf8')
    expect(registry).toContain('SESSION_MAP_DENSITY_VAR')
    expect(registry).toContain('DEFAULT_SESSION_MAP_DENSITY')
    // The literal values must NOT appear beside the entry: a second spelling is how the
    // registry default and the filter's default come to disagree.
    for (const value of SESSION_MAP_DENSITIES) {
      expect(registry, `'${value}' is spelled literally in the registry`).not.toContain(`'${value}'`)
    }
  })

  it('the vocabulary is CLOSED — adding a third value must come here first', () => {
    expect([...SESSION_MAP_DENSITIES].sort()).toEqual(['detailed', 'turns'])
    expect(SESSION_MAP_DENSITIES).toContain(DEFAULT_SESSION_MAP_DENSITY)
  })
})

describe('set → reload → read back, through localStorage', () => {
  it('a pristine store reads the registry default and writes no override', () => {
    const { seen } = mountStore()
    expect(seen.density).toBe(DEFAULT_SESSION_MAP_DENSITY)
    expect(storedSelects()[SESSION_MAP_DENSITY_VAR], 'a default is not an override').toBeUndefined()
  })

  it('the chosen value survives an UNMOUNT and a fresh provider', () => {
    const first = mountStore()
    expect(first.seen.density).toBe(DEFAULT_SESSION_MAP_DENSITY)

    act(() => first.seen.setDensity('turns'))

    // 1. SET: it reached the appearance store…
    expect(first.seen.density).toBe('turns')
    // 2. …and it reached localStorage, which is the half an in-memory setter cannot fake.
    expect(storedSelects()[SESSION_MAP_DENSITY_VAR]).toBe('turns')

    // 3. RELOAD: tear the provider down completely. Only a fresh mount re-runs `load()`.
    first.view.unmount()
    const second = mountStore()

    // 4. READ BACK.
    expect(second.seen.density).toBe('turns')
    expect(second.seen.density).not.toBe(DEFAULT_SESSION_MAP_DENSITY)
  })

  it('a value written by a PREVIOUS session is read on first mount', () => {
    // The reload above shares one jsdom `localStorage`; this is the other direction —
    // a blob that predates the provider entirely, i.e. the real cold-start case.
    localStorage.setItem('appearance', JSON.stringify({ selects: { [SESSION_MAP_DENSITY_VAR]: 'turns' } }))
    expect(mountStore().seen.density).toBe('turns')
  })
})

describe('the stored value is validated on read', () => {
  it('a corrupt stored value degrades to the NAMED default', () => {
    // localStorage is user-writable and survives a downgrade. `appearance.tsx`'s `load()` is
    // a bare JSON.parse spread with no enum check, so the narrowing has to happen here.
    localStorage.setItem('appearance', JSON.stringify({ selects: { [SESSION_MAP_DENSITY_VAR]: 'gigantic' } }))
    const { seen } = mountStore()
    expect(seen.density, 'the store hands back whatever was stored').toBe('gigantic')
    expect(asSessionMapDensity(seen.density)).toBe(DEFAULT_SESSION_MAP_DENSITY)
  })

  it('absent / empty / unknown all land on the default', () => {
    expect(asSessionMapDensity(undefined)).toBe(DEFAULT_SESSION_MAP_DENSITY)
    expect(asSessionMapDensity(null)).toBe(DEFAULT_SESSION_MAP_DENSITY)
    expect(asSessionMapDensity('')).toBe(DEFAULT_SESSION_MAP_DENSITY)
    expect(asSessionMapDensity('Turns')).toBe(DEFAULT_SESSION_MAP_DENSITY) // case matters
    expect(asSessionMapDensity('turns')).toBe('turns')
  })
})

describe('the preference reaches the map', () => {
  // Without this the whole round trip is a value nothing reads — the zero-importer shape
  // this plan has already hit twice.
  const marks = [
    mark(0, 'user', 0),
    mark(1, 'assistant', 1),
    mark(2, 'tool', 1),
    mark(3, 'error', 1),
    mark(4, 'user', 2),
    mark(5, 'assistant', 3),
    mark(6, 'tool', 3),
  ]

  it('detailed is the identity — every mark the derivation emitted', () => {
    expect(sessionMapDensityMarks(marks, 'detailed')).toBe(marks)
  })

  it('turns drops the sub-event marks and KEEPS the jump coordinates', () => {
    const out = sessionMapDensityMarks(marks, 'turns')
    expect(out.map((m) => m.kind)).toEqual(['user', 'assistant', 'user', 'assistant'])
    // The coordinate is what a jump speaks; filtering must not renumber it.
    expect(out.map((m) => m.visibleIndex)).toEqual([0, 1, 2, 3])
  })

  it('turns RE-INDEXES markIndex to the new array position', () => {
    // `markIndex` is documented as "the 0-based position in the returned array" and is the
    // rail's render key; `sessionMapMarkName` counts "Turn X of N" over the list it is given.
    // A filtered list carrying pre-filter indices keeps the field name and breaks its meaning.
    const out = sessionMapDensityMarks(marks, 'turns')
    out.forEach((m, i) => expect(m.markIndex).toBe(i))
    expect(out.map((m) => m.markIndex)).not.toEqual([0, 1, 4, 5])
  })

  it('filtering does not mutate the input array', () => {
    const before = JSON.stringify(marks)
    sessionMapDensityMarks(marks, 'turns')
    expect(JSON.stringify(marks)).toBe(before)
  })

  it('ChatPage applies the preference ONCE, above both forms', () => {
    // The rail and the coarse-pointer drawer must index the IDENTICAL array — a "Turn 3 of 7"
    // that means a different 7 in each form is two maps, not one preference.
    const page = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8')
    expect(page).toContain('sessionMapDensityMarks(allSessionMarks, mapDensity)')
    expect(page).toContain('asSessionMapDensity(')
    expect(page.match(/sessionMapDensityMarks\(/g) ?? [], 'applied in exactly one place').toHaveLength(1)
    // Both consumers read the ONE filtered list.
    expect(page).toContain('<SessionMapRail marks={sessionMarks}')
    expect(page).toContain('<SessionMapDrawer marks={sessionMarks}')
  })
})

describe('config.json and test_config_roundtrip.py are untouched', () => {
  // §A.9 is explicit that a per-surface VIEW preference does NOT go through the Python config
  // round-trip contract. Asserted rather than trusted, because "I did not add a config field"
  // is exactly the kind of claim that rots the moment someone wires a sync later.
  const NAMES = [SESSION_MAP_DENSITY_VAR, 'session_map_density', 'sessionMapDensity']

  it('the preference is not a config field', () => {
    const loader = readFileSync(join(REPO, 'src/personalclaw/config/loader.py'), 'utf8')
    for (const name of NAMES) expect(loader, `${name} leaked into the config loader`).not.toContain(name)
  })

  it('the config round-trip test does not know about it', () => {
    const roundtrip = readFileSync(join(REPO, 'tests/test_config_roundtrip.py'), 'utf8')
    for (const name of NAMES) expect(roundtrip, `${name} leaked into ${'test_config_roundtrip.py'}`).not.toContain(name)
  })

  it('it lives in the appearance blob instead — one storage key, not a config file', () => {
    const { seen } = mountStore()
    act(() => seen.setDensity('turns'))
    expect(Object.keys(localStorage)).toContain('appearance')
    expect(storedSelects()[SESSION_MAP_DENSITY_VAR]).toBe('turns')
  })
})
