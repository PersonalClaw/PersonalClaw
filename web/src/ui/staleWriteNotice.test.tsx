import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { HeldChange, StaleWriteNotice } from './StaleWriteNotice'
import { useStaleWriteGuard, type StaleWriteGuard } from '../lib/useStaleWriteGuard'
import { HELD_CHANGE_REASON, mergeRecord, type Rebase, type Revisioned } from '../lib/staleWrite'

// ── The one recovery for a stale whole-document save ─────────────────────────────────────────
//
// `useStaleWriteGuard`'s `save` resolves false on `409 stale_write` and holds the conflict; the
// stored version is re-read so the change can be re-applied on top of it, and `StaleWriteNotice`
// says so and offers the choice. Every surface that saves a whole document goes through these two,
// so their cases are proved here once: the state machine's edges as rows of one table, then what
// the notice shows and does. Which surface sends what, and keeps its draft, is
// `pages/staleWriteSurfaces.test.tsx`'s; the merges are `lib/staleWrite.test.ts`'s.

const stale = () => Object.assign(new Error('changed'), { status: 409, code: 'stale_write' })
const deferred = <T,>() => {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((res) => { resolve = res })
  return { promise, resolve }
}
const append = (x: string): Rebase<string[]> => (l) => [...l, x]

type Guard = { current: StaleWriteGuard<string[]> }
type Hooks = Parameters<typeof useStaleWriteGuard<string[]>>[0]
const guardOf = (hooks: Hooks): Guard => renderHook(() => useStaleWriteGuard<string[]>(hooks)).result

/** The state machine's edges: what the re-read may and may not do once the user has chosen. */
const MACHINE: Array<{ row: string; run: () => Promise<void> }> = [
  { row: 'a refused save holds the change and re-reads what is stored', run: async () => {
    const write = vi.fn(() => Promise.reject(stale()))
    const g = guardOf({ read: () => Promise.resolve({ value: ['a', 'b'], revision: 'r2' }), write })
    let landed: boolean | undefined
    await act(async () => { landed = await g.current.apply({ value: ['a'], revision: 'r1' }, append('c')) })
    expect(landed).toBe(false)
    expect(write).toHaveBeenCalledWith(['a', 'c'], 'r1')
    expect(g.current.conflict?.mine).toEqual(['a', 'c'])
    expect(g.current.conflict?.theirs).toEqual({ value: ['a', 'b'], revision: 'r2' })
    expect(g.current.conflict?.rebased).toEqual(['a', 'b', 'c'])
  } },
  { row: 'Discard while the re-read is still out stays discarded when it lands', run: async () => {
    const pending = deferred<Revisioned<string[]>>()
    const onDiscard = vi.fn()
    const g = guardOf({ read: () => pending.promise, write: () => Promise.reject(stale()), onDiscard })
    await act(async () => { await g.current.apply({ value: ['a'], revision: 'r1' }, append('c')) })
    expect(g.current.conflict).not.toBeNull()
    act(() => g.current.discard())
    expect(g.current.conflict).toBeNull()
    await act(async () => { pending.resolve({ value: ['a', 'b'], revision: 'r2' }); await pending.promise })
    expect(g.current.conflict, 'the late read must not resurrect a dismissed notice').toBeNull()
    expect(onDiscard).toHaveBeenCalledTimes(1)
  } },
  { row: 'a failed re-read says so, and Try again reads once more', run: async () => {
    let reads = 0
    const read = () => (++reads === 1
      ? Promise.reject(new Error('gateway away'))
      : Promise.resolve<Revisioned<string[]>>({ value: ['b'], revision: 'r2' }))
    const g = guardOf({ read, write: () => Promise.reject(stale()) })
    await act(async () => { await g.current.apply({ value: [], revision: 'r1' }, append('c')) })
    expect(g.current.conflict?.error).toMatch(/Couldn't read the current version: gateway away/)
    await act(async () => { await g.current.retry() })
    expect(g.current.conflict?.error).toBeUndefined()
    expect(g.current.conflict?.rebased).toEqual(['b', 'c'])
  } },
  { row: 'reapply saves over the NEW revision, and a second refusal re-reads again', run: async () => {
    let reads = 0
    const read = () => Promise.resolve<Revisioned<string[]>>(
      ++reads === 1 ? { value: ['b'], revision: 'r2' } : { value: ['b', 'd'], revision: 'r3' })
    const write = vi.fn((_next: string[], base: string) => (base === 'r3' ? Promise.resolve({}) : Promise.reject(stale())))
    const onSaved = vi.fn()
    const g = guardOf({ read, write, onSaved })
    await act(async () => { await g.current.apply({ value: [], revision: 'r1' }, append('c')) })
    await act(async () => { await g.current.reapply() }) // r2 went stale too
    expect(write).toHaveBeenLastCalledWith(['b', 'c'], 'r2')
    expect(g.current.conflict?.rebased).toEqual(['b', 'd', 'c'])
    await act(async () => { await g.current.reapply() })
    expect(write).toHaveBeenLastCalledWith(['b', 'd', 'c'], 'r3')
    expect(g.current.conflict).toBeNull()
    expect(onSaved).toHaveBeenCalledWith(['b', 'd', 'c'])
  } },
  { row: 'a change the other side already made is not written again', run: async () => {
    const write = vi.fn(() => Promise.reject(stale()))
    const onSaved = vi.fn()
    const g = guardOf({ read: () => Promise.resolve({ value: ['a', 'c'], revision: 'r2' }), write, onSaved })
    await act(async () => { await g.current.apply({ value: ['a'], revision: 'r1' }, (l) => (l.includes('c') ? l : [...l, 'c'])) })
    await act(async () => { await g.current.reapply() })
    expect(write).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledWith(['a', 'c'])
    expect(g.current.conflict).toBeNull()
  } },
  { row: 'a failed re-read that lands after Discard stays discarded', run: async () => {
    let fail!: (e: unknown) => void
    const pending = new Promise<Revisioned<string[]>>((_res, rej) => { fail = rej })
    const g = guardOf({ read: () => pending, write: () => Promise.reject(stale()) })
    await act(async () => { await g.current.apply({ value: ['a'], revision: 'r1' }, append('c')) })
    act(() => g.current.discard())
    await act(async () => { fail(new Error('gateway away')); await pending.catch(() => {}) })
    expect(g.current.conflict, 'the late failure must not resurrect a dismissed notice').toBeNull()
  } },
  { row: 'an operation that no longer applies to the copy on screen saves nothing', run: async () => {
    const write = vi.fn(() => Promise.resolve({}))
    const g = guardOf({ read: () => Promise.resolve({ value: [], revision: 'r' }), write })
    let landed: boolean | undefined
    await act(async () => { landed = await g.current.apply({ value: ['a'], revision: 'r1' }, () => null) })
    expect(landed).toBe(false)
    expect(write).not.toHaveBeenCalled()
    expect(g.current.conflict).toBeNull()
  } },
  { row: 'Reapply does nothing when the change cannot be re-applied', run: async () => {
    const write = vi.fn(() => Promise.reject(stale()))
    const g = guardOf({ read: () => Promise.resolve({ value: ['b'], revision: 'r2' }), write })
    await act(async () => { await g.current.save({ value: ['a'], revision: 'r1' }, ['a', 'c'], () => null) })
    expect(g.current.conflict?.rebased).toBeNull()
    let landed: boolean | undefined
    await act(async () => { landed = await g.current.reapply() })
    expect(landed).toBe(false)
    expect(write).toHaveBeenCalledTimes(1)
  } },
  { row: 'a re-applied save that fails for another reason says so, and keeps the change', run: async () => {
    let writes = 0
    const write = vi.fn(() => (++writes === 1 ? Promise.reject(stale()) : Promise.reject(new Error('disk full'))))
    const g = guardOf({ read: () => Promise.resolve({ value: ['b'], revision: 'r2' }), write })
    await act(async () => { await g.current.apply({ value: [], revision: 'r1' }, append('c')) })
    let landed: boolean | undefined
    await act(async () => { landed = await g.current.reapply() })
    expect(landed).toBe(false)
    expect(g.current.conflict?.error).toBe('disk full')
    expect(g.current.conflict?.mine).toEqual(['c'])
  } },
  { row: 'any other failure is the page’s to report, and holds no conflict', run: async () => {
    const g = guardOf({
      read: () => Promise.resolve({ value: [], revision: 'r' }),
      write: () => Promise.reject(Object.assign(new Error('bad value'), { status: 400, code: 'invalid_request' })),
    })
    await expect(act(() => g.current.apply({ value: [], revision: 'r1' }, append('x')))).rejects.toThrow('bad value')
    expect(g.current.conflict).toBeNull()
  } },
]

describe('useStaleWriteGuard', () => {
  it('holds every edge of the state machine', () => expect(MACHINE).toHaveLength(10))
  it.each(MACHINE.map((r) => [r.row, r] as const))('%s', (_row, { run }) => run())
})

// ── The notice ───────────────────────────────────────────────────────────────────────────────

type Doc = Record<string, string>
const BASE: Doc = { name: 'a', prompt: 'one' }

/** A surface in miniature: a field, a Save through the guard, the notice beside it. What is stored
 *  by the time the save lands is `stored`, so the refusal re-reads it. */
function Editor({ stored, read, write, onDiscard, onGuard }: {
  stored: () => Revisioned<Doc>
  /** The re-read, when it is not simply `stored`. */
  read?: () => Promise<Revisioned<Doc>>
  write: (next: Doc, base: string) => Promise<unknown>
  onDiscard?: () => void
  onGuard?: (g: StaleWriteGuard<Doc>) => void
}) {
  const guard = useStaleWriteGuard<Doc>({ read: read ?? (() => Promise.resolve(stored())), write, onDiscard })
  onGuard?.(guard)
  const mine = { ...BASE, prompt: 'two' }
  return (
    <>
      <HeldChange guard={guard}><input aria-label="Prompt" defaultValue="two" /></HeldChange>
      <button type="button" onClick={() => void guard.save({ value: BASE, revision: 'r1' }, mine, (theirs) => mergeRecord(BASE, mine, theirs))}>Save</button>
      <StaleWriteNotice guard={guard} what="Your projection rules" />
    </>
  )
}

/** Each shape a refusal takes: the other change apart from the user's (a clean re-apply), or on the
 *  same part (a conflict, decided only from the review). */
const SHAPES = [
  { row: 'a clean re-apply', theirs: { name: 'b', prompt: 'one' }, reapply: true, saved: { name: 'b', prompt: 'two' },
    elsewhere: /-\s+"name": "a",[\s\S]*\+\s+"name": "b",/, mine: /-\s+"prompt": "one"[\s\S]*\+\s+"prompt": "two"/,
    mineLabel: 'Your change, re-applied', choose: 'Reapply my change' },
  { row: 'a conflict', theirs: { name: 'a', prompt: 'three' }, reapply: false, saved: { name: 'a', prompt: 'two' },
    elsewhere: /-\s+"prompt": "one"[\s\S]*\+\s+"prompt": "three"/, mine: /-\s+"prompt": "one"[\s\S]*\+\s+"prompt": "two"/,
    mineLabel: 'Your change', choose: 'Save mine over it' },
]

describe('StaleWriteNotice', () => {
  const scrolled = vi.fn()
  const original = Element.prototype.scrollIntoView
  beforeEach(() => { Element.prototype.scrollIntoView = scrolled; scrolled.mockClear() })
  afterEach(() => { Element.prototype.scrollIntoView = original })

  async function refuse(theirs: Doc, extra: Partial<Parameters<typeof Editor>[0]> = {}) {
    const write = vi.fn((_next: Doc, base: string) => (base === 'r2' ? Promise.resolve({}) : Promise.reject(stale())))
    render(<Editor stored={() => ({ value: theirs, revision: 'r2' })} write={write} {...extra} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    const alert = await screen.findByRole('alert')
    return { alert, write }
  }

  it('holds every shape of a refusal', () => expect(SHAPES).toHaveLength(2))

  it.each(SHAPES.map((s) => [s.row, s] as const))('%s: says the change was kept, shows both sides, and saves only the choice made', async (_row, s) => {
    const { alert, write } = await refuse(s.theirs)
    expect(alert.getAttribute('data-stale-write')).toBe('true')
    expect(alert.textContent).toMatch(/Your projection rules changed elsewhere/)
    expect(alert.textContent).toMatch(/your change\s+wasn’t saved/)
    expect(alert.textContent?.includes('touch the same part'), 'a conflict says why there is no Reapply').toBe(!s.reapply)
    expect(within(alert).queryByRole('button', { name: 'Reload and reapply' }) !== null).toBe(s.reapply)
    expect(within(alert).getByRole('button', { name: 'Discard my change' })).toBeTruthy()

    const review = within(alert).getByRole('button', { name: 'Review the difference' })
    await waitFor(() => expect(review.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(review) })
    const dialog = await screen.findByRole('dialog', { name: 'Review the difference' })
    expect(within(dialog).getByLabelText('What changed elsewhere').textContent).toMatch(s.elsewhere)
    expect(within(dialog).getByLabelText(s.mineLabel).textContent).toMatch(s.mine)
    expect(write, 'nothing is replaced before the choice').toHaveBeenCalledTimes(1)
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: s.choose })) })
    await waitFor(() => expect(write).toHaveBeenCalledTimes(2))
    expect(write).toHaveBeenLastCalledWith(s.saved, 'r2')
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('Reload and reapply saves the re-applied change over the revision it re-read', async () => {
    const { alert, write } = await refuse({ name: 'b', prompt: 'one' })
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(write).toHaveBeenLastCalledWith({ name: 'b', prompt: 'two' }, 'r2'))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('while the stored version is being read, both actions wait, and say why', async () => {
    let land!: (v: Revisioned<Doc>) => void
    const read = () => new Promise<Revisioned<Doc>>((res) => { land = res })
    render(<Editor stored={() => ({ value: BASE, revision: 'r2' })} read={read} write={() => Promise.reject(stale())} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: /Reload and reapply|Reading/ })
    expect(reapply.hasAttribute('disabled') || reapply.getAttribute('aria-disabled') === 'true', 'nothing to re-apply onto yet').toBe(true)
    const review = within(alert).getByRole('button', { name: 'Review the difference' })
    expect(review.getAttribute('aria-disabled')).toBe('true')
    expect(review.getAttribute('title')).toMatch(/Reading the current version/)
    await act(async () => { land({ value: { name: 'b', prompt: 'one' }, revision: 'r2' }) })
    await waitFor(() => expect(within(alert).getByRole('button', { name: 'Reload and reapply' }).hasAttribute('disabled')).toBe(false))
    expect(review.getAttribute('aria-disabled')).toBeNull()
  })

  it('a failed re-read says so beside Try again, which reads once more', async () => {
    let reads = 0
    let land!: () => void
    const again = new Promise<Revisioned<Doc>>((res) => { land = () => res({ value: { name: 'b', prompt: 'one' }, revision: 'r2' }) })
    const write = vi.fn(() => Promise.reject(stale()))
    function Failing() {
      const guard = useStaleWriteGuard<Doc>({
        read: () => (++reads === 1 ? Promise.reject(new Error('gateway away')) : again),
        write,
      })
      const mine = { ...BASE, prompt: 'two' }
      return (
        <>
          <button type="button" onClick={() => void guard.save({ value: BASE, revision: 'r1' }, mine, (theirs) => mergeRecord(BASE, mine, theirs))}>Save</button>
          <StaleWriteNotice guard={guard} what="Your projection rules" />
        </>
      )
    }
    render(<Failing />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert.textContent).toMatch(/Couldn't read the current version: gateway away/))
    // Neither action has a stored version to work on, and each says why.
    for (const name of ['Review the difference', 'Reload and reapply']) {
      const action = within(alert).getByRole('button', { name })
      expect(action.getAttribute('aria-disabled'), `${name} waits`).toBe('true')
      expect(action.getAttribute('title') ?? '', `${name} says why`).toMatch(/could not be read/)
    }
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Try again' })) })
    // The failure is off the screen while the new read is out, not left beside it.
    expect(alert.textContent).not.toMatch(/Couldn't read/)
    expect(within(alert).queryByRole('button', { name: 'Try again' })).toBeNull()
    await act(async () => { land() })
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
  })

  it('Discard is not offered while a re-applied save is on the wire', async () => {
    let land!: () => void
    let writes = 0
    const write = vi.fn(() => (++writes === 1 ? Promise.reject(stale()) : new Promise<unknown>((res) => { land = () => res({}) })))
    const { alert } = await refuse({ name: 'b', prompt: 'one' }, { write } as never)
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    expect(within(alert).queryByRole('button', { name: 'Discard my change' }), 'that save may land; a Discard beside it would promise otherwise').toBeNull()
    await act(async () => { land() })
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('a stored copy that reads the same as the one edited shows no difference, and says so', async () => {
    const { alert } = await refuse(BASE)
    const review = within(alert).getByRole('button', { name: 'Review the difference' })
    await waitFor(() => expect(review.hasAttribute('disabled') || review.getAttribute('aria-disabled') === 'true').toBe(false))
    await act(async () => { fireEvent.click(review) })
    const dialog = await screen.findByRole('dialog', { name: 'Review the difference' })
    expect(dialog.textContent).toContain('No difference — it already matches.')
  })

  it('Discard my change writes nothing more, and hands the page back what is stored', async () => {
    const onDiscard = vi.fn()
    const { alert, write } = await refuse({ name: 'b', prompt: 'one' }, { onDiscard })
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Discard my change' })) })
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(write).toHaveBeenCalledTimes(1)
    expect(onDiscard).toHaveBeenCalledTimes(1)
  })

  // Two defects the two-tab drive measured, both about what the user can SEE once a save is
  // refused: the notice rendered where it was placed — at the end of a long form it sat at y=2114
  // in a 1000px window, under the sticky Save bar, with focus dropped to <body> — and the form
  // stayed editable, so text typed after the refusal was dropped by Reload and reapply.
  it('scrolls itself into view and takes focus, once, when a refusal arrives', async () => {
    let guard: StaleWriteGuard<Doc> | undefined
    const { alert } = await refuse({ name: 'b', prompt: 'one' }, { onGuard: (g) => { guard = g } })
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' })
    expect(document.activeElement, 'a keyboard user lands on the choice, not on <body>').toBe(alert)
    // The re-read landing changes the conflict, not whether there is one: no second jump.
    await waitFor(() => expect(guard?.conflict?.theirs).toBeDefined())
    expect(scrolled).toHaveBeenCalledTimes(1)
  })

  it('HeldChange turns the editor off while a change is held, and back on when it is settled', async () => {
    let guard: StaleWriteGuard<Doc> | undefined
    render(<Editor stored={() => ({ value: BASE, revision: 'r2' })} write={() => Promise.reject(stale())} onGuard={(g) => { guard = g }} />)
    const field = () => screen.getByLabelText('Prompt') as HTMLInputElement
    expect(field().matches(':disabled')).toBe(false)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    await screen.findByRole('alert')
    expect(field().matches(':disabled'), 'nothing typed now can be left out of what Reapply puts back').toBe(true)
    expect(field().closest('fieldset')?.getAttribute('title')).toBe(HELD_CHANGE_REASON)
    act(() => guard!.discard())
    expect(field().matches(':disabled')).toBe(false)
  })
})
