import { describe, expect, it, vi, type Mock } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProjectionRule } from '../../lib/api'

// ── A refused new rule keeps what you typed (Settings → Tool-output projection) ───────────────────
//
// The add form emptied itself as soon as Add was pressed, before the save answered. A rule the
// server refuses — here a regex that does not compile, which `config/edit_spec.py` rejects with the
// pattern named — took the typed name, pattern and strategy with it, and the reason appeared under
// an empty form. The existing-rule rows already keep their draft through a failed save
// (`projectionRuleEditDraft.test.tsx`); this pins the same for a new rule, and that a rule the
// server TAKES still clears the form for the next one.

const REFUSAL = "invalid regex '^[MYAPP': unterminated character set at position 1"

let setProjectionRules: Mock<(rules: ProjectionRule[]) => Promise<unknown>>

async function mount(outcome: 'refuse' | 'accept') {
  vi.resetModules()
  setProjectionRules = vi.fn((_rules: ProjectionRule[]) =>
    outcome === 'refuse' ? Promise.reject(new Error(REFUSAL)) : Promise.resolve({}))
  vi.doMock('../../lib/api', () => ({
    api: {
      projectionRules: () => Promise.resolve([]),
      setProjectionRules: (rules: ProjectionRule[]) => setProjectionRules(rules),
      toolsSavings: () => Promise.resolve(null),
    },
  }))
  const { ProjectionRulesPanel } = await import('./ProjectionRulesPanel')
  await act(async () => {
    render(<ProjectionRulesPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  const name = (await screen.findByLabelText('New rule name')) as HTMLInputElement
  const regex = screen.getByLabelText('Match regex for the new rule') as HTMLInputElement
  const strategy = screen.getByLabelText('Strategy for the new rule') as HTMLSelectElement
  await userEvent.type(name, 'myapp')
  await userEvent.type(regex, '^[[MYAPP')
  await userEvent.selectOptions(strategy, 'diff')
  await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
  return { name, regex, strategy }
}

describe('adding a projection rule', () => {
  it('🔴 a refused rule keeps the name, pattern and strategy, and says why', async () => {
    const { name, regex, strategy } = await mount('refuse')
    await waitFor(() => expect(screen.getByText(REFUSAL, { exact: false })).toBeTruthy())
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(name.value).toBe('myapp')
    expect(regex.value, 'the pattern to fix is still there').toBe('^[MYAPP')
    expect(strategy.value).toBe('diff')
    // And the reason is announced: it arrives after the click, beside a form it does not replace.
    expect(screen.getByRole('alert').textContent).toContain(REFUSAL)
  })

  it('a rule the server takes clears the form for the next one', async () => {
    const { name, regex, strategy } = await mount('accept')
    await waitFor(() => expect(regex.value).toBe(''))
    expect(setProjectionRules.mock.calls[0][0]).toEqual([
      { name: 'myapp', match_regex: '^[MYAPP', strategy: 'diff' },
    ])
    expect(name.value).toBe('')
    expect(strategy.value).toBe('log')
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
