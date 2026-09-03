import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProjectionRule } from '../../lib/api'

// ── An existing rule's field must survive being typed into (#674) ────────────────────────────────
//
// RuleRow wrote through on EVERY keystroke: each change event fired save() (a whole-list config
// PATCH) and the follow-up refresh() re-seeded the controlled input from server state — so every
// keystroke typed during the round-trip was discarded. Measured: five slow keystrokes, one
// survived. A regex is exactly the value that needs more than one character, so an existing rule's
// pattern could not be corrected at all (delete + re-add was the only path).
//
// The fix mirrors AddRule in the same file: edits land in a LOCAL DRAFT and commit ONCE when focus
// leaves the row (or on Enter). These rails pin the three behaviors that define the shape:
//   1. typing does not save — zero PATCHes while the field has focus, all characters retained;
//   2. leaving the row commits exactly once, with the final value;
//   3. Enter commits without leaving the field (the AddRule affordance, kept for parity).

const RULE: ProjectionRule = { name: 'myapp', match_regex: '^\\[MYAPP\\]', strategy: 'log' }

const setProjectionRules = vi.fn((_rules: ProjectionRule[]) => Promise.resolve({}))

async function mount() {
  vi.resetModules()
  setProjectionRules.mockClear()
  vi.doMock('../../lib/api', () => ({
    api: {
      projectionRules: () => Promise.resolve([{ ...RULE }]),
      setProjectionRules: (rules: ProjectionRule[]) => setProjectionRules(rules),
      toolsSavings: () => Promise.resolve(null),
    },
  }))
  const { ProjectionRulesPanel } = await import('./ProjectionRulesPanel')
  await act(async () => {
    render(<ProjectionRulesPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return screen.findByLabelText('Match regex for myapp')
}

describe('editing an existing projection rule (draft-and-commit, #674)', () => {
  it('typing five characters saves nothing and loses nothing', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('QWERT')
    expect(setProjectionRules).not.toHaveBeenCalled() // no per-keystroke PATCH
    // Every character survived — the write-through+refresh clobber kept exactly one.
    expect((field as HTMLInputElement).value).toBe('^\\[MYAPP\\]QWERT')
  })

  it('leaving the row commits exactly one save carrying the full value', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('QWERT')
    await userEvent.tab() // move focus out of the row → blur commit
    // Focus moves within the row first (regex → ops button); tab to truly leave.
    await userEvent.tab()
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    const sent = setProjectionRules.mock.calls[0][0]
    expect(sent[0].match_regex).toBe('^\\[MYAPP\\]QWERT')
  })

  it('Enter commits from within the field', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('X{Enter}')
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(setProjectionRules.mock.calls[0][0][0].match_regex).toBe('^\\[MYAPP\\]X')
  })
})
