import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ProjectionRule } from '../../lib/api'
import type { Revisioned } from '../../lib/staleWrite'

// ── A save from a stale tab is refused, and the change is kept ───────────────────────────────
//
// The panel saves the WHOLE rules list, built from the copy it read. Tab A read `[a]`, another tab
// then stored `[a, b]`, and tab A added `c`: the old panel sent `[a, c]` and the gateway replaced
// `[a, b]` with it — rule b gone, and nothing on either screen said so. The gateway now refuses a
// stale copy (`409 stale_write`); this pins what the panel does with that refusal.

const A: ProjectionRule = { name: 'a', match_regex: '^\\[A\\]', strategy: 'log' }
const B: ProjectionRule = { name: 'b', match_regex: '^\\[B\\]', strategy: 'diff' }
const C = { name: 'c', match_regex: '^\\[C\\]', strategy: 'log' as const }

function staleWrite() {
  return Object.assign(new Error('This write replaces tools.projection_rules, which changed…'), { status: 409, code: 'stale_write' })
}

async function mount() {
  vi.resetModules()
  sessionStorage.clear()
  // What is stored: the first read is tab A's copy; every later read sees the other tab's save.
  let reads = 0
  const projectionRules = vi.fn((): Promise<Revisioned<ProjectionRule[]>> => {
    reads += 1
    return Promise.resolve(reads === 1 ? { value: [A], revision: 'r1' } : { value: [A, B], revision: 'r2' })
  })
  const setProjectionRules = vi.fn((_rules: ProjectionRule[], base: string) =>
    base === 'r2' ? Promise.resolve({}) : Promise.reject(staleWrite()))
  vi.doMock('../../lib/api', () => ({
    api: { projectionRules, setProjectionRules, toolsSavings: () => Promise.resolve(null) },
  }))
  const { ProjectionRulesPanel } = await import('./ProjectionRulesPanel')
  await act(async () => {
    render(<ProjectionRulesPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return { projectionRules, setProjectionRules }
}

async function addRuleC() {
  const field = await screen.findByLabelText('Match regex for the new rule')
  fireEvent.change(screen.getByLabelText('New rule name'), { target: { value: 'c' } })
  fireEvent.change(field, { target: { value: C.match_regex } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add rule' })) })
}

describe('a projection-rule save from a stale copy', () => {
  it('is refused with the reload offer, and the refused save named the old revision', async () => {
    const { setProjectionRules } = await mount()
    await addRuleC()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/Your projection rules changed elsewhere/)
    expect(alert.textContent).toMatch(/your change\s+wasn’t saved/)
    expect(within(alert).getByRole('button', { name: 'Reload and reapply' })).toBeTruthy()
    expect(within(alert).getByRole('button', { name: 'Review the difference' })).toBeTruthy()
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(setProjectionRules.mock.calls[0]).toEqual([[A, C], 'r1'])
    // What was typed is still in the form: the refusal kept the change, it did not eat it.
    expect((screen.getByLabelText('Match regex for the new rule') as HTMLInputElement).value).toBe(C.match_regex)
    expect((screen.getByLabelText('New rule name') as HTMLInputElement).value).toBe('c')
  })

  it('Reload and reapply puts the change on top of what the other tab stored', async () => {
    const { setProjectionRules } = await mount()
    await addRuleC()
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(setProjectionRules).toHaveBeenCalledTimes(2))
    // B — the other tab's rule — survives, C is added after it, over the NEW revision.
    expect(setProjectionRules.mock.calls[1]).toEqual([[A, B, C], 'r2'])
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    // Stored now, so the add form starts empty again.
    await waitFor(() => expect((screen.getByLabelText('Match regex for the new rule') as HTMLInputElement).value).toBe(''))
  })

  it('Review the difference shows the other change and the kept one', async () => {
    await mount()
    await addRuleC()
    const alert = await screen.findByRole('alert')
    const review = within(alert).getByRole('button', { name: 'Review the difference' })
    await waitFor(() => expect(review.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(review) })
    const dialog = await screen.findByRole('dialog', { name: 'Review the difference' })
    const elsewhere = within(dialog).getByLabelText('What changed elsewhere').textContent ?? ''
    expect(elsewhere).toMatch(/\+\s+"name": "b"/)
    const mine = within(dialog).getByLabelText('Your change, re-applied').textContent ?? ''
    expect(mine).toMatch(/\+\s+"name": "c"/)
    expect(mine, 'the re-applied save removes nothing the other tab stored').not.toMatch(/-\s+"name": "b"/)
  })

  it('Discard my change writes nothing more and re-reads what is stored', async () => {
    const { projectionRules, setProjectionRules } = await mount()
    await addRuleC()
    const alert = await screen.findByRole('alert')
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Discard my change' })) })
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(projectionRules.mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})
