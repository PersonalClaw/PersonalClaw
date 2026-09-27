import { describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useStaleWriteGuard } from './useStaleWriteGuard'
import type { Revisioned } from './staleWrite'

// ── The one recovery for a stale whole-document save ─────────────────────────────────────────
//
// `save` resolves false on `409 stale_write` and holds the conflict; the stored version is re-read
// so the change can be re-applied on top of it. These pin the edges of that state machine: what the
// re-read may and may not do once the user has already chosen.

const stale = () => Object.assign(new Error('changed'), { status: 409, code: 'stale_write' })
const deferred = <T,>() => {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('useStaleWriteGuard', () => {
  it('a refused save holds the change and re-reads what is stored', async () => {
    const read = vi.fn(() => Promise.resolve<Revisioned<string[]>>({ value: ['a', 'b'], revision: 'r2' }))
    const write = vi.fn(() => Promise.reject(stale()))
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({ read, write }))
    let landed: boolean | undefined
    await act(async () => { landed = await result.current.apply({ value: ['a'], revision: 'r1' }, (l) => [...l, 'c']) })
    expect(landed).toBe(false)
    expect(write).toHaveBeenCalledWith(['a', 'c'], 'r1')
    expect(result.current.conflict?.mine).toEqual(['a', 'c'])
    expect(result.current.conflict?.theirs).toEqual({ value: ['a', 'b'], revision: 'r2' })
    expect(result.current.conflict?.rebased).toEqual(['a', 'b', 'c'])
  })

  it('Discard while the re-read is still out stays discarded when it lands', async () => {
    const pending = deferred<Revisioned<string[]>>()
    const onDiscard = vi.fn()
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({
      read: () => pending.promise, write: () => Promise.reject(stale()), onDiscard,
    }))
    await act(async () => { await result.current.apply({ value: ['a'], revision: 'r1' }, (l) => [...l, 'c']) })
    expect(result.current.conflict).not.toBeNull()
    act(() => result.current.discard())
    expect(result.current.conflict).toBeNull()
    await act(async () => { pending.resolve({ value: ['a', 'b'], revision: 'r2' }); await pending.promise })
    expect(result.current.conflict, 'the late read must not resurrect a dismissed notice').toBeNull()
    expect(onDiscard).toHaveBeenCalledTimes(1)
  })

  it('a failed re-read says so, and Try again reads once more', async () => {
    let reads = 0
    const read = vi.fn(() => (++reads === 1
      ? Promise.reject(new Error('gateway away'))
      : Promise.resolve<Revisioned<string[]>>({ value: ['b'], revision: 'r2' })))
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({ read, write: () => Promise.reject(stale()) }))
    await act(async () => { await result.current.apply({ value: [], revision: 'r1' }, (l) => [...l, 'c']) })
    expect(result.current.conflict?.error).toMatch(/Couldn't read the current version: gateway away/)
    await act(async () => { await result.current.retry() })
    expect(result.current.conflict?.error).toBeUndefined()
    expect(result.current.conflict?.rebased).toEqual(['b', 'c'])
  })

  it('reapply saves over the NEW revision, and a second refusal re-reads again', async () => {
    let reads = 0
    const read = vi.fn(() => Promise.resolve<Revisioned<string[]>>(
      ++reads === 1 ? { value: ['b'], revision: 'r2' } : { value: ['b', 'd'], revision: 'r3' }))
    const write = vi.fn((_next: string[], base: string) => (base === 'r3' ? Promise.resolve({}) : Promise.reject(stale())))
    const onSaved = vi.fn()
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({ read, write, onSaved }))
    await act(async () => { await result.current.apply({ value: [], revision: 'r1' }, (l) => [...l, 'c']) })
    await act(async () => { await result.current.reapply() })          // r2 went stale too
    expect(write).toHaveBeenLastCalledWith(['b', 'c'], 'r2')
    expect(result.current.conflict?.rebased).toEqual(['b', 'd', 'c'])
    await act(async () => { await result.current.reapply() })
    expect(write).toHaveBeenLastCalledWith(['b', 'd', 'c'], 'r3')
    expect(result.current.conflict).toBeNull()
    expect(onSaved).toHaveBeenCalledWith(['b', 'd', 'c'])
  })

  it('a change the other side already made is not written again', async () => {
    const write = vi.fn(() => Promise.reject(stale()))
    const read = () => Promise.resolve<Revisioned<string[]>>({ value: ['a', 'c'], revision: 'r2' })
    const onSaved = vi.fn()
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({ read, write, onSaved }))
    await act(async () => { await result.current.apply({ value: ['a'], revision: 'r1' }, (l) => (l.includes('c') ? l : [...l, 'c'])) })
    await act(async () => { await result.current.reapply() })
    expect(write).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledWith(['a', 'c'])
    expect(result.current.conflict).toBeNull()
  })

  it('any other failure is the page’s to report, and holds no conflict', async () => {
    const { result } = renderHook(() => useStaleWriteGuard<string[]>({
      read: () => Promise.resolve({ value: [], revision: 'r' }),
      write: () => Promise.reject(Object.assign(new Error('bad value'), { status: 400, code: 'invalid_request' })),
    }))
    await expect(act(() => result.current.apply({ value: [], revision: 'r1' }, (l) => [...l, 'x']))).rejects.toThrow('bad value')
    expect(result.current.conflict).toBeNull()
  })
})
