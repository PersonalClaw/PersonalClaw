import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook, act } from '@testing-library/react'
import { useResizablePanel } from './useResizablePanel'

// ── A panel wider than the viewport strands its own controls, and there is no scrollbar ────────────
//
// 🔑 THE FACT THAT MAKES THIS UNRECOVERABLE RATHER THAN UNTIDY. `design/tokens.css` sets
// `overflow: hidden` on `html, body, #root`, so the app has **no horizontal page scrollbar ever**.
// A panel whose width exceeds the viewport does not become scrollable — the controls at its far edge
// simply cannot be reached with a pointer.
//
// `ChatFilePanel` persists its width with `MAX_W = 900`. Drag it wide on a large monitor, then
// split-screen to 720px, and both its Expand and Close buttons sit past the edge. Only Escape still
// closed it, so a pointer user had no way out at all. `SidePanel` — the identical right-docked
// shape — has been clamped since `ui/sidePanelClamp.test.tsx` was written; this panel never got it.
//
// 🪤 A VIEWPORT-RELATIVE `max` THUNK DOES NOT FIX THIS, and that is the trap worth recording. The
// primitive consults `resolveMax()` only when a clamp RUNS — inside the pointer-move handler and the
// key handler. Its resize listener updates `resolvedMax` ALONE, on purpose, so `aria-valuemax` stays
// honest; it never re-clamps `width`. The failure needs no drag: the window narrows and nothing
// recomputes. A thunk would have fixed only dragging-while-already-narrow, which is not the bug.
//
// 🪤 AND THE STORED VALUE MUST SURVIVE. `sidePanelClamp.test.tsx` states the reason — "clamps a WIDER
// stored width WITHOUT overwriting it — a wide screen restores the choice" — and asserts the
// persisted value is untouched. A thunk max WOULD have capped storage, silently destroying the
// user's preference the first time they ever split-screened. Hence two values: `width` (stored,
// persisted, `aria-valuenow`) and `fitWidth` (rendered).
//
// This lives in the primitive rather than copied per panel because that copy is how an invariant
// gets lost — the same shape as the armed-delete timer missing from one of seven hand-rolled copies.

const KEY = 'fit-test'
const W_KEY = `${KEY}-w`

function setViewport(w: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, writable: true, configurable: true })
  act(() => { window.dispatchEvent(new Event('resize')) })
}

describe('useResizablePanel keeps the rendered panel inside the viewport', () => {
  beforeEach(() => { localStorage.clear() })
  afterEach(() => { vi.restoreAllMocks() })

  const mount = (def = 480) =>
    renderHook(() => useResizablePanel(KEY, { def, min: 360, max: 900, side: 'right' }))

  it('on a wide viewport fitWidth IS the stored width — the clamp does not engage', () => {
    setViewport(1600)
    localStorage.setItem(W_KEY, '720')
    const { result } = mount()
    expect(result.current.width).toBe(720)
    expect(result.current.fitWidth, 'nothing to clamp at 1600px').toBe(720)
  })

  it('a stored width wider than the viewport renders clamped, leaving an edge sliver', () => {
    setViewport(700)
    localStorage.setItem(W_KEY, '900')
    const { result } = mount()
    // 32 is the default edgePeek — a panel must never own the whole viewport.
    expect(result.current.fitWidth).toBe(700 - 32)
    expect(result.current.fitWidth, 'the far edge must stay reachable').toBeLessThan(700)
  })

  it('and the STORED width is NOT overwritten — a re-widen restores the choice', () => {
    // 🔑 The half a `max` thunk would have got wrong. Capping storage destroys the preference.
    setViewport(700)
    localStorage.setItem(W_KEY, '900')
    const { result } = mount()
    expect(result.current.width, 'the user still chose 900').toBe(900)
    expect(localStorage.getItem(W_KEY), 'and it must still be on disk').toBe('900')
    setViewport(1600)
    expect(result.current.fitWidth, 'a wide screen gives the choice back').toBe(900)
  })

  it('it follows a resize in BOTH directions, not just at mount', () => {
    // A window dragged narrow, then wide again. Clamping only at mount would strand the first state.
    setViewport(1600)
    localStorage.setItem(W_KEY, '800')
    const { result } = mount()
    expect(result.current.fitWidth).toBe(800)
    setViewport(600)
    expect(result.current.fitWidth).toBe(600 - 32)
    setViewport(1600)
    expect(result.current.fitWidth).toBe(800)
  })

  it('a bottom-docked panel clamps against innerHeight, not innerWidth', () => {
    // 🪤 The primitive is axis-agnostic (`side: left|right|top|bottom`). Reading innerWidth for a
    // drawer would clamp the wrong dimension — invisible on a landscape monitor and wrong on a
    // portrait one, which is exactly where it would matter.
    Object.defineProperty(window, 'innerWidth', { value: 1600, writable: true, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 500, writable: true, configurable: true })
    localStorage.setItem('drawer-w', '900')
    const { result } = renderHook(() =>
      useResizablePanel('drawer', { def: 300, min: 100, max: 900, side: 'bottom' }))
    expect(result.current.fitWidth, 'clamped by height, not by the wide viewport').toBe(500 - 32)
  })

  it('edgePeek is configurable, and 0 means the panel may fill the viewport', () => {
    setViewport(700)
    localStorage.setItem('np-w', '900')
    const { result } = renderHook(() =>
      useResizablePanel('np', { def: 480, min: 360, max: 900, side: 'right', edgePeek: 0 }))
    expect(result.current.fitWidth).toBe(700)
  })
})

describe('the two right-docked panels both render the clamped width', () => {
  const strip = (s: string) =>
    s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  const read = (rel: string) => strip(readFileSync(join(process.cwd(), 'src', rel), 'utf8'))

  it('ChatFilePanel renders fitWidth and reports the stored width to aria', () => {
    // The load-bearing half: the hook could expose `fitWidth` and the panel still render `width`.
    const src = read('pages/chat/ChatFilePanel.tsx')
    expect(src, 'the animated width must be the clamped one').toMatch(/animate=\{\{ width: dockW/)
    expect(src, 'and so must the body').toMatch(/style=\{\{ width: dockW \}\}/)
    expect(src, 'aria-valuenow reports the CHOICE, not the clamp').toMatch(/aria-valuenow=\{Math\.round\(width\)\}/)
  })

  it('SidePanel now takes the clamp from the primitive instead of its own copy', () => {
    // 🔁 It invented the mechanism; keeping a private copy is what lets two panels drift apart.
    const src = read('ui/SidePanel.tsx')
    expect(src).toMatch(/fitWidth: dockW/)
    expect(src, 'the local viewport state must be gone').not.toMatch(/setViewportW/)
    expect(src, 'and its hand-rolled clamp with it').not.toMatch(/Math\.min\(width, Math\.max\(0, viewportW/)
  })

  it('the primitive itself carries the mechanism', () => {
    const src = read('ui/useResizablePanel.ts')
    expect(src).toMatch(/const fitWidth = Math\.min\(width, Math\.max\(0, viewport - edgePeek\)\)/)
    expect(src, 'returned alongside the stored width, not instead of it').toMatch(/return \{ width, fitWidth,/)
  })
})
