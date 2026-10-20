import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChecklistEditor } from './formControls'

/** Reordering an action plan keeps the completed steps (issue 487).
 *
 *  Measured before the fix: a 3-step plan with the middle step completed became TWO steps the
 *  instant a row was dragged, and Save persisted the loss. The completed step — and with it the
 *  record of what was actually done — was destroyed, while its own tooltip promised
 *  "A completed step keeps its place".
 *
 *  This file drives `onReorder` with the partial array Motion actually reports. The sibling
 *  `checklistEdit.test.tsx` keeps the real `Reorder` so it can still assert that a locked row
 *  renders outside the group; the stub belongs here, where the write-back is what is under test.
 */

type Row = { description: string; completed: boolean }

const captured: { onReorder?: (next: unknown[]) => void } = {}
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>()
  return {
    ...actual,
    Reorder: {
      Group: ({ children, onReorder }: { children: React.ReactNode; onReorder: (next: unknown[]) => void }) => {
        captured.onReorder = onReorder
        return <div>{children}</div>
      },
      Item: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    },
  }
})

/** The keyed wrapper `ChecklistEditor` hands the primitive. Motion reports the draggable values it
 *  was given, so the test reports the same shape back. */
type Keyed = { it: Row; i: number }

const plan: Row[] = [
  { description: 'step A open', completed: false },
  { description: 'step B DONE', completed: true },
  { description: 'step C open', completed: false },
]

function setup(items: Row[] = plan) {
  const onChange = vi.fn()
  render(
    <ChecklistEditor items={items} onChange={onChange} doneKey="completed" placeholder="Add a step" ordered />,
  )
  return { onChange }
}

describe('dragging an ordered checklist', () => {
  it('does not delete the completed step', () => {
    // The repro, at the layer the data loss was measured: drag step A below step C.
    const { onChange } = setup()
    captured.onReorder?.([{ it: plan[2], i: 2 }, { it: plan[0], i: 0 }] satisfies Keyed[])
    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.calls[0][0] as Row[]
    expect(next).toHaveLength(3)
    expect(next.map((r) => r.description)).toEqual(['step C open', 'step B DONE', 'step A open'])
  })

  it('keeps the completed step at the index it already held', () => {
    // Its position IS the record of what happened in what order — the reason the row is locked.
    const { onChange } = setup()
    captured.onReorder?.([{ it: plan[2], i: 2 }, { it: plan[0], i: 0 }] satisfies Keyed[])
    const next = onChange.mock.calls[0][0] as Row[]
    expect(next[1]).toEqual({ description: 'step B DONE', completed: true })
  })

  it('keeps all six completed steps of a ten-step plan', () => {
    // The loss scaled with the number of ticked rows: six ticked meant six destroyed.
    const long: Row[] = Array.from({ length: 10 }, (_, i) => ({ description: `s${i}`, completed: i < 6 }))
    const { onChange } = setup(long)
    const open = [6, 7, 8, 9].map((i) => ({ it: long[i], i }))
    captured.onReorder?.([open[3], open[0], open[1], open[2]] satisfies Keyed[])
    const next = onChange.mock.calls[0][0] as Row[]
    expect(next.filter((r) => r.completed)).toHaveLength(6)
    expect(next.map((r) => r.description)).toEqual(['s0', 's1', 's2', 's3', 's4', 's5', 's9', 's6', 's7', 's8'])
  })

  it('still reorders a plan with nothing completed', () => {
    // Vacuity guard: the feature has to keep working, not just stop losing rows.
    const open: Row[] = [
      { description: 'one', completed: false },
      { description: 'two', completed: false },
    ]
    const { onChange } = setup(open)
    captured.onReorder?.([{ it: open[1], i: 1 }, { it: open[0], i: 0 }] satisfies Keyed[])
    expect((onChange.mock.calls[0][0] as Row[]).map((r) => r.description)).toEqual(['two', 'one'])
  })
})
