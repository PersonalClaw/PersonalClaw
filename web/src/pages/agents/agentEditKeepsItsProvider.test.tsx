/** Editing an agent must not rewrite its provider or its origin.
 *
 * `AgentForm.draftToPayload` is shared by the create PAGE and by `AgentDetail`'s in-panel EDIT, and it
 * stated two fields the form has no control for:
 *
 *   provider: 'native',  source: 'personalclaw',
 *
 * `PUT /api/agents/{name}` applies each field whenever its key is present — `if "provider" in body:
 * agent.provider = body.get("provider", "")`, and the same for `source`. So changing one word of an
 * agent's description rewrote both. Two distinct losses:
 *
 *  1. **`acp:<cli>` → native.** The profile's runtime binding is destroyed, and `provider_agent` /
 *     `acp_mode` — meaningful only under an ACP provider — are left behind as fields nothing reads.
 *  2. **`'' → native`.** `AgentProfile.provider` is declared `default=""` with *"Empty inherits the
 *     global agent.provider default"*, resolved at `loader.py:4325` as `provider or
 *     config.agent.provider`. An agent that was FOLLOWING the global got silently pinned. This half
 *     needs no ACP setup at all to hit — it is every profile whose provider was never set explicitly.
 *
 * …plus `source`, documented as *"Agent origin: personalclaw, marketplace, or builtin"*: a
 * marketplace-installed agent recorded itself as hand-authored. Nothing gates behaviour on it today,
 * so that half falsifies provenance rather than breaking function.
 *
 * 🪤 AND NOTHING WARNED, because the one surface that could have shown the mismatch was hiding it:
 * `AgentDetail`'s chip was the literal string 'Native' for every non-reserved agent while
 * `providerMeta` sat imported and unused in the same file. An ACP profile read "Native" BEFORE the edit
 * made it true.
 *
 * 🔑 THE FIX SHAPE IS NOT MINE — it is issue 689's, which this file is named after
 * (`schedule/renameKeepsItsAction.test.ts`). There, renaming a notification trigger replaced its
 * action, because `ScheduleForm.draftToPayload` described an action the form could not edit. That
 * file's recorded verdict: *"The server was never at fault: `_update_schedule` only touches the action
 * when one is present in the body… So the fix is to stop describing an action the form cannot edit."*
 * Identical server contract, identical cure. The assertions below sit on the same boundary it chose —
 * the payload — because that is where the data was destroyed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { toDraft, draftToPayload } from './AgentForm'
import { providerMeta } from './agentMeta'
import type { SavedAgent } from '../../lib/api'

/** An ACP-backed profile: the runtime binding lives in three fields the native builder cannot edit. */
const ACP: SavedAgent = {
  name: 'kiro', provider: 'acp:claude-code', provider_agent: 'sonnet', acp_mode: 'acceptEdits',
  description: 'Drives the Claude Code CLI', model: '', source: 'marketplace',
}
/** The commoner case: provider never set, so it INHERITS the global default. */
const INHERITING: SavedAgent = { name: 'scout', provider: '', description: 'follows the global', model: 'x' }

describe('draftToPayload describes no field the form cannot edit', () => {
  it('🔑 omits `provider` entirely — the key’s ABSENCE is what preserves the stored value', () => {
    const body = draftToPayload(toDraft(ACP))
    // Not `toBe('acp:claude-code')`: echoing the value back would also work today, but it would make
    // this form an authority on a field it cannot show. Absence is the contract the server offers.
    expect(body, 'no provider key at all').not.toHaveProperty('provider')
  })

  it('omits `source`, so a marketplace agent stays a marketplace agent', () => {
    expect(draftToPayload(toDraft(ACP))).not.toHaveProperty('source')
  })

  it('🪤 and it still sends everything the form DOES edit', () => {
    // The failure mode of an over-eager fix: strip too much and an edit silently stops saving. Every
    // key here has a control in `AgentForm`.
    const body = draftToPayload(toDraft(ACP))
    for (const k of [
      'name', 'description', 'model', 'system_prompt', 'voice', 'natural_voice', 'approval_mode',
      'skills', 'tools', 'triggers', 'default_dir', 'memory_store', 'specialty', 'route_hints',
    ]) expect(body, `${k} is editable here and must still be sent`).toHaveProperty(k)
  })

  it('an edit that changes only the description sends the description and nothing about the runtime', () => {
    // The issue's own repro, at the payload boundary.
    const body = draftToPayload({ ...toDraft(ACP), description: 'Drives the Claude Code CLI.' })
    expect(body.description).toBe('Drives the Claude Code CLI.')
    expect(body).not.toHaveProperty('provider')
    expect(body).not.toHaveProperty('provider_agent')
    expect(body).not.toHaveProperty('acp_mode')
  })

  it('the inheriting case is covered by the same omission', () => {
    // `'' → 'native'` was the half reachable without any ACP setup: a pin masquerading as a no-op.
    expect(draftToPayload(toDraft(INHERITING))).not.toHaveProperty('provider')
  })
})

describe('the CREATE path still declares native — dropping the key there is the mirror defect', () => {
  const mkAgent = vi.fn(async (_b: Record<string, unknown>): Promise<unknown> => ({ ok: true }))

  beforeEach(() => {
    mkAgent.mockClear()
    vi.resetModules()
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        createAgent: mkAgent,
        skills: () => Promise.resolve([]),
        tools: () => Promise.resolve([]),
        hooks: () => Promise.resolve([]),
      },
    }))
    // 🪤 SPREAD THE ORIGINAL. A bare replacement here deletes `loadAcpDiscovered`, which
    // `agentsData.fetchAgentGroups` calls — and because `vi.doMock` registrations outlive the
    // describe that made them, the chip block below then rendered a page whose fetcher threw
    // `loadAcpDiscovered is not a function`. Mock the export you mean, keep the rest.
    vi.doMock('../../lib/agents', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      useActiveChatModelOptions: () => ({ options: [] }),
    }))
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('🪤 posts provider "native" explicitly, because omitting it would mean INHERIT', async () => {
    // `POST /api/agents` reads `body.get("provider", "")`, and empty inherits the global
    // `agent.provider`. So on an instance whose global default is an ACP CLI, a create payload with no
    // provider would make the *native-agent builder* produce ACP agents. This is why the fix is not
    // "delete the line".
    const { AgentCreatePage } = await import('./AgentCreatePage')
    render(<AgentCreatePage onBack={() => {}} onCreated={() => {}} />)
    await userEvent.type(screen.getByRole('textbox', { name: /name/i }), 'helper')
    await userEvent.click(screen.getByRole('button', { name: /Create agent/i }))
    await waitFor(() => expect(mkAgent).toHaveBeenCalledTimes(1))
    const body = mkAgent.mock.calls[0][0]
    expect(body.provider, 'the create page authors NATIVE agents').toBe('native')
    expect(body.source).toBe('personalclaw')
    expect(body.name).toBe('helper')
  })
})

describe('the chip names the real provider, so the mismatch is visible', () => {
  const catalog = {
    agents: [
      { ...ACP, reserved: false, editable: true },
      { ...INHERITING, reserved: false, editable: true },
    ],
    default_agent: 'scout',
  }
  beforeEach(() => {
    vi.resetModules()
    sessionStorage.clear()
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        agents: () => Promise.resolve(catalog),
        agentProviders: () => Promise.resolve([]),
        syncAgents: () => Promise.resolve({ ok: true }),
        hooks: () => Promise.resolve([]),
        mcpActiveServers: () => Promise.resolve([]),
      },
    }))
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  /** Open the detail panel and return IT, not the page.
   *
   *  🪤 SCOPED ON PURPOSE. The list page renders a SECTION HEADED "Native" (the group that holds every
   *  `cfg.agents` entry — `nativeCaptionHonest.test.ts` owns its caption), so an unscoped
   *  `queryByText('Native')` matches the heading and an unscoped `findByText` throws on two matches.
   *  Both of my first attempts failed that way. `SidePanel` is a `role="region"` named by its title,
   *  which is the agent's name — so the panel is addressable, and the assertions can mean the chip. */
  async function openPanel(name: string) {
    const { AgentsListPage } = await import('./AgentsListPage')
    render(<AgentsListPage query={{ open: `native:${name}` }} setQuery={() => {}} onCreate={() => {}} />)
    await screen.findByRole('button', { name: /^Edit$/i })
    return screen.getByRole('region', { name })
  }

  it('🔴 an acp:<cli> profile is no longer labelled "Native"', async () => {
    const panel = await openPanel('kiro')
    expect(within(panel).getByText('Claude Code')).toBeInTheDocument()
    // The false statement is GONE from the panel, not merely joined by the true one.
    expect(within(panel).queryByText('Native'), 'the hardcoded label must not survive').toBeNull()
  })

  it('🪤 an agent with no explicit provider still reads "Native" — nobody’s label got worse', async () => {
    // `providerMeta('')` answers 'Native'. That is today's string for the common case, so this change
    // cannot regress an existing instance. It is also not strictly TRUE (empty means inherit the
    // global), which the code comments record as a separate, smaller finding.
    const panel = await openPanel('scout')
    expect(within(panel).getByText('Native')).toBeInTheDocument()
  })

  it('providerMeta is the shared owner, and it maps the ids this page can receive', () => {
    expect(providerMeta('').label).toBe('Native')
    expect(providerMeta('native').label).toBe('Native')
    expect(providerMeta('acp:claude-code').label).toBe('Claude Code')
    expect(providerMeta('acp:kiro-cli').label).toBe('Kiro Cli')
  })
})

describe('VACUITY: the server contract and the field semantics this fix relies on', () => {
  const REPO = join(process.cwd(), '..')
  const py = (rel: string) => readFileSync(join(REPO, 'src/personalclaw', rel), 'utf8')

  it('PUT /api/agents/{name} really is a partial update keyed on presence', () => {
    // If this ever became an unconditional assignment, omitting the key would start CLEARING the
    // provider instead of preserving it — the fix would invert into a worse bug, silently.
    const src = py('dashboard/handlers/agents.py')
    // 🪤 Bounded to the next top-level `async def`, not to the next blank line: a `[\s\S]*?\n\n`
    // window stops inside the docstring, well before the assignment block.
    const handler = src.match(
      /async def api_personalclaw_agent_update[\s\S]*?(?=\nasync def |\ndef |$)/,
    )?.[0] ?? ''
    expect(handler, 'found the update handler').not.toBe('')
    expect(handler, 'and the window reached the assignment block').toMatch(/agent\.provider = /)
    expect(handler, 'provider is applied only when present').toMatch(/if "provider" in body:/)
    expect(handler, 'and so is source').toMatch(/if "source" in body:/)
  })

  it('POST /api/agents really defaults an absent provider to "" (inherit), not to native', () => {
    // The whole reason the create page must state `native` itself.
    const src = py('dashboard/handlers/agents.py')
    const handler = src.match(
      /async def api_personalclaw_agents_create[\s\S]*?(?=\nasync def |\ndef |$)/,
    )?.[0] ?? ''
    expect(handler, 'found the create handler').not.toBe('')
    expect(handler).toMatch(/provider=body\.get\("provider", ""\)/)
  })

  it('AgentProfile.provider is declared empty-means-inherit, and the loader honours that', () => {
    const loader = py('config/loader.py')
    const cls = loader.match(/class AgentProfile:[\s\S]*?\n    provider_agent/)?.[0] ?? ''
    expect(cls, 'found AgentProfile.provider').not.toBe('')
    expect(cls, 'it defaults to empty, NOT to "native"').toMatch(/default=""/)
    expect(cls, 'and empty is documented as inheriting the global').toMatch(/[Ee]mpty inherits the/)
    // The resolution site that makes the inheritance real.
    expect(loader).toMatch(/provider = getattr\(agent_cfg, "provider", ""\) or config\.agent\.provider/)
  })

  it('source is a real vocabulary, not a constant the form may assert', () => {
    expect(py('config/loader.py')).toMatch(/personalclaw, marketplace, or builtin/)
  })

  it('GET /api/agents projects every profile — an acp one is not filtered out of this page', () => {
    // Why an ACP profile reaches `NativeAgentDetail` at all: the list handler has no provider filter.
    const handler = py('dashboard/handlers/agents.py')
      .match(/async def api_personalclaw_agents\(request[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the list handler').not.toBe('')
    expect(handler, 'it asdict()s every entry with no provider filter').toMatch(/dataclasses\.asdict\(agent_cfg\)/)
    expect(handler, 'and filters on nothing but reservation').not.toMatch(/provider/)
  })

  it('🔑 REACHABILITY: an acp:<cli> profile is written by an ordinary Settings pick', () => {
    // Without this the whole defect could be dismissed as "only if you hand-edit config.json".
    // `ensureBindableAgentName` materializes a bindable profile POSTing the ACP runtime id as the
    // provider, and its callers are two plain comboboxes — Agent defaults → Default agent, and
    // Chat → Warm pool agent. So a user reaches the corrupt state through the UI alone.
    const agents = readFileSync(join(process.cwd(), 'src/lib/agents.ts'), 'utf8')
    const fn = agents.match(/export async function ensureBindableAgentName[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn, 'found ensureBindableAgentName').not.toBe('')
    expect(fn, 'it POSTs the acp runtime id AS the provider').toMatch(/createAgent\(\{[\s\S]*?provider: providerId/)
    // And the profiles it writes are ordinary editable rows: no provider filter anywhere upstream.
    const data = readFileSync(join(process.cwd(), 'src/pages/agents/agentsData.ts'), 'utf8')
    expect(data, 'the native group takes every profile verbatim').toMatch(/agents: nat\.value\.agents/)
  })

  it('🪤 SEVERITY: a leftover provider_agent is READ by the native bridge, so it is not inert', () => {
    // Why preserving all three fields together matters. `provider: 'native'` + a stale ACP modeId in
    // `provider_agent` makes the bridge look up a profile that does not exist, and the agent's whole
    // definition is dropped for the turn. If either line below moves, re-argue the doc comment.
    expect(py('dashboard/chat_runner.py'), 'provider_agent is preferred as the profile name')
      .toMatch(/agent=provider_agent or session\.agent or None/)
    expect(py('providers/provider_bridge.py'), 'and that name is looked up in cfg.agents')
      .toMatch(/prof = \(cfg\.agents or \{\}\)\.get\(agent\) if agent else None/)
  })

  it('the sibling precedent this fix follows is still on disk and still says why', () => {
    // If issue 689's rail is ever deleted, the reasoning above loses its anchor.
    const sib = readFileSync(join(process.cwd(), 'src/pages/schedule/renameKeepsItsAction.test.ts'), 'utf8')
    // 🪤 The sentence is WRAPPED across two lines of a block comment, so a literal search for it
    // fails against a file that says exactly the right thing. Un-wrap the ` * ` continuations first.
    expect(sib.replace(/\n\s*\*/g, '')).toMatch(/stop describing an\s+action the form cannot edit/)
  })
})
