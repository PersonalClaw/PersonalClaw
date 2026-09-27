import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import type { KnowledgeIntent, KnowledgeItem, WatchedSource } from '../../lib/api'

// ── A knowledge save from a stale copy is refused, and the change is kept ────────────────────
//
// Four knowledge surfaces save a WHOLE document built from the copy they read: an item's body
// (KnowledgeDetail), an intent's record (the list row's pause switch, and the editor), and a
// watched source's spec + budget (the remediation strip). The gateway now refuses a save whose
// copy went stale (`409 stale_write`); these pin what each surface does with that refusal — the
// one notice, the user's change kept, and the re-applied save naming the NEW revision — and that
// a save names the revision of the copy it painted.

function staleWrite() {
  return Object.assign(new Error('This write replaces it, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

/** A landed body save re-enriches the item, and the page follows that on the ingest stream —
 *  which jsdom does not have. The stream's content is not what these tests are about. */
class QuietEventSource {
  addEventListener() {}
  close() {}
}

beforeEach(() => { cleanup(); vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('EventSource', QuietEventSource) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

/** Find first, then click inside `act` — a query awaited INSIDE `act` waits on a render that
 *  `act` itself is holding back. */
async function click(find: Promise<HTMLElement> | HTMLElement) {
  const el = await find
  await act(async () => { fireEvent.click(el) })
}

/** `api` with these methods replaced, and `notify` recorded, before the page module is imported. */
function mockApi(methods: Record<string, unknown>) {
  const notify = vi.fn()
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, api: { ...(real.api as object), ...methods } }
  })
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))
  return notify
}

async function reapply(alert: HTMLElement) {
  const button = within(alert).getByRole('button', { name: 'Reload and reapply' })
  await waitFor(() => expect(button.hasAttribute('disabled')).toBe(false))
  await click(button)
}

// ── an item's body ───────────────────────────────────────────────────────────────────────────

const ITEM: KnowledgeItem = {
  id: 'k1', title: 'Homelab notes', content: 'alpha\nbeta\n', item_type: 'note', type: 'note',
  tags: ['t1'], content_revision: 'r1',
}
// What another writer (the agent, a rename's relink, another tab) stored meanwhile.
const THEIRS: KnowledgeItem = { ...ITEM, content: 'alpha\nbeta\ngamma from the agent\n', content_revision: 'r2' }

async function mountDetail({ stale }: { stale: boolean }) {
  let reads = 0
  const knowledgeItem = vi.fn(() => { reads += 1; return Promise.resolve(reads === 1 ? ITEM : THEIRS) })
  const saveKnowledgeItem = vi.fn((_id: string, _body: Record<string, unknown>, base: string) =>
    stale && base === 'r1' ? Promise.reject(staleWrite()) : Promise.resolve({ ok: true, content_revision: 'r9' }))
  const updateKnowledgeItem = vi.fn(() => Promise.resolve({ ok: true, content_revision: 'r1' }))
  mockApi({
    knowledgeItem, saveKnowledgeItem, updateKnowledgeItem,
    knowledgeItemIntents: () => Promise.resolve({ outcomes: [] }),
    knowledgeItemGraph: () => Promise.reject(new Error('no graph')),
    knowledgeTags: () => Promise.resolve([]),
  })
  const { KnowledgeDetail } = await import('./KnowledgeDetail')
  // The page renders the published header cluster (Edit / Save / Cancel) in its top bar.
  function Host() {
    const [header, setHeader] = useState<{ actions: ReactNode } | null>(null)
    return <>{header?.actions}<KnowledgeDetail item={ITEM} onHeader={setHeader} onChanged={() => {}} onDeleted={() => {}} /></>
  }
  render(<Host />)
  await waitFor(() => expect(knowledgeItem).toHaveBeenCalledTimes(1))
  await click(screen.findByRole('button', { name: 'Edit' }))
  return { knowledgeItem, saveKnowledgeItem, updateKnowledgeItem }
}

const body = () => screen.getByPlaceholderText('Markdown supported…') as HTMLTextAreaElement

async function saveBody(next: string) {
  fireEvent.change(body(), { target: { value: next } })
  await click(screen.getByRole('button', { name: 'Save' }))
}

describe('a knowledge item body saved from a stale copy', () => {
  it('names the revision its copy was read at, and a refusal shows the notice and keeps the draft', async () => {
    const { saveKnowledgeItem, updateKnowledgeItem } = await mountDetail({ stale: true })
    await saveBody('ALPHA\nbeta\n')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This item changed elsewhere/)
    expect(saveKnowledgeItem).toHaveBeenCalledTimes(1)
    expect(saveKnowledgeItem.mock.calls[0]).toEqual(['k1', { content: 'ALPHA\nbeta\n' }, 'r1'])
    expect(updateKnowledgeItem, 'a body edit never goes through the base-less write').not.toHaveBeenCalled()
    // Still editing, and what was typed is still there: the refusal kept the change.
    expect(body().value).toBe('ALPHA\nbeta\n')
  })

  it('Reload and reapply puts the edit on top of what the other writer stored', async () => {
    const { saveKnowledgeItem } = await mountDetail({ stale: true })
    await saveBody('ALPHA\nbeta\n')
    await reapply(await screen.findByRole('alert'))
    await waitFor(() => expect(saveKnowledgeItem).toHaveBeenCalledTimes(2))
    // The agent's line survives, the user's edit is on top, over the NEW revision.
    expect(saveKnowledgeItem.mock.calls[1]).toEqual(['k1', { content: 'ALPHA\nbeta\ngamma from the agent\n' }, 'r2'])
    await waitFor(() => expect(screen.queryByPlaceholderText('Markdown supported…')).toBeNull())
  })

  it('a save that lands names the painted revision and leaves the editor', async () => {
    const { saveKnowledgeItem } = await mountDetail({ stale: false })
    await saveBody('alpha\nbeta, revised\n')
    await waitFor(() => expect(saveKnowledgeItem).toHaveBeenCalledTimes(1))
    expect(saveKnowledgeItem.mock.calls[0][2]).toBe('r1')
    await waitFor(() => expect(screen.queryByPlaceholderText('Markdown supported…')).toBeNull())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a tag edit is sent as names added and removed, never as the whole list', async () => {
    const { saveKnowledgeItem, updateKnowledgeItem } = await mountDetail({ stale: false })
    fireEvent.click(screen.getByRole('button', { name: 'Remove t1' }))
    const add = screen.getByLabelText('Add a tag')
    fireEvent.change(add, { target: { value: 't2' } })
    fireEvent.keyDown(add, { key: 'Enter' })
    await click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateKnowledgeItem).toHaveBeenCalledTimes(1))
    expect(updateKnowledgeItem.mock.calls[0]).toEqual(['k1', { add_tags: ['t2'], remove_tags: ['t1'] }])
    expect(saveKnowledgeItem, 'an unchanged body is not re-sent').not.toHaveBeenCalled()
  })
})

// ── an intent: the list row's pause switch, and the editor ───────────────────────────────────

const INTENT: KnowledgeIntent = {
  id: 'intent-homelab', goal: 'anything that could improve my homelab', enabled: true,
  enabled_for: ['note'], propose_skill: false, revision: 'i1',
}
// Reworded in another tab after this list read it.
const INTENT_THEIRS: KnowledgeIntent = { ...INTENT, goal: 'homelab upgrades, reworded elsewhere', revision: 'i2' }

function intentApi() {
  let reads = 0
  const knowledgeIntents = vi.fn(() => { reads += 1; return Promise.resolve({ intents: [reads === 1 ? INTENT : INTENT_THEIRS] }) })
  const saveKnowledgeIntent = vi.fn((_record: unknown, base: string) =>
    base === 'i1' ? Promise.reject(staleWrite()) : Promise.resolve({ intents: [], id: INTENT.id }))
  const createKnowledgeIntent = vi.fn(() => Promise.resolve({ intents: [], id: INTENT.id }))
  const notify = mockApi({ knowledgeIntents, saveKnowledgeIntent, createKnowledgeIntent })
  return { knowledgeIntents, saveKnowledgeIntent, createKnowledgeIntent, notify }
}

describe('an intent pause from a stale list', () => {
  it('names the revision the row was read at, and a refusal shows the notice and no success', async () => {
    const { saveKnowledgeIntent, notify } = intentApi()
    const { IntentsView } = await import('./KnowledgeListPage')
    render(<IntentsView selectedId={null} onSelect={() => {}} reloadKey={0} />)
    await click(screen.findByRole('switch', { name: /^pause intent:/i }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/The intent “anything that could improve my homelab” changed elsewhere/)
    expect(saveKnowledgeIntent.mock.calls[0]).toEqual([
      { id: INTENT.id, goal: INTENT.goal, enabled: false, enabled_for: ['note'], propose_skill: false }, 'i1'])
    expect(notify, 'a refused pause must not announce "Intent paused"').not.toHaveBeenCalledWith('Intent paused', 'success')
  })

  it('Reload and reapply pauses the record as it is stored now', async () => {
    const { saveKnowledgeIntent, notify } = intentApi()
    const { IntentsView } = await import('./KnowledgeListPage')
    render(<IntentsView selectedId={null} onSelect={() => {}} reloadKey={0} />)
    await click(screen.findByRole('switch', { name: /^pause intent:/i }))
    await reapply(await screen.findByRole('alert'))
    await waitFor(() => expect(saveKnowledgeIntent).toHaveBeenCalledTimes(2))
    // The other tab's rewording survives; the pause is applied on top, over the NEW revision.
    expect(saveKnowledgeIntent.mock.calls[1]).toEqual([
      { id: INTENT.id, goal: INTENT_THEIRS.goal, enabled: false, enabled_for: ['note'], propose_skill: false }, 'i2'])
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Intent paused', 'success'))
  })
})

describe('an intent edit from a stale editor', () => {
  it('names the revision it opened on, and a refusal keeps the edit in the form', async () => {
    const { saveKnowledgeIntent } = intentApi()
    const { IntentEditor } = await import('./KnowledgeListPage')
    const onSaved = vi.fn()
    render(<IntentEditor intent={INTENT} onClose={() => {}} onSaved={onSaved} />)
    fireEvent.change(screen.getByLabelText(/what do you want to track/i), { target: { value: 'homelab power usage' } })
    await click(screen.getByRole('button', { name: /^save changes/i }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This intent changed elsewhere/)
    expect(saveKnowledgeIntent.mock.calls[0]).toEqual([
      { id: INTENT.id, goal: 'homelab power usage', enabled: true, enabled_for: ['note'], propose_skill: false }, 'i1'])
    expect(onSaved, 'a refused edit did not save').not.toHaveBeenCalled()
    expect(screen.getByLabelText(/what do you want to track/i)).toHaveValue('homelab power usage')
  })
})

// ── a watched source's settings ──────────────────────────────────────────────────────────────

const SOURCE: WatchedSource = {
  id: 'src-1', name: 'Product changelog', provider: 'watched-page', kind: 'web_page',
  spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 4 }, revision: 's1',
  enrichment: 'full', poll_interval_secs: 3600, item_type: 'bookmark', enabled: true,
  health_status: 'needs_render', last_error_summary: '', last_escalations: [], last_new_count: 0,
  last_poll_at: new Date().toISOString(), enrolled: true,
  remediation: { kind: 'render_tier', guidance: 'This page builds its content with JavaScript.', detail: '', action: 'allow_render' },
}
// Another tab raised the request budget after this row was read.
const SOURCE_THEIRS: WatchedSource = { ...SOURCE, budget: { max_requests: 9 }, revision: 's2' }

describe('a source remediation from a stale row', () => {
  async function mountRow() {
    const saveKnowledgeSourceSettings = vi.fn((_id: string, _settings: unknown, base: string) =>
      base === 's1' ? Promise.reject(staleWrite()) : Promise.resolve({ source: SOURCE_THEIRS }))
    const notify = mockApi({
      saveKnowledgeSourceSettings,
      knowledgeSources: () => Promise.resolve({ sources: [SOURCE_THEIRS], kinds: [], health_statuses: [], raw_enrichment: 'raw' }),
    })
    const { SourceRow } = await import('./SourcesPage')
    const onChanged = vi.fn()
    render(<SourceRow source={SOURCE} kinds={{ 'watched-page': { display_name: 'Watched Page', form: 'web_page' } }} onChanged={onChanged} />)
    await click(screen.getByRole('button', { name: 'Allow the render tier' }))
    return { saveKnowledgeSourceSettings, notify, onChanged }
  }

  it('names the revision the row was read at, and a refusal shows the notice', async () => {
    const { saveKnowledgeSourceSettings, notify } = await mountRow()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This source’s settings changed elsewhere/)
    expect(saveKnowledgeSourceSettings.mock.calls[0]).toEqual([
      'src-1', { spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 4, allow_render: true } }, 's1'])
    expect(notify).not.toHaveBeenCalledWith('Product changelog may now use the render tier', 'success')
  })

  it('Reload and reapply allows the render tier on top of the budget stored now', async () => {
    const { saveKnowledgeSourceSettings, notify, onChanged } = await mountRow()
    await reapply(await screen.findByRole('alert'))
    await waitFor(() => expect(saveKnowledgeSourceSettings).toHaveBeenCalledTimes(2))
    // The other tab's budget survives, the fix is on top, over the NEW revision.
    expect(saveKnowledgeSourceSettings.mock.calls[1]).toEqual([
      'src-1', { spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 9, allow_render: true } }, 's2'])
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Product changelog may now use the render tier', 'success'))
    expect(onChanged).toHaveBeenCalled()
  })
})
