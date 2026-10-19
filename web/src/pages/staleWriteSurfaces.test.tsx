import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef, useImperativeHandle, useState, type ReactNode } from 'react'
import { FileText } from 'lucide-react'
import { api, ApiError, type Artifact, type KnowledgeIntent, type KnowledgeItem, type PromptItem, type ThemeWrite, type WatchedSource, type WorkflowDef } from '../lib/api'
import { writeQuery } from '../lib/data'
import { HELD_CHANGE_REASON } from '../lib/staleWrite'
import { createAppApi, installAppSdk, type AppContext } from '../app/appSdk'
import { ThemeProvider } from '../app/theme'
import { AppearanceProvider } from '../app/appearance'
import { PersonalityProvider } from '../app/personality'
import { registerContentType } from '../ui/content/contentTypes'
import { registerBuiltinContentTypes } from '../ui/content/registerBuiltins'
import { ArtifactViewer } from './artifacts/ArtifactViewer'
import { FileViewer, type SaveAsArtifact } from './files/browse/FileViewer'
import { KnowledgeDetail } from './knowledge/KnowledgeDetail'
import { IntentEditor, IntentsView } from './knowledge/KnowledgeListPage'
import { SourceRow } from './knowledge/SourcesPage'
import { PromptDetail } from './prompts/PromptDetail'
import { SnippetDetail } from './prompts/SnippetDetail'
import { AlwaysOnConventions } from './settings/AlwaysOnConventions'
import { AppsPanel } from './settings/AppsPanel'
import { DesignPanel } from './settings/DesignPanel'
import { StudioDocEditor, type DocDraft } from './settings/MemoryPanel'
import { ModelsPanel } from './settings/ModelsPanel'
import { MultiInstanceCard } from './settings/MultiInstanceCard'
import { ProjectionRulesPanel } from './settings/ProjectionRulesPanel'
import { ProviderConfigForm } from './settings/ProviderConfigForm'
import { VoicePanel } from './settings/VoicePanel'
import { SETTINGS_WIDGETS } from './settings/settingsWidgets'
import { ToolsPage } from './tools/ToolsPage'
import { PolicyOverridesPanel } from './workflows/PolicyOverridesPanel'
import { WorkflowDefEditor } from './workflows/WorkflowDefEditor'

// ── Every surface that saves a whole document goes over the copy it read ───────────────────────
//
// Each of these saves a WHOLE document, list or record built from the copy it painted, and each
// used to put that copy back over whatever was stored since — another tab's save, the agent's
// edit, a run that appended a line — without a word. The gateway now refuses a stale copy
// (`409 stale_write`, `personalclaw/stale_write.py`), and every surface hands its save to the one
// guard (`lib/useStaleWriteGuard`) and shows the one notice (`ui/StaleWriteNotice`), whose own
// cases are `ui/staleWriteNotice.test.tsx`'s.
//
// What stays per surface is what only the surface decides, so each is a row of one table driven
// through its real component: the revision its save names, that the user's change is kept, and
// that Reload and reapply lands that change on top of what is stored now, over the NEW revision.
// The cases after the table are each a path one surface takes that the row does not, or a bug
// that happened on it.
//
// One gateway stand-in serves every row: `api`'s methods are the real ones unless a row answers
// them (`answer`), and two seams the editors cannot mount under jsdom are drawn at their props —
// `ContentSurface` (Monaco needs web workers) and Monaco's own React wrapper.

const { notified, surface } = vi.hoisted(() => ({
  notified: [] as unknown[][],
  /** The props the viewer handed ContentSurface on its latest render, and what it asked it to hold. */
  surface: { seen: {} as Record<string, any>, replaced: [] as string[] },
}))

vi.mock('../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (...a: unknown[]) => { notified.push(a) },
}))
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../ui/content/ContentSurface', () => ({
  ContentSurface: forwardRef((props: Record<string, any>, ref) => {
    useImperativeHandle(ref, () => ({ save: () => {}, replaceDraft: (t: string) => { surface.replaced.push(t) } }))
    surface.seen = props
    return <div data-testid="surface">{props.headerExtras}{props.banner}</div>
  }),
}))
vi.mock('@monaco-editor/react', () => ({
  default: (p: { value: string; onChange?: (v: string) => void; options?: { ariaLabel?: string; readOnly?: boolean } }) => (
    <textarea aria-label={p.options?.ariaLabel} value={p.value} readOnly={p.options?.readOnly} onChange={(e) => p.onChange?.(e.target.value)} />
  ),
}))

/** What the row answered, by method, and what its test reads back (`stubs.__meanwhile`, …). */
const stubs: Record<string, unknown> = {}

/** Answer these `api` methods for the row, on the one `api` object every module holds; every other
 *  method is the real one, or `rest` when given. */
function answer(methods: Record<string, unknown>, rest?: () => unknown) {
  type Method = (...args: unknown[]) => unknown
  const methodsOf = api as unknown as Record<string, Method>
  if (rest) {
    for (const k of Object.keys(methodsOf)) {
      if (typeof methodsOf[k] === 'function' && !(k in methods)) vi.spyOn(methodsOf, k).mockImplementation(rest)
    }
  }
  for (const [k, fn] of Object.entries(methods)) {
    stubs[k] = fn
    if (k.startsWith('__')) continue
    if (!(k in methodsOf)) throw new Error(`api has no method ${k}`)
    vi.spyOn(methodsOf, k).mockImplementation(fn as Method)
  }
}

const stale = (what = 'it') =>
  new ApiError(`This write replaces ${what}, which changed after the copy it was built from was read.`, 409, 'stale_write')

beforeAll(() => {
  // The content-type registry is module state: the file viewer reads the built-in kinds, and the
  // artifact viewer a probe kind (the kind union names only the shipped kinds).
  registerBuiltinContentTypes()
  registerContentType({
    id: 'stalebodyprobe', label: 'Probe', icon: FileText, tone: '#888888', kinds: ['stalebodyprobe'],
    preview: { render: ({ content }: { content: string }) => <div>{content}</div> }, commentable: false,
  })
})
beforeEach(() => {
  for (const k of Object.keys(stubs)) delete stubs[k]
  notified.length = 0
  surface.seen = {}
  surface.replaced.length = 0
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const tick = () => new Promise((res) => setTimeout(res, 0))

/** Find first, then click inside `act` — a query awaited INSIDE `act` waits on a render that `act`
 *  itself is holding back. */
async function click(find: Promise<HTMLElement> | HTMLElement | null | undefined) {
  const el = await find
  expect(el, 'the control exists').toBeTruthy()
  await act(async () => { fireEvent.click(el!); await tick() })
}

async function type(el: HTMLElement | null, value: string) {
  expect(el, 'the field exists').toBeTruthy()
  await act(async () => { fireEvent.change(el!, { target: { value } }); await tick() })
}

/** The stale-write notice, found by its own marker (a form can carry other alerts), and held to
 *  being an alert: the user pressed Save and it did not save. */
async function staleNotice(): Promise<HTMLElement> {
  const el = await waitFor(() => {
    const found = document.querySelector<HTMLElement>('[data-stale-write="true"]')
    expect(found, 'the refusal notice is on the page').not.toBeNull()
    return found!
  })
  expect(el.getAttribute('role')).toBe('alert')
  return el
}

async function reapply(notice: HTMLElement) {
  const button = within(notice).getByRole('button', { name: 'Reload and reapply' })
  await waitFor(() => expect(button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true').toBe(false))
  await click(button)
}

const noticeGone = () => waitFor(() => expect(document.querySelector('[data-stale-write="true"]')).toBeNull())

// ── the surfaces ─────────────────────────────────────────────────────────────────────────────

/** A write's arguments, checked exactly, or by a check of the row's own. */
type Call = unknown[] | ((args: unknown[]) => void)

interface Surface {
  surface: string
  /** Answer the reads and the write: the first read is the copy the surface paints, every later one
   *  what another writer stored meanwhile; the write refuses the painted revision. Returns the
   *  write's calls so far. */
  stage: () => () => unknown[][]
  /** Mount the real surface, make the edit, and Save. */
  edit: () => Promise<void>
  /** The notice's sentence: the surface's own `what`. */
  what: RegExp
  /** The refused save, naming the revision the surface painted. */
  refused: Call
  /** What the surface shows once refused: the change kept, and nothing reported as a failure. */
  kept?: () => void | Promise<void>
  /** The save Reload and reapply sends: the change on top of what is stored now, over its revision. */
  reapplied: Call
  /** What the surface shows once the re-applied save landed. */
  landed?: () => void | Promise<void>
}

const check = (call: Call, args: unknown[] | undefined) => {
  expect(args, 'the write was sent').toBeDefined()
  if (typeof call === 'function') call(args!)
  else expect(args).toEqual(call)
}

// prompts ─────────────
const PROMPT_ROW: PromptItem = { name: 'triage', kind: 'user', title: 'Triage', description: '', source: 'user', tags: [] }
const PROMPT: PromptItem = { ...PROMPT_ROW, content: 'Sort {{items}} by urgency.', variables: [], revision: 'p1' }
const PROMPT_THEIRS: PromptItem = { ...PROMPT, title: 'Triage (renamed elsewhere)', revision: 'p2' }
const promptEditorReads = () => answer({
  // The editor's live preview and syntax reference read on their own; answered so their failures
  // (jsdom has no gateway) do not put a second alert on screen.
  previewPrompt: vi.fn(async () => ({ ok: true, rendered: '', detected_variables: [], includes: [] })),
  promptSyntax: vi.fn(async () => ({ functions: [], constructs: [] })),
})
const openPrompt = () => render(<PromptDetail prompt={PROMPT_ROW} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} onNavigate={() => {}} />)
const PROMPT_EDIT = 'Sort {{items}} by urgency, then by age.'

// the project overview ─────────────
const OVERVIEW = {
  id: 'project_instruction:overview.md', kind: 'project_instruction', name: 'overview.md',
  scope: 'project', source: 'project:p-1', path: '/home/projects/p-1/context/overview.md',
  chars: 64, editable: true, read_only_reason: '', project_id: 'p-1', preview: 'The deck is stripped.',
}
const OVERVIEW_OPENED = 'The deck is stripped.\n\nNext: flashing.'
// A run completed while the editor was open and appended its line.
const OVERVIEW_AFTER_RUN = OVERVIEW_OPENED + '\n- deploy → complete: shipped'
const OVERVIEW_EDIT = 'The deck is stripped and sealed.\n\nNext: flashing.'

// app and provider settings ─────────────
const APP = {
  name: 'slack-channel', displayName: 'Slack', version: '1.0.0', description: '', enabled: true, icon: 'plug',
  hasUI: false, isProvider: false, hasConfig: true,
}
const APP_SCHEMA = { properties: { room: { type: 'string', 'x-meta': { label: 'Room' } }, greeting: { type: 'string', 'x-meta': { label: 'Greeting' } } } }
const appField = (key: string) => document.getElementById(`app-cfg-slack-channel-${key}`) as HTMLInputElement

const text = (label: string, sensitive = false) => ({ type: 'string', 'x-meta': { label, ...(sensitive ? { sensitive: true } : {}) } })
const INSTANCE_SCHEMA = { type: 'object', properties: { api_key: text('API Key', true), default_model: text('Default Model'), region: text('Region') } }
const instance = (config: Record<string, unknown>, revision: string) => ({
  id: 'abc123', extension_name: 'openai-models', display_name: 'Primary',
  config: { api_key: '••••••••', ...config }, enabled: true, _secret_set: ['api_key'], revision,
})

const PROVIDER_SCHEMA = {
  properties: {
    bot_token: { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } },
    chat_id: { type: 'string', 'x-meta': { label: 'Chat ID' } },
    greeting: { type: 'string', 'x-meta': { label: 'Greeting' } },
  },
}
const providerRead = (chatId: string, revision: string) =>
  ({ config: { bot_token: '••••••••', chat_id: chatId, greeting: 'hi' }, _secret_set: ['bot_token'], revision })

// memory docs ─────────────
const MEMORY = '# User Preferences\n\n- likes short answers\n\n## Learned\n'
// What the agent's memory tool appended while the editor was open.
const MEMORY_LEARNED = MEMORY + '- prefers tea\n'
const memoryBox = () => screen.getByRole('textbox', { name: 'Preferences memory' }) as HTMLTextAreaElement
const memoryDrafts = new Map<string, DocDraft>()
async function openMemory(drafts: Map<string, DocDraft>, next?: string) {
  const view = render(<StudioDocEditor which="preferences" onSaved={() => {}} drafts={drafts} />)
  await waitFor(() => expect(memoryBox().value).toBe(drafts.get('preferences')?.text ?? MEMORY))
  if (next !== undefined) fireEvent.change(memoryBox(), { target: { value: next } })
  return view
}
const saveMemory = () => click(screen.getByRole('button', { name: /save/i }))

// model chains ─────────────
const hosted = (id: string, provider = 'openai') => ({ id, name: id, capabilities: ['chat'], provider, provider_type: provider })
const modelsPanelReads = () => answer({
  modelsAvailable: async () => [
    { name: 'openai', type: 'openai', models: [hosted('gpt-5'), hosted('gpt-4o')] },
    { name: 'anthropic', type: 'anthropic', models: [hosted('claude-x', 'anthropic')] },
  ],
  // Painted: one model, at rev-a.
  activeChains: async () => ({ chat: { value: ['openai:gpt-5'], revision: 'rev-a' } }),
  modelsActive: async () => ({ chat: ['openai:gpt-5'] }),
  // Stored since, by another tab: Claude put first, at rev-b.
  activeChain: async () => ({ value: ['anthropic:claude-x', 'openai:gpt-5'], revision: 'rev-b' }),
  modelDownloads: async () => [],
  modelsHealth: async () => ({ providers: [] }),
  embeddingReindexJobs: async () => ({ jobs: [], active: null }),
  judgeBench: () => Promise.reject(new ApiError('none', 404, 'judge_bench_absent')),
  modelDownloadCleanupCandidates: async () => ({ candidates: [], total_bytes: 0 }),
  hfTokenStatus: async () => ({ sources: [] }),
  modelsLoaded: async () => ({
    loaded: [], providers: [],
    pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
  }),
  personalclawConfig: async () => ({ agent: { prompt_cache_enabled: true }, local_models: {} }),
})

// projection rules ─────────────
const RULE_A = { name: 'a', match_regex: '^\\[A\\]', strategy: 'log' as const }
const RULE_B = { name: 'b', match_regex: '^\\[B\\]', strategy: 'diff' as const }
const RULE_C = { name: 'c', match_regex: '^\\[C\\]', strategy: 'log' as const }
function stageRules() {
  let reads = 0
  const projectionRules = vi.fn(async () => (++reads === 1 ? { value: [RULE_A], revision: 'r1' } : { value: [RULE_A, RULE_B], revision: 'r2' }))
  const setProjectionRules = vi.fn((_rules: unknown, base: string) => (base === 'r2' ? Promise.resolve({}) : Promise.reject(stale('tools.projection_rules'))))
  answer({ projectionRules, setProjectionRules, toolsSavings: async () => null })
  return { projectionRules, setProjectionRules }
}
async function addRuleC() {
  await act(async () => { render(<ProjectionRulesPanel />); await tick() })
  const regex = await screen.findByLabelText('Match regex for the new rule')
  fireEvent.change(screen.getByLabelText('New rule name'), { target: { value: 'c' } })
  fireEvent.change(regex, { target: { value: RULE_C.match_regex } })
  await click(screen.getByRole('button', { name: 'Add rule' }))
}

// speech settings ─────────────
type UseCaseDoc = { value: Record<string, unknown>; revision: string }

/** The gateway's route for a use case's settings file, If-Match check included, as `fetch`: the
 *  speech tile's row proves the request its click sends — the precondition header and the body. */
function speechRoute(stored: Record<string, { settings: Record<string, unknown>; revision: string }>) {
  const puts: unknown[][] = []
  let written = 0
  const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const headers = new Headers(init?.headers)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    if (url === '/api/models/active') return json({ use_cases: { stt: ['whisper:base'], tts: ['piper:en'] }, revisions: {} })
    const route = url.match(/^\/api\/models\/use-cases\/(stt|tts)\/settings$/)
    if (!route) return json({})
    const useCase = route[1]
    if (method === 'GET') return json({ use_case: useCase, settings: stored[useCase].settings, revision: stored[useCase].revision })
    puts.push([url, headers.get('If-Match'), body])
    if (headers.get('If-Match') !== `"${stored[useCase].revision}"`) {
      return json({ error: { code: 'stale_write', message: `This write replaces the ${useCase} settings, which changed.` } }, 409)
    }
    stored[useCase] = { settings: body as Record<string, unknown>, revision: `rev-w${++written}` }
    return json({ ok: true, use_case: useCase, settings: stored[useCase].settings, revision: stored[useCase].revision })
  }))
  return puts
}
const speechStored = {} as Record<string, { settings: Record<string, unknown>; revision: string }>

const voiceStored = {} as Record<string, UseCaseDoc>
const voiceSwitch = () => screen.getByRole('switch', { name: 'Enable speech-to-text' })

// themes ─────────────
const PAINTED_THEME = {
  slug: 'harbor', name: 'Harbor', emoji: '🌊', created_at: '2026-09-01T00:00:00Z',
  dark: { '--color-primary': '#112233' }, light: { '--color-primary': '#445566' },
}
// What is stored by the time the update is sent: another device renamed it, and sync pulled it in.
const STORED_THEME = { ...PAINTED_THEME, name: 'Harbor at dusk' }

// MCP servers ─────────────
const MCP_PAINTED = { name: 'gh', editable: true, transport: 'stdio', command: 'npx', args: ['-y', 'gh-mcp'], env: [] }
function ToolsHarness() {
  const [query, setQueryState] = useState<Record<string, string>>({})
  const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
    return next
  })
  return <ToolsPage query={query} setQuery={setQuery} />
}

// artifacts and files ─────────────
// The user changes line 1; another writer changed line 4 meanwhile — apart, so it re-applies.
const READ = 'a\nb\nc\nd\n'
const AGENTS = 'a\nb\nc\nD\n'
const MINE = 'A\nb\nc\nd\n'
const BOTH = 'A\nb\nc\nD\n'
const SLUG = 'q3-brief'
const PATH = '/ws/notes.py'

function stageArtifact({ acceptFirst = false } = {}) {
  let stored = { content: READ, revision: 'r1' }
  const artifact = vi.fn(async (): Promise<Artifact> => ({
    slug: SLUG, name: 'Q3 brief', kind: 'stalebodyprobe', source: 'chat', description: '', tags: [],
    version: 1, created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z', events: [],
    source_path: '', readonly: false, content: stored.content, content_revision: stored.revision,
  } as unknown as Artifact))
  const saveArtifactBody = vi.fn(async (_s: string, content: string, base: string) => {
    if (base !== stored.revision) throw stale(`the body of the artifact '${SLUG}'`)
    stored = { content, revision: 'r3' }
    return artifact()
  })
  const snapshotArtifactBody = vi.fn(saveArtifactBody)
  answer({
    artifact, saveArtifactBody, snapshotArtifactBody,
    artifactVersions: async () => ({ slug: SLUG, versions: [1] }),
    artifactEvents: async () => ({ slug: SLUG, events: [] }),
    viewRender: async () => ({}),
    deployedArtifacts: async () => [],
  })
  return { saveArtifactBody, snapshotArtifactBody, agentWrites: () => { if (!acceptFirst) stored = { content: AGENTS, revision: 'r2' } } }
}
async function openArtifact(agentWrites: () => void) {
  await act(async () => { render(<ArtifactViewer slug={SLUG} onChanged={() => {}} onDeleted={() => {}} onOpenSourceFile={() => {}} />) })
  await waitFor(() => expect(surface.seen.onSave).toBeTypeOf('function'))
  // The surface reports the draft it seeded — the body as read — so the viewer's base is set…
  act(() => { surface.seen.onDraftChange(READ, false) })
  await waitFor(() => expect(surface.seen.draftBase).toEqual({ value: READ, revision: 'r1' }))
  // …and then the agent writes the body, while the page is open.
  agentWrites()
}

function stageFile({ acceptFirst = false } = {}) {
  // The first read is the copy the page paints; every later read sees the agent's edit.
  let reads = 0
  const fileRead = vi.fn(async () => (++reads === 1 || acceptFirst
    ? { content: READ, truncated: false, binary: false, revision: 'r1' }
    : { content: AGENTS, truncated: false, binary: false, revision: 'r2' }))
  const fileWrite = vi.fn(async (_p: string, _c: string, base: string) =>
    base === 'r2' || acceptFirst ? { ok: true, revision: 'r3' } : Promise.reject(stale(`the file '${PATH}'`)))
  answer({ fileRead, fileWrite, fileWatchUrl: () => '/watch', fileRawUrl: () => '/raw', revealPath: async () => ({}) })
  return { fileRead, fileWrite }
}
async function openFile() {
  const onSaved = vi.fn()
  let saveAsArtifact: SaveAsArtifact | null = null
  await act(async () => {
    render(<FileViewer entry={{ name: 'notes.py', path: PATH, is_dir: false }} onSaved={onSaved}
      onSaveAsArtifact={(_e, save) => { if (typeof save === 'function') saveAsArtifact = save }} />)
  })
  await waitFor(() => expect(surface.seen.onSave).toBeTypeOf('function'))
  return { onSaved, saveAsArtifact: () => saveAsArtifact }
}
let fileOnSaved = vi.fn()

// knowledge ─────────────
/** A landed body save re-enriches the item, and the page follows that on the ingest stream — which
 *  jsdom does not have. The stream's content is not what these rows are about. */
class QuietEventSource { addEventListener() {} close() {} }

const ITEM: KnowledgeItem = {
  id: 'k1', title: 'Homelab notes', content: 'alpha\nbeta\n', item_type: 'note', type: 'note', tags: ['t1'], content_revision: 'r1',
}
// What another writer (the agent, a rename's relink, another tab) stored meanwhile.
const ITEM_THEIRS: KnowledgeItem = { ...ITEM, content: 'alpha\nbeta\ngamma from the agent\n', content_revision: 'r2' }
function stageItem({ stale: refuse }: { stale: boolean }) {
  vi.stubGlobal('EventSource', QuietEventSource)
  let reads = 0
  const knowledgeItem = vi.fn(async () => (++reads === 1 ? ITEM : ITEM_THEIRS))
  const saveKnowledgeItem = vi.fn((_id: string, _body: unknown, base: string) =>
    refuse && base === 'r1' ? Promise.reject(stale()) : Promise.resolve({ ok: true, content_revision: 'r9' }))
  const updateKnowledgeItem = vi.fn(async () => ({ ok: true, content_revision: 'r1' }))
  answer({
    knowledgeItem, saveKnowledgeItem, updateKnowledgeItem,
    knowledgeItemIntents: async () => ({ outcomes: [] }),
    knowledgeItemGraph: () => Promise.reject(new Error('no graph')),
    knowledgeTags: async () => [],
  })
  return { knowledgeItem, saveKnowledgeItem, updateKnowledgeItem }
}
async function openItem(knowledgeItem: ReturnType<typeof vi.fn>) {
  // The page renders the published header cluster (Edit / Save / Cancel) in its top bar.
  function Host() {
    const [header, setHeader] = useState<{ actions: ReactNode } | null>(null)
    return <>{header?.actions}<KnowledgeDetail item={ITEM} onHeader={setHeader} onChanged={() => {}} onDeleted={() => {}} /></>
  }
  render(<Host />)
  await waitFor(() => expect(knowledgeItem).toHaveBeenCalledTimes(1))
  await click(screen.findByRole('button', { name: 'Edit' }))
}
const itemBody = () => screen.getByPlaceholderText('Markdown supported…') as HTMLTextAreaElement
let itemUpdates = vi.fn()

const INTENT: KnowledgeIntent = {
  id: 'intent-homelab', goal: 'anything that could improve my homelab', enabled: true, enabled_for: ['note'], propose_skill: false, revision: 'i1',
}
// Reworded in another tab after this list read it.
const INTENT_THEIRS: KnowledgeIntent = { ...INTENT, goal: 'homelab upgrades, reworded elsewhere', revision: 'i2' }
/** `painted`: the surface reads the list before its edit (the list row), so the first read is the
 *  copy it paints; the editor is handed its copy, so every read it makes is what is stored now. */
function stageIntents(painted = true, theirs = INTENT_THEIRS) {
  let reads = painted ? 0 : 1
  const saveKnowledgeIntent = vi.fn((_record: unknown, base: string) =>
    base === 'i1' ? Promise.reject(stale()) : Promise.resolve({ intents: [], id: INTENT.id }))
  answer({
    knowledgeIntents: vi.fn(async () => ({ intents: [++reads === 1 ? INTENT : theirs] })),
    saveKnowledgeIntent,
    createKnowledgeIntent: vi.fn(async () => ({ intents: [], id: INTENT.id })),
  })
  return saveKnowledgeIntent
}
let intentSaved = vi.fn()

const SOURCE: WatchedSource = {
  id: 'src-1', name: 'Product changelog', provider: 'watched-page', kind: 'web_page',
  spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 4 }, revision: 's1',
  enrichment: 'full', poll_interval_secs: 3600, poll_every_secs: 3600, item_type: 'bookmark', enabled: true,
  health_status: 'needs_render', last_error_summary: '', last_escalations: [], last_new_count: 0,
  last_poll_at: new Date().toISOString(), enrolled: true,
  remediation: { kind: 'render_tier', guidance: 'This page builds its content with JavaScript.', detail: '', action: 'allow_render' },
}
// Another tab raised the request budget after this row was read.
const SOURCE_THEIRS: WatchedSource = { ...SOURCE, budget: { max_requests: 9 }, revision: 's2' }
let sourceChanged = vi.fn()

// workflow definitions ─────────────
type Def = WorkflowDef & Record<string, unknown>
/** A definition of the user's own, edited in place. */
const WORKFLOW: Def = {
  name: 'mine', source: 'user', version: 1, provenance: 'user', spec_semver: '1.0',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  description: 'Read a paper.', tags: ['research'],
  inputs: { paper_url: { type: 'string', required: true, default: null, help: 'Where the paper is.' } },
  metadata: { risk: 'low' }, on_overlap: 'skip',
  root: { kind: 'sequence', id: 'main', children: [{ kind: 'infer', id: 'identify', config: { prompt: 'Name the paper.' } }] },
}
/** The gateway's half for workflow definitions: a store that keeps what it is sent, and refuses the
 *  way `stale_write_refusal` does. A revision is a digest of the definition, so every writer moves it. */
const workflows = {
  store: new Map<string, Def>(),
  saves: [] as Array<{ body: Record<string, unknown>; base: string | undefined }>,
  /** A write that lands after the page's own checks and before its save is looked at. */
  landsFirst: null as (() => void) | null,
}
function revisionOf(def: Def): string {
  const t = JSON.stringify(def)
  let h = 5381
  for (let i = 0; i < t.length; i++) h = ((h * 33) ^ t.charCodeAt(i)) >>> 0
  return h.toString(16)
}
/** What the definition page's A2A publish toggle does: one field, straight to the store. */
function publishElsewhere(name: string) {
  const def = workflows.store.get(name)!
  workflows.store.set(name, { ...def, metadata: { ...def.metadata, a2a_published: true }, version: (def.version ?? 1) + 1 })
}
function stageWorkflows() {
  workflows.store.clear()
  workflows.store.set('mine', structuredClone(WORKFLOW))
  workflows.saves.length = 0
  workflows.landsFirst = null
  answer({
    workflowDef: async (name: string) => {
      const def = workflows.store.get(name)
      if (!def) throw new ApiError(`no workflow definition named '${name}'`, 404, 'not_found')
      return { definition: structuredClone(def), provider: def.source === 'user' ? 'native' : 'bundled', revision: revisionOf(def) }
    },
    workflowDefs: async () => {
      const defs = [...workflows.store.entries()].filter(([k]) => !k.includes('@'))
        .map(([name, d]) => ({ name, description: '', source: d.source ?? 'user', version: d.version ?? 1, tags: [], provider: '' }))
      return { defs, total: defs.length }
    },
    workflowVersion: async (name: string, version: number) => {
      const def = workflows.store.get(`${name}@v${version}`)
      if (!def) throw new ApiError('no such version', 404, 'not_found')
      return { version, saved_by: 'owner', created_at: '', note: '', definition: structuredClone(def) }
    },
    saveWorkflowDef: async (body: Record<string, unknown>, base?: string) => {
      workflows.saves.push({ body: structuredClone(body), base })
      const first = workflows.landsFirst
      workflows.landsFirst = null
      first?.()
      if (body.save === false) return { saved: false, valid: true, issues: [], levels: [], lint: { findings: [] } }
      const name = String(body.name)
      const prior = workflows.store.get(name)
      if (prior?.source === 'user') {
        if (!base) throw new ApiError(`This write is checked against the copy of the workflow '${name}' it was built from…`, 428, 'revision_required')
        if (base !== revisionOf(prior)) throw stale(`the workflow '${name}'`)
      }
      const { save: _save, based_on: _b, based_on_version: _bv, ...fields } = body
      const saved = { ...prior, ...fields, name, source: 'user', version: (prior?.version ?? 0) + 1 } as Def
      workflows.store.set(name, saved)
      return { saved: true, valid: true, issues: [], levels: [], lint: { findings: [] }, definition: structuredClone(saved), revision: revisionOf(saved) }
    },
  })
}
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
type Tab = Awaited<ReturnType<typeof openTab>>
const button = (scope: ParentNode, label: string) => [...scope.querySelectorAll('button')].find((b) => (b.textContent ?? '').trim() === label)
const description = (tab: Tab) => tab.r.container.querySelector('textarea') as HTMLTextAreaElement
async function editPrompt(tab: Tab, value: string) {
  const card = tab.r.container.querySelector('[data-step-path="root.children[0]"]') as HTMLElement
  await click(card.querySelector<HTMLElement>('button[aria-expanded]'))
  await type(card.querySelector<HTMLElement>('[aria-label="prompt"]'), value)
}
const promptOf = (def: Def | undefined) => ((def?.root as { children: Array<{ config: Record<string, unknown> }> }).children[0].config.prompt)
/** Two tabs on one workflow: tab A saves a description, then tab B — opened before it — saves. */
const twoTabs = { a: null as Tab | null, b: null as Tab | null, opened: '', afterA: '' }
async function twoTabsSave(bEdits: (b: Tab) => Promise<void>, aDescription = 'Tab A: read a paper, carefully.') {
  twoTabs.opened = revisionOf(workflows.store.get('mine')!)
  twoTabs.a = await openTab()
  twoTabs.b = await openTab()
  await type(description(twoTabs.a), aDescription)
  await click(button(twoTabs.a.r.container, 'Save'))
  expect(twoTabs.a.saved).toEqual(['mine'])
  twoTabs.afterA = revisionOf(workflows.store.get('mine')!)
  await bEdits(twoTabs.b)
  await click(button(twoTabs.b.r.container, 'Save'))
}

const SURFACES: Surface[] = [
  { surface: 'a prompt',
    stage: () => {
      promptEditorReads()
      const save = vi.fn((_name: string, _body: unknown, base: string) =>
        base === 'p2' ? Promise.resolve({ ok: true, prompt: PROMPT_THEIRS, revision: 'p3' }) : Promise.reject(stale('the prompt')))
      let reads = 0
      answer({ prompt: vi.fn(async () => (++reads === 1 ? PROMPT : PROMPT_THEIRS)), savePrompt: save })
      return () => save.mock.calls
    },
    edit: async () => {
      openPrompt()
      await type(await screen.findByLabelText('Prompt template'), PROMPT_EDIT)
      await click(screen.getByRole('button', { name: /^Save$/ }))
    },
    what: /This prompt changed elsewhere/,
    refused: ([name, body, base]) => { expect([name, base]).toEqual(['triage', 'p1']); expect(body).toMatchObject({ content: PROMPT_EDIT }) },
    kept: () => expect((screen.getByLabelText('Prompt template') as HTMLTextAreaElement).value).toBe(PROMPT_EDIT),
    // The title the other tab gave it survives; the template edit made here lands on top.
    reapplied: ([, body, base]) => { expect(base).toBe('p2'); expect(body).toMatchObject({ title: 'Triage (renamed elsewhere)', content: PROMPT_EDIT }) },
  },
  { surface: 'a prompt snippet',
    stage: () => {
      const read = { name: 'tone', title: 'Tone', source: 'user', content: 'Be brief.', revision: 's1' }
      const save = vi.fn((_name: string, body: unknown, base: string) =>
        base === 's2' ? Promise.resolve({ ok: true, snippet: body, revision: 's3' }) : Promise.reject(stale('the snippet')))
      let reads = 0
      answer({ snippet: vi.fn(async () => (++reads === 1 ? read : { ...read, title: 'Voice', revision: 's2' })), saveSnippet: save })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<SnippetDetail snippet={{ name: 'tone', title: 'Tone', source: 'user' }} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} />)
      await type(await screen.findByLabelText('Snippet content'), 'Be brief. Be kind.')
      await click(screen.getByRole('button', { name: /^Save$/ }))
    },
    what: /This snippet changed elsewhere/,
    refused: ([, body, base]) => { expect(base).toBe('s1'); expect(body).toMatchObject({ content: 'Be brief. Be kind.' }) },
    kept: () => expect((screen.getByLabelText('Snippet content') as HTMLTextAreaElement).value).toBe('Be brief. Be kind.'),
    reapplied: ([, body, base]) => { expect(base).toBe('s2'); expect(body).toMatchObject({ title: 'Voice', content: 'Be brief. Be kind.' }) },
  },
  { surface: 'a project overview',
    stage: () => {
      let reads = 0
      const save = vi.fn((_id: string, _pid: string, body: string, base: string) =>
        base === 'o2' ? Promise.resolve({ ok: true, item: { ...OVERVIEW, body, revision: 'o3' } }) : Promise.reject(stale('the project instruction')))
      answer({
        alwaysOn: async () => ({
          items: [OVERVIEW], project_id: 'p-1', counts: { total: 1, always_skills: 0, project_instructions: 1 },
          always_skill_mechanism: 'always: true in a skill’s SKILL.md frontmatter',
        }),
        alwaysOnDoc: vi.fn(async () => (++reads === 1 ? { ...OVERVIEW, body: OVERVIEW_OPENED, revision: 'o1' } : { ...OVERVIEW, body: OVERVIEW_AFTER_RUN, revision: 'o2' })),
        saveAlwaysOnDoc: save,
        projects: async () => [{ id: 'p-1', name: 'Roofing Rebuild' }],
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<AlwaysOnConventions />)
      await waitFor(() => expect(screen.queryByText('overview.md')).not.toBeNull())
      await click(screen.getByRole('button', { name: 'Edit' }))
      await type(await screen.findByRole('textbox'), OVERVIEW_EDIT)
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /changed elsewhere/,
    refused: ['project_instruction:overview.md', 'p-1', OVERVIEW_EDIT, 'o1'],
    kept: () => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(OVERVIEW_EDIT),
    // The run's line survives, and the edit lands on top of it.
    reapplied: ['project_instruction:overview.md', 'p-1', OVERVIEW_EDIT + '\n- deploy → complete: shipped', 'o2'],
    landed: () => waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(OVERVIEW_EDIT + '\n- deploy → complete: shipped')),
  },
  { surface: "an app's settings (Settings → Apps)",
    stage: () => {
      let stored = { config: { room: 'general', greeting: 'hi' } as Record<string, unknown>, revision: 'rev-1' }
      let written = 0
      const save = vi.fn((name: string, config: Record<string, unknown>, base: string) => {
        if (base !== stored.revision) return Promise.reject(new ApiError(`The app '${name}''s settings changed.`, 409, 'stale_write'))
        stored = { config, revision: `rev-w${++written}` }
        return Promise.resolve({ ok: true, config, revision: stored.revision })
      })
      answer({
        apps: async () => [APP],
        personalclawConfig: async () => ({ apps: {} }),
        appConfig: async (name: string) => ({ name, schema: APP_SCHEMA, config: stored.config, _secret_set: [], revision: stored.revision }),
        saveAppConfig: save,
        // Another tab moves the app to another room while this form is open.
        __meanwhile: () => { stored = { config: { room: 'random', greeting: 'hi' }, revision: 'rev-2' } },
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<AppsPanel />)
      await waitFor(() => expect(appField('greeting')).toBeTruthy())
      fireEvent.change(appField('greeting'), { target: { value: 'hello' } })
      ;(stubs.__meanwhile as () => void)()
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /changed elsewhere/,
    refused: ['slack-channel', { room: 'general', greeting: 'hello' }, 'rev-1'],
    kept: () => expect(appField('greeting').value).toBe('hello'),
    // The greeting on top of what is stored now: the other tab's room survives.
    reapplied: ['slack-channel', { room: 'random', greeting: 'hello' }, 'rev-2'],
  },
  { surface: "a provider instance's settings",
    stage: () => {
      let listed = [instance({ default_model: 'gpt-4o', region: '' }, 'rev-1')]
      const update = vi.fn((_n: string, _id: string, _config: unknown, base: string) =>
        base === 'rev-2' ? Promise.resolve({}) : Promise.reject(new ApiError("The settings of instance 'abc123' changed.", 409, 'stale_write')))
      answer({
        providerSchema: async () => INSTANCE_SCHEMA,
        providerInstances: vi.fn(async () => listed),
        updateProviderInstance: update,
        testProviderInstance: vi.fn(), deleteProviderInstance: vi.fn(),
        // Another tab sets the region while this editor is open.
        __meanwhile: () => { listed = [instance({ default_model: 'gpt-4o', region: 'eu' }, 'rev-2')] },
      })
      return () => update.mock.calls
    },
    edit: async () => {
      render(<MultiInstanceCard ext={{ name: 'openai-models', displayName: 'OpenAI', enabled: true, multiInstance: true } as never} onChanged={vi.fn()} />)
      await click(screen.findByRole('button', { name: 'Edit' }))
      fireEvent.change(await screen.findByLabelText('Default Model'), { target: { value: 'gpt-5' } })
      ;(stubs.__meanwhile as () => void)()
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /changed elsewhere/,
    // The stored secret is blanked in the editor, so a blank here is the PUT's "keep it".
    refused: ['openai-models', 'abc123', { api_key: '', default_model: 'gpt-5', region: '' }, 'rev-1'],
    kept: () => {
      // The editor stays open on what the user typed, and cannot save the held copy again.
      expect((screen.getByLabelText('Default Model') as HTMLInputElement).value).toBe('gpt-5')
      expect(screen.getByRole('button', { name: 'Save' }).getAttribute('aria-disabled')).toBe('true')
    },
    // The other tab's region survives.
    reapplied: ['openai-models', 'abc123', { api_key: '', default_model: 'gpt-5', region: 'eu' }, 'rev-2'],
    landed: () => waitFor(() => expect(screen.queryByLabelText('Default Model')).toBeNull()),
  },
  { surface: "a provider's settings form",
    stage: () => {
      let read = providerRead('42', 'rev-1')
      let refuseNext = false
      const save = vi.fn((_n: string, config: Record<string, unknown>) => {
        if (refuseNext) { refuseNext = false; return Promise.reject(new ApiError("The settings of 'telegram-channel' changed.", 409, 'stale_write')) }
        return Promise.resolve({ config: { ...config, bot_token: '••••••••' }, _secret_set: ['bot_token'], revision: 'rev-3' })
      })
      answer({
        providerSchema: async () => PROVIDER_SCHEMA,
        providerConfig: vi.fn(async () => read),
        saveProviderConfig: save,
        // Apps → Configure changes the chat while this form is open.
        __meanwhile: () => { read = providerRead('99', 'rev-2'); refuseNext = true },
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<ProviderConfigForm name="telegram-channel" />)
      fireEvent.change(await screen.findByLabelText('Greeting'), { target: { value: 'hello' } })
      ;(stubs.__meanwhile as () => void)()
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /changed elsewhere/,
    refused: ['telegram-channel', { bot_token: '', chat_id: '42', greeting: 'hello' }, 'rev-1'],
    kept: () => {
      expect((screen.getByLabelText('Greeting') as HTMLInputElement).value).toBe('hello')
      expect(screen.getByText('Unsaved changes')).toBeTruthy()
    },
    // The greeting on top of what is stored now: the other chat survives.
    reapplied: ['telegram-channel', { bot_token: '', chat_id: '99', greeting: 'hello' }, 'rev-2'],
    landed: () => expect((screen.getByLabelText('Chat ID') as HTMLInputElement).value).toBe('99'),
  },
  { surface: 'a memory doc',
    stage: () => {
      // First read: the copy the editor opens with. Every later read: the agent has learned something.
      let reads = 0
      const save = vi.fn((_w: string, c: string, base: string) =>
        base === 'r2' ? Promise.resolve({ value: c, revision: 'r3' }) : Promise.reject(stale('the preferences memory')))
      answer({ memoryDoc: vi.fn(async () => (++reads === 1 ? { value: MEMORY, revision: 'r1' } : { value: MEMORY_LEARNED, revision: 'r2' })), saveMemoryDoc: save })
      memoryDrafts.clear()
      return () => save.mock.calls
    },
    edit: async () => {
      await openMemory(memoryDrafts, MEMORY.replace('short', 'SHORT'))
      await saveMemory()
    },
    what: /Preferences memory changed elsewhere/,
    refused: ['preferences', MEMORY.replace('short', 'SHORT'), 'r1', ''],
    kept: () => {
      expect(memoryBox().value).toBe(MEMORY.replace('short', 'SHORT'))
      expect(memoryDrafts.get('preferences')?.text, 'and in the Studio’s cache').toBe(MEMORY.replace('short', 'SHORT'))
    },
    // What the agent learned stays, the edit lands on top.
    reapplied: ['preferences', MEMORY_LEARNED.replace('short', 'SHORT'), 'r2', ''],
    landed: async () => {
      // The editor now holds what was stored, and it is no longer an unsaved change.
      await waitFor(() => expect(memoryBox().value).toBe(MEMORY_LEARNED.replace('short', 'SHORT')))
      expect(screen.queryByText('Unsaved changes')).toBeNull()
    },
  },
  { surface: "a use case's model chain",
    stage: () => {
      modelsPanelReads()
      let refuseNext = true
      const save = vi.fn(() => {
        if (refuseNext) { refuseNext = false; return Promise.reject(stale('the chat model chain')) }
        return Promise.resolve({ ok: true, revision: 'rev-c' })
      })
      answer({ setActiveModel: save })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<ModelsPanel />)
      await click(screen.findByRole('button', { name: /^Chat/, expanded: false }))
      await click(screen.findByRole('button', { name: 'gpt-4o' }))
    },
    what: /changed elsewhere/,
    refused: ['chat', ['openai:gpt-5', 'openai:gpt-4o'], 'rev-a'],
    kept: () => {
      // The change waits for the user, and no second edit can be built from the same stale copy
      // meanwhile: a model's row says so and refuses the click, keeping its tab stop.
      const held = screen.getByRole('button', { name: 'claude-x' })
      expect(held.getAttribute('aria-disabled')).toBe('true')
      expect(held.getAttribute('title')).toBe(HELD_CHANGE_REASON)
      fireEvent.click(held)
      expect((stubs.setActiveModel as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(1)
    },
    // The same edit — add gpt-4o — applied to what is stored now: the other tab's Claude stays first.
    reapplied: ['chat', ['anthropic:claude-x', 'openai:gpt-5', 'openai:gpt-4o'], 'rev-b'],
  },
  { surface: 'the projection rules',
    stage: () => {
      const { setProjectionRules } = stageRules()
      return () => setProjectionRules.mock.calls
    },
    edit: addRuleC,
    what: /Your projection rules changed elsewhere/,
    refused: [[RULE_A, RULE_C], 'r1'],
    kept: () => {
      // What was typed is still in the form: the refusal kept the change, it did not eat it.
      expect((screen.getByLabelText('Match regex for the new rule') as HTMLInputElement).value).toBe(RULE_C.match_regex)
      expect((screen.getByLabelText('New rule name') as HTMLInputElement).value).toBe('c')
    },
    // B — the other tab's rule — survives, C is added after it.
    reapplied: [[RULE_A, RULE_B, RULE_C], 'r2'],
    // Stored now, so the add form starts empty again.
    landed: () => waitFor(() => expect((screen.getByLabelText('Match regex for the new rule') as HTMLInputElement).value).toBe('')),
  },
  { surface: "the Settings hub's speech tile",
    stage: () => {
      Object.assign(speechStored, { stt: { settings: { enabled: false }, revision: 'rev-1' }, tts: { settings: { enabled: false }, revision: 'rev-1' } })
      const puts = speechRoute(speechStored)
      return () => puts
    },
    edit: async () => {
      const tile = SETTINGS_WIDGETS.find((w) => w.id === 'voice')
      expect(tile, 'the Speech & Transcription tile').toBeDefined()
      function Host() { return <>{tile!.render('', () => {})}</> }
      render(<Host />)
      const toggle = await screen.findByRole('switch', { name: 'Speech-to-text' })
      // The Slack voice modal saves a language after the hub painted.
      speechStored.stt = { settings: { enabled: false, language: 'fr' }, revision: 'rev-slack' }
      await click(toggle)
    },
    what: /changed elsewhere/,
    // The request the click sends: the precondition header and the body.
    refused: ['/api/models/use-cases/stt/settings', '"rev-1"', { enabled: true }],
    kept: () => {
      // Refused, so the file is as the modal left it, the switch shows what is stored, and a refusal
      // the notice explains is not also reported as a failure.
      expect(speechStored.stt).toEqual({ settings: { enabled: false, language: 'fr' }, revision: 'rev-slack' })
      expect(screen.getByRole('switch', { name: 'Speech-to-text' }).getAttribute('aria-checked')).toBe('false')
      expect(notified).toEqual([])
    },
    reapplied: ['/api/models/use-cases/stt/settings', '"rev-slack"', { enabled: true, language: 'fr' }],
    landed: () => expect(speechStored.stt.settings).toEqual({ enabled: true, language: 'fr' }),
  },
  { surface: 'the speech settings page',
    stage: () => {
      Object.assign(voiceStored, { stt: { value: { enabled: false }, revision: 'rev-1' }, tts: { value: { enabled: false }, revision: 'rev-1' } })
      let written = 0
      const save = vi.fn((useCase: string, next: Record<string, unknown>, base: string) => {
        if (base !== voiceStored[useCase].revision) return Promise.reject(new ApiError(`The ${useCase} settings changed.`, 409, 'stale_write'))
        voiceStored[useCase] = { value: next, revision: `rev-w${++written}` }
        return Promise.resolve({ ok: true, use_case: useCase, settings: next, revision: voiceStored[useCase].revision })
      })
      answer({
        useCaseSettings: async (useCase: string) => voiceStored[useCase],
        saveUseCaseSettings: save,
        // A model bound for both, so the switches are operable.
        modelsActive: async () => ({ stt: ['whisper:base'], tts: ['piper:en'] }),
        personalclawConfig: async () => ({}),
        voiceProfiles: async () => ({ profiles: [], bindings: {} }),
        voiceResolve: async () => ({ surface: '', resolved: true, level: 'built-in' }),
        lexiconTerms: async () => ({ terms: [], total: 0 }),
        lexiconCorrections: async () => ({ corrections: [] }),
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<VoicePanel go={() => {}} />)
      const toggle = await waitFor(voiceSwitch)
      // The Slack voice modal saves a language after this page painted.
      voiceStored.stt = { value: { enabled: false, language: 'fr' }, revision: 'rev-slack' }
      await click(toggle)
    },
    what: /changed elsewhere/,
    refused: ['stt', { enabled: true }, 'rev-1'],
    kept: () => {
      // The change is KEPT — on screen, not rolled back — and nothing is reported as a failure.
      expect(voiceSwitch().getAttribute('aria-checked')).toBe('true')
      expect(notified).toEqual([])
      expect(voiceStored.stt).toEqual({ value: { enabled: false, language: 'fr' }, revision: 'rev-slack' })
    },
    // The language set elsewhere survives beside this change.
    reapplied: ['stt', { enabled: true, language: 'fr' }, 'rev-slack'],
    landed: () => expect(voiceStored.stt.value).toEqual({ enabled: true, language: 'fr' }),
  },
  { surface: '"Update theme" in the Design panel',
    stage: () => {
      let reads = 0
      const update = vi.fn((_slug: string, body: ThemeWrite, base: string) => (base === 'r2'
        ? Promise.resolve({ ok: true, theme: { ...STORED_THEME, ...body }, revision: 'r3' })
        : Promise.reject(stale("the theme 'harbor'"))))
      // The providers around the panel read more than these; each other read answers nothing.
      answer({
        themes: async () => [{ slug: 'harbor', name: 'Harbor', emoji: '🌊', created_at: '' }],
        theme: async () => (++reads === 1 ? { ...PAINTED_THEME, revision: 'r1' } : { ...STORED_THEME, revision: 'r2' }),
        updateTheme: update,
      }, () => Promise.resolve(null))
      return () => update.mock.calls
    },
    edit: async () => {
      render(<ThemeProvider><AppearanceProvider><PersonalityProvider><DesignPanel /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
      // Apply the saved theme: its colors — and the copy they came from — are what this page now
      // holds. The tile itself — not its trash button, which is named after the theme too.
      await click(screen.findByRole('button', { name: /^(?!Delete saved theme).*Harbor/ }))
      fireEvent.click(screen.getByRole('button', { name: /Edit colors & save a custom theme/ }))
      await click(screen.getByRole('button', { name: /Update theme/ }))
    },
    what: /The theme “Harbor” changed elsewhere/,
    refused: ([slug, body, base]) => {
      expect([slug, base]).toEqual(['harbor', 'r1'])
      expect(body).toMatchObject({ name: 'Harbor', emoji: '🌊' })
      expect((body as ThemeWrite).dark['--color-primary']).toBe('#112233')
    },
    // The rename made elsewhere survives, and the colors are saved over it.
    reapplied: ([, body, base]) => { expect(base).toBe('r2'); expect(body).toMatchObject({ name: 'Harbor at dusk' }) },
  },
  { surface: "an MCP server's definition",
    stage: () => {
      let reads = 0
      const save = vi.fn((_n: string, _body: unknown, base: string) =>
        base === 'r2' ? Promise.resolve({ ok: true, name: 'gh', revision: 'r3' }) : Promise.reject(stale("the MCP server 'gh'")))
      answer({
        toolsIndex: async () => ({
          tools: [{ name: 'gh_search', provider: 'gh', description: 'search', parameters: {}, requires_approval: false, risk_level: 'safe' }],
          load_failures: [],
        }),
        mcpServers: async () => [{ name: 'gh', status: 'connected', enabled: true, tools: ['gh_search'] }],
        importableMcp: async () => ({ servers: [], unreadable: [] }),
        mcpPoolStats: async () => ({ available: false }),
        toolGroups: async () => null,
        mcpElicitationServers: async () => [],
        // What is stored by the time the form saves: the provider card changed the arguments.
        mcpServerDefinition: async () => (++reads === 1 ? { ...MCP_PAINTED, revision: 'r1' } : { ...MCP_PAINTED, args: ['-y', 'gh-mcp', '--read-only'], revision: 'r2' }),
        saveMcpServer: save,
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<ToolsHarness />)
      await waitFor(() => expect(screen.getByText('gh_search')).toBeInTheDocument())
      await click(screen.getByRole('button', { name: 'Edit gh' }))
      await type(await screen.findByRole('textbox', { name: 'Command' }), 'uvx')
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /The server “gh” changed elsewhere/,
    refused: ['gh', { transport: 'stdio', command: 'uvx', args: ['-y', 'gh-mcp'] }, 'r1'],
    kept: () => expect((screen.getByRole('textbox', { name: 'Command' }) as HTMLInputElement).value).toBe('uvx'),
    // The provider card's arguments stay, and the new command applies.
    reapplied: ['gh', { transport: 'stdio', command: 'uvx', args: ['-y', 'gh-mcp', '--read-only'] }, 'r2'],
  },
  { surface: "a workflow run's policy overrides",
    stage: () => {
      const save = vi.fn((_id: string, overrides: Record<string, unknown>, base: string) => (base === 'v2'
        ? Promise.resolve({ run_id: 'r1', status: 'draft', policy_overrides: overrides, revisions: { policy_overrides: 'v3' } })
        : Promise.reject(stale('the policy overrides'))))
      answer({
        setWorkflowRunPolicyOverrides: save,
        // What is stored now: the other tab's Attended override landed after this panel read the run.
        workflowRun: async () => ({
          run_id: 'r1', workflow: 'w', status: 'draft', spec_version: 1, nodes: [],
          policy_overrides: { max_cycles: 5, attended: true }, revisions: { policy_overrides: 'v2' },
        }),
      })
      return () => save.mock.calls
    },
    edit: async () => {
      render(<PolicyOverridesPanel runId="r1" initial={{ value: { max_cycles: 5 }, revision: 'v1' }} />)
      await click(screen.getByTitle('Override Idle seconds for this run only'))
    },
    what: /This run's policy overrides changed elsewhere/,
    refused: ['r1', { max_cycles: 5, idle_secs: 120 }, 'v1'],
    // The other tab's Attended override survives; the Idle seconds edit lands on top.
    reapplied: ['r1', { max_cycles: 5, attended: true, idle_secs: 120 }, 'v2'],
    landed: () => expect(screen.getByLabelText('Attended override')).toBeTruthy(),
  },
  { surface: "an artifact's body",
    stage: () => {
      const { saveArtifactBody, agentWrites } = stageArtifact()
      stubs.__meanwhile = agentWrites
      return () => saveArtifactBody.mock.calls
    },
    edit: async () => {
      await openArtifact(stubs.__meanwhile as () => void)
      await act(async () => { await surface.seen.onSave(MINE) })
    },
    what: /This artifact changed elsewhere/,
    refused: [SLUG, MINE, 'r1'],
    kept: () => {
      // A refusal is the notice's, not a failure toast — and the draft is held for it.
      expect(notified).toEqual([])
      expect(surface.seen.locked).toBe(true)
    },
    // On top of the agent's edit.
    reapplied: [SLUG, BOTH, 'r2'],
  },
  { surface: 'a file',
    stage: () => {
      const { fileWrite } = stageFile()
      return () => fileWrite.mock.calls
    },
    edit: async () => {
      fileOnSaved = (await openFile()).onSaved
      await act(async () => { await surface.seen.onSave(MINE) })
    },
    what: /This file changed elsewhere/,
    refused: [PATH, MINE, 'r1'],
    kept: () => {
      // Kept: the viewer asked the surface to replace nothing, and holds the draft still so the
      // change the notice re-applies is the one on screen.
      expect(surface.replaced).toEqual([])
      expect(surface.seen.locked).toBe(true)
      expect(fileOnSaved).not.toHaveBeenCalled()
    },
    // On top of the agent's edit.
    reapplied: [PATH, BOTH, 'r2'],
    landed: () => {
      // What was stored is now the editor's text and its copy — not the pre-reapply draft.
      expect(surface.replaced).toEqual([BOTH])
      expect(surface.seen.content).toBe(BOTH)
      expect(surface.seen.draftBase).toEqual({ value: BOTH, revision: 'r3' })
      expect(surface.seen.locked).toBe(false)
      expect(fileOnSaved).toHaveBeenCalledWith(BOTH)
    },
  },
  { surface: "a knowledge item's body",
    stage: () => {
      const { knowledgeItem, saveKnowledgeItem, updateKnowledgeItem } = stageItem({ stale: true })
      stubs.__item = knowledgeItem
      itemUpdates = updateKnowledgeItem
      return () => saveKnowledgeItem.mock.calls
    },
    edit: async () => {
      await openItem(stubs.__item as ReturnType<typeof vi.fn>)
      await type(itemBody(), 'ALPHA\nbeta\n')
      await click(screen.getByRole('button', { name: 'Save' }))
    },
    what: /This item changed elsewhere/,
    refused: ['k1', { content: 'ALPHA\nbeta\n' }, 'r1'],
    kept: () => {
      expect(itemUpdates, 'a body edit never goes through the base-less write').not.toHaveBeenCalled()
      // Still editing, and what was typed is still there.
      expect(itemBody().value).toBe('ALPHA\nbeta\n')
    },
    // The agent's line survives, the user's edit is on top.
    reapplied: ['k1', { content: 'ALPHA\nbeta\ngamma from the agent\n' }, 'r2'],
    landed: () => waitFor(() => expect(screen.queryByPlaceholderText('Markdown supported…')).toBeNull()),
  },
  { surface: "a knowledge intent's pause switch",
    stage: () => {
      const save = stageIntents()
      return () => save.mock.calls
    },
    edit: async () => {
      render(<IntentsView selectedId={null} onSelect={() => {}} reloadKey={0} />)
      await click(screen.findByRole('switch', { name: /^pause intent:/i }))
    },
    what: /The intent “anything that could improve my homelab” changed elsewhere/,
    refused: [{ id: INTENT.id, goal: INTENT.goal, enabled: false, enabled_for: ['note'], propose_skill: false }, 'i1'],
    kept: () => expect(notified, 'a refused pause must not announce "Intent paused"').not.toContainEqual(['Intent paused', 'success']),
    // The other tab's rewording survives; the pause is applied on top.
    reapplied: [{ id: INTENT.id, goal: INTENT_THEIRS.goal, enabled: false, enabled_for: ['note'], propose_skill: false }, 'i2'],
    landed: () => waitFor(() => expect(notified).toContainEqual(['Intent paused', 'success'])),
  },
  { surface: 'a knowledge intent’s editor',
    stage: () => {
      // Turned on for bookmarks in another tab while the editor was open.
      const save = stageIntents(false, { ...INTENT, enabled_for: ['note', 'bookmark'], revision: 'i2' })
      return () => save.mock.calls
    },
    edit: async () => {
      intentSaved = vi.fn()
      render(<IntentEditor intent={INTENT} onClose={() => {}} onSaved={intentSaved} />)
      fireEvent.change(screen.getByLabelText(/what do you want to track/i), { target: { value: 'homelab power usage' } })
      await click(screen.getByRole('button', { name: /^save changes/i }))
    },
    what: /This intent changed elsewhere/,
    refused: [{ id: INTENT.id, goal: 'homelab power usage', enabled: true, enabled_for: ['note'], propose_skill: false }, 'i1'],
    kept: () => {
      expect(intentSaved, 'a refused edit did not save').not.toHaveBeenCalled()
      expect(screen.getByLabelText(/what do you want to track/i)).toHaveValue('homelab power usage')
    },
    // The other tab's bookmarks stay on; the new goal lands on top.
    reapplied: [{ id: INTENT.id, goal: 'homelab power usage', enabled: true, enabled_for: ['note', 'bookmark'], propose_skill: false }, 'i2'],
  },
  { surface: "a watched source's remediation",
    stage: () => {
      const save = vi.fn((_id: string, _settings: unknown, base: string) =>
        base === 's1' ? Promise.reject(stale()) : Promise.resolve({ source: SOURCE_THEIRS }))
      answer({
        saveKnowledgeSourceSettings: save,
        knowledgeSources: async () => ({ sources: [SOURCE_THEIRS], kinds: [], health_statuses: [], raw_enrichment: 'raw' }),
      })
      return () => save.mock.calls
    },
    edit: async () => {
      sourceChanged = vi.fn()
      render(<SourceRow source={SOURCE} kinds={{ 'watched-page': { display_name: 'Watched Page', form: 'web_page' } }} onChanged={sourceChanged} />)
      await click(screen.getByRole('button', { name: 'Allow the render tier' }))
    },
    what: /This source’s settings changed elsewhere/,
    refused: ['src-1', { spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 4, allow_render: true } }, 's1'],
    kept: () => expect(notified).not.toContainEqual(['Product changelog may now use the render tier', 'success']),
    // The other tab's budget survives, the fix is on top.
    reapplied: ['src-1', { spec: { url: 'https://example.com/changelog' }, budget: { max_requests: 9, allow_render: true } }, 's2'],
    landed: async () => {
      await waitFor(() => expect(notified).toContainEqual(['Product changelog may now use the render tier', 'success']))
      expect(sourceChanged).toHaveBeenCalled()
    },
  },
  { surface: 'a workflow definition, from a second tab',
    stage: () => {
      stageWorkflows()
      // The first save is the other tab's, which lands.
      return () => workflows.saves.slice(1).map((s) => [s.body, s.base])
    },
    edit: () => twoTabsSave((b) => editPrompt(b, 'Name the paper and its venue.')),
    what: /This workflow changed elsewhere/,
    refused: ([, base]) => expect(base, 'each tab’s save named the revision it read').toBe(twoTabs.opened),
    kept: () => {
      const b = twoTabs.b!
      expect(b.saved, 'the refused save did not leave the editor').toEqual([])
      expect(workflows.store.get('mine')!.description).toBe('Tab A: read a paper, carefully.')
      expect(workflows.saves[0].base).toBe(twoTabs.opened)
      // Kept — and frozen while it is held, so nothing typed now is left out of what Reapply sends.
      expect(description(b).matches(':disabled')).toBe(true)
      const save = button(b.r.container, 'Save') as HTMLButtonElement
      expect(save.disabled || save.getAttribute('aria-disabled') === 'true').toBe(true)
      expect(save.getAttribute('title')).toContain(HELD_CHANGE_REASON)
    },
    // Over the version it was re-applied to.
    reapplied: ([, base]) => expect(base).toBe(twoTabs.afterA),
    landed: async () => {
      await waitFor(() => expect(twoTabs.b!.saved).toEqual(['mine']))
      const stored = workflows.store.get('mine')!
      expect(stored.description, 'the other tab’s change survives').toBe('Tab A: read a paper, carefully.')
      expect(promptOf(stored), 'and yours lands on top of it').toBe('Name the paper and its venue.')
    },
  },
]

describe('a save from a stale copy, on every surface that saves a whole document', () => {
  it('holds every surface', () => expect(SURFACES).toHaveLength(21))

  it.each(SURFACES.map((s) => [s.surface, s] as const))('%s: refused into the notice with the change kept, then re-applied over the new revision', async (_surface, s) => {
    const writes = s.stage()
    await s.edit()
    const notice = await staleNotice()
    expect(notice.textContent).toMatch(s.what)
    expect(writes(), 'one save, refused').toHaveLength(1)
    check(s.refused, writes()[0])
    await s.kept?.()

    await reapply(notice)
    await waitFor(() => expect(writes()).toHaveLength(2))
    check(s.reapplied, writes()[1])
    await noticeGone()
    await s.landed?.()
  })
})

// ── the paths one surface takes that its row does not ─────────────────────────────────────────

describe('Discard my change shows what is stored, and writes nothing', () => {
  it('the projection rules re-read what is stored', async () => {
    const { projectionRules, setProjectionRules } = stageRules()
    await addRuleC()
    await click(within(await staleNotice()).getByRole('button', { name: 'Discard my change' }))
    await noticeGone()
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(projectionRules.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('a file puts its current text back in the editor', async () => {
    const { fileWrite } = stageFile()
    await openFile()
    await act(async () => { await surface.seen.onSave(MINE) })
    await click(within(await staleNotice()).getByRole('button', { name: 'Discard my change' }))
    await noticeGone()
    expect(fileWrite).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(surface.replaced).toEqual([AGENTS]))
    expect(surface.seen.draftBase).toEqual({ value: AGENTS, revision: 'r2' })
  })

  it('a workflow shows the definition as it is stored now, unfrozen', async () => {
    stageWorkflows()
    await twoTabsSave((b) => type(description(b), 'Tab B’s description.'), 'Tab A’s description.')
    const b = twoTabs.b!
    const count = workflows.saves.length
    await click(within(await staleNotice()).getByRole('button', { name: 'Discard my change' }))
    await waitFor(() => expect(description(b)?.value).toBe('Tab A’s description.'))
    expect(document.querySelector('[data-stale-write="true"]')).toBeNull()
    expect(description(b).matches(':disabled')).toBe(false)
    expect(workflows.saves.length, 'a discard writes nothing').toBe(count)
  })
})

describe('a landed save moves the next save’s base to the revision it answered', () => {
  it('a memory doc editor that stays open', async () => {
    const save = vi.fn()
      .mockResolvedValueOnce({ value: MEMORY + '- one\n', revision: 'r2' })
      .mockResolvedValue({ value: MEMORY + '- one\n- two\n', revision: 'r3' })
    answer({ memoryDoc: vi.fn(async () => ({ value: MEMORY, revision: 'r1' })), saveMemoryDoc: save })
    await openMemory(new Map(), MEMORY + '- one\n')
    await saveMemory()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    fireEvent.change(memoryBox(), { target: { value: MEMORY + '- one\n- two\n' } })
    await saveMemory()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save.mock.calls.map((c) => c[2])).toEqual(['r1', 'r2'])
  })

  it("a workflow run's policy overrides", async () => {
    const save = vi.fn()
      .mockResolvedValueOnce({ run_id: 'r1', status: 'draft', policy_overrides: { max_cycles: 5, idle_secs: 120 }, revisions: { policy_overrides: 'v2' } })
      .mockResolvedValue({ run_id: 'r1', status: 'draft', policy_overrides: { max_cycles: 5, idle_secs: 120, attended: true }, revisions: { policy_overrides: 'v3' } })
    answer({ setWorkflowRunPolicyOverrides: save })
    render(<PolicyOverridesPanel runId="r1" initial={{ value: { max_cycles: 5 }, revision: 'v1' }} />)
    await click(screen.getByTitle('Override Idle seconds for this run only'))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    await click(screen.getByTitle('Override Attended for this run only'))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save.mock.calls.map((c) => c[2])).toEqual(['v1', 'v2'])
  })

  it('a file', async () => {
    const { fileWrite } = stageFile({ acceptFirst: true })
    const { onSaved } = await openFile()
    await act(async () => { await surface.seen.onSave(MINE) })
    expect(fileWrite.mock.calls[0]).toEqual([PATH, MINE, 'r1'])
    expect(document.querySelector('[data-stale-write="true"]')).toBeNull()
    expect(onSaved).toHaveBeenCalledWith(MINE)
    await waitFor(() => expect(surface.seen.draftBase).toEqual({ value: MINE, revision: 'r3' }))
  })

  it('an artifact names the revision the page painted', async () => {
    const { saveArtifactBody, agentWrites } = stageArtifact({ acceptFirst: true })
    await openArtifact(agentWrites)
    await act(async () => { await surface.seen.onSave(MINE) })
    expect(saveArtifactBody.mock.calls[0]).toEqual([SLUG, MINE, 'r1'])
    expect(document.querySelector('[data-stale-write="true"]')).toBeNull()
  })

  it('a knowledge item leaves the editor', async () => {
    const { knowledgeItem, saveKnowledgeItem } = stageItem({ stale: false })
    await openItem(knowledgeItem)
    await type(itemBody(), 'alpha\nbeta, revised\n')
    await click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saveKnowledgeItem).toHaveBeenCalledTimes(1))
    expect(saveKnowledgeItem.mock.calls[0][2]).toBe('r1')
    await waitFor(() => expect(screen.queryByPlaceholderText('Markdown supported…')).toBeNull())
    expect(document.querySelector('[data-stale-write="true"]')).toBeNull()
  })
})

describe('a second door onto the same save', () => {
  it('an artifact’s Snapshot is the same save: the same base, the same refusal', async () => {
    const { snapshotArtifactBody, agentWrites } = stageArtifact()
    await openArtifact(agentWrites)
    const snapshot = (surface.seen.actions as Array<{ label: string; run: (d: string) => Promise<void> }>).find((a) => a.label === 'Snapshot')!
    await act(async () => { await snapshot.run(MINE) })
    expect(snapshotArtifactBody.mock.calls[0]).toEqual([SLUG, MINE, 'r1'])
    expect((await staleNotice()).textContent).toMatch(/This artifact changed elsewhere/)
  })

  it('a file’s “Save as artifact” goes over the same base, and a refusal lands in the same notice with the draft kept', async () => {
    stageFile()
    const { saveAsArtifact } = await openFile()
    act(() => { surface.seen.onDraftChange(MINE, true) })
    // The header's Artifact action hands the host the viewer's bound save.
    fireEvent.click(screen.getByRole('button', { name: /artifact/i }))
    const create = vi.fn(async () => { throw stale() })
    let landed: boolean | undefined
    await act(async () => { landed = await saveAsArtifact()!(create) })
    expect(create.mock.calls[0]).toEqual([MINE, 'r1'])
    expect(landed).toBe(false)
    expect((await staleNotice()).textContent).toMatch(/This file changed elsewhere/)
    expect(surface.replaced).toEqual([])
  })

  it('a knowledge item’s tag edit is sent as names added and removed, never as the whole list', async () => {
    const { knowledgeItem, saveKnowledgeItem, updateKnowledgeItem } = stageItem({ stale: false })
    await openItem(knowledgeItem)
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

describe('the workflow editor’s own paths', () => {
  beforeEach(stageWorkflows)

  it('the JSON tab is read-only while a refused change is held, showing that change', async () => {
    await twoTabsSave((b) => type(description(b), 'Tab B’s description.'), 'Tab A’s description.')
    const b = twoTabs.b!
    await staleNotice()
    await click([...b.r.container.querySelectorAll<HTMLElement>('[role="radio"]')].find((el) => (el.textContent ?? '').includes('JSON')))
    const raw = b.r.container.querySelector('[aria-label="mine definition (JSON)"]') as HTMLTextAreaElement
    expect(raw.readOnly).toBe(true)
    expect(JSON.parse(raw.value).description).toBe('Tab B’s description.')
  })

  it('the publish toggle is not undone by the editor’s save: it is refused, and Reapply keeps both', async () => {
    const b = await openTab()
    publishElsewhere('mine') // the definition page's A2A toggle, in another tab
    await type(description(b), 'Edited after it was published.')
    await click(button(b.r.container, 'Save'))
    const notice = await staleNotice()
    expect(workflows.store.get('mine')!.metadata).toEqual({ risk: 'low', a2a_published: true })
    expect(workflows.store.get('mine')!.description).toBe('Read a paper.')
    await reapply(notice)
    await waitFor(() => expect(b.saved).toEqual(['mine']))
    expect(workflows.store.get('mine')!.metadata).toEqual({ risk: 'low', a2a_published: true })
    expect(workflows.store.get('mine')!.description).toBe('Edited after it was published.')
  })

  it('a restore of an old version saved over a newer one is refused, and saved over it only from the review', async () => {
    workflows.store.set('mine', { ...structuredClone(WORKFLOW), version: 3, description: 'Version three.' })
    workflows.store.set('mine@v1', { ...structuredClone(WORKFLOW), version: 1, description: 'The first one.' })
    const restore = await openTab('mine', 1)
    const other = await openTab()
    await type(description(other), 'Version four, from another tab.')
    await click(button(other.r.container, 'Save'))
    expect(workflows.store.get('mine')!.version).toBe(4)

    await click(button(restore.r.container, 'Save'))
    const notice = await staleNotice()
    await waitFor(() => expect(within(notice).getByRole('button', { name: 'Review the difference' }).hasAttribute('disabled')).toBe(false))
    expect(workflows.saves.at(-1)!.body).toMatchObject({ name: 'mine', based_on_version: 1 })
    expect(workflows.store.get('mine')!.description, 'the newer version is still the one that runs').toBe('Version four, from another tab.')
    // Merging a newer version into an old one would restore neither: no Reapply, only the review.
    expect(within(notice).queryByRole('button', { name: 'Reload and reapply' })).toBeNull()
    await click(within(notice).getByRole('button', { name: 'Review the difference' }))
    const dialog = await screen.findByRole('dialog')
    await click(within(dialog).getByRole('button', { name: 'Save mine over it' }))
    await waitFor(() => expect(restore.saved).toEqual(['mine']))
    expect(workflows.store.get('mine')!.description).toBe('The first one.')
    expect(workflows.store.get('mine')!.version).toBe(5)
  })

  it('discarding a refused restore goes back to the workflow instead of re-opening the old version', async () => {
    workflows.store.set('mine', { ...structuredClone(WORKFLOW), version: 3 })
    workflows.store.set('mine@v1', { ...structuredClone(WORKFLOW), version: 1, description: 'The first one.' })
    const restore = await openTab('mine', 1)
    publishElsewhere('mine')
    await click(button(restore.r.container, 'Save'))
    await click(within(await staleNotice()).getByRole('button', { name: 'Discard my change' }))
    expect(restore.cancelled).toEqual(['mine'])
    expect(restore.saved).toEqual([])
  })

  it('a copy replaces nothing and names no revision; a name taken after the check is refused at the name field', async () => {
    workflows.store.set('paper-ingest', { ...structuredClone(WORKFLOW), name: 'paper-ingest', source: 'bundled' })
    const copy = await openTab('paper-ingest')
    workflows.landsFirst = () => workflows.store.set('paper-ingest-copy', { ...structuredClone(WORKFLOW), name: 'paper-ingest-copy' })
    await click(button(copy.r.container, 'Save copy'))
    expect(workflows.saves.at(-1)!.base).toBeUndefined()
    expect(copy.saved).toEqual([])
    expect(copy.r.container.textContent).toContain('You already have a workflow named paper-ingest-copy')
    expect(workflows.store.get('paper-ingest-copy')!.description, 'the workflow that took the name is untouched').toBe('Read a paper.')
    await type(copy.r.container.querySelector<HTMLElement>('input[maxlength="63"]'), 'my-paper-ingest')
    await click(button(copy.r.container, 'Save copy'))
    expect(copy.saved).toEqual(['my-paper-ingest'])
    expect(workflows.saves.at(-1)!.base).toBeUndefined()
  })
})

describe('bugs that happened', () => {
  // The panel re-opens a prompt it read before: the full record paints from the cache while the
  // re-read is still out. It was taken as "seeded" while the draft still held the LIST ROW, so a
  // Save there sent an empty template — over the cached record's revision, which the gateway would
  // accept.
  it('a prompt already in the cache seeds the draft before the form can save it', async () => {
    promptEditorReads()
    writeQuery('prompt:triage', PROMPT, true)
    const save = vi.fn(async () => ({ ok: true, prompt: PROMPT, revision: 'p2' }))
    answer({ prompt: () => new Promise(() => {}), savePrompt: save })
    openPrompt()
    const template = await screen.findByLabelText('Prompt template') as HTMLTextAreaElement
    expect(template.value).toBe('Sort {{items}} by urgency.')
    await click(screen.getByRole('button', { name: /^Save$/ }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect((save.mock.calls[0] as unknown[])[1]).toMatchObject({ content: 'Sort {{items}} by urgency.' })
    expect((save.mock.calls[0] as unknown[])[2]).toBe('p1')
  })

  // Type, leave (the Studio unmounts the editor), come back after the agent learned something. The
  // restored draft was built on r1; saving it over the fresh read's r2 would erase "prefers tea" —
  // the overwrite this guards — so it must name r1 and be refused.
  it('a memory-doc draft restored after the doc changed elsewhere names the copy it was typed on', async () => {
    let reads = 0
    const save = vi.fn((_w: string, c: string, base: string) =>
      base === 'r2' ? Promise.resolve({ value: c, revision: 'r3' }) : Promise.reject(stale('the preferences memory')))
    answer({ memoryDoc: vi.fn(async () => (++reads === 1 ? { value: MEMORY, revision: 'r1' } : { value: MEMORY_LEARNED, revision: 'r2' })), saveMemoryDoc: save })
    const drafts = new Map<string, DocDraft>()
    const first = await openMemory(drafts, MEMORY + '- my own line\n')
    first.unmount()
    await openMemory(drafts)
    await saveMemory()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0][2]).toBe('r1')
    await staleNotice()
  })
})

// ── the app SDK's client, which an app's own page saves through ──────────────────────────────
//
// An app page saving its own settings (`PUT /api/apps/<name>/config`) had no way to send If-Match:
// the SDK's `put`/`patch`/`post` took a path and a body and nothing else, so it got
// `428 revision_required` every time, whatever it had read — and the refusal read
// "[object Object]", because the client took `body.error`, an OBJECT in the platform envelope, as
// the message. The fake gateway refuses exactly as `stale_write_refusal` does — no If-Match is 428,
// a revision other than the stored one 409 — so each case is a round trip through the client an app
// bundle imports, two copies of the app's page sharing one store.

const APP_CONTEXT: AppContext = { name: 'vector-store-qdrant', permissions: { api: ['/api/apps/vector-store-qdrant'] } }
const APP_CONFIG = '/api/apps/vector-store-qdrant/config'
type AppRead = { config: Record<string, unknown>; revision: string }

function appGateway() {
  let config: Record<string, unknown> = { url: 'http://localhost:6333', collection: 'notes' }
  let version = 1
  const revision = () => `rev-${version}`
  const seen: { method: string; headers: Record<string, string> }[] = []
  const envelope = (status: number, code: string, message: string) =>
    new Response(JSON.stringify({ error: { code, message } }), { status, headers: { 'Content-Type': 'application/json' } })
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>))
    if (url === `/api/apps/${APP_CONTEXT.name}/token`) return json({ token: 'app-token', expires_in: 3600 })
    seen.push({ method, headers })
    if (url !== APP_CONFIG) return new Response('not found', { status: 404 })
    if (method === 'GET') return json({ name: APP_CONTEXT.name, config, revision: revision() })
    const claimed = (headers['If-Match'] ?? '').replace(/^W\//, '').replace(/"/g, '').trim()
    if (!claimed) {
      return envelope(428, 'revision_required',
        "This write is checked against the copy of the settings of 'vector-store-qdrant' it was built from, so it must name that copy: read the settings of 'vector-store-qdrant' and send its revision in If-Match.")
    }
    if (claimed !== revision()) {
      return envelope(409, 'stale_write',
        "This write replaces the settings of 'vector-store-qdrant', which changed after the copy it was built from was read — saving it would have undone that change. Nothing was saved: read it again, re-apply the edit, and save.")
    }
    config = JSON.parse(String(init?.body))
    version += 1
    return json({ ok: true, name: APP_CONTEXT.name, config, revision: revision() })
  }))
  return { seen, stored: () => config }
}

describe("an app page saves its settings over the copy it read", () => {
  it('names the revision it read, and the save lands', async () => {
    const gateway = appGateway()
    const api = createAppApi(APP_CONTEXT)
    const read = await api.get<AppRead>(APP_CONFIG)
    const saved = await api.put<AppRead>(APP_CONFIG, { ...read.config, collection: 'journal' }, { basedOn: read.revision })
    expect(saved.config.collection).toBe('journal')
    expect(gateway.stored().collection).toBe('journal')
    // The precondition rides beside the app's own identity, not instead of it.
    const put = gateway.seen.find((r) => r.method === 'PUT')!
    expect(put.headers['If-Match']).toBe(`"${read.revision}"`)
    expect(put.headers.Authorization).toBe('Bearer app-token')
  })

  it('a save from a copy that changed since is refused with 409, and the page can tell', async () => {
    const gateway = appGateway()
    // Two copies of the app's page, both opened on the same settings.
    const first = createAppApi(APP_CONTEXT)
    const second = createAppApi(APP_CONTEXT)
    const a = await first.get<AppRead>(APP_CONFIG)
    const b = await second.get<AppRead>(APP_CONFIG)
    await second.put(APP_CONFIG, { ...b.config, collection: 'journal' }, { basedOn: b.revision })

    const refused = await first.put(APP_CONFIG, { ...a.config, url: 'http://qdrant:6333' }, { basedOn: a.revision }).then(() => null, (e: unknown) => e)
    expect(refused).toBeInstanceOf(Error)
    expect((refused as { status?: number }).status).toBe(409)
    expect((refused as { code?: string }).code).toBe('stale_write')
    // The sentence the gateway wrote, not "[object Object]".
    expect((refused as Error).message).toMatch(/^This write replaces the settings of 'vector-store-qdrant', which changed/)
    // The other page's save survived.
    expect(gateway.stored()).toEqual({ url: 'http://localhost:6333', collection: 'journal' })

    // The SDK hands an app the same predicate core's pages branch on.
    installAppSdk()
    const sdk = (window as unknown as { __personalclaw_modules: Record<string, Record<string, unknown>> })
      .__personalclaw_modules['@personalclaw/app-sdk']
    const isStaleWrite = sdk.isStaleWrite as (e: unknown) => boolean
    expect(typeof isStaleWrite).toBe('function')
    expect(isStaleWrite(refused)).toBe(true)
  })

  it("a save that names no revision is 428, in the gateway's words", async () => {
    const gateway = appGateway()
    const api = createAppApi(APP_CONTEXT)
    const read = await api.get<AppRead>(APP_CONFIG)
    const refused = await api.put(APP_CONFIG, { ...read.config, collection: 'journal' }).then(() => null, (e: unknown) => e)
    expect((refused as { status?: number }).status).toBe(428)
    expect((refused as { code?: string }).code).toBe('revision_required')
    expect((refused as Error).message).toMatch(/must name that copy/)
    expect(gateway.stored().collection).toBe('notes')
  })

  it('patch and post carry the revision the same way', async () => {
    const gateway = appGateway()
    const api = createAppApi(APP_CONTEXT)
    await api.patch(APP_CONFIG, {}, { basedOn: 'rev-7' }).catch(() => {})
    await api.post(APP_CONFIG, {}, { basedOn: 'rev-8' }).catch(() => {})
    const [patch, post] = gateway.seen.filter((r) => r.method !== 'GET')
    expect(patch.headers['If-Match']).toBe('"rev-7"')
    expect(post.headers['If-Match']).toBe('"rev-8"')
  })
})
