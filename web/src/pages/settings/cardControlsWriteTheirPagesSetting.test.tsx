/** A control on a Settings card writes the same setting, the same way, as the control of the same
 *  name on its page (#5897).
 *
 *  The overview's cards carry quick controls: the settings people change most, and the switches
 *  that cut something off. A card is a second writer of each one, so each is held to the first: the
 *  request a click on the card sends is compared with the request the page's own control sends,
 *  method, address and body, at the network, where the gateway sees them. A card that wrote a
 *  neighbouring key, or the right key by another route, would show one thing and change another.
 *
 *  A switch that cuts something off can do only that from a card. Turning inbound access, LAN
 *  discovery or the workflow engine back on widens what reaches in or what runs, and so does
 *  resuming unattended work: the card refuses those, and the page, which explains what opens, is
 *  where they happen. Each refusal is pinned below too: a disabled control, and no request sent. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

type Call = { method: string; url: string; body: unknown }
let calls: Call[] = []
let routes: Record<string, unknown> = {}
/** When set, a read of an address `routes` does not hold never answers, so its card stays loading
 *  rather than drawing an answer of the wrong shape. */
let hangUnknown = false
const ok = (v: unknown) => new Response(JSON.stringify(v), { status: 200, headers: { 'Content-Type': 'application/json' } })
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0)) }

/** The backups status as the gateway answers it, every job present (`durabilityShowsWhatRan`'s shape). */
const job = { last_run: 0, due_in_secs: 0, due: false, ok: null, last_success: 0, problem: null }
const DURABILITY_STATUS = {
  enabled: true,
  export: job,
  snapshot: { ...job, detail: '', folder: '/tmp/snapshots', newest: null },
  drill: { last_run: 0, due_in_secs: 0, due: false },
  sync: {
    ...job, enabled: false, transport: '', encrypt: 'auto', encrypted: false, skipped: '', passphrase_credential: '',
    passphrase_stored: false, removes_old_copies: true, keeps_previous_secs: 900, removal_failed: '',
  },
}

/** What the home holds: each read a card or a page makes, answered the way the gateway answers it. */
function home(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    '/api/config/personalclaw': {
      buddy: { show: false, window: true, tray: true, dock: true, notifications: true },
      routing: { enabled: false, min_samples: 20, local_attempt_timeout_secs: 30 },
      workflows: { enabled: true, surface_mode_default: 'off', workspace_default_mode: 'scratch' },
      companion: { discovery_enabled: true, instance_name: 'Test home' },
      browse: { user_browser_enabled: false },
      durability: { auto_backup: true, time_travel: true },
      external_access: { enabled: true },
      agent: { approval_mode: 'interactive', yolo: false },
      evals: { enabled: false },
      legibility: { discover_tips: true, context_adapters: false },
      revisions: {},
    },
    '/api/dashboard/config': { restore_sessions: true, send_on_enter: true, show_timestamps: false, widget_density: 'comfortable' },
    '/api/agents': { agents: [], default_agent: '' },
    '/api/feedback/producers': { enabled: false },
    '/api/notifications/settings': {
      settings: { mute_all: false, min_severity: 'info', quiet_hours_enabled: false, quiet_hours_start: '22:00', quiet_hours_end: '07:00' },
    },
    '/api/buddy/policy': { show: false, window: true, tray: true, dock: true, notifications: true, steps_back: [] },
    '/api/models/active': { use_cases: { tts: ['LocalOllama:kokoro'], stt: [] } },
    '/api/models/use-cases/tts/settings': { use_case: 'tts', settings: { enabled: true, auto_speak: false }, revision: 'r1' },
    '/api/models/use-cases/stt/settings': { use_case: 'stt', settings: { enabled: false }, revision: 'r1' },
    '/api/incident': { active: false, reason: '', started_at: '' },
    '/api/external-access': { enabled: true, surfaces: [], clients: [], caps: {} },
    '/api/companion/discovery': {
      advertising: true, reason: 'advertising', detail: 'Announced on the local network', service_type: '_personalclaw._tcp',
      instance_name: 'Test home', port: 10880, addresses: ['192.168.1.20'], txt: {},
    },
    '/api/notifications/rules': { rules: [], digest: { schedule: 'off' }, targets: [] },
    '/api/channels': { channels: [] },
    '/api/models/health': { providers: [], callers: [], generated_from: 0 },
    '/api/autonomy': { rungs: [], rung_meta: [], incident_active: false, types: [], reversals: [] },
    '/api/voice/profiles': { profiles: [], bindings: {}, engines: [], bound_engine: '' },
    '/api/models/telemetry': { use_case: 'reasoning', query_class: 'long_reasoning', rows: [] },
    '/api/durability/status': DURABILITY_STATUS,
    '/api/durability/archive': {
      directory: '/tmp/snapshots', archives: [], would_prune: [], tiers: { daily: 14, weekly: 8, monthly: 12 },
      last_drill: { ran: false, ok: null, at: 0, detail: '', archive: '', on_disk: null },
    },
    ...over,
  }
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  localStorage.clear()
  calls = []
  routes = home()
  hangUnknown = false
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (method === 'GET') {
      const known = routes[url.split('?')[0]]
      if (known === undefined && hangUnknown) return new Promise<Response>(() => {})
      return ok(known ?? {})
    }
    if (url === '/api/incident') return ok({ active: true, reason: 'Activated from Settings', started_at: '2026-11-02T08:00:00Z' })
    if (url.startsWith('/api/models/use-cases/')) return ok({ ok: true, settings: init?.body ? JSON.parse(String(init.body)) : {}, revision: 'r2' })
    if (url === '/api/notifications/settings') return ok({ settings: init?.body ? JSON.parse(String(init.body)) : {} })
    return ok({ ok: true })
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

/** Every request a click on `control` sent: the writes, with the reads it set off left out. */
async function writesOf(control: HTMLElement): Promise<Call[]> {
  const before = calls.length
  await act(async () => { fireEvent.click(control); await flush() })
  return calls.slice(before).filter((c) => c.method !== 'GET')
}

/** A control by its name, once the read behind it has landed and it can be used. */
async function enabledControl(name: string): Promise<HTMLElement> {
  const control = await screen.findByRole('switch', { name })
  await waitFor(() => expect(control).not.toBeDisabled())
  return control
}

/** The card for section `id`, as the overview renders it. */
async function card(id: string) {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === id)
  if (!tile) throw new Error(`no card for ${id}`)
  function Host() { return <>{tile!.render('', () => {})}</> }
  render(<Host />)
}

/** The reads a page makes that answer a shape the plain routes above do not carry, stubbed at the
 *  api the way that page's own tests stub them. Only reads: every write still goes to the network. */
const PAGE_READS: Record<string, (api: typeof import('../../lib/api').api) => void> = {
  durability: (api) => {
    vi.spyOn(api, 'settingsProviders').mockResolvedValue([])
    vi.spyOn(api, 'durabilityConflicts').mockResolvedValue({
      conflicts: [], truncated: false, counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
      surfaces: {}, sync: { enabled: false, transport: '', configured: false },
    } as never)
    vi.spyOn(api, 'durabilityHistory').mockResolvedValue({ enabled: false, git: true, dir: '/tmp/h', roots: [] } as never)
    vi.spyOn(api, 'durabilityHistoryTimeline').mockResolvedValue({ root: 'config', label: 'Configuration', commits: 0, entries: [], forward_refs: [] } as never)
  },
}

/** The page panel for a section, as its page renders it. */
async function page(id: string) {
  const { api } = await import('../../lib/api')
  PAGE_READS[id]?.(api)
  const { SECTIONS } = await import('./SettingsPage')
  const section = SECTIONS.find((s) => s.id === id)
  if (!section) throw new Error(`no section ${id}`)
  function Host() { return <>{section!.render({ go: () => {}, navigate: () => {}, query: {}, setQuery: () => {} })}</> }
  render(<Host />)
}

/** Each quick control: the section, its name on the card and on the page, and the request both send. */
const CONTROLS: { id: string; card: string; page: string; sends: Call }[] = [
  { id: 'notifications', card: 'Mute all notifications', page: 'Mute all notifications',
    sends: { method: 'PUT', url: '/api/notifications/settings', body: { mute_all: true } } },
  { id: 'notifications', card: 'Enable quiet hours', page: 'Enable quiet hours',
    sends: { method: 'PUT', url: '/api/notifications/settings', body: { quiet_hours_enabled: true } } },
  { id: 'buddy', card: 'Buddy status', page: 'Buddy status',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'buddy.show', value: true } } },
  { id: 'voice', card: 'Speak replies aloud', page: 'Speak replies aloud',
    sends: { method: 'PUT', url: '/api/models/use-cases/tts/settings', body: { enabled: true, auto_speak: true } } },
  { id: 'routing', card: 'Adaptive routing', page: 'Adaptive routing',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'routing.enabled', value: true } } },
  { id: 'durability', card: 'Automatic backups', page: 'Automatic backups',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'durability.auto_backup', value: false } } },
  // The switches that cut something off, turned off: the one way a card turns them.
  { id: 'workflows', card: 'Workflow engine', page: 'Workflow engine',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'workflows.enabled', value: false } } },
  { id: 'external-access', card: 'Allow inbound access', page: 'Allow inbound access',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'external_access.enabled', value: false } } },
  { id: 'companion', card: 'LAN discovery', page: 'LAN discovery',
    sends: { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'companion.discovery_enabled', value: false } } },
  // Pausing unattended work is the incident switch turned ON: that is the direction that stops things.
  { id: 'guardrails', card: 'Pause unattended work', page: 'Incident mode',
    sends: { method: 'POST', url: '/api/incident', body: { reason: 'Activated from Settings' } } },
]

describe('each card control writes what its page\'s control writes', () => {
  it('🪤 vacuity floor: ten quick controls, over nine sections', () => {
    expect(CONTROLS).toHaveLength(10)
    expect(new Set(CONTROLS.map((c) => c.id)).size).toBe(9)
  })

  it.each(CONTROLS.map((c) => [`${c.id} › ${c.card}`, c] as const))('%s', async (_name, c) => {
    await card(c.id)
    const fromCard = await writesOf(await enabledControl(c.card))
    cleanup()
    vi.resetModules()
    sessionStorage.clear()
    routes = home()
    await page(c.id)
    const fromPage = await writesOf(await enabledControl(c.page))
    expect(fromCard, 'the card sent one write').toHaveLength(1)
    expect(fromPage, 'the page sent one write').toHaveLength(1)
    expect(fromCard[0], 'the card writes what the page writes').toEqual(fromPage[0])
    expect(fromCard[0]).toEqual(c.sends)
  })
})

describe('each control on a card is named on screen by the name it is announced by', () => {
  // A switch draws no words of its own, so a row that shows a status pill beside it ("Guardrails ·
  // Normal operation · [switch]") reads as the pill's state being switched: "guardrails are off".
  // Each control's row shows its accessible name as its label, the way the page's own rows do.

  /** Whether `name` is drawn as text in `control`'s row: an element reading exactly `name`, outside
   *  the control and not hidden from the a11y tree, whose nearest ancestor holding the control holds
   *  no other control. So a name drawn on another row, or a card title, does not count. */
  function namedInItsRow(control: HTMLElement, name: string): boolean {
    return screen.queryAllByText(name, { exact: true }).some((label) => {
      if (control.contains(label) || label.closest('[aria-hidden="true"]')) return false
      let row = label.parentElement
      while (row && !row.contains(control)) row = row.parentElement
      if (!row) return false
      const others = [...row.querySelectorAll('[role="switch"], [role="combobox"], select, button, a[href]')]
      return others.every((c) => c === control || control.contains(c) || c.contains(control))
    })
  }

  /** The words drawn next to a control, for the failure message: the text of its nearest enclosing
   *  element that has any outside it. */
  function besideIt(control: HTMLElement): string[] {
    for (let el = control.parentElement; el; el = el.parentElement) {
      const texts: string[] = []
      const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        const at = n.parentElement
        if (!at || control.contains(at) || at.closest('[aria-hidden="true"]')) continue
        const t = n.textContent?.trim()
        if (t) texts.push(t)
      }
      if (texts.length) return texts
    }
    return []
  }

  /** The card for section `id` inside the providers the overview has (Appearance reads them). */
  async function cardInApp(id: string) {
    const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
    const { ThemeProvider } = await import('../../app/theme')
    const { AppearanceProvider } = await import('../../app/appearance')
    const { PersonalityProvider } = await import('../../app/personality')
    const { IdentityProvider } = await import('../../app/identity')
    const tile = SETTINGS_WIDGETS.find((w) => w.id === id)
    if (!tile) throw new Error(`no card for ${id}`)
    function Host() { return <>{tile!.render('', () => {})}</> }
    render(
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <Host />
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
    )
  }

  it('🔑 every switch and select on the overview\'s cards shows its accessible name in its row', async () => {
    const { SETTINGS_PAGES } = await import('./settingsPages')
    const ids = SETTINGS_PAGES.flatMap((p) => p.parts ?? p.sections)
    const seen: string[] = []
    const unnamed: string[] = []
    hangUnknown = true
    for (const id of ids) {
      cleanup()
      routes = home()
      await cardInApp(id)
      await act(flush)
      for (const control of [...screen.queryAllByRole('switch'), ...screen.queryAllByRole('combobox')]) {
        const name = control.getAttribute('aria-label') ?? ''
        if (!name || !namedInItsRow(control, name)) {
          unnamed.push(`${id} › ${name || '(no name)'}: beside it is ${JSON.stringify(besideIt(control))}`)
        }
        seen.push(name)
      }
    }
    expect(unnamed, 'controls whose row does not show their name').toEqual([])
    // Vacuity floor: the ten quick controls are drawn, and so checked, with this home's data.
    for (const c of CONTROLS) expect(seen, `${c.id} › ${c.card} was not drawn`).toContain(c.card)
    expect(seen.length).toBeGreaterThanOrEqual(CONTROLS.length)
  })
})

describe('a card only cuts off: what widens is done on the page', () => {
  it.each([
    ['workflows', 'Workflow engine', { workflows: { enabled: false } }],
    ['external-access', 'Allow inbound access', {}],
    ['companion', 'LAN discovery', { companion: { discovery_enabled: false, instance_name: 'Test home' } }],
  ] as const)('%s › %s, off, cannot be turned on from the card', async (id, name, cfg) => {
    const base = home()
    routes = {
      ...base,
      '/api/config/personalclaw': { ...(base['/api/config/personalclaw'] as object), ...cfg },
      '/api/external-access': { enabled: false, surfaces: [], clients: [], caps: {} },
      '/api/companion/discovery': { advertising: false, reason: 'disabled', detail: 'LAN discovery is off', instance_name: 'Test home' },
    }
    await card(id)
    const control = await screen.findByRole('switch', { name })
    await waitFor(() => expect(control.getAttribute('aria-checked')).toBe('false'))
    // Soft-off: reachable, and saying where the other direction is done.
    expect(control.getAttribute('aria-disabled'), 'turning it on is the page\'s').toBe('true')
    expect(control.getAttribute('title')).toMatch(/ from /)
    expect(await writesOf(control)).toEqual([])
  })

  it('guardrails › Pause unattended work, once paused, cannot be resumed from the card', async () => {
    routes = home({ '/api/incident': { active: true, reason: 'Activated from Settings', started_at: '2026-11-02T08:00:00Z' } })
    await card('guardrails')
    const control = await screen.findByRole('switch', { name: 'Pause unattended work' })
    await waitFor(() => expect(control.getAttribute('aria-checked')).toBe('true'))
    expect(control.getAttribute('aria-disabled'), 'resuming is the page\'s').toBe('true')
    expect(control.getAttribute('title')).toMatch(/Security › Guardrails/)
    expect(await writesOf(control)).toEqual([])
  })
})
