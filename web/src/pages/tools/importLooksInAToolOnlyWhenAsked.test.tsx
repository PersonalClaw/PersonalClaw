import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'

// ── The Tools page reads another tool's settings only when she asks ──────────────────────────────
//
// Every visit sent `GET /api/mcp/importable`, and the gateway answered it by reading Claude Code's
// and Codex's settings, while Settings › Security says PersonalClaw reads only inside its home
// unless a place is turned on there. The page's own read now names no tool: a tool not turned on
// comes back `looked: false`, and the page says it has not looked there and offers the press that
// does, Look in <tool>, which names that tool's place for that one read. What the press found is
// kept by the page, so no refresh reads that tool again; an Import is a press too.

const importableMcp = vi.fn()
const importMcpServer = vi.fn()
const probeMcp = vi.fn()

const CLAUDE = { place: 'setup:claude_code', name: 'Claude Code' }
const CODEX = { place: 'setup:codex', name: 'Codex' }
const TOOLS = [CLAUDE, CODEX]
const WEATHER = {
  id: 'a1b2c3d4e5f60718', name: 'weather-fixture', backend: 'Claude Code', place: CLAUDE.place,
  scope: 'user', origin: 'User scope', note: '', transport: 'stdio', command: 'npx', args: ['weather'],
  url: '', env: [], headers: [],
}
/** What each tool holds that PersonalClaw does not. */
let held: Record<string, (typeof WEATHER)[]> = {}
/** The places turned on in Settings › Security. */
let allowed: string[] = []

/** The gateway's answer: a tool is read only when the request names it or it is turned on. */
function answer(lookIn: string[] = []) {
  const looked = (place: string) => lookIn.includes(place) || allowed.includes(place)
  return {
    servers: TOOLS.filter((t) => looked(t.place)).flatMap((t) => held[t.place] ?? []),
    unreadable: [],
    tools: TOOLS.map((t) => ({ ...t, looked: looked(t.place), allowed: allowed.includes(t.place) })),
  }
}

function mockApi() {
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: () => {},
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({
        tools: [{ name: 'gh_search', provider: 'gh', description: 'search', parameters: {}, requires_approval: false, risk_level: 'safe' }],
        load_failures: [],
      }),
      mcpServers: () => Promise.resolve([]),
      importableMcp: (...a: unknown[]) => importableMcp(...a),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      probeMcp: () => probeMcp(),
      importMcpServer: (...a: unknown[]) => importMcpServer(...a),
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  function Harness() {
    const [query, setQueryState] = useState<Record<string, string>>({})
    const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return <ToolsPage query={query} setQuery={setQuery} />
  }
  render(<Harness />)
  await waitFor(() => expect(screen.getByText('gh_search')).toBeInTheDocument())
}

/** The places each read named, in order: `[]` for a read that named none. */
const named = () => importableMcp.mock.calls.map(([places]) => (places as string[] | undefined) ?? [])

const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  held = { [CLAUDE.place]: [WEATHER], [CODEX.place]: [] }
  allowed = []
  importableMcp.mockReset().mockImplementation((places?: string[]) => Promise.resolve(answer(places)))
  importMcpServer.mockReset().mockResolvedValue({ results: [{ name: WEATHER.name }] })
  probeMcp.mockReset().mockResolvedValue({ ok: true })
  mockApi()
})

describe('the import list on a visit', () => {
  it('names no tool, says it has not looked in them, and offers the press for each', async () => {
    await mount()
    expect(await screen.findByText(/PersonalClaw has not looked in Claude Code or Codex for MCP servers to import/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Look in Claude Code/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Look in Codex/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings › Security' })).toHaveAttribute('href', '#/settings/security')
    expect(named().every((places) => places.length === 0)).toBe(true)
    expect(screen.queryByText(WEATHER.name)).toBeNull()
    expect(screen.queryByText(/Discovered in other tools/)).toBeNull()
  })

  it('lists a tool turned on in Settings › Security without a press, and still offers the others', async () => {
    allowed = [CLAUDE.place]
    await mount()
    expect(await screen.findByText(/Discovered in other tools \(1\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Look in Claude Code/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Look in Codex/ })).toBeInTheDocument()
    expect(named().every((places) => places.length === 0)).toBe(true)
  })
})

describe('the Look in press', () => {
  it('reads the tool it names and no other, and shows what it found', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /Look in Claude Code/ }))
    // Opened: she asked to see what the tool holds.
    expect(await screen.findByText(WEATHER.name)).toBeInTheDocument()
    expect(named().filter((places) => places.length > 0)).toEqual([[CLAUDE.place]])
    expect(screen.queryByRole('button', { name: /Look in Claude Code/ })).toBeNull()
    expect(screen.getByText(/PersonalClaw has not looked in Codex for MCP servers to import/)).toBeInTheDocument()
  })

  it('says so when the tool it looked in has nothing to import', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /Look in Codex/ }))
    expect(await screen.findByText(/Nothing to import from Codex: it has no MCP server PersonalClaw doesn't already have/)).toBeInTheDocument()
    expect(named().filter((places) => places.length > 0)).toEqual([[CODEX.place]])
  })

  it('is not repeated by a refresh: a re-read of the page names no tool', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /Look in Claude Code/ }))
    await screen.findByText(WEATHER.name)
    const before = importableMcp.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Re-probe MCP servers' }))
    await waitFor(() => expect(importableMcp.mock.calls.length).toBeGreaterThan(before))
    await settle()
    expect(named().slice(before).every((places) => places.length === 0)).toBe(true)
    // What the press found still stands.
    expect(screen.getByText(WEATHER.name)).toBeInTheDocument()
  })

  it('an Import names the place its row came from, and looks again only where she looked', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /Look in Claude Code/ }))
    await screen.findByText(WEATHER.name)
    held = { [CLAUDE.place]: [], [CODEX.place]: [] }
    fireEvent.click(screen.getByRole('button', { name: /Import/ }))
    await waitFor(() => expect(importMcpServer).toHaveBeenCalledTimes(1))
    expect(importMcpServer.mock.calls[0][0]).toMatchObject({ id: WEATHER.id, name: WEATHER.name, place: CLAUDE.place })
    await waitFor(() => expect(screen.queryByText(WEATHER.name)).toBeNull())
    expect(named().filter((places) => places.length > 0)).toEqual([[CLAUDE.place], [CLAUDE.place]])
  })
})
