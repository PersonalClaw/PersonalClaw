/**
 * #514 — the hub's task-count badge, and the project-name cap.
 *
 * The badge read `task_count ?? count` since the initial public commit while no endpoint emitted
 * either key, so `typeof count === 'number'` was always false and it never rendered. The backend
 * now emits `task_count`; these rails pin that the badge renders for a number, stays hidden when
 * the field is ABSENT (a count that could not be computed is not the same as zero), and — the one
 * that matters most — that the cap the field enforces is the SAME number the server enforces,
 * read from the Python source so the two cannot drift.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MAX_NAME_LEN, type TaskListItem } from '../../lib/api'
import { TaskListRow } from './ProjectsSection'

afterEach(() => cleanup())

const list = (over: Partial<TaskListItem> = {}): TaskListItem => ({
  id: 'tl-1', name: 'General', project_id: 'p-1', ...over,
})

describe('the task-list count badge', () => {
  it('renders the count when the endpoint supplied one', () => {
    render(<TaskListRow list={list({ task_count: 2 })} active={false} onOpen={() => {}} />)
    expect(screen.getByText('2')).toBeTruthy()
  })

  it('renders 0 — an empty list is a real reading', () => {
    render(<TaskListRow list={list({ task_count: 0 })} active={false} onOpen={() => {}} />)
    expect(screen.getByText('0')).toBeTruthy()
  })

  it('renders NO badge when the field is absent', () => {
    // The pre-fix state, and still the honest render when counting failed: the row shows the name
    // and claims nothing about how many tasks it holds.
    const { container } = render(<TaskListRow list={list()} active={false} onOpen={() => {}} />)
    expect(screen.getByText('General')).toBeTruthy()
    expect(container.querySelector('.tabular-nums'), 'no count element at all').toBeNull()
  })
})

describe('the project-name cap is one number, not two', () => {
  const source = (rel: string) => readFileSync(join(process.cwd(), rel), 'utf8')

  it('matches the server constant exactly', () => {
    // 🔑 The anti-drift rail. A cap enforced in the form but not the store (or vice versa) is how
    // 3000 characters persisted in the first place.
    const py = source('../src/personalclaw/tasks/hierarchy.py')
    const declared = /^MAX_NAME_LEN = (\d+)$/m.exec(py)
    expect(declared, 'hierarchy.py must declare MAX_NAME_LEN').not.toBeNull()
    expect(MAX_NAME_LEN).toBe(Number(declared![1]))
  })

  it('both project-name inputs carry the cap', () => {
    // Structural: these two inputs live inside internal components (a modal and an inline rename).
    const src = source('src/pages/projects/ProjectsSection.tsx')
    const capped = src.match(/maxLength=\{MAX_NAME_LEN\}/g) ?? []
    expect(capped.length, 'the New-project input AND the inline rename input').toBe(2)
  })

  it('the store refuses over-long rather than truncating', () => {
    // Pinned here too because it is the reason the form caps instead of trimming on submit.
    const py = source('../src/personalclaw/tasks/hierarchy.py')
    expect(py).toMatch(/must be at most \{MAX_NAME_LEN\} characters/)
    expect(py, 'a truncating slice would silently rename the user’s project')
      .not.toMatch(/name\s*=\s*name\[:\s*MAX_NAME_LEN\s*\]/)
  })
})
