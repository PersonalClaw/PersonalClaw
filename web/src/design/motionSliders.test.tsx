import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { fireEvent } from '@testing-library/dom'

// The provider fetches saved themes on mount; nothing here cares about them. The stub
// stays PENDING on purpose — "themes have not loaded yet" is a real state, and a promise
// that settles after render would land a setState outside act() and bury the run in
// warnings for a fetch this file is not testing.
vi.mock('../lib/api', () => ({ api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) } }))

// Imported after the mock so the provider binds it.
const { AppearanceProvider } = await import('../app/appearance')
const { ScalarControl } = await import('../ui/TokenControls')
const { TOKENS } = await import('./tokenRegistry')
const { physics } = await import('./motion')
const { runtime } = await import('./runtime')

// ── The dial the presets are supposed to track is a REAL, RENDERED control ───
// FM-1's done_when says the presets "scale with the bounciness slider". motion.test.ts
// proves the presets follow `runtime.bounciness`; this file closes the other half of
// that loop — that a slider exists in Settings → Design → Motion, and that moving it is
// what writes `runtime`. The Motion controls are GENERATED from tokenRegistry, so
// nothing in pages/settings/ mentions them by name and no test would otherwise touch
// them: a Motion token could be registered with a broken runtime key and every existing
// suite would stay green while the slider did nothing.

const MOTION_SCALARS = TOKENS.filter((t) => t.kind === 'scalar' && t.group === 'Motion')

function renderMotionGroup() {
  return render(
    <AppearanceProvider>
      {MOTION_SCALARS.map((t) => <ScalarControl key={t.varName} token={t as never} />)}
    </AppearanceProvider>,
  )
}

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const DEFAULTS = { bounciness: runtime.bounciness, dragElastic: runtime.dragElastic, swipeVelocity: runtime.swipeVelocity, swipeDistance: runtime.swipeDistance }

beforeEach(() => {
  // jsdom has no matchMedia and the provider's useIsMobile calls it unguarded.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  Object.assign(runtime, DEFAULTS)
})

describe('Settings → Design → Motion sliders', () => {
  it('renders one named slider per Motion scalar, Bounciness among them', () => {
    renderMotionGroup()
    for (const t of MOTION_SCALARS) {
      const slider = screen.getByRole('slider', { name: t.label })
      expect(slider).toHaveAttribute('min', String((t as { min: number }).min))
      expect(slider).toHaveAttribute('max', String((t as { max: number }).max))
    }
    expect(screen.getByRole('slider', { name: 'Bounciness' })).toBeInTheDocument()
    for (const label of ['Drag elasticity', 'Swipe flick speed', 'Swipe distance']) {
      expect(screen.getByRole('slider', { name: label })).toBeInTheDocument()
    }
  })

  it('dragging Bounciness to 0 flattens the presets — the full loop, slider to spring', () => {
    renderMotionGroup()
    const playfulAtDefault = (physics.playful as { damping: number }).damping

    fireEvent.change(screen.getByRole('slider', { name: 'Bounciness' }), { target: { value: '0' } })

    expect(runtime.bounciness).toBe(0)
    const flattened = (physics.playful as { damping: number }).damping
    expect(flattened).toBeGreaterThan(playfulAtDefault) // higher damping = calmer
    expect(flattened).toBe(34)                          // the preset's calm endpoint
  })

  it('each gesture slider writes the runtime key its helper reads', () => {
    renderMotionGroup()
    const cases: [string, keyof typeof DEFAULTS, string][] = [
      ['Drag elasticity', 'dragElastic', '0.5'],
      ['Swipe flick speed', 'swipeVelocity', '900'],
      ['Swipe distance', 'swipeDistance', '150'],
    ]
    for (const [label, key, value] of cases) {
      fireEvent.change(screen.getByRole('slider', { name: label }), { target: { value } })
      expect(runtime[key], `${label} did not reach runtime.${key}`).toBe(Number(value))
    }
  })
})
