import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { TaskBoard } from './TaskBoard'
import type { TaskItem } from '../../lib/api'

// ── A scroll container with nothing focusable needs its own tab stop ─────────
//
// The kanban columns scroll, and their cards are `div`s (drag-and-drop, not buttons). So a
// column had ZERO focusable descendants: a keyboard user could neither reach it nor scroll it.
// Measured on a real board at 390px — **729px of cards unreachable**, and axe agreed
// (scrollable-region-focusable, serious).
//
// WCAG 2.1.1: if content scrolls, the scrolling must be operable by keyboard. `tabIndex={0}`
// makes the region focusable, at which point the browser's own arrow/PageUp/PageDown scrolling
// works for free. `role="group"` + `aria-label` keep it announced as a labelled container
// rather than an unnamed widget.
//
// The COUNTERPART case is asserted in the sibling assertion below: List and Cards views each
// expose 38 focusable rows, so their shared container must NOT get a redundant tab stop — an
// extra stop before every list is its own annoyance. That is why the page scopes the attribute
// to the DAG view instead of putting it on the shared wrapper.

const task = (id: string, status: string): TaskItem => ({
  id, title: `Task ${id}`, status, priority: 'medium',
} as unknown as TaskItem)

describe('kanban column scroll regions', () => {
  it('every column is a keyboard-reachable, named region', () => {
    const { container } = render(
      <TaskBoard tasks={[task('a', 'open'), task('b', 'in_progress')]} onOpen={() => {}} onMove={vi.fn()} />,
    )
    const scrollers = [...container.querySelectorAll('.overflow-y-auto')]
    expect(scrollers.length).toBeGreaterThan(0)
    for (const el of scrollers) {
      // Without this a keyboard user cannot scroll the column at all.
      expect(el.getAttribute('tabindex'), 'column scroll region must own a tab stop').toBe('0')
      expect(el.getAttribute('role')).toBe('group')
      // Named for the column it holds, with its count — an unnamed region is announced as
      // nothing useful.
      expect(el.getAttribute('aria-label')).toBeTruthy()
    }
  })

  it('the region name carries the column label and a correctly pluralised count', () => {
    const { container } = render(
      <TaskBoard tasks={[task('a', 'open')]} onOpen={() => {}} onMove={vi.fn()} />,
    )
    const labels = [...container.querySelectorAll('.overflow-y-auto')]
      .map((el) => el.getAttribute('aria-label') ?? '')
    // The one `open` task gives the singular form — "Not started — 1 task", never "1 tasks".
    expect(labels.some((l) => /— 1 task$/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
    expect(labels.every((l) => !/— 1 tasks$/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
    // Every rendered region names its column, so none is an anonymous scroll box.
    expect(labels.every((l) => /^\S/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
  })
})
