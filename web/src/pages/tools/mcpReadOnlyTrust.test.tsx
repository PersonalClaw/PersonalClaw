import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import type { ReactNode } from 'react'

// ── An MCP server's "read-only" labels are believed only for the tools you saw when you trusted it ─
//
// A server labels its own tools (`readOnlyHint`), and it can label anything read-only. Until the
// owner trusts a server's labels, every one of its tools is treated as a change: it asks, and Ask
// and Plan mode refuse it. Trusting them covers the tools the card shows, each sent back with the
// digest of its definition the card carried (`readOnlyTrust.listed`), so a tool the server adds or
// changes later asks until she reviews it — and the card says what changed, with Review.

const trustMcpReadOnly = vi.fn()
const distrustMcpReadOnly = vi.fn()
const confirmSpy = vi.fn()

const SEARCH = { name: 'alpha_search', description: 'Search the alpha notes.', annotations: { readOnlyHint: true } }
const EDIT = { name: 'alpha_edit', description: 'Change an alpha note.', annotations: { readOnlyHint: false } }
const EXPORT = { name: 'alpha_export', description: 'Export every alpha note.', annotations: { readOnlyHint: true } }
const LISTED = { alpha_search: 'a'.repeat(64), alpha_edit: 'b'.repeat(64) }

type Trust = Record<string, unknown> | undefined

function server(name: string, tools: object[], readOnlyTrust: Trust) {
  return { name, status: 'ok', enabled: true, tools, ...(readOnlyTrust ? { readOnlyTrust } : {}) }
}

function mockApi(servers: object[], answer = true) {
  const tools = servers.flatMap((s) => ((s as { tools: { name: string }[] }).tools).map((t) => ({
    name: t.name, provider: (s as { name: string }).name, description: t.name, parameters: {},
    requires_approval: true, risk_level: 'caution',
  })))
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: (opts: unknown) => { confirmSpy(opts); return Promise.resolve(answer) },
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      trustMcpReadOnly: (...a: unknown[]) => { trustMcpReadOnly(...a); return Promise.resolve({ ok: true }) },
      distrustMcpReadOnly: (...a: unknown[]) => { distrustMcpReadOnly(...a); return Promise.resolve() },
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getAllByText('alpha_search').length).toBeGreaterThan(0))
}

const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))
const asked = () => confirmSpy.mock.calls[0][0] as { title: string; body: ReactNode; confirmLabel?: string }

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  trustMcpReadOnly.mockClear()
  distrustMcpReadOnly.mockClear()
  confirmSpy.mockClear()
})

describe("trusting an MCP server's read-only labels", () => {
  it('asks first, names the tools it labels read-only, and trusts the tools the card showed', async () => {
    mockApi([server('alpha', [SEARCH, EDIT], { trusted: false, listed: LISTED })])
    await mount()
    const control = screen.getByRole('button', { name: "Trust alpha's read-only labels" })
    expect(control.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(control)
    await waitFor(() => expect(trustMcpReadOnly).toHaveBeenCalledWith('alpha', LISTED))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const question = asked()
    expect(question.title).toMatch(/alpha/)
    expect(String(question.body), 'the question names what will run unasked').toMatch(/alpha_search/)
    expect(String(question.body)).not.toMatch(/alpha_edit/)
    expect(String(question.body), 'and says what a lying label lets happen').toMatch(/without anyone being asked/)
    expect(String(question.body), 'and that a later tool asks again').toMatch(/adds or changes later asks you until you review it/)
    expect(distrustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('a declined question writes nothing', async () => {
    mockApi([server('alpha', [SEARCH, EDIT], { trusted: false, listed: LISTED })], false)
    await mount()
    fireEvent.click(screen.getByRole('button', { name: "Trust alpha's read-only labels" }))
    await settle()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('stopping trusting one server writes that one removal, with no question', async () => {
    mockApi([
      server('alpha', [SEARCH, EDIT], { trusted: true, listed: LISTED, at: '2026-10-01T09:00:00+00:00', added: [], changed: [], removed: [] }),
      server('beta', [{ name: 'beta_fetch', annotations: { readOnlyHint: true } }], { trusted: true, listed: { beta_fetch: 'c'.repeat(64) }, added: [], changed: [], removed: [] }),
    ])
    await mount()
    const control = screen.getByRole('button', { name: "Stop trusting alpha's read-only labels" })
    expect(control.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(control)
    await waitFor(() => expect(distrustMcpReadOnly).toHaveBeenCalledWith('alpha'))
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('a server whose tools are not listed yet has nothing to trust, and its switch says why', async () => {
    mockApi([server('alpha', [SEARCH], { trusted: false, listed: null })])
    await mount()
    const control = screen.getByRole('button', { name: "Trust alpha's read-only labels" })
    expect(control).toHaveAttribute('aria-disabled', 'true')
    expect(control.getAttribute('title')).toMatch(/not been listed yet, so there is nothing to trust/)
    fireEvent.click(control)
    await settle()
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it("PersonalClaw's own server offers no trust: its tools say what they do themselves", async () => {
    mockApi([server('alpha', [SEARCH], undefined)])
    await mount()
    expect(screen.queryByRole('button', { name: /read-only labels/ })).toBeNull()
  })
})

describe('a trusted server whose tools changed', () => {
  const CHANGED = {
    trusted: true,
    at: '2026-10-01T09:00:00+00:00',
    listed: { alpha_search: 'd'.repeat(64), alpha_export: 'e'.repeat(64) },
    added: ['alpha_export'],
    changed: [{ name: 'alpha_search', parts: ['description'] }],
    removed: ['alpha_edit'],
  }

  it('says on its card what it added, changed and removed, and that those ask until reviewed', async () => {
    mockApi([server('alpha', [{ ...SEARCH, description: 'Search the alpha notes and their archive.' }, EXPORT], CHANGED)])
    await mount()
    expect(screen.getByText(
      "Since you trusted alpha's read-only labels, it added alpha_export, changed alpha_search (its description) "
      + 'and removed alpha_edit. Until you review them, those tools ask before they run.',
    )).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Stop trusting alpha's read-only labels" })).toBeInTheDocument()
  })

  it('Review shows each change as the server lists it now, and seals the tools as the card showed them', async () => {
    mockApi([server('alpha', [{ ...SEARCH, description: 'Search the alpha notes and their archive.' }, EXPORT], CHANGED)])
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Review what alpha changed' }))
    await waitFor(() => expect(trustMcpReadOnly).toHaveBeenCalledWith('alpha', CHANGED.listed))
    const review = asked()
    expect(review.title).toMatch(/alpha/)
    expect(review.confirmLabel).toBe('Trust them as they are now')
    const { container } = render(<>{review.body}</>)
    const text = container.textContent ?? ''
    expect(text).toMatch(/alpha_export is new, labelled read-only\./)
    expect(text).toMatch(/Export every alpha note\./)
    expect(text).toMatch(/alpha_search: its description changed\. It is labelled read-only\./)
    expect(text, 'the description as the server says it now, shown as text').toMatch(/Search the alpha notes and their archive\./)
    expect(text).toMatch(/alpha_edit is no longer listed\./)
    expect(distrustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('a declined review seals nothing', async () => {
    mockApi([server('alpha', [SEARCH, EXPORT], CHANGED)], false)
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Review what alpha changed' }))
    await settle()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('a trusted server with nothing changed shows no review', async () => {
    mockApi([server('alpha', [SEARCH, EDIT], { trusted: true, listed: LISTED, added: [], changed: [], removed: [] })])
    await mount()
    expect(screen.queryByRole('button', { name: /Review what alpha changed/ })).toBeNull()
    expect(screen.queryByText(/Since you trusted alpha/)).toBeNull()
  })
})
