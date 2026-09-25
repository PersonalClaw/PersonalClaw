import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ActionConfig } from './ActionConfig'
import type { ActionProvider } from '../../lib/api'

// ── The action picker offers what a PERSON can configure (2026-09-25 validation run) ─────────────
//
// The picker listed the Self-QA loop's four steps — "Triage Commits for Self-QA", "File Self-QA
// Finding", "Seal Self-QA Evidence Bundle", "Self-QA commit watch" — beside Notify and Bash. Each
// takes config its reconciler writes (a commit sha, a run workspace, a scenario id), so picking one
// by hand builds a trigger that cannot do anything. The catalog marks them `internal`; the actions
// themselves stay registered, because the loop that owns them still dispatches them.

vi.mock('../prompts/promptWidgets', () => ({ usePromptWidgets: () => ({ prompts: [], widgets: {} }) }))

const P = (name: string, display_name: string, internal = false): ActionProvider => ({
  name, display_name, internal, supports_blocking: false, settingsSchema: { type: 'object', properties: {} },
})

const PROVIDERS = [
  P('notify', 'Dashboard Notification'),
  P('bash', 'Bash Command'),
  P('selfqa-triage', 'Triage Commits for Self-QA', true),
  P('selfqa-commit-watch', 'Self-QA commit watch', true),
]

function mount(provider = '') {
  render(<ActionConfig providers={PROVIDERS} provider={provider} config={{}} onProvider={vi.fn()} onConfig={vi.fn()} vars={[]} />)
}

const optionNames = () => screen.getAllByRole('option').map((o) => o.textContent ?? '')

describe('internal actions are left out of the picker', () => {
  it('offers the user-facing actions and none of the internal ones', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /pick an action/i }))
    const names = optionNames()
    expect(names.some((n) => n.includes('Dashboard Notification'))).toBe(true)
    expect(names.some((n) => n.includes('Bash Command'))).toBe(true)
    expect(names.some((n) => /self-qa/i.test(n))).toBe(false)
  })

  it('still shows an internal action a row ALREADY runs, so its picker never blanks out', () => {
    mount('selfqa-triage')
    fireEvent.click(screen.getByRole('button', { name: /triage commits for self-qa/i }))
    const names = optionNames()
    expect(names.some((n) => n.includes('Triage Commits for Self-QA'))).toBe(true)
    // …and only that one: the others stay hidden.
    expect(names.some((n) => n.includes('Self-QA commit watch'))).toBe(false)
  })
})
