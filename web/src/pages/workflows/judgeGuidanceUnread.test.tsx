import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

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
const setProjectInstructions = vi.fn()
const notify = vi.fn()
/** The dialog, answered the way a user who clicks Save without typing answers it: with whatever
 *  the field was seeded with. */
const promptForm = vi.fn(async (opts: { fields: Array<{ name: string; initial?: string }> }) =>
  Object.fromEntries(opts.fields.map((f) => [f.name, f.initial ?? ''])))

vi.mock('../../lib/api', () => ({
  api: {
    workflowSteering: () => Promise.resolve({ pending: [] }),
    project: (...a: unknown[]) => project(...a),
    setProjectInstructions: (...a: unknown[]) => setProjectInstructions(...a),
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
  setProjectInstructions.mockResolvedValue({ id: 'proj-1', name: 'P' })
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
    expect(setProjectInstructions, 'Save wrote blank guidance over the stored one').not.toHaveBeenCalled()
  })

  it('a read guidance is offered as stored, and what the user saves is written', async () => {
    // The control: seeded from a real read, the write carries the user's answer.
    project.mockResolvedValue({ agent_instructions_template: 'Prefer primary sources.', revisions: { agent_instructions_template: 'g1' } })
    promptForm.mockImplementationOnce(async () => ({ guidance: 'Prefer primary sources. Cite them.' }))
    openGuidance()
    // Over the revision of the text the dialog was seeded with (the stale-write contract).
    await waitFor(() => expect(setProjectInstructions).toHaveBeenCalledWith('proj-1', 'Prefer primary sources. Cite them.', 'g1'))
    expect(promptForm.mock.calls[0][0].fields[0].initial).toBe('Prefer primary sources.')
  })
})

describe('Judge guidance saved from a stale copy', () => {
  // Accepting a learning proposal appends an instruction to this text. An accept that landed while
  // the dialog was open was erased by its Save; the gateway now refuses the stale copy
  // (`409 stale_write`), and the answer the user gave waits in the notice instead of vanishing.
  const stale = () => Object.assign(new Error('This write replaces the agent instructions…'), { status: 409, code: 'stale_write' })
  const opened = { agent_instructions_template: 'Prefer primary sources.\nCite them inline.', revisions: { agent_instructions_template: 'g1' } }
  const accepted = {
    agent_instructions_template: 'Prefer primary sources.\nCite them inline.\n\nRun make lint before pushing.',
    revisions: { agent_instructions_template: 'g2' },
  }

  it('is refused with the notice, which keeps the answer and re-applies it over the new revision', async () => {
    project.mockResolvedValueOnce(opened).mockResolvedValue(accepted)
    setProjectInstructions.mockImplementation((_id: string, _text: string, base: string) =>
      base === 'g2' ? Promise.resolve({ id: 'proj-1', name: 'P' }) : Promise.reject(stale()))
    promptForm.mockImplementationOnce(async () => ({ guidance: 'Prefer PRIMARY sources.\nCite them inline.' }))
    openGuidance()
    const notice = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('[data-stale-write="true"]')
      expect(el).not.toBeNull()
      return el!
    })
    expect(notice.getAttribute('role')).toBe('alert')
    expect(notice.textContent).toMatch(/This project's judge guidance changed elsewhere/)
    expect(setProjectInstructions).toHaveBeenCalledWith('proj-1', 'Prefer PRIMARY sources.\nCite them inline.', 'g1')
    expect(notify, 'a refused save is not reported as saved').not.toHaveBeenCalledWith('Judge guidance saved for this project.')

    const reapply = within(notice).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(setProjectInstructions).toHaveBeenCalledTimes(2))
    // The accepted instruction survives; the user's edit lands on top of it.
    expect(setProjectInstructions.mock.calls[1]).toEqual(['proj-1', 'Prefer PRIMARY sources.\nCite them inline.\n\nRun make lint before pushing.', 'g2'])
    expect(notify).toHaveBeenCalledWith('Judge guidance saved for this project.')
  })
})
