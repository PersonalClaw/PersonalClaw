import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── "Judge guidance…" edits only guidance it has read ──────────────────────────────────────────
//
// The dialog is seeded with the project's `agent_instructions_template` — the standing guidance
// every run under the project carries — and Save PUTs the field back. A failed read of the project
// used to "fall through with an empty field": the dialog offered the guidance as BLANK, and Save,
// with no typing at all, PUT `{"agent_instructions_template": ""}` over it.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// an empty state, and the empty state licensed a write.

const project = vi.fn()
const updateProject = vi.fn()
const notify = vi.fn()
/** The dialog, answered the way a user who clicks Save without typing answers it: with whatever
 *  the field was seeded with. */
const promptForm = vi.fn(async (opts: { fields: Array<{ name: string; initial?: string }> }) =>
  Object.fromEntries(opts.fields.map((f) => [f.name, f.initial ?? ''])))

vi.mock('../../lib/api', () => ({
  api: {
    workflowSteering: () => Promise.resolve({ pending: [] }),
    project: (...a: unknown[]) => project(...a),
    updateProject: (...a: unknown[]) => updateProject(...a),
  },
}))
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  promptForm: (opts: Parameters<typeof promptForm>[0]) => promptForm(opts),
}))
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (...a: unknown[]) => notify(...a),
}))

import { SteeringPanel } from './SteeringPanel'

beforeEach(() => {
  vi.clearAllMocks()
  updateProject.mockResolvedValue({ ok: true })
})
afterEach(() => vi.restoreAllMocks())

const openGuidance = () => {
  render(<SteeringPanel runId="run-1" projectId="proj-1" nodes={[]} />)
  fireEvent.click(screen.getByRole('button', { name: /Judge guidance/ }))
}

describe('Judge guidance…', () => {
  it('a failed read of the project opens nothing, writes nothing, and says why', async () => {
    project.mockRejectedValue(new Error('project store unreadable'))
    openGuidance()
    await waitFor(() => expect(notify).toHaveBeenCalledWith(
      "Couldn't read this project's guidance, so nothing was opened or changed: project store unreadable", 'error'))
    await act(() => new Promise((r) => setTimeout(r, 30)))
    expect(promptForm, 'the guidance was offered for editing as blank').not.toHaveBeenCalled()
    expect(updateProject, 'Save wrote blank guidance over the stored one').not.toHaveBeenCalled()
  })

  it('a read guidance is offered as stored, and what the user saves is written', async () => {
    // The control: seeded from a real read, the write carries the user's answer.
    project.mockResolvedValue({ agent_instructions_template: 'Prefer primary sources.' })
    promptForm.mockImplementationOnce(async () => ({ guidance: 'Prefer primary sources. Cite them.' }))
    openGuidance()
    await waitFor(() => expect(updateProject).toHaveBeenCalledWith('proj-1', { agent_instructions_template: 'Prefer primary sources. Cite them.' }))
    expect(promptForm.mock.calls[0][0].fields[0].initial).toBe('Prefer primary sources.')
  })
})
