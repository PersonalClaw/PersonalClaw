/**
 * #485 — the Tasks page's own loader, pinned by SOURCE, and labelled as such.
 *
 * Everything behavioural about this fix lives where it can be driven: `lib/taskCollection.test.ts`
 * proves the walk, `ui/PartialNotice.test.tsx` proves the disclosure, and
 * `prerequisitePickerIsComplete.test.tsx` drives a real call site. What none of them can reach is
 * WHICH READ THIS PAGE CHOSE — and that was the entire defect: a correct client, asked for one
 * window, feeding a dependency graph.
 *
 * Mounting `TasksListPage` is not a proportionate way to learn that. It takes twenty props and
 * fans out to projects, task lists, code loops, ready tasks and search on mount, so the fixture
 * would be an order of magnitude larger than the claim. This file's neighbours
 * (`boardDragAlternative.test.tsx`) already pin this page's wiring the same way. So: source
 * assertions, narrow, on the loader line and the disclosure — not dressed up as behaviour.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { depMap, prereqIds } from './dag'
import type { TaskItem } from '../../lib/api'

const src = (f: string) => readFileSync(join(process.cwd(), 'src/pages/tasks', f), 'utf8')

/** *f* with `//` line comments and `/* *\/` blocks removed, so a source rail asserts about code
 *  rather than about the prose explaining it. */
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')

describe('the Tasks page reads the whole set', () => {
  const page = src('TasksListPage.tsx')
  const code = stripComments(page)

  it('loads through the collection, not a single window', () => {
    expect(page).toMatch(/useQuery\('tasks', \(\) => api\.allTasks\(\)/)
  })

  it('makes no windowed /api/tasks call at all', () => {
    // The `limit: 1` owner probe was one too: it existed only because the list call threw away
    // everything but `tasks`, and `owner` rides on the same payload.
    //
    // Matched against the file with comments STRIPPED. The claim is about the code, and this page
    // carries long rationale blocks that legitimately *name* `api.tasks()` while explaining why it
    // is the wrong read — a raw text match calls that prose a call site, and the only way to keep
    // such a rail green is to make the comment vaguer.
    expect(code.match(/api\.tasks\(/g), 'every read here is the collection').toBeNull()
  })

  it('discloses an incomplete collection in every view', () => {
    // Two render branches — the board is a fixed-height shell, the other views share a scroll
    // column — so a notice in one is invisible in the other.
    expect(page.match(/<PartialNotice/g) ?? []).toHaveLength(2)
    expect(page).toMatch(/complete=\{collection\?\.complete \?\? true\}/)
  })
})

describe('why completeness is the fix, and not a wider edge filter', () => {
  const t = (id: string, deps: string[] = []): TaskItem =>
    ({ id, title: id, dependencies: deps.map((d) => ({ depends_on_task_id: d, dependency_type: 'BLOCKS' })) }) as unknown as TaskItem

  it('the graph drops an edge to a task it does not hold — correctly', () => {
    // 🔑 The anchor for the whole change. This filter is RIGHT: you cannot draw an edge to a node
    // you do not have. It is what makes an incomplete FETCH produce a confidently wrong picture
    // rather than an obviously broken one, which is why the fetch had to be fixed instead.
    const partial = [t('b', ['a'])] // 'a' fell outside the window
    expect(prereqIds(partial[0])).toEqual(['a'])
    expect(depMap(partial).get('b'), 'reads as a task with NO prerequisites').toEqual([])
  })

  it('and keeps the edge once the set is whole', () => {
    const whole = [t('b', ['a']), t('a')]
    expect(depMap(whole).get('b')).toEqual(['a'])
  })
})
