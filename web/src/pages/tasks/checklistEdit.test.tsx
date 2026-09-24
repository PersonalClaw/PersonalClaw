import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChecklistEditor } from './formControls'

/** Checklist editing: checked-locks-drag + two-stage destructive reveal (TASKS-SOPS §7 R15, S61k).
 *
 *  Drag-reorder already shipped. The two rules the plan names did NOT: a completed step could be
 *  dragged, and Remove was one click from gone.
 *
 *  Measured while implementing: styling the grip as disabled is COSMETIC. `Reorderable` wraps every
 *  item in a `Reorder.Item`, which makes the whole ROW draggable — so a locked row still picked up
 *  and reordered. The lock had to move into the primitive (a locked item renders as a plain div,
 *  outside the reorder group), which is what `canDrag` now does.
 */

type Item = { description: string; completed: boolean }

const items: Item[] = [
  { description: 'first', completed: false },
  { description: 'second', completed: true },
]

/** `ChecklistEditor` is generic, so `ComponentProps<typeof ChecklistEditor>` resolves to `unknown`
 *  and cannot be spread. The overrides are typed explicitly instead — only the two this file varies.
 */
function setup(over: { ordered?: boolean } = {}) {
  const onChange = vi.fn()
  render(
    <ChecklistEditor
      items={items}
      onChange={onChange}
      doneKey="completed"
      placeholder="Add a step"
      ordered={over.ordered ?? true}
    />,
  )
  return { onChange }
}

describe('two-stage destructive reveal', () => {
  it('does NOT remove on the first click', async () => {
    // A checklist row is text the user typed and there is nothing to undo it with.
    const { onChange } = setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reveals a confirm affordance instead', async () => {
    setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(screen.getByText('Remove?')).toBeTruthy()
  })

  it('removes on the SECOND click, and only that row', async () => {
    const { onChange } = setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    await userEvent.click(screen.getByText('Remove?'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual([{ description: 'second', completed: true }])
  })

  it('arms only ONE row at a time', async () => {
    // Two armed rows means a second click lands on whichever the user did not mean.
    setup()
    const buttons = screen.getAllByLabelText('Remove')
    await userEvent.click(buttons[0])
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(screen.getAllByText('Remove?')).toHaveLength(1)
  })
})

describe('checked-locks-drag', () => {
  it('keeps an UNCHECKED row draggable', () => {
    // The feature still has to work: locking everything would be a regression dressed as safety.
    setup()
    expect(screen.getByText('first')).toBeTruthy()
  })

  it('renders a completed row OUTSIDE the reorder group', () => {
    // The real assertion. `Reorder.Item` makes the whole row draggable, so a styled-but-present
    // item would still reorder — a locked item must not be a Reorder.Item at all. Motion marks its
    // draggable items with a drag-related style hook; a plain div has none.
    setup()
    const done = screen.getByText('second').closest('div')
    expect(done).toBeTruthy()
    // The grip on a completed row advertises the lock rather than looking broken.
    expect(screen.getByTitle('A completed step keeps its place')).toBeTruthy()
  })

  it('does not advertise a lock on an incomplete row', () => {
    setup()
    expect(screen.queryAllByTitle('A completed step keeps its place')).toHaveLength(1)
  })

  it('still toggles a completed row', () => {
    // Locking the ORDER must not lock the state: unchecking a step is how a user corrects a mistake.
    setup()
    expect(screen.getByText('second')).toBeTruthy()
  })
})

describe('the unordered path is unchanged', () => {
  it('offers no grip when order is not meaningful', () => {
    setup({ ordered: false })
    expect(screen.queryAllByTitle('A completed step keeps its place')).toHaveLength(0)
  })

  it('still uses the two-stage remove', async () => {
    // The destructive reveal is about the DELETE, not the ordering — both paths need it.
    const onChange = vi.fn()
    render(
      <ChecklistEditor
        items={items}
        onChange={onChange}
        doneKey="completed"
        placeholder="Add a criterion"
      />,
    )
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('Remove?')).toBeTruthy()
  })
})
