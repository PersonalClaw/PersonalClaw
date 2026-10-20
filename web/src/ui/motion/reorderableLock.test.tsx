import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Reorderable, mergeLockedOrder } from './Reorderable'

/** A locked row survives a reorder.
 *
 *  Measured data loss (issue 487): a locked item is deliberately rendered OUTSIDE the reorder
 *  group, so Motion's `onReorder` describes only the draggable rows. Handing that partial array
 *  straight to the consumer replaced the whole list with it, silently DELETING every locked row —
 *  in an action plan, exactly the completed steps whose position is the record of what was done.
 *
 *  The merge belongs to the primitive that removed the rows, so `onReorder` keeps the contract its
 *  type states (the FULL list, in its new order) for every consumer, present and future.
 */

type Row = { id: string; done: boolean }
const key = (r: Row) => r.id
const unlocked = (r: Row) => !r.done

describe('mergeLockedOrder', () => {
  it('keeps a locked row at its own index while the draggable rows move', () => {
    // The issue's repro, as list algebra: A(open) B(DONE) C(open), drag A below C.
    const items: Row[] = [{ id: 'A', done: false }, { id: 'B', done: true }, { id: 'C', done: false }]
    const merged = mergeLockedOrder(items, [{ id: 'C', done: false }, { id: 'A', done: false }], unlocked, key)
    expect(merged.map(key)).toEqual(['C', 'B', 'A'])
  })

  it('never shortens the list — a reorder is not a delete', () => {
    // The load-bearing invariant. Row count is what the data loss showed up as.
    const items: Row[] = [
      { id: 'a', done: false }, { id: 'b', done: true }, { id: 'c', done: true }, { id: 'd', done: false },
    ]
    const merged = mergeLockedOrder(items, [{ id: 'd', done: false }, { id: 'a', done: false }], unlocked, key)
    expect(merged).toHaveLength(4)
    expect(merged.map(key)).toEqual(['d', 'b', 'c', 'a'])
  })

  it('keeps EVERY locked row when most of the list is locked', () => {
    // A 10-step plan with 6 ticked lost 6 rows on the first drag.
    const items: Row[] = Array.from({ length: 10 }, (_, i) => ({ id: String(i), done: i < 6 }))
    const merged = mergeLockedOrder(items, [{ id: '9', done: false }, ...items.slice(6, 9)], unlocked, key)
    expect(merged).toHaveLength(10)
    expect(merged.slice(0, 6).map(key)).toEqual(['0', '1', '2', '3', '4', '5'])
    expect(merged.slice(6).map(key)).toEqual(['9', '6', '7', '8'])
  })

  it('drops nothing when the reported subset is incomplete', () => {
    // Defensive: a wrong ORDER is recoverable by dragging again; a lost row is not.
    const items: Row[] = [{ id: 'a', done: false }, { id: 'b', done: false }, { id: 'c', done: false }]
    const merged = mergeLockedOrder(items, [{ id: 'c', done: false }], unlocked, key)
    expect(merged).toHaveLength(3)
    expect(merged.map(key)).toEqual(['c', 'a', 'b'])
  })

  it('is a plain reorder when nothing is locked', () => {
    // Vacuity guard: the unlocked path must behave exactly as before.
    const items: Row[] = [{ id: 'a', done: false }, { id: 'b', done: false }]
    const next: Row[] = [{ id: 'b', done: false }, { id: 'a', done: false }]
    expect(mergeLockedOrder(items, next, unlocked, key).map(key)).toEqual(['b', 'a'])
  })
})

// Motion's real drag needs layout the jsdom environment does not have, so the group is stubbed to
// hand the test the callback the gesture would fire. `importOriginal` keeps every other export
// (motion, AnimatePresence) real for anything else this render pulls in.
const captured: { onReorder?: (next: unknown[]) => void; values?: unknown[] } = {}
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>()
  return {
    ...actual,
    Reorder: {
      Group: ({ children, values, onReorder }: {
        children: React.ReactNode; values: unknown[]; onReorder: (next: unknown[]) => void
      }) => {
        captured.onReorder = onReorder
        captured.values = values
        return <div data-testid="group">{children}</div>
      },
      Item: ({ children }: { children: React.ReactNode }) => <div data-testid="item">{children}</div>,
    },
  }
})

describe('Reorderable with a per-item lock', () => {
  const items: Row[] = [{ id: 'A', done: false }, { id: 'B', done: true }, { id: 'C', done: false }]

  function setup() {
    const onReorder = vi.fn()
    render(
      <Reorderable
        items={items}
        onReorder={onReorder}
        getKey={key}
        canDrag={unlocked}
        renderItem={(r) => <span>{r.id}</span>}
      />,
    )
    return { onReorder }
  }

  it('registers only the draggable rows with the group', () => {
    // The group's `values` and its rendered `Reorder.Item`s have to be the same set, or Motion
    // reasons about rows that cannot move.
    setup()
    expect((captured.values as Row[]).map(key)).toEqual(['A', 'C'])
  })

  it('hands the consumer the FULL list, not the draggable subset', () => {
    const { onReorder } = setup()
    captured.onReorder?.([{ id: 'C', done: false }, { id: 'A', done: false }])
    expect(onReorder).toHaveBeenCalledTimes(1)
    expect((onReorder.mock.calls[0][0] as Row[]).map(key)).toEqual(['C', 'B', 'A'])
  })

  it('passes the callback straight through when no lock is configured', () => {
    // Without `canDrag` there is nothing to splice, and the primitive must not add a hop.
    const onReorder = vi.fn()
    render(
      <Reorderable items={items} onReorder={onReorder} getKey={key} renderItem={(r) => <span>{r.id}</span>} />,
    )
    expect((captured.values as Row[]).map(key)).toEqual(['A', 'B', 'C'])
    captured.onReorder?.([{ id: 'C', done: false }, { id: 'B', done: true }, { id: 'A', done: false }])
    expect((onReorder.mock.calls[0][0] as Row[]).map(key)).toEqual(['C', 'B', 'A'])
  })
})
