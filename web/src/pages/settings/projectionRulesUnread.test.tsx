import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ProjectionRule } from '../../lib/api'

// ── Adding a projection rule needs the stored rules first ──────────────────────────────────────
//
// `tools.projection_rules` is written as ONE list, and the server replaces it: Add sends
// `[...list, newRule]`. The panel reported a failed read correctly (an InlineError with Retry) but
// kept the add form live beside it, where `list` was `rules ?? []` — so typing one rule and pressing
// Add PATCHed `[newRule]` and deleted every stored rule. The same held while the read was still out.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): an unread state stood
// in for an empty one, and licensed a write.

const STORED: ProjectionRule = { name: 'myapp', match_regex: '^\\[MYAPP\\]', strategy: 'log' }
const setProjectionRules = vi.fn((_rules: ProjectionRule[]) => Promise.resolve({}))

async function mount(read: () => Promise<ProjectionRule[]>) {
  vi.resetModules()
  sessionStorage.clear()
  setProjectionRules.mockClear()
  vi.doMock('../../lib/api', () => ({
    api: {
      projectionRules: read,
      setProjectionRules: (rules: ProjectionRule[]) => setProjectionRules(rules),
      toolsSavings: () => Promise.resolve(null),
    },
  }))
  const { ProjectionRulesPanel } = await import('./ProjectionRulesPanel')
  await act(async () => {
    render(<ProjectionRulesPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

const addField = () => screen.queryByLabelText('Match regex for the new rule')

describe('the add-rule form', () => {
  it('is not offered while the stored rules could not be read', async () => {
    await mount(() => Promise.reject(new Error('config unreadable')))
    expect(await screen.findByText(/Couldn't load your projection rules/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(addField(), 'an add here writes [newRule] over every stored rule').toBeNull()
    expect(setProjectionRules).not.toHaveBeenCalled()
  })

  it('is not offered while the read is still out', async () => {
    await mount(() => new Promise(() => {}))
    expect(addField()).toBeNull()
  })

  it('once the rules are read, adds to them rather than replacing them', async () => {
    // The control: the whole-list write is correct when the list it extends was read.
    await mount(() => Promise.resolve([{ ...STORED }]))
    const field = await waitFor(() => { const f = addField(); expect(f).not.toBeNull(); return f as HTMLInputElement })
    fireEvent.change(field, { target: { value: '^\\[OTHER\\]' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(setProjectionRules).toHaveBeenCalledTimes(1))
    expect(setProjectionRules.mock.calls[0][0]).toEqual([STORED, { name: '', match_regex: '^\\[OTHER\\]', strategy: 'log' }])
  })
})
