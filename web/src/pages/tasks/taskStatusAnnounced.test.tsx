import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TaskBoard } from './TaskBoard'
import { STATUSES, statusMeta } from './taskMeta'
import type { TaskItem } from '../../lib/api'

// ── A task's status must reach assistive tech on every surface that shows it ──────────────────
//
// `taskMeta`'s STATUSES is the declared owner of the task status vocabulary, and it carries a `label`
// for each key ("Not started", "In progress", "Blocked", "Completed", "Cancelled"). Four surfaces
// render a task's status. Measured on a home seeded from the `demo-home` fixture — ten tasks spanning
// all five statuses — three of them said it and one did not:
//
//   TaskDetail          chip: `<sm.icon/> {sm.label}`                      text ✓
//   TaskCard (cards)    the same chip in the meta row                      text ✓
//   TaskBoard           column header text + `role="group"` aria-label     text ✓
//   TaskRow (list)      a 20px lucide glyph and NOTHING else               ✗ — the defect
//
// The glyph ships `aria-hidden` (lucide's default), the row carried no status word, and there was no
// sr-only copy, so the row's accessible name was the bare title. All ten tasks read identically to a
// screen reader: `done`, `blocked` and `cancelled` indistinguishable from `open`. The visual cues that
// carry it for a sighted user — icon shape, tone, and `line-through` on a terminal task — are all
// invisible to assistive tech.
//
// 🔑 WHY THE FIX IS THE NAME AND NOT A VISIBLE CHIP. The compact row is deliberately terse; the cards
// view exists for the denser presentation and already has the chip. Adding one to the row would change
// the surface's density, which is a taste call, not a defect fix. Putting the status in the name gives
// assistive tech exact parity with what the glyph tells everyone else, and changes no pixels.
//
// 🪤 THE SHARED HELPER WOULD HAVE SWALLOWED IT. `lib/rowSubject` is the app's row-name composer, so it
// looks like the right tool — but it caps at 55 characters, and 3 of the fixture's 10 titles are
// already longer, so the appended status would have been truncated away on exactly the rows most in
// need of it. Its own docstring draws the line: "capping data is not the same as bounding a name you
// assembled." Uncapped template, title first.

const task = (id: string, status: string, title: string): TaskItem =>
  ({ id, title, status, priority: 'medium' } as unknown as TaskItem)

const SRC = (rel: string) => readFileSync(join(process.cwd(), 'src/pages/tasks', rel), 'utf8')

describe('the status vocabulary has a label for every key', () => {
  // Vacuity floor. Every assertion below leans on STATUSES having real labels; an emptied or
  // relabelled registry would make them pass while announcing nothing.
  it('all five backend statuses carry a non-empty label', () => {
    expect(STATUSES).toHaveLength(5)
    expect(STATUSES.map((s) => s.key).sort())
      .toEqual(['blocked', 'cancelled', 'done', 'in_progress', 'open'])
    for (const s of STATUSES) {
      expect(s.label.trim(), `${s.key} has a label`).not.toBe('')
      expect(s.label, `${s.key}'s label is prose, not the key`).not.toBe(s.key)
    }
  })
})

describe('the task list row announces its status', () => {
  const src = SRC('TasksListPage.tsx')

  it("the row's accessible name carries the status label, not just the title", () => {
    // The row's name is owned by the shared hit target; `sm` is `statusMeta(t.status)`.
    expect(src).toMatch(/<RowHitTarget label=\{`\$\{t\.title\} — \$\{sm\.label\}`\} \/>/)
  })

  it('the status glyph stays hidden, because the name now carries it', () => {
    // Naming the icon TOO would announce the status twice. It is decorative once the name says it.
    expect(src, 'no aria-label was added to the glyph').not.toMatch(/<sm\.icon[^>]*aria-label/)
  })

  it('the row is not capped through rowSubject, which would truncate the status away', () => {
    expect(src, 'the list page does not route row names through the 55-char capper')
      .not.toMatch(/RowHitTarget label=\{rowSubject/)
  })
})

describe('the other three surfaces already said it, and still do', () => {
  it('the detail chip renders the label as text', () => {
    expect(SRC('TaskDetail.tsx')).toMatch(/<sm\.icon size=\{13\} \/> \{sm\.label\}/)
  })

  it('the card renders the label as a visible chip', () => {
    // This is why the CARD's hit-target name is left as the bare title: the status is real text in the
    // card, reachable in browse mode. The row's glyph was reachable in no mode at all.
    const src = SRC('TasksListPage.tsx')
    const card = src.slice(src.indexOf('function TaskCard'))
    expect(card).toMatch(/\{sm\.label\}/)
  })

  it("a board column names its group with the status, so its cards inherit it", () => {
    const { container } = render(
      <TaskBoard
        tasks={[task('a', 'cancelled', 'Cancel the duplicate weekly reminder'), task('b', 'done', 'Set up the template')]}
        onOpen={() => {}}
        onMove={vi.fn()}
      />,
    )
    const named = [...container.querySelectorAll('[role="group"][aria-label]')]
      .map((el) => el.getAttribute('aria-label') ?? '')
    // Rendered, not read from source: the column that holds the cancelled card says "Cancelled".
    expect(named.some((n) => n.startsWith(statusMeta('cancelled').label)), `saw: ${named.join(' | ')}`).toBe(true)
    expect(named.some((n) => n.startsWith(statusMeta('done').label)), `saw: ${named.join(' | ')}`).toBe(true)
  })
})
