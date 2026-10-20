/** The workflow editor names a step by its label, as every run surface does.
 *
 *  A step's label is the name its run page rows, its ending and its failure lines use. The editor's
 *  step rows printed only the id (`draft`, `check`), and a step that runs after others listed them by
 *  id too, so the one screen where a workflow is changed spoke a different vocabulary from every
 *  screen where it runs. A step without a label keeps its id, which is all it has. */
import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { WorkflowDef } from '../../lib/api'
import { WorkflowDefEditor } from './WorkflowDefEditor'

vi.mock('@monaco-editor/react', () => ({ default: () => null }))

const DEF: WorkflowDef & Record<string, unknown> = {
  name: 'labelled', source: 'user', version: 1, provenance: 'user', description: '', tags: [],
  inputs: {},
  root: {
    kind: 'sequence', id: 'main', children: [
      { kind: 'infer', id: 'draft', label: 'Draft the summary', config: { prompt: 'Summarize.' } },
      { kind: 'infer', id: 'plain', config: { prompt: 'No label.' } },
      { kind: 'infer', id: 'check', label: 'Check the draft', needs: ['draft', 'plain'], config: { prompt: 'Check it.' } },
    ],
  },
}

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowDef: async () => ({ definition: structuredClone(DEF), provider: 'native' }),
      workflowDefs: async () => ({ defs: [], total: 0 }),
      workflowVersions: async () => ({ versions: [], maturity: null }),
    },
  }
})

const tick = () => new Promise((res) => setTimeout(res, 0))

async function mount() {
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefEditor name="labelled" onCancel={() => {}} onSaved={() => {}} />)
    await tick(); await tick()
  })
  return r
}

const row = (r: ReturnType<typeof render>, path: string) =>
  r.container.querySelector(`[data-step-path="${path}"] button[aria-expanded]`) as HTMLElement

describe('the editor’s step rows', () => {
  it('🔴 name a labelled step by its label', async () => {
    const r = await mount()
    const draft = row(r, 'root.children[0]')
    expect(draft.textContent).toContain('Draft the summary')
    expect(draft.getAttribute('aria-label')).toBe('Step 1: Draft the summary (infer)')
  })

  it('and a step with no label by its id', async () => {
    const r = await mount()
    expect(row(r, 'root.children[1]').getAttribute('aria-label')).toBe('Step 2: plain (infer)')
  })

  it('🔴 name the steps one runs after the same way', async () => {
    const r = await mount()
    await act(async () => { row(r, 'root.children[2]').click(); await tick() })
    expect(r.container.textContent).toContain('Runs after Draft the summary, plain.')
  })
})
