import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'
import { STORED_VALUE_MASK, buildMcpEdit, envFormFields, formatArgs, parseArgs } from './mcpServerEnv'

// ── An MCP server can be imported, edited and removed, and a stored value never reaches the page ──
//
// Three defects on the Tools page, one surface:
//   · Import did nothing a user could see. The request added the server and removed it again (a
//     second scope name pointing at the same file), answered 200, and the row stayed under
//     "Discovered in other tools". The call also had no catch, so a real refusal said nothing.
//   · `/api/mcp/importable` sent each Claude Code server's env and header VALUES to this page, which
//     never shows them. It sends names and whether each has a value now.
//   · There was no edit for an existing server. The edit form reads names, plain values, and for a
//     stored value only that one is saved: the line shows a mask, and left as it is, the saved value
//     is kept (`keepEnv`) — so the secret never comes to the browser and back.

describe('the edit form’s fields', () => {
  it('prefills a stored value as the mask and a plain one as itself', () => {
    expect(envFormFields([
      { name: 'GITHUB_TOKEN', plain: false, hasValue: true },
      { name: 'EMPTY', plain: false, hasValue: false },
      { name: 'LOG_LEVEL', plain: true, value: 'debug' },
    ])).toEqual({ secretText: `GITHUB_TOKEN=${STORED_VALUE_MASK}\nEMPTY=`, plainText: 'LOG_LEVEL=debug' })
  })

  it('a line left holding the mask keeps the saved value, and a typed one replaces it', () => {
    expect(buildMcpEdit(`GITHUB_TOKEN=${STORED_VALUE_MASK}\nNEW=abc`, 'LOG_LEVEL=info')).toEqual({
      env: { LOG_LEVEL: 'info', NEW: 'abc' },
      plainEnv: ['LOG_LEVEL'],
      keepEnv: ['GITHUB_TOKEN'],
    })
    expect(buildMcpEdit('GITHUB_TOKEN=ghp_new', '')).toEqual({
      env: { GITHUB_TOKEN: 'ghp_new' }, plainEnv: undefined, keepEnv: undefined,
    })
  })

  it('a masked line moved to Plain values keeps the value and marks it plain', () => {
    expect(buildMcpEdit('', `REGION=${STORED_VALUE_MASK}`)).toEqual({
      env: undefined, plainEnv: ['REGION'], keepEnv: ['REGION'],
    })
  })

  it('a deleted line sends nothing for it, so the variable is removed', () => {
    expect(buildMcpEdit('', '')).toEqual({ env: undefined, plainEnv: undefined, keepEnv: undefined })
  })
})

describe('arguments survive an edit that does not touch them', () => {
  it.each([
    [['-y', '@modelcontextprotocol/server-filesystem', '/tmp']],
    [['--root', '/Users/me/My Documents', '--name', 'say "hi"']],
    [['C:\\Program Files\\srv\\bin.exe', 'plain\\backslash', 'trailing\\']],
    [['', "it's"]],
  ])('%j', (args) => {
    expect(parseArgs(formatArgs(args))).toEqual(args)
  })

  it('reads what a user types: quotes group, a bare backslash is itself', () => {
    expect(parseArgs('  --name "My Server"  C:\\x \'single quoted\'  ')).toEqual(
      ['--name', 'My Server', 'C:\\x', 'single quoted'])
  })
})

// ── The page ─────────────────────────────────────────────────────────────────────────────────────

const saveMcpServer = vi.fn()
const importMcpServer = vi.fn()
const notify = vi.fn()
const servers = [{ name: 'gh', status: 'connected', enabled: true, tools: ['gh_search'] }]
const tools = [
  { name: 'gh_search', provider: 'gh', description: 'search', parameters: {}, requires_approval: false, risk_level: 'safe' },
]
const importable = [{
  name: 'cc-notion', backend: 'Claude Code', command: 'npx', args: ['-y', 'notion-mcp'], url: '',
  env: [{ name: 'NOTION_TOKEN', hasValue: true }], headers: [],
}]

function mockApi(definition: unknown) {
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (...a: unknown[]) => notify(...a),
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve(importable),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      mcpServerDefinition: () => Promise.resolve(definition),
      saveMcpServer: (...a: unknown[]) => { saveMcpServer(...a); return Promise.resolve({ ok: true, name: 'gh' }) },
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

const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  saveMcpServer.mockClear()
  importMcpServer.mockReset()
  notify.mockClear()
})

describe('Edit on an MCP server', () => {
  it('shows a stored value as the mask, and saving untouched keeps it without sending it', async () => {
    mockApi({
      name: 'gh', editable: true, command: 'npx', args: ['-y', 'gh-mcp', '/My Repos'],
      env: [
        { name: 'GITHUB_TOKEN', plain: false, hasValue: true },
        { name: 'LOG_LEVEL', plain: true, value: 'debug' },
      ],
    })
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Edit gh' }))
    const envField = await screen.findByRole('textbox', { name: 'Environment' })
    expect((envField as HTMLTextAreaElement).value).toBe(`GITHUB_TOKEN=${STORED_VALUE_MASK}`)
    expect((screen.getByRole('textbox', { name: 'Plain values' }) as HTMLTextAreaElement).value).toBe('LOG_LEVEL=debug')
    expect((screen.getByRole('textbox', { name: 'Arguments' }) as HTMLInputElement).value).toBe('-y gh-mcp "/My Repos"')

    fireEvent.change(screen.getByRole('textbox', { name: 'Command' }), { target: { value: 'uvx' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saveMcpServer).toHaveBeenCalledTimes(1))
    expect(saveMcpServer).toHaveBeenCalledWith('gh', {
      command: 'uvx',
      args: ['-y', 'gh-mcp', '/My Repos'],
      env: { LOG_LEVEL: 'debug' },
      plainEnv: ['LOG_LEVEL'],
      keepEnv: ['GITHUB_TOKEN'],
    })
  })

  it('a server the form does not own opens to the reason, with nothing to save', async () => {
    mockApi({ name: 'gh', editable: false, reason: 'PersonalClaw manages this server itself and sets it up again on every start.' })
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Edit gh' }))
    expect(await screen.findByText('PersonalClaw manages this server itself and sets it up again on every start.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })
})

describe('Import from another tool', () => {
  it('lists the variables it brings by name, and says so when the import did not land', async () => {
    mockApi({ name: 'gh', editable: false, reason: '-' })
    importMcpServer.mockRejectedValue(new Error("No MCP server named 'cc-notion' was found to add."))
    await mount()
    fireEvent.click(screen.getByRole('button', { name: /Discovered in other tools/ }))
    expect(await screen.findByText('Sets NOTION_TOKEN')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Import/ }))
    await settle()
    expect(importMcpServer).toHaveBeenCalledWith('cc-notion')
    expect(notify).toHaveBeenCalledWith(
      "Couldn't import \"cc-notion\": No MCP server named 'cc-notion' was found to add.", 'error')
  })
})

describe('api.importMcpServer', () => {
  // The REAL client: the page tests above replace `lib/api`, and a `doMock` outlives resetModules.
  beforeEach(() => { vi.doUnmock('../../lib/api'); vi.doUnmock('../../app/appSdk') })
  afterEach(() => { vi.unstubAllGlobals() })

  const answer = (body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }))

  it('asks for the PersonalClaw scope and leaves Claude Code’s own entry in place', async () => {
    const fetchMock = answer({ ok: true, results: [{ name: 'cc-notion', actions: { personalclaw: 'added', ccGlobal: 'noop' } }] })
    vi.stubGlobal('fetch', fetchMock)
    const { api } = await import('../../lib/api')
    await api.importMcpServer('cc-notion')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/mcp/apply')
    expect(JSON.parse(String(init.body))).toEqual({
      changes: [{ name: 'cc-notion', personalclaw: true, ccGlobal: true }],
    })
  })

  it('throws the change’s own sentence when the 200 says it did not land', async () => {
    vi.stubGlobal('fetch', answer({ ok: true, results: [{ name: 'x', error: "No MCP server named 'x' was found to add." }] }))
    const { api } = await import('../../lib/api')
    await expect(api.importMcpServer('x')).rejects.toThrow("No MCP server named 'x' was found to add.")
  })
})
