import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUnsavedGuard } from './useUnsavedGuard'

/** The browser-exit guard, extracted from `useFileTabs` so a second surface could share it
 *  rather than copy it (issue 525). Its behaviour was never directly tested; these are the
 *  three things a consumer relies on.
 */

let add: ReturnType<typeof vi.spyOn>
let remove: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  add = vi.spyOn(window, 'addEventListener')
  remove = vi.spyOn(window, 'removeEventListener')
})
afterEach(() => vi.restoreAllMocks())

const beforeUnloadCalls = (spy: typeof add) =>
  spy.mock.calls.filter((c: unknown[]) => c[0] === 'beforeunload')

describe('useUnsavedGuard', () => {
  it('arms nothing while clean — a clean editor must never nag', () => {
    renderHook(() => useUnsavedGuard(false))
    expect(beforeUnloadCalls(add)).toHaveLength(0)
  })

  it('arms exactly one handler while dirty', () => {
    renderHook(() => useUnsavedGuard(true))
    expect(beforeUnloadCalls(add)).toHaveLength(1)
  })

  it('disarms when the work is saved, and on unmount', () => {
    const { rerender, unmount } = renderHook(({ d }: { d: boolean }) => useUnsavedGuard(d), {
      initialProps: { d: true },
    })
    rerender({ d: false })
    expect(beforeUnloadCalls(remove)).toHaveLength(1)
    unmount()
    // Still one: the effect was already torn down when it went clean, so unmount adds no second
    // removal — what matters is that no handler outlives the dirty state.
    expect(beforeUnloadCalls(remove)).toHaveLength(1)
  })

  it('cancels the event the way the browser contract requires', () => {
    renderHook(() => useUnsavedGuard(true))
    const handler = beforeUnloadCalls(add)[0][1] as (e: Event & { returnValue?: unknown }) => void
    const event = { preventDefault: vi.fn(), returnValue: undefined } as unknown as Event & {
      preventDefault: () => void
      returnValue?: unknown
    }
    handler(event)
    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.returnValue).toBe('')
  })
})
