import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../lib/data'
import type { PendingApproval, RunningList, RunningRow, WaitingList } from '../../lib/api'
import { SURFACE_LIMITS } from '../../lib/buddy/copy'
import { waitingApproval, waitingItem, waitingList } from '../../test/waiting'
import { runningLine, waitingLine, waitingOrbState, waitingWorkName, workKindOf } from './allWork'

// ── View one: everything at once, as the buddy shows it (#3898) ─────────────────────────────────
//
// Every piece of work as an orb and one line: what waits on you first, in the waiting list's one
// order, then what is running. Each row has the buddy's orb for its state, its work's kind glyph,
// its one line from the buddy's templates (the phone's 48 characters, the work's name cut first),
// and its exact phase in the phase table's words. Small orbs are told apart by state and glyph.

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const waiting = vi.fn<() => Promise<WaitingList>>()
const running = vi.fn<() => Promise<RunningList>>()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      waiting: () => waiting(),
      running: () => running(),
      resolveApproval: vi.fn(),
      pushStatus: () => Promise.reject(new Error('no push in this test')),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxOpen: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
    },
  }
})

const AP: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'chat', tool: 'Bash', tool_input: 'ls', tool_purpose: 'List the folder',
  session: 'c1', ts: Math.round(Date.now() / 1000) - 30, session_title: 'Release notes', agent: '', risk: 'caution',
  grant_agent: '', source_label: 'chat “Release notes”', confirms: [], work: 'chat:c1',
}
const GATE = waitingItem({
  kind: 'gate', id: 'gate:r1:approve:0', work: 'workflow:r1', consequence: 'changes',
  brief: { workflow: 'release', node_id: 'approve', ask: { kind: 'approval', prompt: 'Ship v1.2?' } },
  route: '#/workflows/runs/r1',
})
const LOOP = waitingItem({
  kind: 'loop_question', id: 'loop_question:l1', work: 'loop:l1', consequence: 'asks',
  brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' },
  route: '#/loops/l1',
})

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c9', kind: 'chat', session: 'c9', phase: 'tool', line: 'Running the tests', since: null,
    route: '#/chat/c9', blocked: false, waiting: [], name: 'Fix the flaky test', agent: '', started_at: null,
    ...over,
  } as RunningRow
}

const route = () => ({ sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() })

beforeEach(() => {
  waiting.mockReset().mockResolvedValue(waitingList(waitingApproval(AP), GATE, LOOP))
  running.mockReset().mockResolvedValue({ running: [row()], finished: [] })
  invalidateKeys('companion:', true)
  for (const k of ['running-companion', 'loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']) invalidateKeys(k)
})
afterEach(() => { cleanup() })

describe('the lines', () => {
  it('an approval needs your OK, any other ask asks, each at most the phone’s 48', () => {
    const long = 'A'.repeat(200)
    const ap = waitingApproval(AP)
    expect(waitingLine(ap, 'Release notes')).toBe('Release notes needs your OK: Bash')
    // Past 48, the work's name is cut first and the question kept whole.
    expect(waitingLine(LOOP, 'Sort the inbox')).toBe('Sort the… asks: May I archive last year’s mail?')
    for (const item of [ap, GATE, LOOP]) {
      const line = waitingLine(item, long)
      expect(line.length, item.kind).toBeLessThanOrEqual(SURFACE_LIMITS.phoneAllWork)
      expect(line, item.kind).toMatch(item.kind === 'approval' ? / needs your OK/ : / asks/)
    }
  })

  it('a running row says its phase in its own template', () => {
    expect(runningLine(row(), Date.now())).toBe('Fix the flaky test: Running the tests')
    expect(runningLine(row({ phase: 'thinking', line: '' }), Date.now())).toBe('Fix the flaky test: thinking')
    expect(runningLine(row({ name: 'B'.repeat(200) }), Date.now()).length).toBeLessThanOrEqual(SURFACE_LIMITS.phoneAllWork)
  })

  it('names the work by its running row, else its chat, else its source', () => {
    const rows = new Map([['loop:l1', row({ work: 'loop:l1', kind: 'loop', name: 'Inbox sorter' })]])
    expect(waitingWorkName(LOOP, rows)).toBe('Inbox sorter')
    expect(waitingWorkName(waitingApproval(AP), new Map())).toBe('Release notes')
    expect(waitingWorkName(GATE, new Map())).toBe('Workflow “release”')
  })

  it('an approval’s orb is the waiting still with "!", every other ask’s with "?"', () => {
    expect(waitingOrbState(waitingApproval(AP))).toBe('approval')
    expect(waitingOrbState(GATE)).toBe('input')
    expect(workKindOf('workflow:r1')).toBe('workflow')
    expect(workKindOf('')).toBeNull()
  })
})

describe('view one', () => {
  it('lists every ask before the running work, each with its orb, its glyph, its line and its phase', async () => {
    render(<CompanionPage {...route()} />)
    await screen.findByText('Release notes needs your OK: Bash')
    const heads = [...document.querySelectorAll('[data-work-head]')]
    const lines = heads.map((h) => h.querySelector('p')?.textContent)
    expect(lines.slice(0, 3)).toEqual([
      'Release notes needs your OK: Bash',
      'Workflow “release” asks: Ship v1.2?',
      // No running row names the loop here, so its source does, and both parts are cut to fit.
      'Loop “Sort… asks: May I archive last year’s…',
    ])
    expect(await screen.findByText('Fix the flaky test: Running the tests')).toBeTruthy()
    const all = [...document.querySelectorAll('[data-work-head]')]
    expect(all.length).toBe(4)
    // Each orb is its state, by name, never a colour each.
    const orbs = all.map((h) => h.querySelector('[data-buddy-orb]')?.getAttribute('data-buddy-state'))
    expect(orbs).toEqual(['approval', 'input', 'input', 'tool'])
    const glyphs = all.map((h) => h.querySelector('[data-kind-glyph]')?.getAttribute('data-kind-glyph') ?? null)
    expect(glyphs).toEqual(['chat', 'workflow', 'loop', 'chat'])
    // The exact phase, in the phase table's words.
    expect([...document.querySelectorAll('[data-phase]')].map((p) => p.textContent))
      .toEqual(['Needs your OK', 'Waiting for your answer', 'Waiting for your answer'])
    // The asks come before what runs.
    const firstRunning = all[3]
    for (const ask of all.slice(0, 3)) {
      expect(ask.compareDocumentPosition(firstRunning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
  })

  it('every orb on the list holds still, and is decorative beside the words that say the same', async () => {
    render(<CompanionPage {...route()} />)
    await screen.findByText('Fix the flaky test: Running the tests')
    const orbs = [...document.querySelectorAll('[data-work-head] [data-buddy-orb]')]
    expect(orbs.length).toBe(4)
    for (const orb of orbs) {
      expect(orb.getAttribute('data-buddy-moving')).toBe('false')
      expect(orb.closest('[aria-hidden="true"]'), 'the line and the phase say it').toBeTruthy()
    }
    expect(within(document.body).queryAllByRole('img')).toHaveLength(0)
  })
})
