/** A workflow can be edited in the dashboard.
 *
 *  Before this there was no editor at all: `api.saveWorkflowDef` had no caller, and the only way to
 *  change a workflow was to ask a chat agent to re-author it. These tests drive the page against a
 *  fake backend that keeps what it is sent, so "open, edit, save, reload, run" goes through real
 *  state rather than a mock that only records the call.
 *
 *  The backend half — that the save keeps `runtime_hints`, restores the values the read hides, and
 *  refuses a hidden value it cannot restore at its step — is `tests/test_workflow_def_edit_round_trip.py`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { ApiError, type WorkflowDef } from '../../lib/api'
import { WorkflowDefEditor } from './WorkflowDefEditor'
import { WorkflowDefDetail } from './WorkflowDefDetail'

// Monaco needs real layout and a worker; under jsdom the JSON tab gets a plain textarea that speaks
// the same value/onChange contract and carries the editor's own accessible name.
vi.mock('@monaco-editor/react', () => ({
  default: (p: { value: string; onChange?: (v: string) => void; options?: { ariaLabel?: string } }) => (
    <textarea aria-label={p.options?.ariaLabel} value={p.value} onChange={(e) => p.onChange?.(e.target.value)} />
  ),
}))

/** A shipped template, shaped like `paper-ingest`: an action with arguments, a model step whose
 *  schema holds a value the read hides (`authors` → `_has_authors`), fields no control edits
 *  (`runtime_hints`, `defaults`, `on_overlap`), and the bookkeeping the store owns. */
const TEMPLATE: WorkflowDef & Record<string, unknown> = {
  name: 'paper-ingest', source: 'bundled', version: 1, provenance: 'user', spec_semver: '1.0',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  description: 'Read a paper.', tags: ['research'],
  inputs: { paper_url: { type: 'string', required: true, default: null, help: 'Where the paper is.' } },
  metadata: { risk: 'low' },
  runtime_hints: { judge: { rubric: ['cites the paper'] } },
  defaults: { budget: { _has_max_tokens: true, max_cost: 0 } },
  on_overlap: 'skip',
  root: {
    kind: 'sequence', id: 'main', children: [
      { kind: 'action', id: 'fetch', config: { provider: 'web-fetch', with: { url: '{{inputs.paper_url}}' } } },
      { kind: 'infer', id: 'identify', config: { prompt: 'Name the paper.', schema: { title: 'string', _has_authors: true } } },
    ],
  },
}

// ── the fake backend ──────────────────────────────────────────────────────────────────────────

const store = new Map<string, WorkflowDef & Record<string, unknown>>()
const saves: Array<Record<string, unknown>> = []
const runs: Array<Record<string, unknown>> = []
let refuseWith: ApiError | null = null

function versionsOf(name: string) {
  const def = store.get(name)
  return { versions: def ? [{ version: def.version ?? 1, source: 'user', created_at: '', note: '', run_ids: [], ops_count: 0 }] : [], pinned: def?.version ?? 1, maturity: null }
}

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowDef: async (name: string) => {
        const def = store.get(name)
        if (!def) throw new actual.ApiError(`no workflow definition named '${name}'`, 404, 'not_found')
        return { definition: structuredClone(def), provider: def.source === 'user' ? 'native' : 'bundled' }
      },
      workflowDefs: async () => ({ defs: [...store.values()].map((d) => ({ name: d.name, description: '', source: d.source ?? 'user', version: d.version ?? 1, tags: [], provider: '' })), total: store.size }),
      workflowVersion: async (name: string, version: number) => {
        const def = store.get(`${name}@v${version}`)
        if (!def) throw new actual.ApiError('no such version', 404, 'not_found')
        return { version, source: 'user', created_at: '', note: '', definition: structuredClone(def) }
      },
      workflowVersions: async (name: string) => versionsOf(name),
      workflowVersionDiff: async () => ({ a: 0, b: 0, ops: [] }),
      workflowLedger: async (name: string) => ({ name, runs: [], total: 0 }),
      refineWorkflow: async () => ({ run_id: 'refine-1' }),
      publishWorkflowToA2A: async () => ({ ok: true, name: '', a2a_published: false }),
      startWorkflowRun: async (body: Record<string, unknown>) => { runs.push(body); return { run_id: `run-${runs.length}`, status: 'running' } },
      saveWorkflowDef: async (body: Record<string, unknown>) => {
        saves.push(structuredClone(body))
        if (refuseWith) throw refuseWith
        if (body.save === false) return { saved: false, valid: true, issues: [], levels: [], lint: { findings: [] } }
        const name = String(body.name)
        const prior = store.get(name)
        const { save: _save, based_on: _b, based_on_version: _bv, ...fields } = body
        const saved = { ...fields, name, source: 'user', version: (prior?.version ?? 0) + 1 } as WorkflowDef & Record<string, unknown>
        store.set(name, saved)
        return { saved: true, valid: true, issues: [], levels: [], lint: { findings: [] }, definition: structuredClone(saved) }
      },
    },
  }
})

beforeEach(() => {
  store.clear()
  store.set('paper-ingest', structuredClone(TEMPLATE))
  saves.length = 0
  runs.length = 0
  refuseWith = null
})

const tick = () => new Promise((res) => setTimeout(res, 0))

async function mountEditor(name = 'paper-ingest', fromVersion?: number) {
  const saved: string[] = []
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefEditor name={name} fromVersion={fromVersion} onCancel={() => {}} onSaved={(n) => saved.push(n)} />)
    await tick(); await tick()
  })
  return { r, saved }
}

const button = (r: ReturnType<typeof render>, text: string) =>
  [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').trim() === text || (b.textContent ?? '').includes(text))

async function click(el: Element | undefined | null) {
  expect(el, 'the control exists').toBeTruthy()
  await act(async () => { fireEvent.click(el!); await tick(); await tick() })
}

const step = (r: ReturnType<typeof render>, path: string) =>
  r.container.querySelector(`[data-step-path="${path}"]`) as HTMLElement | null

async function openStep(r: ReturnType<typeof render>, path: string) {
  await click(step(r, path)?.querySelector('button[aria-expanded]'))
}

function field(scope: ParentNode, label: string) {
  return scope.querySelector(`[aria-label="${label}"]`) as HTMLTextAreaElement | HTMLInputElement | null
}

async function type(el: HTMLTextAreaElement | HTMLInputElement | null, value: string) {
  expect(el, 'the field exists').toBeTruthy()
  await act(async () => { fireEvent.change(el!, { target: { value } }); await tick() })
}

// ── the structured view ───────────────────────────────────────────────────────────────────────

describe('the Steps view', () => {
  it('🔑 lists every step in the order it runs, with its name and what it does', async () => {
    const { r } = await mountEditor()
    const rows = [...r.container.querySelectorAll('[data-step-path]')].map((el) => el.getAttribute('data-step-path'))
    expect(rows).toEqual(['root', 'root.children[0]', 'root.children[1]'])
    const fetch = step(r, 'root.children[0]')!.textContent ?? ''
    expect(fetch).toContain('1')
    expect(fetch).toContain('fetch')
    expect(fetch).toContain('action')
    expect(fetch).toContain('web-fetch') // what the action does
    expect(step(r, 'root.children[1]')!.textContent).toContain('identify')
  })

  it('an action step shows its inputs as fields, bindings included', async () => {
    const { r } = await mountEditor()
    await openStep(r, 'root.children[0]')
    const card = step(r, 'root.children[0]')!
    expect(card.textContent).toContain('Action inputs')
    expect((field(card, 'url') as HTMLTextAreaElement).value).toBe('{{inputs.paper_url}}')
  })

  it('a value the read hides is shown as kept — never as an empty field to fill in', async () => {
    const { r } = await mountEditor()
    await openStep(r, 'root.children[1]')
    const card = step(r, 'root.children[1]')!
    expect(card.textContent).toContain('hidden value')
    expect(card.textContent).toContain('authors')
    expect(card.textContent).toContain('kept when you save')
  })

  it('the declared run inputs are editable', async () => {
    const { r } = await mountEditor()
    const inputs = r.container.querySelector('[data-input="paper_url"]')!
    expect(inputs).toBeTruthy()
    expect((field(inputs, 'paper_url help') as HTMLInputElement).value).toBe('Where the paper is.')
  })
})

// ── saving ────────────────────────────────────────────────────────────────────────────────────

describe('saving', () => {
  it('🔑 a copy of a shipped template saves the WHOLE definition with the edit, under your name', async () => {
    const { r, saved } = await mountEditor()
    await openStep(r, 'root.children[1]')
    await type(field(step(r, 'root.children[1]')!, 'prompt'), 'Name the paper and its venue.')
    await click(button(r, 'Save copy'))

    expect(saves).toHaveLength(1)
    const body = saves[0]
    expect(body.name).toBe('paper-ingest-copy')
    expect(body.based_on).toBe('paper-ingest') // where the hidden values come back from
    expect(body.save).toBe(true)
    const identify = (body.root as { children: Array<{ config: Record<string, unknown> }> }).children[1]
    expect(identify.config.prompt).toBe('Name the paper and its venue.')
    // What no control edits still goes back — dropping any of these is the defect.
    expect(body.runtime_hints).toEqual(TEMPLATE.runtime_hints)
    expect(body.defaults).toEqual(TEMPLATE.defaults)
    expect(body.on_overlap).toBe('skip')
    expect(body.metadata).toEqual(TEMPLATE.metadata)
    expect(body.inputs).toEqual(TEMPLATE.inputs)
    expect(identify.config.schema).toEqual({ title: 'string', _has_authors: true })
    // ...and what the store owns does not.
    for (const owned of ['version', 'source', 'provenance', 'created_at', 'updated_at', 'spec_semver']) {
      expect(body, owned).not.toHaveProperty(owned)
    }
    expect(saved).toEqual(['paper-ingest-copy'])
  })

  it('🔑 a copy never replaces a workflow you already have', async () => {
    store.set('paper-ingest-copy', { ...structuredClone(TEMPLATE), name: 'paper-ingest-copy', source: 'user' })
    const { r } = await mountEditor()
    const nameField = r.container.querySelector('input[maxlength="63"]') as HTMLInputElement
    expect(nameField.value).toBe('paper-ingest-copy-2') // the taken name is not offered
    await type(nameField, 'paper-ingest-copy')
    await click(button(r, 'Save copy'))
    expect(saves).toHaveLength(0)
    expect(r.container.textContent).toContain('You already have a workflow named paper-ingest-copy')
    expect(store.get('paper-ingest-copy')!.description).toBe(TEMPLATE.description)
  })

  it('a copy name that breaks the name rule is refused at the name field, before any request', async () => {
    const { r } = await mountEditor()
    await type(r.container.querySelector('input[maxlength="63"]') as HTMLInputElement, 'My Paper Ingest')
    await click(button(r, 'Save copy'))
    expect(saves).toHaveLength(0)
    expect(r.container.textContent).toContain('lowercase letters, digits and hyphens')
  })

  it('an edit of your own definition is saved in place, and not before something changed', async () => {
    store.set('mine', { ...structuredClone(TEMPLATE), name: 'mine', source: 'user' })
    const { r } = await mountEditor('mine')
    const save = button(r, 'Save')!
    expect(save.getAttribute('aria-disabled') === 'true' || (save as HTMLButtonElement).disabled).toBe(true)
    await type(r.container.querySelector('textarea') as HTMLTextAreaElement, 'Read a paper, carefully.')
    await click(button(r, 'Save'))
    expect(saves[0].name).toBe('mine')
    expect(saves[0].description).toBe('Read a paper, carefully.')
  })
})

// ── the dry run and the engine's issues ───────────────────────────────────────────────────────

describe('Check (the engine’s dry run)', () => {
  it('🔑 sends save:false and writes nothing', async () => {
    const { r, saved } = await mountEditor()
    await click(button(r, 'Check'))
    expect(saves).toHaveLength(1)
    expect(saves[0].save).toBe(false)
    expect(store.has('paper-ingest-copy')).toBe(false)
    expect(saved).toEqual([])
    expect(r.container.textContent).toContain('No problems found')
  })

  it('🔑 pins each issue to the step it names, in the platform’s own sentence', async () => {
    const pipe = "unknown pipe 'shout' (in {{inputs.paper_url | shout}})"
    refuseWith = new ApiError('the spec did not validate', 422, 'validation_failed', {
      valid: false,
      issues: [
        { code: 'WF_UNKNOWN_PIPE', message: pipe, path: 'root.children[0]', severity: 'error' },
        { code: 'WF_INPUT_BAD_LOOP_FIELD', message: "input 'paper_url' declares loop_field 'tsak'", path: 'inputs', severity: 'error' },
      ],
      lint: { findings: [{ code: 'WFL_NO_ID', message: 'Give this step an id.', path: 'root.children[1]', severity: 'warning' }] },
    })
    const { r } = await mountEditor()
    await click(button(r, 'Check'))

    const fetch = step(r, 'root.children[0]')!
    expect(fetch.textContent).toContain(pipe) // verbatim, at its step
    expect(fetch.querySelector('[data-issue-code="WF_UNKNOWN_PIPE"]')).toBeTruthy()
    expect(step(r, 'root.children[1]')!.textContent).toContain('Give this step an id.') // lint: advice, at its step
    expect(r.container.querySelector('[data-input="paper_url"]')!.closest('section')!.textContent)
      .toContain("declares loop_field 'tsak'")
    expect(r.container.textContent).toContain('2 problems stop this definition from saving')
  })

  it('a refused Save shows the same issues and stays on the page', async () => {
    refuseWith = new ApiError('the spec did not validate', 422, 'validation_failed', {
      issues: [{ code: 'WF_HIDDEN_VALUE_UNMATCHED', message: '`schema.authors` on step identify is hidden…', path: 'root.children[1]', severity: 'error' }],
    })
    const { r, saved } = await mountEditor()
    await click(button(r, 'Save copy'))
    expect(saved).toEqual([])
    expect(step(r, 'root.children[1]')!.textContent).toContain('`schema.authors` on step identify is hidden…')
  })
})

// ── the JSON tab ──────────────────────────────────────────────────────────────────────────────

describe('the JSON tab', () => {
  const tab = (r: ReturnType<typeof render>, label: string) =>
    [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes(label))

  it('🔑 holds the whole definition, and an edit there is what the Steps view shows', async () => {
    const { r } = await mountEditor()
    await click(tab(r, 'JSON'))
    const raw = field(r.container, 'paper-ingest-copy definition (JSON)') as HTMLTextAreaElement
    const doc = JSON.parse(raw.value)
    expect(doc.runtime_hints).toEqual(TEMPLATE.runtime_hints)
    expect(doc).not.toHaveProperty('version')
    doc.description = 'Edited as JSON.'
    await type(raw, JSON.stringify(doc))
    await click(tab(r, 'Steps'))
    expect((r.container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('Edited as JSON.')
  })

  it('JSON that does not parse blocks Save and says why', async () => {
    const { r } = await mountEditor()
    await click(tab(r, 'JSON'))
    await type(field(r.container, 'paper-ingest-copy definition (JSON)'), '{ "root": ')
    await click(tab(r, 'Steps')) // refused: stays on the JSON tab
    expect(field(r.container, 'paper-ingest-copy definition (JSON)')).toBeTruthy()
    expect(r.container.querySelector('[role="alert"]')!.textContent).toMatch(/JSON/)
    const save = button(r, 'Save copy') as HTMLButtonElement
    expect(save.disabled || save.getAttribute('aria-disabled') === 'true').toBe(true)
  })
})

// ── the round trip ────────────────────────────────────────────────────────────────────────────

describe('open, edit, save, reload, run', () => {
  it('🔑 a copy of a shipped template comes back with the edit, and a run starts from it', async () => {
    const { r, saved } = await mountEditor()
    await openStep(r, 'root.children[1]')
    await type(field(step(r, 'root.children[1]')!, 'prompt'), 'Name the paper and its venue.')
    await click(button(r, 'Save copy'))
    expect(saved).toEqual(['paper-ingest-copy'])
    r.unmount()

    // Reload: the definition page reads what the backend now holds.
    let detail!: ReturnType<typeof render>
    await act(async () => {
      detail = render(<WorkflowDefDetail name="paper-ingest-copy" onBack={() => {}} onStarted={() => {}} onEdit={() => {}} />)
      await tick(); await tick()
    })
    expect(detail.container.textContent).toContain('Name the paper and its venue.')
    expect(button(detail, 'Edit a copy')).toBeUndefined() // it is yours now
    expect(store.get('paper-ingest-copy')!.runtime_hints).toEqual(TEMPLATE.runtime_hints)

    // Run it.
    await type(detail.container.querySelector('input') as HTMLInputElement, 'https://example.org/paper.pdf')
    await click(button(detail, 'Run'))
    expect(runs).toEqual([{ name: 'paper-ingest-copy', inputs: { paper_url: 'https://example.org/paper.pdf' } }])
  })
})

// ── restoring a version ───────────────────────────────────────────────────────────────────────

describe('restoring a recorded version', () => {
  it('🔑 opens that version and saves it as the newest, based on it', async () => {
    store.set('mine', { ...structuredClone(TEMPLATE), name: 'mine', source: 'user', version: 3 })
    store.set('mine@v1', { ...structuredClone(TEMPLATE), name: 'mine', source: 'user', version: 1, description: 'The first one.' })
    const { r, saved } = await mountEditor('mine', 1)
    expect((r.container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('The first one.')
    expect(r.container.textContent).toContain('Saving it makes it the newest version')
    await click(button(r, 'Save')) // untouched is the point of a restore
    expect(saves[0]).toMatchObject({ name: 'mine', based_on: 'mine', based_on_version: 1, description: 'The first one.' })
    expect(saved).toEqual(['mine'])
    expect(store.get('mine')!.version).toBe(4)
  })
})
