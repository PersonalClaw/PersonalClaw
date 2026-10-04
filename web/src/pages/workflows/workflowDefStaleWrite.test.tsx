/** A save from the workflow editor never replaces a change made since the page read it.
 *
 *  The editor sends the WHOLE definition back (`api.saveWorkflowDef`). With two tabs open on one
 *  workflow, the second tab's Save made its older copy the newest version — the one a run executes —
 *  and the first tab's change was gone from it without a word. So was the A2A publish toggle, or the
 *  agent's rewrite, landing between this page's read and its save. The gateway now refuses a save
 *  that names a revision other than the stored one (`409 stale_write`; the route's own proof is
 *  `tests/test_stale_write_workflow_defs.py`). These tests drive two editors against one fake backend
 *  that keeps what it is sent and refuses the way the gateway does.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, waitFor, within } from '@testing-library/react'
import { type WorkflowDef } from '../../lib/api'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'
import { WorkflowDefEditor } from './WorkflowDefEditor'

// Monaco needs real layout and a worker; under jsdom the JSON tab gets a plain textarea that speaks
// the same value/onChange/readOnly contract and carries the editor's own accessible name.
vi.mock('@monaco-editor/react', () => ({
  default: (p: { value: string; onChange?: (v: string) => void; options?: { ariaLabel?: string; readOnly?: boolean } }) => (
    <textarea aria-label={p.options?.ariaLabel} value={p.value} readOnly={p.options?.readOnly} onChange={(e) => p.onChange?.(e.target.value)} />
  ),
}))

type Def = WorkflowDef & Record<string, unknown>

/** A definition of the user's own, edited in place. */
const MINE: Def = {
  name: 'mine', source: 'user', version: 1, provenance: 'user', spec_semver: '1.0',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  description: 'Read a paper.', tags: ['research'],
  inputs: { paper_url: { type: 'string', required: true, default: null, help: 'Where the paper is.' } },
  metadata: { risk: 'low' },
  on_overlap: 'skip',
  root: {
    kind: 'sequence', id: 'main', children: [
      { kind: 'infer', id: 'identify', config: { prompt: 'Name the paper.' } },
    ],
  },
}

// ── the fake backend ──────────────────────────────────────────────────────────────────────────

const store = new Map<string, Def>()
const saves: Array<{ body: Record<string, unknown>; base: string | undefined }> = []
/** A write that lands after the page's own checks and before its save is looked at. */
let landsFirst: (() => void) | null = null

/** The gateway's revision: a digest of the definition as read, so every writer changes it. */
function revisionOf(def: Def): string {
  const text = JSON.stringify(def)
  let h = 5381
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0
  return h.toString(16)
}

/** What the definition page's A2A publish toggle does: one field, straight to the store. */
function publishElsewhere(name: string) {
  const def = store.get(name)!
  store.set(name, { ...def, metadata: { ...def.metadata, a2a_published: true }, version: (def.version ?? 1) + 1 })
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
        return { definition: structuredClone(def), provider: def.source === 'user' ? 'native' : 'bundled', revision: revisionOf(def) }
      },
      workflowDefs: async () => {
        const defs = [...store.entries()].filter(([k]) => !k.includes('@'))
          .map(([name, d]) => ({ name, description: '', source: d.source ?? 'user', version: d.version ?? 1, tags: [], provider: '' }))
        return { defs, total: defs.length }
      },
      workflowVersion: async (name: string, version: number) => {
        const def = store.get(`${name}@v${version}`)
        if (!def) throw new actual.ApiError('no such version', 404, 'not_found')
        return { version, saved_by: 'owner', created_at: '', note: '', definition: structuredClone(def) }
      },
      saveWorkflowDef: async (body: Record<string, unknown>, base?: string) => {
        saves.push({ body: structuredClone(body), base })
        const first = landsFirst
        landsFirst = null
        first?.()
        if (body.save === false) return { saved: false, valid: true, issues: [], levels: [], lint: { findings: [] } }
        const name = String(body.name)
        const prior = store.get(name)
        if (prior?.source === 'user') {
          if (!base) throw new actual.ApiError(`This write is checked against the copy of the workflow '${name}' it was built from…`, 428, 'revision_required')
          if (base !== revisionOf(prior)) throw new actual.ApiError(`This write replaces the workflow '${name}', which changed after the copy it was built from was read…`, 409, 'stale_write')
        }
        const { save: _save, based_on: _b, based_on_version: _bv, ...fields } = body
        const saved = { ...prior, ...fields, name, source: 'user', version: (prior?.version ?? 0) + 1 } as Def
        store.set(name, saved)
        return { saved: true, valid: true, issues: [], levels: [], lint: { findings: [] }, definition: structuredClone(saved), revision: revisionOf(saved) }
      },
    },
  }
})

beforeEach(() => {
  store.clear()
  store.set('mine', structuredClone(MINE))
  saves.length = 0
  landsFirst = null
})

// ── driving the page ──────────────────────────────────────────────────────────────────────────

const tick = () => new Promise((res) => setTimeout(res, 0))

/** One browser tab with the editor open. */
async function openTab(name = 'mine', fromVersion?: number) {
  const saved: string[] = []
  const cancelled: string[] = []
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefEditor name={name} fromVersion={fromVersion} onCancel={() => cancelled.push(name)} onSaved={(n) => saved.push(n)} />)
    await tick(); await tick()
  })
  return { r, saved, cancelled }
}

const button = (scope: ParentNode, text: string) =>
  [...scope.querySelectorAll('button')].find((b) => (b.textContent ?? '').trim() === text)

async function click(el: Element | undefined | null) {
  expect(el, 'the control exists').toBeTruthy()
  await act(async () => { fireEvent.click(el!); await tick(); await tick() })
}

async function type(el: HTMLTextAreaElement | HTMLInputElement | null, value: string) {
  expect(el, 'the field exists').toBeTruthy()
  await act(async () => { fireEvent.change(el!, { target: { value } }); await tick() })
}

const description = (r: ReturnType<typeof render>) => r.container.querySelector('textarea') as HTMLTextAreaElement

async function editPrompt(r: ReturnType<typeof render>, value: string) {
  const card = r.container.querySelector('[data-step-path="root.children[0]"]') as HTMLElement
  await click(card.querySelector('button[aria-expanded]'))
  await type(card.querySelector('[aria-label="prompt"]') as HTMLTextAreaElement, value)
}

const notice = (r: ReturnType<typeof render>) => waitFor(() => {
  const el = r.container.querySelector<HTMLElement>('[data-stale-write="true"]')
  expect(el, 'the refusal notice is on the page').not.toBeNull()
  return el!
})

/** The notice once the stored version it compares against has been read. */
async function settled(r: ReturnType<typeof render>) {
  const el = await notice(r)
  await waitFor(() => expect(within(el).getByRole('button', { name: 'Review the difference' }).hasAttribute('disabled')).toBe(false))
  return el
}

const promptOf = (def: Def | undefined) =>
  ((def?.root as { children: Array<{ config: Record<string, unknown> }> }).children[0].config.prompt)

// ── two tabs, one workflow ────────────────────────────────────────────────────────────────────

describe('two tabs editing one workflow', () => {
  it('🔑 the second tab’s save is refused where you are, keeps what you typed, and the first tab’s change is what is stored', async () => {
    const opened = revisionOf(store.get('mine')!)
    const a = await openTab()
    const b = await openTab()

    await type(description(a.r), 'Tab A: read a paper, carefully.')
    await click(button(a.r.container, 'Save'))
    expect(a.saved).toEqual(['mine'])

    await type(description(b.r), 'Tab B: read two papers.')
    await click(button(b.r.container, 'Save'))

    const el = await notice(b.r)
    expect(el.getAttribute('role')).toBe('alert')
    expect(el.textContent).toMatch(/This workflow changed elsewhere/)
    expect(b.saved, 'the refused save did not leave the editor').toEqual([])
    expect(store.get('mine')!.description).toBe('Tab A: read a paper, carefully.')
    expect(saves.map((s) => s.base), 'each save named the revision its tab read').toEqual([opened, opened])
    // Kept — and frozen while it is held, so nothing typed now is left out of what Reapply sends.
    expect(description(b.r).value).toBe('Tab B: read two papers.')
    expect(description(b.r).matches(':disabled')).toBe(true)
    const save = button(b.r.container, 'Save') as HTMLButtonElement
    expect(save.disabled || save.getAttribute('aria-disabled') === 'true').toBe(true)
    expect(save.getAttribute('title')).toContain(HELD_CHANGE_REASON)
  })

  it('🔑 Reload and reapply puts your change on top of the other tab’s, and both are stored', async () => {
    const a = await openTab()
    const b = await openTab()
    await type(description(a.r), 'Tab A: read a paper, carefully.')
    await click(button(a.r.container, 'Save'))
    const afterA = revisionOf(store.get('mine')!)

    await editPrompt(b.r, 'Name the paper and its venue.')
    await click(button(b.r.container, 'Save'))
    const el = await settled(b.r)
    await click(within(el).getByRole('button', { name: 'Reload and reapply' }))

    await waitFor(() => expect(b.saved).toEqual(['mine']))
    const stored = store.get('mine')!
    expect(stored.description, 'the other tab’s change survives').toBe('Tab A: read a paper, carefully.')
    expect(promptOf(stored), 'and yours lands on top of it').toBe('Name the paper and its venue.')
    expect(saves.at(-1)!.base, 'over the version it was re-applied to').toBe(afterA)
  })

  it('when both changed the same thing there is no Reapply: the review shows both, and yours is saved over it only from there', async () => {
    const a = await openTab()
    const b = await openTab()
    await type(description(a.r), 'Tab A’s description.')
    await click(button(a.r.container, 'Save'))
    await type(description(b.r), 'Tab B’s description.')
    await click(button(b.r.container, 'Save'))

    const el = await settled(b.r)
    expect(within(el).queryByRole('button', { name: 'Reload and reapply' })).toBeNull()
    expect(el.textContent).toMatch(/touch the same part/)
    await click(within(el).getByRole('button', { name: 'Review the difference' }))
    const dialog = await waitFor(() => {
      const d = document.body.querySelector<HTMLElement>('[role="dialog"]')
      expect(d).not.toBeNull()
      return d!
    })
    expect(dialog.textContent).toContain('Tab A’s description.')
    expect(dialog.textContent).toContain('Tab B’s description.')
    expect(store.get('mine')!.description, 'nothing is replaced before the choice').toBe('Tab A’s description.')
    await click(within(dialog).getByRole('button', { name: 'Save mine over it' }))
    await waitFor(() => expect(b.saved).toEqual(['mine']))
    expect(store.get('mine')!.description).toBe('Tab B’s description.')
  })

  it('Discard my change shows the workflow as it is stored now, and saves nothing', async () => {
    const a = await openTab()
    const b = await openTab()
    await type(description(a.r), 'Tab A’s description.')
    await click(button(a.r.container, 'Save'))
    await type(description(b.r), 'Tab B’s description.')
    await click(button(b.r.container, 'Save'))

    const el = await notice(b.r)
    const count = saves.length
    await click(within(el).getByRole('button', { name: 'Discard my change' }))
    await waitFor(() => expect(description(b.r)?.value).toBe('Tab A’s description.'))
    expect(b.r.container.querySelector('[data-stale-write="true"]')).toBeNull()
    expect(description(b.r).matches(':disabled')).toBe(false)
    expect(saves.length, 'a discard writes nothing').toBe(count)
  })

  it('the JSON tab is read-only while a refused change is held', async () => {
    const a = await openTab()
    const b = await openTab()
    await type(description(a.r), 'Tab A’s description.')
    await click(button(a.r.container, 'Save'))
    await type(description(b.r), 'Tab B’s description.')
    await click(button(b.r.container, 'Save'))
    await notice(b.r)

    const json = [...b.r.container.querySelectorAll('[role="radio"]')].find((el) => (el.textContent ?? '').includes('JSON'))
    await click(json)
    const raw = b.r.container.querySelector('[aria-label="mine definition (JSON)"]') as HTMLTextAreaElement
    expect(raw.readOnly).toBe(true)
    expect(JSON.parse(raw.value).description, 'showing the change that is held').toBe('Tab B’s description.')
  })
})

// ── the gateway writing the workflow between the read and the save ────────────────────────────

describe('a change the gateway made after the page read the workflow', () => {
  it('🔑 the publish toggle is not undone by the editor’s save: it is refused, and Reapply keeps both', async () => {
    const b = await openTab()
    publishElsewhere('mine') // the definition page's A2A toggle, in another tab
    await type(description(b.r), 'Edited after it was published.')
    await click(button(b.r.container, 'Save'))

    const el = await settled(b.r)
    expect(store.get('mine')!.metadata).toEqual({ risk: 'low', a2a_published: true })
    expect(store.get('mine')!.description).toBe('Read a paper.')
    await click(within(el).getByRole('button', { name: 'Reload and reapply' }))
    await waitFor(() => expect(b.saved).toEqual(['mine']))
    const stored = store.get('mine')!
    expect(stored.metadata).toEqual({ risk: 'low', a2a_published: true })
    expect(stored.description).toBe('Edited after it was published.')
  })
})

// ── a restore, and a copy ─────────────────────────────────────────────────────────────────────

describe('a restore and a copy', () => {
  it('🔑 a restore of an old version saved over a newer one is refused, and saved over it only from the review', async () => {
    store.set('mine', { ...structuredClone(MINE), version: 3, description: 'Version three.' })
    store.set('mine@v1', { ...structuredClone(MINE), version: 1, description: 'The first one.' })
    const restore = await openTab('mine', 1)
    const other = await openTab()
    await type(description(other.r), 'Version four, from another tab.')
    await click(button(other.r.container, 'Save'))
    expect(store.get('mine')!.version).toBe(4)

    await click(button(restore.r.container, 'Save'))
    const el = await settled(restore.r)
    expect(saves.at(-1)!.body).toMatchObject({ name: 'mine', based_on_version: 1 })
    expect(store.get('mine')!.description, 'the newer version is still the one that runs').toBe('Version four, from another tab.')
    // Merging a newer version into an old one would restore neither: no Reapply, only the review.
    expect(within(el).queryByRole('button', { name: 'Reload and reapply' })).toBeNull()
    await click(within(el).getByRole('button', { name: 'Review the difference' }))
    const dialog = await waitFor(() => {
      const d = document.body.querySelector<HTMLElement>('[role="dialog"]')
      expect(d).not.toBeNull()
      return d!
    })
    await click(within(dialog).getByRole('button', { name: 'Save mine over it' }))
    await waitFor(() => expect(restore.saved).toEqual(['mine']))
    expect(store.get('mine')!.description).toBe('The first one.')
    expect(store.get('mine')!.version).toBe(5)
  })

  it('discarding a refused restore goes back to the workflow instead of re-opening the old version', async () => {
    store.set('mine', { ...structuredClone(MINE), version: 3 })
    store.set('mine@v1', { ...structuredClone(MINE), version: 1, description: 'The first one.' })
    const restore = await openTab('mine', 1)
    publishElsewhere('mine')
    await click(button(restore.r.container, 'Save'))
    const el = await notice(restore.r)
    await click(within(el).getByRole('button', { name: 'Discard my change' }))
    expect(restore.cancelled).toEqual(['mine'])
    expect(restore.saved).toEqual([])
  })

  it('a copy replaces nothing and names no revision; a name taken after the check is refused at the name field', async () => {
    store.set('paper-ingest', { ...structuredClone(MINE), name: 'paper-ingest', source: 'bundled' })
    const copy = await openTab('paper-ingest')
    landsFirst = () => store.set('paper-ingest-copy', { ...structuredClone(MINE), name: 'paper-ingest-copy' })
    await click(button(copy.r.container, 'Save copy'))

    expect(saves.at(-1)!.base).toBeUndefined()
    expect(copy.saved).toEqual([])
    expect(copy.r.container.textContent).toContain('You already have a workflow named paper-ingest-copy')
    expect(store.get('paper-ingest-copy')!.description, 'the workflow that took the name is untouched').toBe('Read a paper.')

    const name = copy.r.container.querySelector('input[maxlength="63"]') as HTMLInputElement
    await type(name, 'my-paper-ingest')
    await click(button(copy.r.container, 'Save copy'))
    expect(copy.saved).toEqual(['my-paper-ingest'])
    expect(saves.at(-1)!.base).toBeUndefined()
  })
})
