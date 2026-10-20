import { describe, expect, it, vi } from 'vitest'
import { act, render, fireEvent } from '@testing-library/react'

// ── The workflow's page says who saved each version, and which version each automation runs ──
//
// An automation runs the version of the workflow its owner allowed, or a newer one she saved in
// the editor: never one an agent, a sync, an import or an app saved, which waits for her Use vN on
// the automation. So the Versions tab names who saved each version, and lists the automations that
// run the workflow, each with the version it runs and any newer one waiting for her.

const VERSIONS = [
  { version: 1, saved_by: 'owner', created_at: '', note: '', run_ids: [], ops_count: 0 },
  { version: 2, saved_by: 'agent', created_at: '', note: '', run_ids: [], ops_count: 0 },
  { version: 3, saved_by: '', created_at: '', note: '', run_ids: [], ops_count: 0 },
]

const RUNS_V1 = {
  workflow: 'weekly-report', runs: 1, saved_by: 'owner', allowed: 1, follows: false,
  newer: 3, since: [{ version: 2, saved_by: 'agent' }, { version: 3, saved_by: '' }], problem: '',
  steps: [], use: { version: 3, steps: {} },
}

function makeApi(automations: unknown[]) {
  return {
    workflowDef: () => Promise.resolve({
      definition: { name: 'weekly-report', source: 'user', version: 3, root: { kind: 'sequence', id: 'main' } },
      provider: 'native',
    }),
    workflowVersions: () => Promise.resolve({ versions: VERSIONS, maturity: null }),
    workflowVersionDiff: () => Promise.resolve({ a: 2, b: 3, ops: [] }),
    workflowAutomations: vi.fn(() => Promise.resolve({ name: 'weekly-report', automations })),
  }
}

async function versionsTab(api: Record<string, unknown>) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({ api }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="weekly-report" onBack={() => {}} onStarted={() => {}} onEdit={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  const tab = [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes('Versions'))
  await act(async () => {
    fireEvent.click(tab!)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('the workflow page', () => {
  it('names who saved each version', async () => {
    const text = (await versionsTab(makeApi([]))).container.textContent ?? ''
    expect(text).toContain('you, in the editor')
    expect(text).toContain('an agent')
    expect(text).toContain('not recorded')
  })

  it('lists the automations that run it, each with the version it runs', async () => {
    const api = makeApi([
      {
        id: 'manual:weekly-report', name: 'Weekly report', enabled: true, needs_grant: [],
        workflow_version: RUNS_V1, via: '', status_url: '#/triggers?open=manual:weekly-report',
      },
      {
        id: 'clock:not-yet', name: 'Not allowed yet', enabled: false, needs_grant: ['Run workflow'],
        workflow_version: { ...RUNS_V1, runs: 3, allowed: 0, newer: 0, since: [], use: null },
        via: '', status_url: '#/triggers?open=clock:not-yet',
      },
      {
        id: 'clock:monthly', name: 'Monthly summary', enabled: true, needs_grant: [],
        workflow_version: {
          ...RUNS_V1, workflow: 'monthly-summary', runs: 4, allowed: 4, newer: 0, since: [],
          steps: [{
            workflow: 'weekly-report', via: 'monthly-summary', runs: 1, saved_by: 'owner',
            newer: 3, since: [{ version: 2, saved_by: 'agent' }], problem: '',
          }],
          use: { version: 4, steps: { 'weekly-report': 3 } },
        },
        via: 'monthly-summary', status_url: '#/triggers?open=clock:monthly',
      },
    ])
    const r = await versionsTab(api)
    const text = r.container.textContent ?? ''

    expect(api.workflowAutomations).toHaveBeenCalledWith('weekly-report')
    expect(text).toContain('Automations')
    expect(text).toContain('runs v1 · v3 waits for you on the automation')
    expect(text).toContain('not allowed to run yet')
    expect(text).toContain('runs v1 as a step of “monthly-summary” · v3 waits for you on the automation')
    const link = [...r.container.querySelectorAll('a')].find((a) => a.textContent === 'Weekly report')
    expect(link?.getAttribute('href')).toBe('#/triggers?open=manual:weekly-report')
  })

  it('says so when no automation runs it', async () => {
    const text = (await versionsTab(makeApi([]))).container.textContent ?? ''
    expect(text).toContain('No automation runs this workflow.')
  })
})
