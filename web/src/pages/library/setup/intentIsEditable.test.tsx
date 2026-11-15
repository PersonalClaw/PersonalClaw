// @module-tag tree-scan
/**
 * #542: a knowledge intent is editable and pausable, not create-only.
 *
 * The old Knowledge list's editor was mounted only for a NEW intent, so three things were dead: the
 * list's paused badge (the save hard-coded `enabled: true`), pausing at all (though
 * `Intent.applies_to` ANDs on `enabled`), and `propose_skill` after creation. Measured on a live
 * gateway: a run over five items answered `{evaluated: 5}` while active and `{evaluated: 0}` while
 * paused, one model call each, so Delete was the only way to stop a fan-out.
 *
 * The library's set-up (#6101) is where an intent is edited and paused now: its row's switch and its
 * form. These assert the REQUEST BODY rather than the source text: the defect was a hard-coded field,
 * and watching what gets sent proves more than scanning for a literal.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { answers, calls, resetGateway } from '../../../test/libraryHarness'
import type { KnowledgeIntent } from '../../../lib/api'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../../test/sourceTree'

vi.mock('../../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../../test/libraryHarness')
  return { ...(await orig<typeof import('../../../lib/api')>()), api: gatewayApi() }
})
const { IntentForm } = await import('./IntentForm')
const { SetupPanel } = await import('./SetupPanel')
const { setupViewOf } = await import('./SetupPane')
const { resetDataStore } = await import('../../../lib/data/store')

const EXISTING: KnowledgeIntent = {
  id: 'intent-a1b2c3d4', goal: 'anything that could improve my homelab', enabled: true,
  enabled_for: ['note'], propose_skill: false, revision: 'r1',
}

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.saveKnowledgeIntent = { intents: [], id: EXISTING.id }
  answers.createKnowledgeIntent = { intents: [], id: 'track-homelab' }
  answers.intentCandidates = { total: 0 }
  answers.intentRun = new Error('no run')
  answers.researchReports = { reports: [] }
  answers.knowledgeSources = { sources: [], kinds: [] }
})
afterEach(cleanup)

const save = () => fireEvent.click(screen.getByRole('button', { name: /^(Save|Start looking)$/ }))
const saved = () => calls.saveKnowledgeIntent?.[0]?.[0] as Record<string, unknown>
const goalBox = () => screen.getByRole('textbox', { name: /Look out for/ })
const checking = () => screen.getByRole('switch', { name: /^Checking new items:/ })

// ── the form, opened on an EXISTING intent ───────────────────────────────────────────────────

describe('editing an existing intent', () => {
  it('🔑 seeds every field from the intent and calls itself Edit, not New', () => {
    render(<IntentForm intent={EXISTING} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Edit intent' })).toBeTruthy()
    expect(goalBox()).toHaveValue(EXISTING.goal)
    expect(screen.getByRole('checkbox', { name: 'Notes' })).toBeChecked()
  })

  it('🔑 sends the existing id and the revision it was read at', async () => {
    render(<IntentForm intent={EXISTING} onClose={vi.fn()} onSaved={vi.fn()} />)
    save()
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(calls.saveKnowledgeIntent[0][1]).toBe('r1')
    expect(saved().id, 'without the id the gateway would CREATE a second intent').toBe(EXISTING.id)
  })

  it('🔑 sends enabled from the switch instead of a hard-coded true', async () => {
    render(<IntentForm intent={EXISTING} onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.click(checking())
    save()
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved().enabled, 'turning Checking new items off must PAUSE the intent').toBe(false)
  })

  it('can turn propose_skill on for an intent created without it', async () => {
    render(<IntentForm intent={EXISTING} onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Offer to build a skill from what it finds' }))
    save()
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved().propose_skill).toBe(true)
  })

  it('🔴 round-trips an unchanged intent without altering a field', async () => {
    // Vacuity guard, and the rail that closes the class for this record: opening the form and saving
    // must not change anything by itself.
    render(<IntentForm intent={{ ...EXISTING, enabled: false, propose_skill: true }} onClose={vi.fn()} onSaved={vi.fn()} />)
    save()
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved()).toEqual({ id: EXISTING.id, goal: EXISTING.goal, enabled: false, propose_skill: true, enabled_for: ['note'] })
  })

  it('resumes a paused intent', async () => {
    render(<IntentForm intent={{ ...EXISTING, enabled: false }} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(checking()).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(checking())
    save()
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved().enabled).toBe(true)
  })
})

// ── creation ─────────────────────────────────────────────────────────────────────────────────

describe('creating an intent', () => {
  it('🔑 omits the id so the gateway derives the slug, and starts it checking', async () => {
    render(<IntentForm onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'New intent' })).toBeTruthy()
    fireEvent.change(goalBox(), { target: { value: 'track homelab' } })
    save()
    await waitFor(() => expect(calls.createKnowledgeIntent).toHaveLength(1))
    const body = calls.createKnowledgeIntent[0][0] as Record<string, unknown>
    expect('id' in body, 'a new intent must NOT send an id: the slug is derived from the goal').toBe(false)
    expect(body.enabled, 'a new intent checks new items from the start').toBe(true)
  })

  it('refuses an empty goal without calling the gateway, and says why', () => {
    render(<IntentForm onClose={vi.fn()} onSaved={vi.fn()} />)
    const start = screen.getByRole('button', { name: 'Start looking' })
    expect(start.getAttribute('aria-disabled') === 'true' || (start as HTMLButtonElement).disabled).toBe(true)
    expect(start.getAttribute('title') ?? '').toContain('Say what to look out for first.')
    save()
    expect(calls.createKnowledgeIntent).toBeUndefined()
  })
})

// ── the set-up row: the one-step stop for a running fan-out ──────────────────────────────────

describe("the intent's set-up row", () => {
  async function mountRow(intent: KnowledgeIntent, nav = { form: vi.fn(), item: vi.fn() }) {
    answers.knowledgeIntents = { intents: [intent] }
    render(<SetupPanel nav={nav} onClose={vi.fn()} />)
    await screen.findByText(intent.goal!)
    return nav
  }
  const rowSwitch = () => screen.getByRole('switch', { name: `Checking new items: ${EXISTING.goal}` })

  it('🔑 pauses an intent from the row, without opening a form', async () => {
    await mountRow(EXISTING)
    fireEvent.click(rowSwitch())
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved()).toMatchObject({ id: EXISTING.id, enabled: false, goal: EXISTING.goal })
  })

  it('🔑 carries the rest of the record through the pause, so nothing else is rewritten', async () => {
    // 🪤 The endpoint is a WHOLE-RECORD upsert, not a PATCH: a switch that sent only `{id, enabled}`
    // would blank the goal and drop `propose_skill`.
    await mountRow({ ...EXISTING, propose_skill: true })
    fireEvent.click(rowSwitch())
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved()).toMatchObject({ propose_skill: true, enabled_for: ['note'] })
  })

  it('resumes a paused one', async () => {
    await mountRow({ ...EXISTING, enabled: false })
    expect(rowSwitch()).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(rowSwitch())
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(saved().enabled).toBe(true)
  })

  it('🔑 its Edit opens the form for that intent, at an address the pane reads', async () => {
    // REACHABILITY: the whole old defect was a correct editor the page never mounted for an existing
    // intent. The row's Edit names the intent, and the pane routes that address to its form.
    const nav = await mountRow(EXISTING)
    fireEvent.click(screen.getByRole('button', { name: `Edit ${EXISTING.goal}` }))
    expect(nav.form).toHaveBeenCalledWith(`intent:${EXISTING.id}`)
    expect(setupViewOf(`intent:${EXISTING.id}`)).toEqual({ view: 'intent', id: EXISTING.id })
  })
})

describe('one writer', () => {
  it('routes every intent write through the single writer', () => {
    // Two call sites hand-assembling a whole-record upsert body is how `enabled: true` got
    // hard-coded in the first place. The upsert is two api calls — a create names no base, an edit
    // names the revision it replaces — and each may be spelled exactly once in the whole tree,
    // inside the writer (`intentRecord.ts`), which the set-up rows and the form share.
    const files = filesUnder(join(process.cwd(), 'src'), (n) => /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n))
      .filter((f) => !f.endsWith(join('lib', 'api.ts')))
    for (const call of [/api\.createKnowledgeIntent\(/g, /api\.saveKnowledgeIntent\(/g]) {
      const sites = files.flatMap((f) => (readSource(f).match(call) ?? []).map(() => f.slice(process.cwd().length + 1)))
      expect(sites, `only \`writeIntent\` may call ${call.source}`).toEqual(['src/pages/knowledge/intentRecord.ts'])
    }
    const writerSrc = readFileSync(join(process.cwd(), 'src/pages/knowledge/intentRecord.ts'), 'utf8')
    const at = writerSrc.indexOf('function writeIntent(')
    expect(at, 'the single writer must exist').toBeGreaterThan(-1)
    const writer = writerSrc.slice(at, writerSrc.indexOf('\n}\n', at))
    expect(writer).toMatch(/api\.createKnowledgeIntent\(/)
    expect(writer).toMatch(/api\.saveKnowledgeIntent\(/)
    // And the form and the set-up rows still write through it.
    for (const rel of ['src/pages/library/setup/IntentForm.tsx', 'src/pages/library/setup/SetupPanel.tsx']) {
      expect(readFileSync(join(process.cwd(), rel), 'utf8'), rel).toMatch(/from '\.\.\/\.\.\/knowledge\/intentRecord'/)
    }
  })
})
