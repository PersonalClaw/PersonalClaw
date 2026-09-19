/**
 * #542 — a knowledge intent is editable and pausable, not create-only.
 *
 * `IntentEditor` — whose own save comment said *"Edits keep their existing id"* — was mounted only
 * for a NEW intent, because the panel branched on whether the intent had an id:
 *
 *     selectedIntent.id ? <IntentDetail …/>   // every EXISTING intent -> read-only
 *                       : <IntentEditor …/>   // only id === ''
 *
 * So the backend's documented upsert-with-id path had zero callers for an existing intent, and
 * three things were dead:
 *
 *   1. the list's off badge was unreachable markup — the save hard-coded `enabled: true`;
 *   2. an intent could never be paused, though `Intent.applies_to` ANDs on `enabled`;
 *   3. `propose_skill` was frozen at creation, stranding the Generate-skill action.
 *
 * MEASURED against a live gateway on an isolated home, which is why (2) is the expensive one:
 * `POST /api/knowledge/intents/<id>/run` over the same five items answered `{evaluated: 5,
 * errors: 5}` while active and `{evaluated: 0, errors: 0}` while paused — `evaluated` is
 * literally the number of items handed to the matcher, one model call each. Delete was the only
 * way to stop a fan-out, and it destroys everything the intent has gathered.
 *
 * These assert the REQUEST BODY rather than the source text: the defect was a hard-coded field,
 * and scanning for the absence of a literal proves much less than watching what gets sent.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { KnowledgeIntent } from '../../lib/api'

const EXISTING: KnowledgeIntent = {
  id: 'intent-a1b2c3d4',
  goal: 'anything that could improve my homelab',
  enabled: true,
  enabled_for: ['note'],
  propose_skill: false,
}

let upsert: ReturnType<typeof vi.fn>

/** Mock the api module and hand back the freshly-imported page module. */
async function withApi(extra: Record<string, unknown>) {
  upsert = vi.fn(() => Promise.resolve({ intents: [], id: EXISTING.id }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: { ...(real.api as object), upsertKnowledgeIntent: upsert, ...extra },
    }
  })
  return await import('./KnowledgeListPage')
}

async function mountEditor(intent: KnowledgeIntent, onSaved = () => {}) {
  const { IntentEditor } = await withApi({})
  render(<IntentEditor intent={intent} onClose={() => {}} onSaved={onSaved} />)
}

const save = () => fireEvent.click(screen.getByRole('button', { name: /^save/i }))
const body = () => upsert.mock.calls[0][0]

beforeEach(() => { cleanup(); vi.resetModules(); sessionStorage.clear() })
afterEach(() => cleanup())

// ── the editor, opened on an EXISTING intent ─────────────────────────────────────────────────

describe('editing an existing intent', () => {
  it('seeds every field from the intent and calls itself Edit, not New', async () => {
    await mountEditor(EXISTING)
    expect(screen.getByText('Edit intent'), 'the heading said "New intent" unconditionally').toBeTruthy()
    expect(screen.getByLabelText(/what do you want to track/i)).toHaveValue(EXISTING.goal)
    expect(screen.getByLabelText(/limit to types/i)).toHaveValue('note')
  })

  it('sends the existing id — the upsert path that had no caller', async () => {
    await mountEditor(EXISTING)
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().id, 'without the id the backend would CREATE a second intent').toBe(EXISTING.id)
  })

  it('sends enabled from the control instead of a hard-coded true', async () => {
    // 🔑 THE DEFECT: `enabled: true` was a literal in the save body.
    await mountEditor(EXISTING)
    fireEvent.click(screen.getByRole('switch', { name: /active/i }))
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().enabled, 'turning Active off must PAUSE the intent').toBe(false)
  })

  it('can turn propose_skill on for an intent created without it', async () => {
    // Frozen at creation before: the Generate-skill action was permanently unreachable.
    expect(EXISTING.propose_skill).toBe(false)
    await mountEditor(EXISTING)
    fireEvent.click(screen.getByRole('checkbox', { name: /build a skill/i }))
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().propose_skill).toBe(true)
  })

  it('round-trips an unchanged intent without altering a field', async () => {
    // Vacuity guard, and the rail that closes the whole class for this record: opening the editor
    // and saving must not mutate anything by itself. A future field added to `Intent` and left
    // unbound (or frozen at a literal) fails here even if the census in
    // `lib/frozenRecordField.test.ts` cannot see the call site.
    await mountEditor({ ...EXISTING, enabled: false, propose_skill: true })
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body()).toMatchObject({
      id: EXISTING.id, goal: EXISTING.goal, enabled: false, propose_skill: true, enabled_for: ['note'],
    })
  })

  it('resumes a paused intent', async () => {
    await mountEditor({ ...EXISTING, enabled: false })
    expect(screen.getByRole('switch', { name: /active/i })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('switch', { name: /active/i }))
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().enabled).toBe(true)
  })
})

// ── creation is unchanged ────────────────────────────────────────────────────────────────────

describe('creating an intent still behaves as before', () => {
  const BLANK: KnowledgeIntent = { id: '', goal: '', enabled: true, enabled_for: [], propose_skill: false }

  it('omits the id so the backend derives the slug, and defaults to active', async () => {
    await mountEditor(BLANK)
    expect(screen.getByText('New intent')).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/what do you want to track/i), { target: { value: 'track homelab' } })
    save()
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().id, 'a new intent must NOT send an id — the slug is derived from the goal').toBeUndefined()
    expect(body().enabled, 'a new intent is active by default, as before').toBe(true)
  })

  it('still refuses an empty goal without calling the API', async () => {
    await mountEditor(BLANK)
    save()
    await waitFor(() => expect(screen.getByText(/describe what you want to track/i)).toBeTruthy())
    expect(upsert).not.toHaveBeenCalled()
  })
})

// ── the list row: the one-click escape from a running fan-out ────────────────────────────────

describe('the intents list row', () => {
  async function mountList(intents: KnowledgeIntent[]) {
    const { IntentsView } = await withApi({
      knowledgeIntents: () => Promise.resolve({ intents }),
    })
    render(<IntentsView selectedId={null} onSelect={() => {}} reloadKey={0} />)
    await waitFor(() => expect(screen.getByText(intents[0].goal!)).toBeTruthy())
  }

  it('pauses an intent from the row, without opening a panel', async () => {
    // The cost lever where the two siblings in this directory put theirs (a watched source and a
    // research report). Reaching a pause only through Edit -> toggle -> Save is four steps between
    // a user and a fan-out they want stopped.
    await mountList([EXISTING])
    fireEvent.click(screen.getByRole('switch', { name: /^pause intent:/i }))
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body()).toMatchObject({ id: EXISTING.id, enabled: false, goal: EXISTING.goal })
  })

  it('carries the rest of the record through the pause, so nothing else is rewritten', async () => {
    // 🪤 The endpoint is a WHOLE-RECORD upsert, not a PATCH: a row switch that sent only
    // `{id, enabled}` would blank the goal and silently drop `propose_skill`.
    await mountList([{ ...EXISTING, propose_skill: true }])
    fireEvent.click(screen.getByRole('switch', { name: /^pause intent:/i }))
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body()).toMatchObject({ propose_skill: true, enabled_for: ['note'] })
  })

  it('resumes a paused one, and shows the badge that used to be unreachable', async () => {
    await mountList([{ ...EXISTING, enabled: false }])
    // The badge says "Paused" — the word its two siblings in this directory use — and carries a
    // title naming the consequence rather than restating the state.
    const badge = screen.getByText('Paused')
    expect(badge.getAttribute('title')).toMatch(/not evaluated against new items/i)
    fireEvent.click(screen.getByRole('switch', { name: /^resume intent:/i }))
    await waitFor(() => expect(upsert).toHaveBeenCalledTimes(1))
    expect(body().enabled).toBe(true)
  })

  it('shows no Paused badge for an active intent', async () => {
    await mountList([EXISTING])
    expect(screen.queryByText('Paused')).toBeNull()
  })
})

// ── the detail panel offers the way in, and explains a paused intent ─────────────────────────

describe('the detail panel', () => {
  async function mountDetail(intent: KnowledgeIntent, onEdit = () => {}) {
    const { IntentDetail } = await withApi({
      knowledgeIntentOutcomes: () => Promise.resolve({ outcomes: [] }),
    })
    render(<IntentDetail intent={intent} onChanged={() => {}} onClose={() => {}} onEdit={onEdit} onOpenItem={() => {}} />)
  }

  it('has an Edit control that opens the editor', async () => {
    const onEdit = vi.fn()
    await mountDetail(EXISTING, onEdit)
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    expect(onEdit, 'this affordance did not exist at all').toHaveBeenCalledTimes(1)
  })

  it('says a paused intent will find nothing, rather than letting Run look broken', async () => {
    // Run is enabled and SUCCEEDS on a paused intent; it just evaluates nothing.
    await mountDetail({ ...EXISTING, enabled: false })
    expect(screen.getByText(/^Paused —/)).toBeTruthy()
  })

  it('says nothing of the sort for an active one', async () => {
    await mountDetail(EXISTING)
    await waitFor(() => expect(screen.getByRole('button', { name: /run on existing/i })).toBeTruthy())
    expect(screen.queryByText(/^Paused —/)).toBeNull()
  })
})

// ── REACHABILITY, pinned structurally ───────────────────────────────────────────────────────
//
// Everything above proves the three components behave. It does NOT prove the editor is REACHABLE:
// the entire defect was a correct editor that the page never mounted for an existing intent. A
// behavioural version means rendering the whole page (a dozen endpoints and a URL router), so the
// mount decision is pinned by source instead — narrowly, on the branch itself.
describe('the page can actually reach the editor', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeListPage.tsx'), 'utf8')

  it('no longer decides "editor vs detail" by whether the intent has an id alone', () => {
    expect(src, 'the id-keyed branch is what made every existing intent read-only')
      .not.toMatch(/\{selectedIntent\.id\s*\n?\s*\?\s*<IntentDetail/)
  })

  it('mounts the editor on an explicit editing state', () => {
    expect(src).toMatch(/selectedIntent\.id && !editingIntent/)
  })

  it('passes onEdit down so the detail panel can open it', () => {
    expect(src).toMatch(/onEdit=\{\(\) => setEditingIntent\(true\)\}/)
  })

  it('uses the app-wide ?edit=1 primitive rather than a bespoke flag', () => {
    // `useEditFlag` is the canonical "edit-vs-view of an open record" state, already carrying
    // Tasks, Triggers, Agents and Prompts. VERIFIED in a real browser against a live gateway:
    // Edit pushes `&edit=1`, and Back leaves the editor with the detail panel still open.
    //
    // 🪤 It does NOT survive a browser reload, and that is a SEPARATE pre-existing defect rather
    // than a gap in this fix: `?intent=<id>` alone does not restore the panel either, because
    // `IntentsView`'s resolver is gated on `selectedId`, which is derived from `resolvedIntent`
    // (null until the resolver runs) instead of from the URL token. Measured both ways — with and
    // without `edit=1` — and both stay closed. Left alone here; it is the detail panel's bug.
    expect(src).toMatch(/useEditFlag\(query, setQuery\)/)
  })

  it('leaves edit mode when the selection changes, but not when it merely re-resolves', () => {
    // 🪤 Clearing on EVERY call is wrong: `IntentsView`'s resolver re-selects the already-open
    // intent each time the list reloads — which a save and a row pause both trigger — so an
    // unconditional clear throws the user out of the editor mid-edit.
    expect(src).toMatch(/if \(nextTok !== intentTok\) setEditingIntent\(false\)/)
  })

  it('routes every intent write through the single writer', () => {
    // Two call sites hand-assembling a whole-record upsert body is how `enabled: true` got
    // hard-coded in the first place.
    expect(src.match(/api\.upsertKnowledgeIntent\(/g) ?? [], 'only `writeIntent` may call the upsert')
      .toHaveLength(1)
    expect(src).toMatch(/function writeIntent\(/)
  })
})
