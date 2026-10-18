/**
 * The default look has no backdrop, so it asks for no animation frame.
 *
 * `--bg-style` defaults to `none` (`web/DESIGN.md` §7): a calm canvas that paints nothing, where
 * the default used to be the wave field, a loop that repainted ~19,000 dots on every frame of an
 * idle chat. Each identity brings its own backdrop as a dial; the default identity declares none,
 * so it gets the token default.
 *
 * Mounted under the REAL appearance store, because the claim is about what an empty store
 * resolves to — a mocked `selectValue` would only restate the mock. The 2D context is stubbed
 * (jsdom has none, and without one DotGlow returns before it could schedule anything, so "no
 * frame" would hold for nothing), and every `requestAnimationFrame` is queued and counted.
 *
 * The positive control is a stored pick of the wave field: the same mount must then ask for
 * frames. It is also the upgrade half of the change — a backdrop the owner chose by hand is still
 * theirs once the default moves.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

// The store fetches saved themes on mount; left pending so nothing settles outside act().
vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider, useAppearance } = await import('../app/appearance')
const { TOKENS } = await import('../design/tokenRegistry')
const { DotGlow } = await import('./DotGlow')

const BG_STYLE = TOKENS.find((t) => t.varName === '--bg-style')!

let frames: FrameRequestCallback[] = []
let resolved = ''
const ORIGINAL = {
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
}

function Probe() {
  resolved = useAppearance().selectValue(BG_STYLE)
  return null
}

function mount() {
  return render(
    <AppearanceProvider>
      <Probe />
      <DotGlow />
    </AppearanceProvider>,
  )
}

beforeEach(() => {
  frames = []
  resolved = ''
  localStorage.clear()
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb) })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  HTMLCanvasElement.prototype.getContext = (() => new Proxy({}, { get: () => () => {} })) as never
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, { configurable: true, value: prop === 'clientWidth' ? 800 : 600 })
  }
})

afterEach(() => {
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const prop of ['clientWidth', 'clientHeight'] as const) delete (HTMLDivElement.prototype as unknown as Record<string, unknown>)[prop]
  localStorage.clear()
  document.documentElement.removeAttribute('style')
})

describe('the default backdrop', () => {
  it('is none: an empty store resolves the Background control to it', () => {
    mount()
    expect(BG_STYLE.kind === 'select' && BG_STYLE.value).toBe('none')
    expect(resolved).toBe('none')
  })

  it('asks for no animation frame', () => {
    mount()
    expect(frames, 'the default look scheduled a frame').toHaveLength(0)
  })
})

describe('a backdrop the owner picked by hand survives the new default', () => {
  it('the stored wave field still runs — and the probe above can see frames', () => {
    localStorage.setItem('appearance', JSON.stringify({ selects: { '--bg-style': 'waves' } }))
    mount()
    expect(resolved).toBe('waves')
    expect(frames.length, 'the stored wave field asked for no frame').toBe(1)
    frames.shift()!(16)
    expect(frames.length, 'and it keeps asking, one frame at a time').toBe(1)
  })
})
