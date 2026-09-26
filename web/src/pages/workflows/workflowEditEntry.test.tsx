/** The definition page leads to the workflow editor.
 *
 *  Kept apart from `workflowEditor.test.tsx` on purpose: this file imports only the definition page,
 *  so on a tree with no editor it still loads and fails on the missing control itself, rather than
 *  on an import. */
import { describe, expect, it } from 'vitest'
import { vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'

type Def = { name: string; source: string; version: number; description: string; root: { kind: string; id: string } }

const DEFS: Record<string, Def> = {
  'paper-ingest': { name: 'paper-ingest', source: 'bundled', version: 1, description: 'Read a paper.', root: { kind: 'sequence', id: 'main' } },
  mine: { name: 'mine', source: 'user', version: 3, description: 'My own.', root: { kind: 'sequence', id: 'main' } },
}

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowDef: async (name: string) => ({ definition: DEFS[name], provider: DEFS[name].source === 'user' ? 'native' : 'bundled' }),
      workflowVersions: async (name: string) => ({
        versions: name === 'mine'
          ? [1, 2, 3].map((version) => ({ version, source: 'user', created_at: '', note: '', run_ids: [], ops_count: 0 }))
          : [{ version: 1, source: 'user', created_at: '', note: '', run_ids: [], ops_count: 0 }],
        pinned: DEFS[name].version,
        maturity: null,
      }),
      workflowVersionDiff: async () => ({ a: 0, b: 0, ops: [] }),
      workflowLedger: async (name: string) => ({ name, runs: [], total: 0 }),
    },
  }
})

const tick = () => new Promise((res) => setTimeout(res, 0))

async function mount(name: string) {
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  const edits: Array<number | undefined> = []
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name={name} onBack={() => {}} onStarted={() => {}} onEdit={(v?: number) => edits.push(v)} />)
    await tick(); await tick()
  })
  return { r, edits }
}

const buttonNamed = (r: ReturnType<typeof render>, text: string) =>
  [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').trim() === text)

async function click(el: Element | undefined) {
  expect(el, 'the control exists').toBeTruthy()
  await act(async () => { fireEvent.click(el!); await tick() })
}

describe('the definition page leads to the editor', () => {
  it('🔑 a shipped template offers "Edit a copy", since it cannot be changed in place', async () => {
    const { r, edits } = await mount('paper-ingest')
    expect(buttonNamed(r, 'Edit')).toBeUndefined()
    await click(buttonNamed(r, 'Edit a copy'))
    expect(edits).toEqual([undefined])
  })

  it('🔑 a definition of your own offers "Edit"', async () => {
    const { r, edits } = await mount('mine')
    expect(buttonNamed(r, 'Edit a copy')).toBeUndefined()
    await click(buttonNamed(r, 'Edit'))
    expect(edits).toEqual([undefined])
  })

  it('🔑 an earlier version is restored through the editor, and the current one is not offered', async () => {
    const { r, edits } = await mount('mine')
    const versions = [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await click(versions)
    const restore = [...r.container.querySelectorAll('button')].filter((b) => (b.textContent ?? '').includes('Restore'))
    expect(restore).toHaveLength(2) // v1 and v2 — v3 is the definition's own version
    expect(r.container.textContent).toContain('current')
    await click(restore[1]) // newest first: v2, then v1
    expect(edits).toEqual([1])
  })
})
