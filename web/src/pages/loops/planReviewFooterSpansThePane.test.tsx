/**
 * Plan Review's step bar (Cancel / Back · the next step · Launch) is a shell bar at the foot of the
 * pane, and its hairline has to run the pane's full width, the way every create page's footer does.
 *
 * It used to carry the content-width cap itself, so at any width preset narrower than the pane the
 * line stopped short on both sides. Measured at 1920 wide with the Default preset: the line ran
 * 508–1608, 1100px of a 1724px pane, with bare canvas past each end. Only the ROW of buttons keeps
 * to the content width, so they still line up with the step above.
 *
 * jsdom lays nothing out, so this pins the cause: no width cap on the bar or on any box it sits in,
 * and the cap on the row inside it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { Loop } from '../../lib/api'
import type { LoopDraft } from './loopDraft'

const loop = {
  id: 'L1',
  name: 'Tidy the reading list',
  kind: 'goal',
  task: 'tidy the reading list and tag every saved article by topic',
  status: 'review',
  granularity: 'balanced',
  attended: true,
  kind_config: { goal_type: 'open_ended' },
} as unknown as Loop

const draft: LoopDraft = {
  loopId: 'L1',
  classification: { kind: 'goal', execution: 'solo', kind_config: {} },
  rigor: 'minimal',
  agent: 'a',
  model: 'm',
  granularity: 'balanced',
  attended: true,
}

const capOf = (el: HTMLElement) =>
  el.style.maxWidth || Array.from(el.classList).find((c) => /^max-w-/.test(c)) || ''

describe('Plan Review\'s step bar spans the pane', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => { cleanup(); vi.doUnmock('./useRunStream'); vi.doUnmock('../../lib/api') })

  it('🔴 runs its line edge to edge, and only its row of buttons keeps to the content width', async () => {
    vi.doMock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))
    vi.doMock('../../lib/api', async (orig) => {
      const real = await orig<Record<string, unknown>>()
      return {
        ...real,
        api: {
          uLoop: () => Promise.resolve(loop),
          savedAgents: () => Promise.resolve([]),
          skills: () => Promise.resolve([]),
          saveULoopSpec: () => Promise.resolve(loop),
          uLoopAction: () => Promise.resolve(undefined),
        },
      }
    })
    const { LoopPlanReview } = await import('./LoopPlanReview')
    const { container } = render(<LoopPlanReview draft={draft} onLaunched={() => {}} onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText('Step 1 / 3')).toBeTruthy())

    const cancel = screen.getByRole('button', { name: /Cancel/ })
    const forward = screen.getByRole('button', { name: /Capabilities/ })
    const bar = cancel.closest('.border-t') as HTMLElement | null
    expect(bar, 'the step bar draws its hairline').not.toBeNull()
    expect(bar!.contains(forward), 'and holds both of its buttons').toBe(true)

    const capped: string[] = []
    for (let el: HTMLElement | null = bar; el && el !== container; el = el.parentElement) {
      const cap = capOf(el)
      if (cap) capped.push(`<${el.tagName.toLowerCase()} class="${el.className}"> max-width ${cap}`)
    }
    expect(capped, 'a width cap on the bar or a box it sits in').toEqual([])

    // The row: one box inside the bar, centred at the content width, holding both buttons.
    const row = cancel.parentElement as HTMLElement
    expect(row.parentElement).toBe(bar)
    expect(row.style.maxWidth).toBe('var(--content-width)')
    expect(row.classList).toContain('mx-auto')
    expect(row.contains(forward)).toBe(true)
  })
})
