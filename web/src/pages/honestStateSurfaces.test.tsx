import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { api, type FeedbackProducerRow } from '../lib/api'
import { writeQuery } from '../lib/data'
import { ContextDisclosure } from '../ui/composer/ContextDisclosure'
import { AgentsListPage } from './agents/AgentsListPage'
import { ChatHistoryPage } from './chat/history/ChatHistoryPage'
import { ChatHistorySidePanelBody } from './chat/history/ChatHistorySidePanel'
import { KnowledgeListPage } from './knowledge/KnowledgeListPage'
import { ContrastView, TokensView } from './loops/DesignCockpitPage'
import { AgentDefaultsPanel } from './settings/AgentDefaultsPanel'
import { AmbientPanel } from './settings/AmbientPanel'
import { EvalsPanel } from './settings/EvalsPanel'
import { FeedbackPanel } from './settings/FeedbackPanel'
import { GuardrailsPanel } from './settings/GuardrailsPanel'
import { LegibilityPanel } from './settings/LegibilityPanel'
import { MemoryPanel } from './settings/MemoryPanel'
import { RemoteModelProviders } from './settings/ModelBackends'
import { NotificationsPanel } from './settings/NotificationsPanel'
import { ProjectionRulesPanel } from './settings/ProjectionRulesPanel'
import { RoutingPanel } from './settings/RoutingPanel'
import { UpdatesPanel } from './settings/UpdatesPanel'
import { VoicePanel } from './settings/VoicePanel'
import { TaskDetail } from './tasks/TaskDetail'

// ── A surface says only what it knows ──────────────────────────────────────────────────────────
//
// Each of these surfaces once stated something nobody had measured, or that a failed read had
// made up: a pill saying a producer "stopped surfacing" when nothing about it stopped, a chip
// saying semantic search was off (with advice to go and fix it) because the stats read failed,
// "Loading tokens…" forever after the token read had already failed, a runner row reading "0 ms"
// for a handshake nobody timed. Each is asserted where a user meets it, in both directions: the
// claim is gone when it is false, and still there when it is true, or the first half proves
// nothing.
//
// One gateway stand-in serves every case: each `api` method a case answers is a stub on the one
// `api` object every module holds, and every other method rejects, as an unreachable gateway
// would. The source-side pins of the same family are `pages/honestStateSources.test.ts`'s.

vi.mock('../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify: vi.fn() }))

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

beforeEach(() => { sessionStorage.clear(); localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

// ── Feedback: a pill that says "Stopped surfacing" must describe something that stopped ─────────
//
// Feedback-Signal computes a withholding set over all six producer kinds, but only a kind with a
// surfacing gate can act on it: `skills.surfacing` withholds a matched skill. The other five keep
// surfacing and earn the retire PROPOSAL only. The route used to set `suppressed: true` for any
// below-threshold producer of ANY kind, and the panel rendered that as a red pill titled "Stopped
// surfacing", so for five of six kinds it asserted an effect that never happened. The route-shape
// half is `tests/test_feedback_suppression_enforcement.py`'s.

describe('the feedback panel claims "stopped surfacing" only where something stops', () => {
  const UNENFORCED: FeedbackProducerRow = {
    producer_kind: 'prompt', producer_id: 'task-inbox-classify',
    ups: 3, downs: 6, n: 9, accuracy: 0.333, proposal_only: true,
  }
  const ENFORCED: FeedbackProducerRow = {
    producer_kind: 'skill_synthesis', producer_id: 'some-skill',
    ups: 1, downs: 8, n: 9, accuracy: 0.111, suppressed: true,
  }
  const mount = (...producers: FeedbackProducerRow[]) => {
    answer({
      feedbackProducers: () => Promise.resolve({ producers, min_n: 5, window_days: 90 }),
      // The Tuning section renders below the table and reads the four `feedback.*` knobs.
      personalclawConfig: () => Promise.resolve({ feedback: { enabled: true, min_n: 5, window_days: 90, retire_threshold: 0.4 } }),
    })
    render(<FeedbackPanel />)
  }

  it('a below-threshold producer with NO surfacing gate reads "retire proposed", never "suppressed"', async () => {
    mount(UNENFORCED)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.getByText('retire proposed')).toBeTruthy()
    expect(
      screen.queryByText('suppressed'),
      'a prompt producer is still injected verbatim; calling it suppressed says its output stopped',
    ).toBeNull()
  })

  it('the honest pill explains WHY it still runs', async () => {
    // Without the reason a user reads "retire proposed" as a synonym for suppressed.
    mount(UNENFORCED)
    const pill = await waitFor(() => screen.getByText('retire proposed'))
    expect(pill.getAttribute('title') ?? '').toMatch(/no surfacing gate|still runs/i)
  })

  it('an ENFORCED kind below threshold DOES read "suppressed"', async () => {
    // Under-reporting a real withholding leaves a user unable to explain why a skill stopped
    // appearing, which is the more dangerous direction.
    mount(ENFORCED)
    await waitFor(() => expect(screen.getByText('some-skill')).toBeTruthy())
    expect(screen.getByText('suppressed')).toBeTruthy()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })

  it('the two states never render on one row', async () => {
    mount({ ...UNENFORCED, suppressed: false, proposal_only: true })
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryAllByText(/^(suppressed|retire proposed)$/)).toHaveLength(1)
  })

  it('a healthy producer claims neither', async () => {
    mount({ ...UNENFORCED, accuracy: 0.95, proposal_only: undefined, suppressed: false })
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryByText('suppressed')).toBeNull()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })

  it('the panel HEADER does not promise a withholding only one kind gets', async () => {
    // On an empty panel the header is the ONLY thing said on the subject, so an unconditional
    // "keeps being wrong stops surfacing" would reproduce the overclaim the pills were rewritten
    // to avoid.
    mount()
    const hint = await waitFor(() => screen.getByText(/attributed to the source that produced it/))
    expect(hint.textContent ?? '').toMatch(/asks to be reviewed/)
    expect(hint.textContent ?? '', 'unconditional "stops surfacing" holds for one of six kinds')
      .not.toMatch(/keeps being wrong stops surfacing/)
  })
})

// ── Knowledge: a failed stats read must not claim a capability is off, least of all with advice ──
//
// `knowledgeStats()` used to end `.catch(() => ({ items: 0, entities: 0, relations: 0, embeddings:
// { enabled: false } }))`, turning a failed GET into four confident statements. Three were wrong
// counts. The fourth was worse: the embedding chip rendered "semantic search off", titled "No
// embedding model active — … Set one in Settings › AI & Models", so an unreachable gateway sent the
// user to reconfigure a setting that may already have been right. And the fabricated zero emptied
// a populated library: `empty` is `stats && stats.items === 0`, and it gates both the "Knowledge
// base is empty" card and the Home shelves.
//
// 🪤 `empty` STAYS ON `stats.items`. `items` is filtered by search and shelf, so a no-match search
// keyed on it would claim the library was empty (`libraryHomeReachable.test.tsx` caught that).

describe('the knowledge stats strip never states a capability it could not read', () => {
  const okStats = { items: 3, entities: 9, relations: 4, embeddings: { enabled: true, embedded_items: 3, model: 'm' } }
  const mount = (over: Answers = {}) => {
    answer({
      knowledgeStats: () => Promise.resolve(okStats),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeIntents: () => Promise.resolve([]),
      ...over,
    })
    render(
      <KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}}
        onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />,
    )
  }

  it('a failed stats read renders no "semantic search off" claim and no advice', async () => {
    mount({ knowledgeStats: boom })
    // 🪤 WAIT ON A POSITIVE SIGNAL FIRST, NEVER ON THE ABSENCE ITSELF: a `waitFor` on the absence
    // succeeds on its first check, before the read could ever have produced the text. The title is
    // there whichever way either read goes.
    await waitFor(() => expect(screen.getByText('Knowledge')).toBeInTheDocument())
    await waitFor(() => expect(api.knowledgeStats).toHaveBeenCalled())
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(screen.queryByText(/semantic search off/i), 'a failed read must not claim the capability is off').toBeNull()
    expect(screen.queryByText(/No embedding model active/i), 'and the advice must not appear either').toBeNull()
    expect(screen.queryByText(/Set one in Settings/i)).toBeNull()
  })

  it('a SUCCESSFUL read still renders the strip, or the case above proves nothing', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('entities')).toBeInTheDocument())
    expect(screen.getByText('relations')).toBeInTheDocument()
  })

  it('a populated library is NOT called empty when the stats read fails', async () => {
    mount({ knowledgeStats: boom, knowledgeItems: () => Promise.resolve({ items: [{ id: 'a', title: 'Real note', type: 'note' }] }) })
    await waitFor(() => expect(screen.getByText('Real note')).toBeInTheDocument())
    expect(screen.queryByText('Knowledge base is empty'), 'a stats outage must not empty a populated library').toBeNull()
  })

  it('and a genuinely DISABLED embedding model still says so: the claim is real then', async () => {
    mount({ knowledgeStats: () => Promise.resolve({ ...okStats, embeddings: { enabled: false } }) })
    await waitFor(() => expect(screen.getByText(/semantic search off/i)).toBeInTheDocument())
  })
})

// ── Design tokens: "not read yet" is not "could not be read" ────────────────────────────────────
//
// The cockpit loaded its token set with `.catch(() => {})`, and five consumers inferred their
// whole state from `tokens === null`: both tabs said "Loading tokens…" FOREVER, one export was
// dead with no reason given, and two exports handed over a well-formed file with the wrong
// content. A permanent "Loading…" is the worst of the three possible messages: it is false, it
// blames the network, and it gives the user nothing to do.

describe('a tokens view distinguishes "not read yet" from "could not be read"', () => {
  const okTokens = {
    scheme: 'light',
    css: ':root{--x:1}',
    resolved: { color: { semantic: { light: { primary: '#ff6b5b' } } } },
  } as never

  it('an in-flight read still says Loading: the honest state, unchanged', () => {
    render(<TokensView tokens={null} scheme="light" />)
    expect(screen.getByText('Loading tokens…')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a FAILED read stops saying Loading and offers the retry it never had', () => {
    const onRefresh = vi.fn()
    render(<TokensView tokens={null} tokensErr={new Error('gateway down')} onRefresh={onRefresh} scheme="light" />)
    expect(screen.queryByText('Loading tokens…'), 'a finished request is not still loading').toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Couldn’t read this design system’s tokens/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }))
    expect(onRefresh, 'without this the honest message is a dead end').toHaveBeenCalled()
  })

  it('the Contrast tab gets the same treatment: it had the identical lie', () => {
    render(<ContrastView tokens={null} tokensErr={new Error('gateway down')} onRefresh={vi.fn()} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('a SUCCESSFUL read renders the real view, or the cases above prove nothing', () => {
    render(<TokensView tokens={okTokens} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

// ── Settings → Agent defaults: the runner rows ──────────────────────────────────────────────────
//
// Two things no python test can see, asserted where a user meets them:
//
//  · the VERBATIM probe error reaches the screen. A row reading "unavailable" while the API
//    carried "'gemini' not found on PATH …" throws away the only part of the message that says
//    which binary to install;
//  · an UNMEASURED value renders as unknown, never as a number. `latency_ms: null` must not
//    become "0 ms", which reads as "the handshake was instant"; the same goes for an unparseable
//    version and a never-recorded capability matrix.

describe('the runner rows in Settings → Agent defaults', () => {
  const VERBATIM = "'gemini' not found on PATH (looked for: gemini); set GEMINI_CLI_EXECUTABLE to override"
  const unhealthyRow = {
    id: 'gemini-cli', display_name: 'Gemini CLI', runtime_id: 'acp:gemini-cli', source: 'builtin',
    dialect: '', bin_names: ['gemini'], version_args: ['--version'], set_up: false,
    waiting: false, allow_revision: '',
    health: {
      ok: false, probe: 'path', checked_at: '2026-08-17T10:00:00+00:00',
      version: null, latency_ms: null, error: VERBATIM, resolved_command: [],
    },
    health_stale: false,
    capabilities: null,
    adapter: { npm_pkg: '', pinned: false, state: 'no_adapter', verified: true, detail: 'launches its own binary' },
    lease: null,
  }
  const healthyRow = {
    id: 'claude-code', display_name: 'Claude Code', runtime_id: 'acp:claude-code', source: 'builtin',
    dialect: 'claude-code', bin_names: ['claude'], version_args: ['--version'], set_up: true,
    waiting: false, allow_revision: '',
    health: {
      ok: true, probe: 'version', checked_at: '2026-08-17T10:00:00+00:00',
      version: '2.1.233', latency_ms: 58, error: null, resolved_command: ['/usr/bin/claude'],
    },
    health_stale: false,
    capabilities: {
      source: 'initialize', recorded_at: '2026-08-17T10:00:00+00:00',
      models: ['m1', 'm2'], permission_modes: ['default', 'acceptEdits'], efforts: ['low', 'high'],
    },
    adapter: { npm_pkg: '@x/adapter', pinned: false, state: 'unverified', verified: false, detail: 'no recorded provenance' },
    lease: null,
  }
  // A definition of yours that waits: it came from another machine, or changed since. Its CLI
  // runs for nothing, a Check included, until you allow what it runs here.
  const waitingRow = {
    ...healthyRow, id: 'my-cli', display_name: 'My CLI', runtime_id: 'acp:claude-code',
    source: 'user', bin_names: ['my-cli'], waiting: true, allow_revision: 'rev-1',
  }
  const checkAgentRunner = vi.fn()
  const allowAgentRunner = vi.fn()
  const mount = (rows: unknown, over: Answers = {}) => {
    checkAgentRunner.mockReset()
    allowAgentRunner.mockReset()
    answer({
      agentRunners: typeof rows === 'function' ? rows as Answers[string] : () => Promise.resolve(rows),
      checkAgentRunner: (...a) => checkAgentRunner(...a),
      allowAgentRunner: (...a) => allowAgentRunner(...a),
      personalclawConfig: () => Promise.resolve({ agent: { unattended_requires_verified_adapter: false } }),
      // The Reply style row's list.
      replyStyles: () => Promise.resolve({ styles: [], precedence: [] }),
      // The agent picker's catalog, and the durable-workers hint's ask of the host.
      savedAgents: () => Promise.resolve([]),
      agentsInstalled: () => Promise.resolve([]),
      agentProviders: () => Promise.resolve([]),
      agents: () => Promise.resolve({ agents: [], default_agent: '' }),
      terminalSessions: () => Promise.resolve({ persist_available: true, sessions: [] }),
      ...over,
    })
    render(<AgentDefaultsPanel />)
  }
  const both = [healthyRow, unhealthyRow]

  // What runs, and when: a runner's CLI runs only for its own Check. "Re-check runners" used to
  // run `<bin> --version` for EVERY catalogued CLI on PATH, and the button did not say so.
  it('reading the runners runs no CLI, and nothing offers to run every CLI at once', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(api.agentRunners).toHaveBeenCalled()
    expect(checkAgentRunner).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /re-check/i })).toBeNull()
  })

  it("a set-up runner's Check says what it runs, and runs it for that runner alone", async () => {
    mount(both)
    checkAgentRunner.mockResolvedValue({ ...healthyRow, health: { ...healthyRow.health, version: '2.2.0' } })
    const check = await screen.findByRole('button', { name: 'Check version: Claude Code' })
    expect(check.getAttribute('title')).toBe('Runs claude --version')
    expect(screen.getByText('claude --version')).toBeTruthy()
    await act(async () => { fireEvent.click(check) })
    await waitFor(() => expect(screen.getByText('v2.2.0')).toBeTruthy())
    expect(checkAgentRunner).toHaveBeenCalledTimes(1)
    expect(checkAgentRunner).toHaveBeenCalledWith('claude-code')
  })

  it('a runner definition that waits offers Allow and no Check, and says why', async () => {
    mount([waitingRow])
    await screen.findByRole('button', { name: 'Allow: My CLI' })
    expect(screen.getByText('waiting for you')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Check version: My CLI' })).toBeNull()
    expect(screen.getByText(/came from another machine, or changed since you allowed it/)).toBeTruthy()
    expect(checkAgentRunner).not.toHaveBeenCalled()
  })

  it('Allow names the revision it was shown, and the allowed row replaces the waiting one', async () => {
    mount([waitingRow])
    allowAgentRunner.mockResolvedValue({ ...waitingRow, waiting: false, allow_revision: '' })
    const allow = await screen.findByRole('button', { name: 'Allow: My CLI' })
    await act(async () => { fireEvent.click(allow) })
    await screen.findByRole('button', { name: 'Check version: My CLI' })
    expect(allowAgentRunner).toHaveBeenCalledWith('my-cli', 'rev-1')
    expect(screen.queryByText('waiting for you')).toBeNull()
  })

  it('a runner that does not wait offers no Allow', async () => {
    // VACUITY FLOOR: an Allow on every row would ask the owner about shipped runners too.
    mount(both)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /^Allow:/ })).toBeNull()
    expect(screen.queryByText('waiting for you')).toBeNull()
  })

  it('a runner nothing set up offers no Check and says PersonalClaw never runs it', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Check version: Gemini CLI' })).toBeNull()
    expect(screen.getByText('Not set up here — no installed agent app runs this CLI, so PersonalClaw never runs it.')).toBeTruthy()
  })

  it('prints the probe error verbatim, not a house paraphrase', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText(VERBATIM)).toBeTruthy())
  })

  it('renders an unmeasured latency and version as unknown, never as a number', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText('latency unknown')).toBeTruthy())
    expect(screen.getByText('version unknown')).toBeTruthy()
    // The fabrication this guards: `latency_ms: null` rendered through a `?? 0`.
    expect(screen.queryByText('0 ms')).toBeNull()
  })

  it('shows measured evidence and capability chips for a healthy runner', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText('v2.1.233')).toBeTruthy())
    expect(screen.getByText('58 ms')).toBeTruthy()
    expect(screen.getByText('acceptEdits')).toBeTruthy()
    expect(screen.getByText('2 models')).toBeTruthy()
  })

  it('marks an overdue check without contradicting the reading itself', async () => {
    // "healthy" was the measurement; "check overdue" says it is older than
    // agent.runner_health_check_secs, so a stale value is not read as the present state.
    mount([{ ...healthyRow, health_stale: true }])
    await waitFor(() => expect(screen.getByText('check overdue')).toBeTruthy())
    expect(screen.getByText('healthy')).toBeTruthy()
  })

  it('does not mark a fresh or unknown-age check overdue', async () => {
    // `false` states freshness and `null` is "we do not know the age"; neither may wear the chip,
    // or every row wears it forever.
    mount([healthyRow, { ...unhealthyRow, health_stale: null }])
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText('check overdue')).toBeNull()
  })

  it('says capabilities are unknown when no handshake was ever recorded', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText(/Capabilities unknown/)).toBeTruthy())
  })

  it('surfaces each row adapter verdict so the gate is legible before it fires', async () => {
    mount(both)
    await waitFor(() => expect(screen.getByText('adapter unverified')).toBeTruthy())
    expect(screen.getByText('no recorded provenance')).toBeTruthy()
  })

  it('renders the failure instead of an empty runner list when the read fails', async () => {
    // A swallowed rejection would paint "No runners in the catalog": "you have none" rather than
    // "we could not ask".
    mount(boom)
    await waitFor(() => expect(screen.getByText(/Couldn't load your runners/i)).toBeTruthy())
    expect(screen.queryByText(/No runners in the catalog/)).toBeNull()
  })

  // "Its lease holder is visible in Settings" is a SURFACE claim, so it is asserted on rendered
  // text: a python test only proves the endpoint carries `lease`.
  const held = { holder: 'chat:web:alice', taken_at: 0, expires_at: 0, renewals: 2, age_secs: 42, expires_in_secs: 1758 }

  it('names the session holding a runner, and says when idle-release takes it back', async () => {
    mount([{ ...healthyRow, lease: held }, unhealthyRow])
    await waitFor(() => expect(screen.getByText('held by chat:web:alice')).toBeTruthy())
    // Not just WHO but for how long, and until when: "held by X" alone does not say whether to
    // wait or to go and look at that session.
    expect(screen.getByText('held for 42s')).toBeTruthy()
    expect(screen.getByText('released in 1758s if idle')).toBeTruthy()
    expect(screen.getByText('healthy'), 'a held runner is still a healthy one').toBeTruthy()
  })

  it('shows no holder for a free runner', async () => {
    // VACUITY FLOOR: a chip rendered unconditionally would make every runner look taken.
    mount(both)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText(/held by/)).toBeNull()
    expect(screen.queryByText(/released in/)).toBeNull()
  })
})

// ── Settings: a panel whose gating read failed says so, and draws no form from it ──────────────
//
// Each of these panels reads what its form shows, and each once answered a failed read with a
// form anyway or with a skeleton forever. A substituted `{}` passes an `if (!data)` gate and draws
// every control at its fallback, indistinguishable from "this is what you saved", so a user could
// "correct" a setting whose stored value was never read (every voice control PUTs on change). A
// substituted `null` cannot be told from "still loading", so the panel shimmers forever. Measured
// with the read at 500 and a cold cache: 9, 6, 6 and 2 editable controls on the agent, ambient,
// guardrails and legibility panels, and none of them said a thing.
//
// The swallow itself is the load-error census's to count; what is per panel is that the failure
// is said, in the panel's own words, and that no control is drawn from a read that did not land.
// A read that is only a panel's SECOND one fails on its own: the per-kind notification rules say
// they could not be read, and the global switches above them stay.

/** The controls a form draws: none may be drawn from a read that failed. */
const controls = (root: HTMLElement) => root.querySelectorAll('input, select, textarea, [role="switch"], [role="combobox"]').length

// Every other read on the page rejects too, as it would with the gateway down; `fails` is the read
// the row is about, so the message it shows is that read's own.
const SETTINGS: Array<{ row: string; mount: () => ReactElement; fails: string; answers?: Answers; says: RegExp; keepsControls?: boolean }> = [
  { row: 'agent defaults, a failed config read', mount: () => <AgentDefaultsPanel />, fails: 'personalclawConfig', says: /Couldn't load your settings/ },
  { row: 'ambient, a failed config read', mount: () => <AmbientPanel />, fails: 'personalclawConfig', says: /Couldn't load your settings/ },
  { row: 'evals, a failed config read', mount: () => <EvalsPanel />, fails: 'personalclawConfig', says: /Couldn't load your settings/ },
  { row: 'guardrails, a failed config read', mount: () => <GuardrailsPanel />, fails: 'personalclawConfig', says: /Couldn't load your settings/ },
  { row: 'legibility, a failed config read', mount: () => <LegibilityPanel />, fails: 'personalclawConfig', says: /Couldn't load your settings/ },
  { row: 'notifications, a failed settings read', mount: () => <NotificationsPanel />, fails: 'notificationSettings', says: /Couldn't load your notification settings/ },
  {
    row: 'notifications, a failed rules read alone',
    mount: () => <NotificationsPanel />,
    fails: 'notificationRules',
    answers: {
      notificationSettings: () => Promise.resolve({
        mute_all: false, min_severity: 'info', quiet_hours_enabled: false, quiet_hours_start: '22:00', quiet_hours_end: '07:00',
      }),
    },
    says: /Couldn't load your per-kind notification rules/,
    keepsControls: true,
  },
  { row: 'updates, a failed status read', mount: () => <UpdatesPanel />, fails: 'updateCheck', says: /Couldn't load your update status/ },
  { row: 'speech, a failed settings read', mount: () => <VoicePanel />, fails: 'useCaseSettings', says: /Couldn't load your speech settings/ },
  { row: 'memory → Audit, a failed audit read', mount: () => <MemoryPanel query={{ tab: 'audit' }} setQuery={() => {}} />, fails: 'memoryEvents', says: /Couldn't load your memory audit log/ },
  { row: 'model providers, a failed instance read', mount: () => <RemoteModelProviders onChanged={() => {}} />, fails: 'modelProviders', says: /Couldn't load your model provider instances: gateway down/ },
]

describe('a settings panel says its read failed, and draws nothing from it', () => {
  it.each(SETTINGS.map((r) => [r.row, r] as const))('%s', async (_, r) => {
    answer({ ...r.answers, [r.fails]: boom })
    const { container } = render(r.mount())
    const said = await waitFor(() => screen.getByText(r.says))
    // The panel's own failure, whatever else on the page could not be read.
    const box = said.closest('[role="alert"]')
    expect(box, 'it is announced').not.toBeNull()
    expect(within(box as HTMLElement).getByRole('button', { name: /Retry/ }), 'with a way back').toBeInTheDocument()
    if (r.keepsControls) expect(controls(container), 'the global switches above stay').toBeGreaterThan(0)
    else expect(controls(container), 'no control is drawn from a read that did not land').toBe(0)
  })
})

describe('the guardrails reads state their failure instead of inventing one', () => {
  // The incident read used to substitute `{ active: false }`: "no incident is active", a claim about
  // the control that suspends all unattended work, with its switch ENABLED while its true state was
  // unknown. And a failed health read said "No background model calls recorded yet".
  const mount = () => {
    answer({ personalclawConfig: () => Promise.resolve({ guardrails: {} }), incident: boom, modelsHealth: boom })
    render(<GuardrailsPanel />)
  }

  it('the incident hint says the state is unknown, and the switch stays off-limits', async () => {
    mount()
    await waitFor(() => expect(screen.getByText(/Couldn't check whether incident mode is active: gateway down/)).toBeInTheDocument())
    const toggle = screen.getByRole('switch', { name: 'Incident mode' })
    expect(toggle, 'a switch whose state is unknown offers no direction').toBeDisabled()
  })

  it('provider health says its read failed, announced, because it replaces what was being read', async () => {
    mount()
    const alert = await waitFor(() => screen.getByText(/Couldn't check provider health: gateway down/))
    expect(alert.closest('[role="alert"]')).not.toBeNull()
    expect(screen.queryByText(/No background model calls recorded yet/)).toBeNull()
  })
})

describe("a task's comment thread that failed to load is not an empty thread", () => {
  // `Comments` loaded with `.catch(() => setComments([]))`, so a 500 rendered as "this task has no
  // comments": a thread the user had written into read as blank, with no way to retry.
  const task = { id: 't1', title: 'Rebuild the stall layout', status: 'todo' } as never
  const mount = (taskComments: Answers[string]) => {
    answer({ taskComments })
    render(<TaskDetail task={task} onSaved={() => {}} onDeleted={() => {}} editing={false} onEditingChange={() => {}} />)
  }

  it("says so in words, with the server's reason and a retry, and a good retry clears it", async () => {
    let calls = 0
    mount(() => (calls++ === 0 ? boom() : Promise.resolve([{ id: 'c1', author: 'me', body: 'First note', created_at: '2026-09-26T10:00:00Z' }])))
    const line = await waitFor(() => screen.getByText("Couldn't load the comments: gateway down"))
    await act(async () => { fireEvent.click(within(line.closest('[role="alert"]') ?? line.parentElement!).getByRole('button', { name: /Retry/ })) })
    await waitFor(() => expect(screen.getByText('First note')).toBeInTheDocument())
    expect(screen.queryByText(/Couldn't load the comments/), 'a good read clears the failure').toBeNull()
  })
})

describe('copy that says what the surface does', () => {
  it("#/agents: the Native caption names both populations and what each can do", async () => {
    // The group renders reserved built-ins (definition fixed) and the agents you made (editable)
    // together, and its caption used to read "Your PersonalClaw agent definitions — fully
    // editable.": a blanket promise the rows under it contradicted with their own Lock chips.
    answer({
      agents: () => Promise.resolve({
        agents: [{ name: 'PersonalClaw', model: 'x', reserved: true, editable: false }, { name: 'scout', model: 'x' }],
        default_agent: 'PersonalClaw',
      }),
      agentProviders: () => Promise.resolve([]),
    })
    render(<AgentsListPage query={{}} setQuery={() => {}} onCreate={() => {}} />)
    await waitFor(() => expect(screen.getByText('scout')).toBeInTheDocument())
    expect(screen.getByText('Built-ins run the platform — definition fixed, model swappable. Agents you create are fully editable.')).toBeInTheDocument()
    expect(screen.queryByText(/definitions\s*—\s*fully editable/i)).toBeNull()
  })

  it('#/settings/routing: the header does not deny the controls below it', async () => {
    // It opened with "Observation only: this does not change routing (that's a later
    // capability)", about 350px above a Mode select, a Pin select and a reorder list that all
    // persist. The policy section's own rows are `routingReorderAnnounced.test.tsx`'s.
    answer({})
    render(<RoutingPanel query={{}} setQuery={() => {}} />)
    const hint = await waitFor(() => screen.getByText(/Real per-model efficiency/))
    expect(hint.textContent, 'it points at where the decision is made').toMatch(/Routing policy, below, turns that observation into a decision/)
    expect(hint.textContent).not.toMatch(/Observation only|does not change routing|a later capability/i)
  })

  it('#/settings/tool-output: an unread rule list is a skeleton, not "No custom rules"', async () => {
    // It rendered `rules ?? []`, so every cold open asserted "No custom rules" before the config
    // arrived.
    answer({ projectionRules: () => new Promise(() => {}) })
    render(<ProjectionRulesPanel />)
    await waitFor(() => expect(api.projectionRules).toHaveBeenCalled())
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(screen.queryByText(/No custom rules/)).toBeNull()
    expect(document.querySelector('[aria-busy="true"]'), 'it says it is still reading').not.toBeNull()
  })
})

// ── The chat list: telling an account with 31 sessions that it has none ────────────────────────
//
// Driven with `/api/chat/sessions` at 500 against a home holding 31 sessions, the history page
// said "No chats yet" with a New chat action, and the side panel "No chats yet.": both readers had
// `.catch(() => [])`, so every reader took the "you have none" branch, and the page's empty state
// carried an invitation to act on the false claim. The swallow is the load-error census's to count;
// what each surface owes is the branch: the failure first, the list in hand kept through a failed
// revalidation, and the empty state, still there, for an account that really has none.

describe('the chat list says when it could not be read', () => {
  const trip = { key: 'chat-1', title: 'Trip planning', updated_at: '2026-09-26T10:00:00Z', origin: 'manual' }
  const SURFACES = [
    { row: 'the side panel', mount: () => <ChatHistorySidePanelBody navigate={() => {}} onOpen={() => {}} />, empty: () => screen.getByText('No chats yet.') },
    {
      row: 'the history page',
      mount: () => <ChatHistoryPage navigate={() => {}} query={{}} setQuery={() => {}} />,
      empty: () => screen.getByRole('heading', { name: 'No chats yet' }),
    },
  ]

  it.each(SURFACES.map((r) => [r.row, r] as const))('%s: a failed read is a retryable LoadError, not "No chats yet"', async (_, r) => {
    answer({ chatSessions: boom })
    render(r.mount())
    const alert = await waitFor(() => screen.getByRole('heading', { name: "Couldn't load your chats" }).closest('[role="alert"]') as HTMLElement)
    expect(within(alert).getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByText(/No chats yet/), 'a failed read knows nothing about your chats').toBeNull()
  })

  it.each(SURFACES.map((r) => [r.row, r] as const))('%s: the list in hand stays when its revalidation fails', async (_, r) => {
    writeQuery('chat:sessions', [trip])
    answer({ chatSessions: boom })
    render(r.mount())
    await waitFor(() => expect(api.chatSessions).toHaveBeenCalled())
    await act(async () => { await new Promise((res) => setTimeout(res, 20)) })
    expect(screen.getByText('Trip planning')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: "Couldn't load your chats" })).toBeNull()
  })

  it.each(SURFACES.map((r) => [r.row, r] as const))('%s: an account with none says so, and it is no alert', async (_, r) => {
    answer({ chatSessions: () => Promise.resolve([]) })
    render(r.mount())
    await waitFor(() => expect(r.empty()).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: "Couldn't load your chats" })).toBeNull()
  })
})

// ── The composer's context reading never states an unmeasured percentage ───────────────────────
//
// The backend emitted a `context_usage` frame every turn with `pct: 0.0`, and the model pill drew
// its ring from it, so it said "Context: 0% used" on turns carrying 18 KB of injected context. Now
// `pct` is `null` when nothing was measured, and the two answers render distinctly: unmeasured
// carries NO percentage anywhere in the markup, and a measured 0 is a reading. The reading lives in
// the composer's Context control now (#3823: collapsed until opened or pinned), and it keeps every
// rule the ring had.
//
// 🪤 The guard this replaced was `contextPct !== undefined && contextPct > 0`. The `> 0` hid the
// fabricated ring while the producer could not say "unknown", and is the INVERSE defect once it
// can: it folds a legitimately empty context into "unmeasured". Both directions are asserted, and
// that the two markups DISAGREE.
//
// An unmeasured reading also says WHY, and it has more than one cause: the gateway names the
// window with each reading (`null` when nothing declared or served one), and the remedy is offered
// only then.

describe('the context reading never states an unmeasured percentage', () => {
  /** The Context control for a reading, opened: its panel's markup and text. */
  function opened(pct?: number, windowTokens?: number | null): HTMLElement {
    cleanup()
    render(<ContextDisclosure pct={pct} window={windowTokens} cost={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'Context' }))
    return screen.getByRole('dialog', { name: 'Context' })
  }

  it('renders no percentage at all when the backend measured nothing', () => {
    const panel = opened(undefined)
    expect(within(panel).queryByRole('progressbar')).toBeNull()
    expect(panel.innerHTML).not.toContain('%')
  })

  it('renders a 0% reading when the context was measured and is empty', () => {
    const panel = opened(0)
    expect(within(panel).getByRole('progressbar', { name: 'Context used' }).getAttribute('aria-valuenow')).toBe('0')
    expect(within(panel).getByText('0% of the context window used')).toBeTruthy()
  })

  it('renders the measured value when there is one', () => {
    expect(within(opened(61.5)).getByText('62% of the context window used')).toBeTruthy()
  })

  it('unmeasured and measured-zero produce different markup', () => {
    const unmeasured = opened(undefined).innerHTML
    const measuredZero = opened(0).innerHTML
    expect(unmeasured).not.toEqual(measuredZero)
  })

  it('names the control that fixes it when the gateway says no window is known', () => {
    const said = opened(undefined, null).textContent!
    expect(said.toLowerCase()).toContain('unknown')
    // The exact label of the field on the provider form (ModelBackends.tsx), and where it lives.
    expect(said).toContain('Served context window')
    expect(said).toContain('Settings')
  })

  it('says it is not measured yet, and names the window, when the model has one', () => {
    const said = opened(undefined, 32768).textContent!
    expect(said).toContain('not measured yet')
    expect(said).toContain('32,768-token window')
    expect(said).not.toContain('declared')
  })

  it('says it is not measured yet before the gateway has said anything', () => {
    const said = opened(undefined).textContent!
    expect(said).toContain('not measured yet')
    expect(said).not.toContain('declared')
  })

  it('still states no percentage: explaining is not fabricating', () => {
    for (const w of [undefined, null, 32768]) {
      const panel = opened(undefined, w)
      expect(panel.innerHTML).not.toContain('%')
      expect(within(panel).queryByRole('progressbar')).toBeNull()
    }
  })

  it('a measured reading does NOT claim unknown', () => {
    // The reading itself: the Context window entry (the parts below it each say their own state).
    const measured = within(opened(61.5, 32768)).getByText('Context window').nextElementSibling!.textContent!
    expect(measured.toLowerCase()).not.toContain('unknown')
    expect(measured).not.toContain('not measured')
  })
})
