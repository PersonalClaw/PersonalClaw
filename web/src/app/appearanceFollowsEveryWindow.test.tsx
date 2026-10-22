import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, act, screen, fireEvent } from '@testing-library/react'

// ── A look changed in one window is the look of every window (#3901) ───────────────────────────────
//
// The scheme, the dials and the personality are kept per browser profile in localStorage, and each
// window read them once, when it opened: a second window, the buddy's among them, kept the look it
// opened with until it reloaded. Only the theme mode followed (`theme.tsx`'s `storage` listener).
// Now each listens as the mode does: when another window writes the key, this one takes what it
// wrote. The sound-cue switch was already read on every cue; the toggle that shows it follows too.
//
// A `storage` event fires in every OTHER document of the origin when one writes the key, which is
// how a test stands two windows up: two providers, each with its own state, and the event the
// browser would deliver to the second.

vi.mock('../lib/api', () => ({ api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) } }))

const { AppearanceProvider, useAppearance } = await import('./appearance')
const { PersonalityProvider, usePersonality } = await import('./personality')
const { TOKENS } = await import('../design/tokenRegistry')
const { SCHEMES, DEFAULT_SCHEME } = await import('../design/schemes')
const { SOUND_CUES_KEY } = await import('../design/soundCues')
const { SoundCuesToggle } = await import('../pages/settings/PersonalityPicker')

const stores: Record<string, ReturnType<typeof useAppearance>> = {}
function Probe({ name }: { name: string }) { stores[name] = useAppearance(); return null }
const DIAL = TOKENS.find((t) => t.kind === 'scalar')!
const OTHER_SCHEME = SCHEMES.find((s) => s.id !== DEFAULT_SCHEME)!.id

/** What the browser delivers to the other windows when one writes *key*. */
const delivered = (key: string) => act(() => {
  window.dispatchEvent(new StorageEvent('storage', { key, newValue: window.localStorage.getItem(key) }))
})

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  window.localStorage.clear()
  document.documentElement.removeAttribute('style')
})
afterEach(() => cleanup())

describe('the look follows every window', () => {
  it('a scheme and a dial set in one window are the other window\'s, at once', () => {
    render(<>
      <AppearanceProvider><Probe name="main" /></AppearanceProvider>
      <AppearanceProvider><Probe name="buddy" /></AppearanceProvider>
    </>)
    act(() => stores.main.applyScheme(OTHER_SCHEME))
    act(() => stores.main.setScalar(DIAL.varName, (DIAL as { max: number }).max))
    expect(stores.buddy.activeScheme, 'not before the browser says so').toBe(DEFAULT_SCHEME)
    delivered('appearance')
    expect(stores.buddy.activeScheme).toBe(OTHER_SCHEME)
    expect(stores.buddy.scalarValue(DIAL)).toBe((DIAL as { max: number }).max)
  })

  it('another key written elsewhere changes nothing here', () => {
    render(<AppearanceProvider><Probe name="buddy" /></AppearanceProvider>)
    window.localStorage.setItem('appearance', JSON.stringify({ scheme: OTHER_SCHEME }))
    delivered('something-else')
    expect(stores.buddy.activeScheme).toBe(DEFAULT_SCHEME)
  })

  it('the personality chosen in one window is the other window\'s', () => {
    let seen = ''
    function Who() { seen = usePersonality().personality.id; return null }
    render(<AppearanceProvider><PersonalityProvider><Who /></PersonalityProvider></AppearanceProvider>)
    const before = seen
    window.localStorage.setItem('personality', 'claw-arcade')
    delivered('personality')
    expect(seen).not.toBe(before)
    expect(seen).toBe('claw-arcade')
  })

  it('the sound-cue switch shows what another window set', () => {
    render(<SoundCuesToggle />)
    const toggle = screen.getByRole('switch')
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    window.localStorage.setItem(SOUND_CUES_KEY, 'on')
    delivered(SOUND_CUES_KEY)
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    expect(window.localStorage.getItem(SOUND_CUES_KEY)).not.toBe('on')
  })
})
