import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The board moves cards by drag ONLY, and that is legal because of a file it never mentions ──
//
// `#/tasks?view=board` changes a task's status by HTML5 drag: `TaskBoard` sets `text/plain` on
// dragstart and each column's drop handler calls `onMove(id, status)` — `TasksListPage` names it
// "Kanban drag-to-restatus". A card's `onKeyDown` handles Enter and Space, and both only OPEN the
// task; there is no keyboard move, and no click-based move on the board either.
//
// That is **not** a WCAG failure, and this file exists to say so precisely rather than let a later
// pass re-raise it as one:
//
//   2.1.1 Keyboard (A)            satisfied — the FUNCTION (set a status) is keyboard-reachable
//   2.5.7 Dragging Movements (AA) satisfied — a single-pointer alternative exists
//
// …because two other paths set the same field:
//
//   TaskForm     `<Field label="Status"><Segmented options={STATUSES…}>`  ← the edit form's control
//   TasksListPage  the bulk bar's `runBulk('update', { status })`          ← multi-select, no drag
//
// 🔑 THE POINT OF PINNING IT: the board's accessibility obligation is discharged **entirely by a
// control in a different file**, and nothing in either file records the dependency. Delete or
// re-shape the form's Status field — a plausible edit, since it looks like a plain form control —
// and the board silently becomes drag-only, which IS a WCAG 2.1.1 failure on a tier-1 surface. axe
// would report zero, because the defect is a missing alternative rather than a broken element.
//
// 🪤 AND THIS IS THE SHAPE THAT ALMOST FOOLED ME. Grepping `TaskDetail.tsx` for a status control
// returns NOTHING — the detail panel has no status picker — so the board reads as drag-only until you
// find the field on the FORM. A first pass called this "a design question about keyboard reordering";
// it is neither a defect nor a design question, it is an undocumented cross-file contract.

const TASKS = join(process.cwd(), 'src/pages/tasks')
const read = (f: string) => readFileSync(join(TASKS, f), 'utf8')
const code = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the board is drag-only, and the census says so', () => {
  it('still moves cards by native drag', () => {
    // Vacuity: if this stops being true the rest of the file is measuring nothing.
    const board = code('TaskBoard.tsx')
    expect(board, 'cards must still be a drag source').toMatch(/draggable=\{!readOnly\}/)
    expect(board, 'columns must still accept a drop').toMatch(/onDrop:/)
    expect(code('TasksListPage.tsx'), 'the drop must still persist a status').toMatch(/function moveTask\(id: string, status: string\)/)
  })

  it('offers no keyboard or click move ON the board — which is why the alternative matters', () => {
    // The card's keyboard contract is deliberately open-only (see the card's own comment). Asserted
    // so that if someone DOES add a keyboard move here, they are told to relax this file rather than
    // leave two competing stories about how a status changes.
    const board = code('TaskBoard.tsx')
    const keyHandler = board.match(/onKeyDown=\{\(e\) => \{[^}]*\}[^}]*\}/)?.[0] ?? ''
    expect(keyHandler, 'the card keydown must still exist').toMatch(/Enter/)
    expect(keyHandler, 'and it must still only OPEN — no move keys').not.toMatch(/onMove|ArrowLeft|ArrowRight/)
  })
})

describe('the non-drag path that makes the board legal', () => {
  it('the edit form exposes Status as a real, keyboard-operable control', () => {
    // 🔴 THE LOAD-BEARING ASSERTION. Not "the payload carries a status" — a payload default is not a
    // control (`TaskForm` also has `status: d.status ?? 'open'` in its builder, which would pass a
    // careless check while giving the user nothing to operate).
    const form = code('TaskForm.tsx')
    expect(form, 'a labelled Status field must exist').toMatch(/<Field label="Status">/)
    expect(form, 'and it must be the Segmented control, fed from the canonical registry')
      .toMatch(/<Field label="Status">\s*<Segmented[\s\S]{0,120}?STATUSES\.map/)
    // The registry it reads from must actually offer the board's columns, or the "alternative" cannot
    // reach every state a drag can.
    expect(code('taskMeta.tsx')).toMatch(/export const STATUSES: StatusMeta\[\]/)
  })

  it('the bulk bar can set a status without any drag', () => {
    expect(code('TasksListPage.tsx'), 'the second non-drag path')
      .toMatch(/runBulk\('update', \{ status:/)
  })

  it('a card is still openable from the keyboard (the fix that got it here)', () => {
    // Cycle 164's measurement: 30 draggable cards, role/tabindex/aria-label all null, 0 of 70 Tab
    // presses landed on one. Pinned because reaching the form is the whole alternative — a card you
    // cannot focus is a form you cannot open.
    const board = code('TaskBoard.tsx')
    expect(board).toMatch(/role="button" tabIndex=\{0\} aria-label=\{t\.title\}/)
    expect(board, 'Space must not scroll the column instead of opening').toMatch(/e\.key === ' '[\s\S]{0,60}?preventDefault\(\)/)
  })
})
