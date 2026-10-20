import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { api } from '../lib/api'
import { writeQuery } from '../lib/data'
import { AgentsListPage } from './agents/AgentsListPage'
import { InboxPage } from './inbox/InboxPage'
import { KnowledgeListPage } from './knowledge/KnowledgeListPage'
import { SkillProposals } from './skills/SkillProposals'
import { SkillsPage } from './skills/SkillsPage'
import { ToolsPage } from './tools/ToolsPage'
import { TriggersSection } from './triggers/TriggersSection'
import { WeekGridView } from './triggers/WeekGridView'
import { startOfDay } from './triggers/weekGrid'

// ── A failed read is not an empty collection, on every surface that lists one ──────────────────
//
// Each of these pages renders a collection, and each once answered a failed read with its empty
// state: "No native agents", "No tools", "No triggers", a 7×24 grid of empty cells under
// "Projecting…" forever, "Inbox zero — you're all caught up" (pixel-identical to a healthy empty
// inbox), "No skills installed" under a "Browse skills" CTA, "No skill proposals", and a blank
// region where the knowledge library should be. Each swallowed in its own place: a `.catch(() =>
// [])` in the fetcher, a `Promise.allSettled` that never rejects, an adapter that dropped the
// hook's `error`, or a call site that never bound it.
//
// What the primitive says is `ui/loadError.test.tsx`'s, and which surfaces adopt it, tree-wide,
// is `ui/loadErrorCensus.test.ts`'s. What stays per surface is the one thing only the surface
// decides — which of its reads is the collection — so each is a row of one table, driven through
// its real component at both states, because the whole defect is which one renders:
//
//   · its collection read rejects → one `role="alert"` naming what failed, a Retry, and NOT the
//     empty state;
//   · the read resolves empty     → the empty state, and no alert;
//   · an answer is already in hand and its revalidation fails → the answer stays, and no alert.
//     A mount always revalidates, so a cached copy painted first and a read that then fails is
//     an ordinary visit, and an error gate that does not ask whether data is in hand blanks it.
//
// Then the reads each surface deliberately tolerates, from the other side: a peripheral read that
// fails must not take the page down, so a later pass cannot "finish the job" by making every read
// fatal.
//
// One gateway stand-in serves every row: each `api` method the row answers is a stub on the one
// `api` object every module holds, and every other method rejects, as an unreachable gateway would.
// The knowledge library reads its items through `knowledgeStore`, which is drawn at that seam.

const { store } = vi.hoisted(() => ({
  /** The knowledge store's answers for the row; empty when the row does not mount the library. */
  store: {} as Record<string, (...args: unknown[]) => Promise<unknown>>,
}))

vi.mock('./knowledge/knowledgeStore', async (orig) => {
  const real = await orig<Record<string, unknown>>()
  const seam = (name: string) => (...args: unknown[]) =>
    (store[name] ?? (real[name] as (...a: unknown[]) => Promise<unknown>))(...args)
  return { ...real, listKnowledge: seam('listKnowledge'), knowledgeStats: seam('knowledgeStats') }
})

type Answers = Record<string, (...args: unknown[]) => unknown>

const boom = () => Promise.reject(new Error('gateway down'))
const unanswered = () => Promise.reject(new Error('not answered in this test'))

/** Answer these `api` methods; every other one rejects. */
function answer(methods: Answers) {
  const methodsOf = api as unknown as Answers
  for (const k of Object.keys(methodsOf)) {
    if (typeof methodsOf[k] !== 'function') continue
    vi.spyOn(methodsOf, k).mockImplementation((k in methods ? methods[k] : unanswered) as Answers[string])
  }
  for (const k of Object.keys(methods)) if (!(k in methodsOf)) throw new Error(`api has no method ${k}`)
}

beforeEach(() => {
  for (const k of Object.keys(store)) delete store[k]
  sessionStorage.clear()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const nav = () => {}
const q = {}
const setQuery = () => {}

const nativeOk = { agents: [{ name: 'scout', model: 'x' }], default_agent: 'scout' }
const AGENTS: Answers = {
  agents: () => Promise.resolve(nativeOk),
  agentProviders: () => Promise.resolve([]),
  syncAgents: () => Promise.resolve({ ok: true, synced: [], skipped: [], unreadable: [], scanned: 0, message: 'Already up to date.' }),
}
const toolsIndex = { tools: [{ name: 'read_file', provider: 'native', risk: 'low' }], load_failures: [] }
const TOOLS: Answers = {
  toolsIndex: () => Promise.resolve(toolsIndex),
  mcpServers: () => Promise.resolve([]),
  importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
  mcpPoolStats: () => Promise.resolve({}),
  toolGroups: () => Promise.resolve(null),
  mcpElicitationServers: () => Promise.resolve([] as string[]),
}
const TRIGGERS: Answers = {
  schedules: () => Promise.resolve({ jobs: [] }),
  hooks: () => Promise.resolve([]),
  storeTriggers: () => Promise.resolve([]),
  callbacks: () => Promise.resolve([]),
  triggerReview: () => Promise.resolve([]),
  actionProviders: () => Promise.resolve([]),
  triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
}
const WEEK = {
  start: '2026-09-18T00:00:00', end: '2026-09-25T00:00:00', server_tz: 'UTC', truncated: [] as string[],
  // A fire at noon today, so it falls inside the week the grid draws.
  occurrences: [{ trigger_id: 'schedule:a', trigger_name: 'digest', at: startOfDay(new Date()).getTime() / 1000 + 12 * 3600, suppressed_by: '', reason: '' }],
}
const INBOX: Answers = {
  inbox: () => Promise.resolve([]),
  inboxStatus: () => Promise.resolve({ providers: [], enabled: false }),
  inboxKinds: () => Promise.resolve([]),
  inboxProviders: () => Promise.resolve([]),
  // The digest card mounts above the list. Resolved `uninstalled`, so it renders its install offer
  // and no alert of its own: a second `role="alert"` would make the row's one ambiguous.
  proactiveDigest: () => Promise.resolve({ state: 'uninstalled', enabled: false, installed: false, error: '' }),
}
const SKILLS: Answers = {
  skills: () => Promise.resolve([]),
  skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
  // The learning summary block reads this on mount; a zero total keeps the block absent.
  learningSummary: () => Promise.resolve({
    window_days: 7, total: 0,
    new_skills: { count: 0, names: [] }, refined_skills: { count: 0, names: [] },
    pending_proposals: { count: 0, names: [] }, facts: { count: 0, names: [] },
  }),
}
const KNOWLEDGE: Answers = {
  knowledgeCollections: () => Promise.resolve([]),
  knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0 }),
  // The embedding chip follows the one re-index, and reads whether one runs on mount.
  embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
}
const KNOWLEDGE_STORE = {
  listKnowledge: () => Promise.resolve([]),
  knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0, embedded: 0 }),
}

interface Surface {
  row: string
  mount: () => ReactElement
  /** Everything the page reads on mount, answered healthy and empty. */
  answers: Answers
  /** The collection read, failed. */
  fails: Answers
  /** The knowledge store's answers, when the page reads through it. */
  store?: Record<string, (...args: unknown[]) => Promise<unknown>>
  storeFails?: Record<string, (...args: unknown[]) => Promise<unknown>>
  /** What the alert names. */
  names: RegExp
  /** The empty state's heading, which a failed read must not show. */
  empty: string
  /** What else a failed read must not draw. */
  notDrawn?: () => void
  /** An answer already in hand, by cache key, and what it shows. */
  warm: { seed: Record<string, unknown>; shows: () => void }
}

const heading = (name: string) => () => expect(screen.getByRole('heading', { name })).toBeInTheDocument()
const text = (t: string) => () => expect(screen.getByText(t)).toBeInTheDocument()
/** The week grid's key for the week that holds today. */
const thisWeek = () => `triggers:week:${startOfDay(new Date()).toISOString().slice(0, 10)}`

const SURFACES: Surface[] = [
  {
    // The swallow was two layers deep: the adapter dropped the hook's `error`, and its fetcher's
    // `Promise.allSettled` never rejected, so a failed native read became `agents: []`.
    row: '#/agents, the native agents',
    mount: () => <AgentsListPage query={q} setQuery={setQuery} onCreate={() => {}} />,
    answers: { ...AGENTS, agents: () => Promise.resolve({ agents: [], default_agent: '' }) },
    fails: { agents: boom },
    names: /agents/i,
    empty: 'No native agents',
    warm: { seed: { 'agents:groups': [{ kind: 'native', agents: nativeOk.agents, defaultAgent: 'scout' }] }, shows: text('scout') },
  },
  {
    // Six reads in one fetcher, and the index is the collection: its `.catch(() => ({ tools: [] }))`
    // rendered the newcomer state to someone whose tools were all there.
    row: '#/tools, the tool index',
    mount: () => <ToolsPage query={q} setQuery={setQuery} />,
    answers: { ...TOOLS, toolsIndex: () => Promise.resolve({ tools: [], load_failures: [] }) },
    fails: { toolsIndex: boom },
    names: /tools/i,
    empty: 'No tools',
    warm: {
      seed: { 'tools:index': { tools: toolsIndex.tools, loadFailures: [], importable: [], importableError: '', importUnreadable: [], importTools: [], poolStats: {}, groups: null, elicitationServers: [] } },
      shows: text('read_file'),
    },
  },
  {
    // Every list source used to `.catch(() => [])`, so a gateway that was down composed the same
    // empty array a fresh home does.
    row: '#/triggers, every list source',
    mount: () => <TriggersSection sub="" navigate={nav} navEpoch={0} query={q} setQuery={setQuery} />,
    answers: TRIGGERS,
    fails: { schedules: boom, hooks: boom, storeTriggers: boom },
    names: /triggers/i,
    empty: 'No triggers',
    warm: { seed: { 'triggers:schedules': { jobs: [] }, 'triggers:hooks': [], 'triggers:store': [], 'triggers:callbacks': [] }, shows: heading('No triggers') },
  },
  {
    // It bound `{ data: week }` alone, so a failure stayed on "Projecting…" forever and drew 168
    // empty cells under a legend of four states none of them could be in.
    row: '#/triggers → Week, the projection',
    mount: () => <WeekGridView />,
    answers: { triggersWeek: () => Promise.resolve({ ...WEEK, occurrences: [] }) },
    fails: { triggersWeek: boom },
    names: /week/i,
    empty: 'No fires this week',
    warm: { seed: { [thisWeek()]: { ...WEEK, occurrences: [] } }, shows: heading('No fires this week') },
    notDrawn: () => {
      expect(document.querySelectorAll('td').length, 'no phantom grid under the error').toBe(0)
      expect(document.body.textContent, 'and it is no longer "projecting"').not.toMatch(/Projecting/)
    },
  },
  {
    // A 500 was pixel-identical to a healthy empty inbox: "Inbox zero — you're all caught up".
    // The heading here is "Nothing has arrived yet" because this inbox was never used; "Inbox
    // zero" is for a queue the user cleared, which `inboxBlankSlateTruth.test.tsx` owns.
    row: '#/inbox, the items',
    mount: () => <InboxPage query={q} setQuery={setQuery} navigate={nav} />,
    answers: INBOX,
    fails: { inbox: boom },
    names: /inbox/i,
    empty: 'Nothing has arrived yet',
    warm: { seed: { 'inbox:items': [] }, shows: heading('Nothing has arrived yet') },
    notDrawn: () => {
      expect(screen.queryByRole('heading', { name: /Inbox zero/ }), 'a failed read knows nothing about the queue').toBeNull()
    },
  },
  {
    row: '#/skills, the installed skills',
    mount: () => <SkillsPage query={q} setQuery={setQuery} />,
    answers: SKILLS,
    fails: { skills: boom },
    names: /skills/i,
    empty: 'No skills installed',
    warm: { seed: { skills: [] }, shows: heading('No skills installed') },
  },
  {
    row: '#/skills → Proposals',
    mount: () => <SkillProposals />,
    answers: SKILLS,
    fails: { skillProposals: boom },
    names: /proposals/i,
    empty: 'No skill proposals',
    warm: { seed: { 'skill-proposals': { proposals: [], lastReview: null } }, shows: heading('No skill proposals') },
  },
  {
    // Its search branch never caught, so a failure fell through `items === null` with
    // `itemsLoading` false and rendered a blank region.
    row: '#/knowledge, the library',
    mount: () => (
      <KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}}
        onOpenChat={() => {}} query={q} setQuery={setQuery} />
    ),
    answers: KNOWLEDGE,
    store: KNOWLEDGE_STORE,
    fails: {},
    storeFails: { listKnowledge: boom },
    names: /knowledge items/i,
    empty: 'Knowledge base is empty',
    warm: { seed: { 'knowledge:items::': [] }, shows: heading('Knowledge base is empty') },
  },
]

/** Mount the surface with its reads healthy and empty, or with its collection read failed; the
 *  count is of failed reads made. */
function stage(s: Surface, failed: boolean): { calls: number } {
  const made = { calls: 0 }
  const counted = <F extends (...args: unknown[]) => unknown>(fns: Record<string, F>) =>
    Object.fromEntries(Object.entries(fns).map(([k, fn]) => [k, (...a: unknown[]) => { made.calls++; return fn(...a) }]))
  answer(failed ? { ...s.answers, ...counted(s.fails) } : s.answers)
  Object.assign(store, s.store ?? {}, failed ? counted(s.storeFails ?? {}) : {})
  render(s.mount())
  return made
}

describe('a failed collection read says so, and an empty one is the empty state', () => {
  it('covers every surface', () => {
    expect(SURFACES.length).toBe(8)
  })

  it.each(SURFACES.map((s) => [s.row, s] as const))('%s: a failed read is a retryable LoadError', async (_, s) => {
    stage(s, true)
    // `role="alert"` is LoadError's signature; the empty state has no live region, which is why
    // the old behaviour was unreadable to a screen reader as well as to an eye.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(s.names)
    expect(screen.getByRole('button', { name: /Retry/ }), 'and offers a way back').toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: s.empty }), 'not the empty state').toBeNull()
    s.notDrawn?.()
  })

  it.each(SURFACES.map((s) => [s.row, s] as const))('%s: an empty read is the empty state', async (_, s) => {
    stage(s, false)
    await waitFor(() => expect(screen.getByRole('heading', { name: s.empty })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty collection is not an error').toBeNull()
  })

  it.each(SURFACES.map((s) => [s.row, s] as const))('%s: an answer in hand stays when its revalidation fails', async (_, s) => {
    for (const [key, value] of Object.entries(s.warm.seed)) writeQuery(key, value)
    const made = stage(s, true)
    await waitFor(() => expect(made.calls, 'the mount revalidated').toBeGreaterThan(0))
    // Let the rejection land and the surface render what it does with it.
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    s.warm.shows()
    expect(screen.queryByRole('alert'), 'a failed revalidation does not blank what is in hand').toBeNull()
  })
})

describe('the reads a surface tolerates', () => {
  it('#/agents: a failed provider read still renders the native list', async () => {
    // An unreachable runtime must not take the page down: showing it as an unready group is
    // that surface's whole job.
    answer({ ...AGENTS, agentProviders: boom })
    render(<AgentsListPage query={q} setQuery={setQuery} onCreate={() => {}} />)
    await waitFor(() => expect(screen.getByText('scout')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a provider outage is not a page failure').toBeNull()
  })

  it('#/tools: failed peripheral reads still render the tools', async () => {
    // A dead MCP server, an unreachable pool or a missing groups config must not hide the
    // built-in tools.
    answer({ ...TOOLS, mcpServers: boom, importableMcp: boom, mcpPoolStats: boom, toolGroups: boom })
    render(<ToolsPage query={q} setQuery={setQuery} />)
    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a peripheral read is not a page failure').toBeNull()
  })

  it('#/triggers: one failed list source is a LoadError, because the list cannot be completed', async () => {
    // `triggers` composes once all three list sources answer, and the flag is `triggers === null
    // && anyError`: with the store feed failed alone the list stays null, and the honest call is
    // to say so rather than draw the schedules as though they were all there is.
    answer({ ...TRIGGERS, storeTriggers: boom })
    render(<TriggersSection sub="" navigate={nav} navEpoch={0} query={q} setQuery={setQuery} />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})

describe('the week grid between the two states', () => {
  it('still says "Projecting…" while the read is in flight', async () => {
    // A promise that never settles is the honest model of "in flight": the state the old code was
    // stuck in must stay reachable for the case that really is loading.
    answer({ triggersWeek: () => new Promise(() => {}) })
    render(<WeekGridView />)
    await waitFor(() => expect(screen.getByText(/Projecting/)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a pending read is not an error').toBeNull()
  })

  it('draws the grid when the read lands with fires', async () => {
    answer({ triggersWeek: () => Promise.resolve(WEEK) })
    render(<WeekGridView />)
    // 🪤 Waited on the summary, not on the cells: the grid is drawn, empty, while the read is in
    // flight, so cells alone are true before anything has landed.
    await waitFor(() => expect(document.body.textContent, 'the summary replaced the projecting label').not.toMatch(/Projecting/))
    expect(document.querySelectorAll('td').length).toBeGreaterThan(0)
    expect(screen.queryByRole('heading', { name: 'No fires this week' }), 'the fire is drawn').toBeNull()
    expect(screen.queryByRole('alert'), 'a healthy week is not an error').toBeNull()
  })
})

describe("#/agents: a runtime whose agents are not known says why, and shows no count", () => {
  // The agents a runtime offers are read in the session its Test opened. A read that failed was
  // dropped by `Promise.allSettled` and the group fell back to `[]`, so the page said "No agents
  // discovered." about a runtime whose agents nobody could read; and its header read "0" for a
  // runtime nobody had tried, one that needs a sign-in and one that could not be listed alike.
  const runtime = (over: Record<string, unknown> = {}) => ({
    name: 'acp:demo-cli', provider_id: 'acp:demo-cli', type: 'acp_agent', extension: 'demo-cli-agent',
    ready: true, state: 'ready', detail: '', login_command: null, tested_at: '2026-09-28T10:00:00+00:00', ...over,
  })
  const reason = "Couldn't list the agents Demo Cli offers: its last Test got an answer that could not be read: bad shape."
  const mount = (over: Answers) => {
    answer({ ...AGENTS, ...over })
    render(<AgentsListPage query={q} setQuery={setQuery} onCreate={() => {}} />)
  }
  // providerMeta's label for an `acp:<cli>` runtime it has no brand for: the cli name, title-cased.
  const header = async () => (await screen.findByText('Demo Cli', { selector: 'span' })).parentElement as HTMLElement
  const countIn = (row: HTMLElement) => row.querySelector('.tabular-nums')?.textContent ?? null

  it('a failed listing shows the reason in place of "No agents discovered."', async () => {
    mount({ agentProviders: () => Promise.resolve([runtime()]), agentProviderAgents: () => Promise.reject(new Error(reason)) })
    const alert = await waitFor(() => screen.getByText(reason))
    expect(alert.getAttribute('role')).toBe('alert')
    expect(screen.queryByText('No agents discovered.'), 'a failed read is not an empty catalog').toBeNull()
  })

  it('a runtime that really offers none still says "No agents discovered.", with its count', async () => {
    mount({
      agentProviders: () => Promise.resolve([runtime()]),
      agentProviderAgents: () => Promise.resolve({ agents: [], permission_modes: [], tested_at: '2026-09-28T10:00:00+00:00' }),
    })
    await waitFor(() => expect(screen.getByText('No agents discovered.')).toBeInTheDocument())
    expect(screen.queryByText(reason)).toBeNull()
    expect(countIn(await header())).toBe('0')
  })

  it.each([
    ['untested', 'Not tried yet. PersonalClaw starts it only when you press Test (Settings → Providers).', 'not tried yet'],
    ['needs_login', 'demo-cli: sign in first', 'unavailable'],
  ])('%s: the reason in place of a count', async (state, detail, badge) => {
    mount({ agentProviders: () => Promise.resolve([runtime({ ready: false, state, detail, tested_at: null })]) })
    await waitFor(() => expect(screen.getByText(detail)).toBeInTheDocument())
    const row = await header()
    expect(countIn(row), 'no count for agents nobody listed').toBeNull()
    expect(row.textContent).toContain(badge)
  })
})
