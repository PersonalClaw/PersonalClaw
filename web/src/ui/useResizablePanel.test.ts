import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useResizablePanel } from './useResizablePanel'

// ── The two capabilities the terminal drawer needed (cycle 192) ────────────────────────────────
//
// The Code cockpit + SidePanel + ChatFilePanel adoptions all had a STATIC max and a `-w` key, so the
// hook served them unchanged. The terminal drawer needed two generalisations, and both are pure
// state/localStorage/resize logic — so they are verified here directly, which is more rigorous than a
// browser drive of a drawer that only mounts once the app is onboarded (the dev home this cycle was
// NOT, which is exactly why this lives in the suite instead of a screenshot):
//
//   · a DYNAMIC max — a `() => window.innerHeight * frac` thunk, resolved live at every clamp, with the
//     returned `max` (→ `aria-valuemax`) tracking window resize so a screen reader is never told a
//     stale ceiling;
//   · a `storageKey` OVERRIDE — so a panel whose key predates the `-w` convention (`terminal-drawer-h`)
//     adopts the hook without resetting its saved size.
//
// jsdom is a valid harness for all of this: `window.innerHeight` is settable, `resize` dispatches, and
// localStorage works — none of it depends on layout (unlike the focus-trap, which needs `offsetParent`).

const setH = (h: number) => Object.defineProperty(window, 'innerHeight', { value: h, configurable: true })

describe('useResizablePanel — dynamic max + storageKey (the terminal-drawer capabilities)', () => {
  beforeEach(() => { localStorage.clear(); setH(900) })
  afterEach(() => { localStorage.clear() })

  it('persists to the OVERRIDE key, never to `${key}-w`', () => {
    const { result, unmount } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    // Drive the keyboard grow (side 'bottom' → ArrowUp grows) and let the unmount flush write.
    act(() => { result.current.onHandleKey({ key: 'ArrowUp', preventDefault() {} } as unknown as React.KeyboardEvent) })
    unmount()
    expect(localStorage.getItem('terminal-drawer-h'), 'writes the legacy key').toBe('336') // 320 + 16
    expect(localStorage.getItem('terminal-drawer-w'), 'must NOT write the -w convention key').toBeNull()
    expect(localStorage.getItem('terminal-drawer-collapsed'), 'width-only: no collapsed key').toBeNull()
  })

  it('reads the OVERRIDE key on init — a saved size round-trips, not resets', () => {
    localStorage.setItem('terminal-drawer-h', '300')
    const { result } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    expect(result.current.width).toBe(300)
  })

  it('End clamps to the LIVE dynamic max, and the returned max tracks a resize', () => {
    const { result } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    // 900 × 0.85 = 765
    expect(result.current.max).toBe(765)
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width, 'End → the current ceiling').toBe(765)
    // Shrink the viewport: the returned max (→ aria-valuemax) must follow, and End must clamp lower.
    act(() => { setH(600); window.dispatchEvent(new Event('resize')) })
    expect(result.current.max, 'aria-valuemax tracks the viewport').toBe(510) // 600 × 0.85
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width, 'End clamps to the SHRUNK ceiling').toBe(510)
  })

  it('a STATIC max still works and adds no resize listener churn (the other four adopters)', () => {
    const { result } = renderHook(() =>
      useResizablePanel('side', { def: 420, min: 320, max: 720, side: 'right' }))
    expect(result.current.max).toBe(720)
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width).toBe(720)
    // A resize must NOT move a static max.
    act(() => { setH(400); window.dispatchEvent(new Event('resize')) })
    expect(result.current.max).toBe(720)
  })
})
