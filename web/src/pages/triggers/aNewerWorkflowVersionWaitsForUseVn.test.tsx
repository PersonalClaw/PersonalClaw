/**
 * An automation that runs a workflow runs the version its owner allowed, and a newer version
 * anything but her own editor save made waits for her: its panel says so, names who saved each
 * version since, and offers Use vN — which asks first, in the gateway's words, and changes nothing
 * until she allows it. Driven through a stubbed `fetch`, so what is asserted is the wire: the first
 * request carries no consent, and only an accepted dialog sends a second one that does.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import type { AutomationWorkflowVersion } from '../../lib/api'
import { WorkflowVersionNote } from './WorkflowVersionNote'

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => true))
vi.mock('../../ui/dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const CONSENT =
  '“Weekly report” runs version 1 of “weekly-report”. Allowing this lets it run version 2 ' +
  'instead, when it runs, unattended included. Saved since version 1: v2 by an agent.'

const NEWER: AutomationWorkflowVersion = {
  workflow: 'weekly-report', runs: 1, saved_by: 'owner', allowed: 1, follows: false,
  newer: 2, since: [{ version: 2, saved_by: 'agent' }], problem: '',
  steps: [], use: { version: 2, steps: {} },
}

/** It runs its workflow's newest version, and a workflow one of its steps starts has a newer one. */
const STEP_NEWER: AutomationWorkflowVersion = {
  ...NEWER, newer: 0, since: [],
  steps: [{
    workflow: 'report-part', via: 'weekly-report', runs: 1, saved_by: 'owner',
    newer: 2, since: [{ version: 2, saved_by: 'agent' }], problem: '',
  }],
  use: { version: 1, steps: { 'report-part': 2 } },
}

const reply = (status: number, o: unknown) =>
  new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

/** A gateway that asks before moving an automation to another version, as the route does. */
function gateway() {
  const sent: Array<{ url: string; body: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    sent.push({ url, body })
    if (body.confirm !== true) {
      return reply(400, {
        error: {
          code: 'confirmation_required',
          message: 'send {"confirm": true} to confirm',
          detail: {
            field: 'triggers.store:manual:weekly-report.workflow_version',
            consent: CONSENT,
            title: 'Run version 2 of “weekly-report”?',
            change: 'v1 → v2',
          },
        },
      })
    }
    return reply(200, { ok: true, trigger: {} })
  }))
  return sent
}

afterEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
  vi.unstubAllGlobals()
})

async function press(name: RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }))
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('a newer version of the workflow an automation runs', () => {
  it('is said on its panel, with the version it runs and who saved each version since', () => {
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={NEWER} onChanged={() => {}} />)
    const note = screen.getByRole('note')
    expect(note.textContent).toContain('“weekly-report” has a newer version, v2')
    expect(note.textContent).toContain('This automation runs v1, the version you allowed.')
    expect(note.textContent).toContain('Saved since: v2 by an agent.')
    expect(screen.getByRole('button', { name: /Use v2/ })).toBeTruthy()
  })

  it('says when what it runs is a version she saved in the editor after her Allow', () => {
    const followed = {
      ...NEWER, runs: 3, saved_by: 'owner', allowed: 2, newer: 4,
      since: [{ version: 4, saved_by: 'agent' }], use: { version: 4, steps: {} },
    }
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={followed} onChanged={() => {}} />)
    const note = screen.getByRole('note')
    expect(note.textContent).toContain('This automation runs v3, the newest version you saved in the workflow’s editor.')
    expect(note.textContent).toContain('Saved since: v4 by an agent.')
    expect(screen.getByRole('button', { name: /Use v4/ })).toBeTruthy()
  })

  it('Use vN asks in the gateway’s words first, and only the accepted resend carries confirm', async () => {
    const sent = gateway()
    const onChanged = vi.fn()
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={NEWER} onChanged={onChanged} />)

    await press(/Use v2/)

    expect(sent.map((s) => s.body)).toEqual([
      { version: 2, steps: {} },
      { version: 2, steps: {}, confirm: true },
    ])
    expect(sent[0].url).toContain('/api/triggers/store%3Amanual%3Aweekly-report/workflow-version')
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const asked = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(asked.title).toBe('Run version 2 of “weekly-report”?')
    expect(asked.body).toBe(`${CONSENT}\n\nv1 → v2`)
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('a declined question sends nothing more, changes nothing and says nothing went wrong', async () => {
    confirmSpy.mockImplementation(async () => false)
    const sent = gateway()
    const onChanged = vi.fn()
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={NEWER} onChanged={onChanged} />)

    await press(/Use v2/)

    expect(sent.map((s) => s.body)).toEqual([{ version: 2, steps: {} }])
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a Use that came after the row changed says so, changes nothing, and reads the row again', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      if (body.confirm !== true) {
        return reply(400, {
          error: {
            code: 'confirmation_required', message: 'send {"confirm": true} to confirm',
            detail: { field: 'f', consent: CONSENT, title: 'Run version 2 of “weekly-report”?', change: 'v1 → v2' },
          },
        })
      }
      return reply(409, {
        error: { code: 'stale_write', message: 'Nothing was changed: “weekly-report” is version 3 now, not 2: look at it again.' },
      })
    }))
    const onChanged = vi.fn()
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={NEWER} onChanged={onChanged} />)

    await press(/Use v2/)

    expect(screen.getByRole('note').textContent).toContain('is version 3 now, not 2: look at it again.')
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('says why a fire would run nothing, and still offers the version there is', () => {
    const gone = { ...NEWER, runs: 0, problem: 'the version it was allowed (v1) is no longer kept here' }
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={gone} onChanged={() => {}} />)
    expect(screen.getByRole('note').textContent).toContain('is no longer kept here')
    expect(screen.getByRole('button', { name: /Use v2/ })).toBeTruthy()
  })

  it('shows nothing for an automation that runs the newest version', () => {
    const current = { ...NEWER, runs: 2, allowed: 2, newer: 0, since: [], use: null }
    const { container } = render(
      <WorkflowVersionNote triggerId="store:manual:weekly-report" version={current} onChanged={() => {}} />,
    )
    expect(container.textContent).toBe('')
  })

  it('says which workflow a step starts has a newer version, and Use sends the versions it offered', async () => {
    const sent = gateway()
    const onChanged = vi.fn()
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={STEP_NEWER} onChanged={onChanged} />)
    const note = screen.getByRole('note')
    expect(note.textContent).toContain('“report-part”, which it runs as a step, has a newer version')
    expect(note.textContent).toContain('It runs “report-part” as a step of “weekly-report” at v1; saved since: v2 by an agent.')
    expect(note.textContent).not.toContain('This automation runs v1')

    await press(/Use the newest versions/)

    expect(sent.map((s) => s.body)).toEqual([
      { version: 1, steps: { 'report-part': 2 } },
      { version: 1, steps: { 'report-part': 2 }, confirm: true },
    ])
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('says why a step would not run', () => {
    const refused = {
      ...STEP_NEWER,
      steps: [{ ...STEP_NEWER.steps[0], runs: 0, problem: 'this automation was not allowed to run “report-part”' }],
    }
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={refused} onChanged={() => {}} />)
    expect(screen.getByRole('note').textContent).toContain(
      '“report-part”, which “weekly-report” runs as a step: this automation was not allowed to run “report-part”',
    )
  })

  it('offers no Use vN on someone else’s automation', () => {
    render(<WorkflowVersionNote triggerId="store:manual:weekly-report" version={NEWER} onChanged={() => {}} readOnly />)
    expect(screen.queryByRole('button', { name: /Use v2/ })).toBeNull()
  })
})
