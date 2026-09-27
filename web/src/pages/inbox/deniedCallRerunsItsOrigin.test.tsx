/** A call denied without an answer during a trigger's or a workflow's run can be run again from its
 *  Inbox note, the way a chat's can be asked again.
 *
 *  #3698 gave only a chat's note a next step ("Ask it to try again"). A trigger's note said which
 *  tool was denied and offered nothing, and a workflow step's only opened the run. The engine does
 *  support both re-runs: a trigger's Run now, and a live run's rewind of one step. So the note
 *  offers the one that exists, where it exists:
 *
 *  - a trigger's run — Run now. When the trigger is not allowed to run its action (#3702), the
 *    re-run goes through the same Allow its page asks (the switch sent on again, with consent);
 *    a trigger that is off is not switched on from here, because that also makes it fire on its own;
 *  - a workflow step — re-run that step, while the run is live; an ended run cannot run again;
 *  - a call an unattended run declined without asking would be declined the same way, so it offers
 *    no re-run.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem, Trigger } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { ApiError } from '../../lib/api'
import { ConsentDeclined } from '../../lib/securityConsent'
import { InboxDetail } from './InboxDetail'

const triggers = vi.fn()
const runSchedule = vi.fn()
const runStoreTrigger = vi.fn()
const enableSchedule = vi.fn()
const toggleStoreTrigger = vi.fn()
const workflowRun = vi.fn()
const rewindWorkflowRun = vi.fn()
const confirmDialog = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    triggers: (...a: unknown[]) => triggers(...a),
    runSchedule: (...a: unknown[]) => runSchedule(...a),
    runStoreTrigger: (...a: unknown[]) => runStoreTrigger(...a),
    enableSchedule: (...a: unknown[]) => enableSchedule(...a),
    toggleStoreTrigger: (...a: unknown[]) => toggleStoreTrigger(...a),
    workflowRun: (...a: unknown[]) => workflowRun(...a),
    rewindWorkflowRun: (...a: unknown[]) => rewindWorkflowRun(...a),
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
  },
}))
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirm: (...a: unknown[]) => confirmDialog(...a),
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function note(refs: Record<string, unknown>, message = 'Denied, no answer: write_file'): InboxItem {
  return {
    id: 'system_1', channel: 'system', channel_name: 'system', message, sender_id: 'system', sender_name: 'system',
    item_kind: 'system', classification: 'needs_reply', confidence: 'high', status: 'pending', refs,
  } as InboxItem
}

function trigger(over: Partial<Trigger> = {}): Trigger {
  return {
    kind: 'schedule', id: 'schedule:nightly', raw_id: 'nightly', name: 'Nightly digest', enabled: true,
    needs_grant: [], ...over,
  } as Trigger
}

const fromTrigger = note({ auto_denied: 'expired', tool: 'write_file', trigger: 'nightly', call: 'k1' })

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  triggers.mockResolvedValue({ triggers: [trigger()], server_tz: 'UTC' })
  runSchedule.mockResolvedValue({ ok: true, name: 'Nightly digest', result: 'ran' })
  runStoreTrigger.mockResolvedValue({ ok: true, name: 'deploy', result: 'ran' })
  enableSchedule.mockResolvedValue({ ok: true })
  toggleStoreTrigger.mockResolvedValue({ ok: true })
  workflowRun.mockResolvedValue({ run_id: 'run-7', status: 'running' })
  rewindWorkflowRun.mockResolvedValue({ ok: true })
})

describe('a trigger’s denied call', () => {
  it('🔑 runs that trigger again, and says it will ask while you are here', async () => {
    const onChanged = vi.fn()
    render(<InboxDetail item={fromTrigger} onChanged={onChanged} navigate={() => {}} />)

    expect(await screen.findByText(/Runs “Nightly digest” again now, so it asks you for write_file/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Run it again' }))

    await waitFor(() => expect(runSchedule).toHaveBeenCalledWith('nightly'))
    expect(await screen.findByText(/Started “Nightly digest” again/)).toBeTruthy()
    expect(onChanged).toHaveBeenCalled()
    expect(enableSchedule).not.toHaveBeenCalled()
  })

  it('a store trigger runs through its own Run now', async () => {
    triggers.mockResolvedValue({ triggers: [trigger({ kind: 'store', id: 'store:event:deploy', raw_id: 'event:deploy', name: 'deploy' })], server_tz: 'UTC' })
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', trigger: 'event:deploy' })} onChanged={() => {}} navigate={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run it again' }))
    await waitFor(() => expect(runStoreTrigger).toHaveBeenCalledWith('event:deploy'))
    expect(runSchedule).not.toHaveBeenCalled()
  })

  it('🔑 a trigger not allowed to run its action is allowed first, through the page’s own Allow', async () => {
    triggers.mockResolvedValue({ triggers: [trigger({ needs_grant: ['Bash Command'] })], server_tz: 'UTC' })
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={() => {}} />)

    expect(await screen.findByText(/is not allowed to use “Bash Command” now\. Allowing it asks you first/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Allow and run it again' }))

    await waitFor(() => expect(runSchedule).toHaveBeenCalledWith('nightly'))
    // The switch sent on again is where the gateway asks for the grant (`withSecurityConsent`).
    expect(enableSchedule).toHaveBeenCalledWith('nightly', true)
    expect(enableSchedule.mock.invocationCallOrder[0]).toBeLessThan(runSchedule.mock.invocationCallOrder[0])
  })

  it('a declined Allow runs nothing and says so', async () => {
    triggers.mockResolvedValue({ triggers: [trigger({ needs_grant: ['Bash Command'] })], server_tz: 'UTC' })
    enableSchedule.mockRejectedValue(new ConsentDeclined('trigger.grant'))
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={() => {}} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Allow and run it again' }))

    expect(await screen.findByText('Not run: you did not allow it.')).toBeTruthy()
    expect(runSchedule).not.toHaveBeenCalled()
  })

  it('🔑 a trigger that is off is not switched on from here — its page decides', async () => {
    triggers.mockResolvedValue({ triggers: [trigger({ enabled: false, needs_grant: ['Bash Command'] })], server_tz: 'UTC' })
    const navigate = vi.fn()
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={navigate} />)

    expect(await screen.findByText(/“Nightly digest” is switched off and is not allowed to use “Bash Command”/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /run it again/i })).toBeNull()
    await userEvent.click(screen.getAllByRole('button', { name: 'Open the trigger' })[0])
    expect(navigate).toHaveBeenCalledWith('triggers?open=nightly')
    expect(enableSchedule).not.toHaveBeenCalled()
  })

  it('a run the gateway refuses says why, in its words', async () => {
    runSchedule.mockResolvedValue({ ok: false, name: 'Nightly digest', refused: 'Automations are paused (incident mode).' })
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run it again' }))
    expect(await screen.findByText("Couldn't run it again: Automations are paused (incident mode).")).toBeTruthy()
  })

  it('a trigger that is gone cannot run again, and says so', async () => {
    triggers.mockResolvedValue({ triggers: [], server_tz: 'UTC' })
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={() => {}} />)
    expect(await screen.findByText(/no longer exists, so it cannot run again/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Run it again' })).toBeNull()
  })

  it('opens the trigger from the note', async () => {
    const navigate = vi.fn()
    render(<InboxDetail item={fromTrigger} onChanged={() => {}} navigate={navigate} />)
    await screen.findByRole('button', { name: 'Run it again' })
    await userEvent.click(screen.getByRole('button', { name: 'Open the trigger' }))
    expect(navigate).toHaveBeenCalledWith('triggers?open=nightly')
  })

  it('a call its unattended run declined without asking offers no re-run: it would be declined again', () => {
    render(<InboxDetail item={note({ auto_denied: 'unattended', tool: 'write_file', trigger: 'nightly', session: 'subagent:ab12' }, 'Denied, no one to ask: write_file')} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(/ran with nobody there to ask, so running it again would be declined the same way/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /run it again/i })).toBeNull()
    expect(triggers).not.toHaveBeenCalled()
  })
})

describe('a workflow step’s denied call', () => {
  const fromStep = note({ auto_denied: 'expired', tool: 'write_file', session: 'workflow:run-7:write' })

  it('🔑 re-runs that step while the run is live', async () => {
    const onChanged = vi.fn()
    render(<InboxDetail item={fromStep} onChanged={onChanged} navigate={() => {}} />)

    expect(await screen.findByText(/Runs the write step of this run again, so it asks you for write_file/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Run this step again' }))

    await waitFor(() => expect(rewindWorkflowRun).toHaveBeenCalledWith('run-7', { node_id: 'write' }))
    expect(await screen.findByText(/The write step is running again/)).toBeTruthy()
    expect(onChanged).toHaveBeenCalled()
  })

  it('asks first when re-running it resets other steps, in the engine’s words — the run page’s flow', async () => {
    rewindWorkflowRun
      .mockRejectedValueOnce(new ApiError('confirm', 409, 'confirmation_required', { preview: { rerun: ['write', 'publish'], committed_effects: [] } }))
      .mockResolvedValueOnce({ ok: true })
    confirmDialog.mockResolvedValue(true)
    render(<InboxDetail item={fromStep} onChanged={() => {}} navigate={() => {}} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Run this step again' }))

    await waitFor(() => expect(rewindWorkflowRun).toHaveBeenLastCalledWith('run-7', { node_id: 'write', confirm_cascade: true }))
    expect(String(confirmDialog.mock.calls[0][0].body)).toContain('will reset 2 steps')
  })

  it('🔑 an ended run cannot run a step again, so it offers no button', async () => {
    workflowRun.mockResolvedValue({ run_id: 'run-7', status: 'complete' })
    render(<InboxDetail item={fromStep} onChanged={() => {}} navigate={() => {}} />)
    expect(await screen.findByText('The run has ended, so this step cannot run again in it.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Run this step again' })).toBeNull()
  })
})
