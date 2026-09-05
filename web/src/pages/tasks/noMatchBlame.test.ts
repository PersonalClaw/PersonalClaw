import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The no-match state blames the control that actually narrowed (NZ3 residual) ────────────────
//
// The list/cards no-match branch is reachable through FOUR narrowing controls — search, the
// status filter, the project list bar, and Assigned › Mine — and it rendered ONE hint for all of
// them: "No tasks match this filter." A user who typed a search was told to check a dropdown they
// never touched. Same defect class the code list fixed (see emptyStateNoMatch.test.tsx); this file
// pins the tasks copy of the split so a future edit cannot flatten it back to one blame-everything
// sentence.
//
// Source-scan on purpose: TasksListPage is the page shell (query params, five views, three data
// slices), and this page's pins are all source pins for that reason (taskStatusAnnounced,
// createErrorVisible). The rendered canonical shape is already pinned by emptyStateNoMatch.

const src = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')

describe('the tasks no-match state names its narrower', () => {
  it('the blame-everything sentence is gone from the filtered branch', () => {
    // "No tasks match this scope." legitimately remains — scope is chosen by navigation and has
    // no in-page escape. The FILTER wording is what lied, because it also fired for search.
    expect(src).not.toMatch(/No tasks match this filter\./)
  })

  it('a search that narrows to nothing names the query and offers Clear search', () => {
    expect(src).toMatch(/No tasks match “\$\{q\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => setQ\(''\)/)
  })

  it("the view escape resets EVERY in-page narrower, not just the status filter", () => {
    // A "View all tasks" that reset only `filter` would be a lying affordance whenever the list
    // bar or Assigned › Mine did the narrowing — clicking it would change nothing.
    expect(src).toMatch(/setFilter\('all'\); setListFilter\(null\); setAssigned\(ASSIGNED_EVERYONE\)/)
  })

  it('both no-match variants count what is really there', () => {
    // The count hint is what keeps a narrowed-to-nothing list from reading as "you have no
    // tasks" — the conflation the genuinely-empty branch's create CTA exists for.
    const counts = src.match(/You have \$\{tasks\?\.length \?\? 0\} task/g) ?? []
    expect(counts.length).toBeGreaterThanOrEqual(2)
  })
})
