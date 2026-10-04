import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import type { DesktopBridge } from '../../lib/desktopBridge'

// ── A server at a URL that asks for OAuth signs in from its card ─────────────────────────────────
//
// A remote MCP server that authenticates with OAuth answered every request 401, and the card could
// only say "error" with no way forward: the Tools page had no sign-in at all. Now the gateway reports
// the server's sign-in (`auth`: required / signed_out / signed_in, presence only), and the card offers
// the one action that answers it. In a browser the authorization server's page opens in a tab opened
// INSIDE the click (a tab opened after an `await` is a blocked popup), cut off from this page
// (`opener = null`) before it is pointed anywhere, and the page looks for the sign-in to finish until
// it does.
//
// In the desktop app that blank tab is what the shell refuses: its windows hold only the gateway's
// pages. So the sign-in opened nothing there at all, while the card said "in the tab that opened".
// There the page now opens no window and hands the sign-in page to the app (`systemBrowser.open`),
// which opens it in the system browser and says whether it did. A tab a browser blocked, and a
// browser the app could not open, are said too, with the way to open the page by hand.

let servers: Array<Record<string, unknown>> = []
const startMcpSignIn = vi.fn()
const signOutMcp = vi.fn()
const notify = vi.fn()
const confirm = vi.fn()
const copyText = vi.fn()

function mockModules() {
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))
  vi.doMock('../../ui/dialog', async (orig) => ({ ...(await orig<Record<string, unknown>>()), confirm }))
  vi.doMock('../../app/clipboard', () => ({ copyText }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools: [], load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      startMcpSignIn,
      signOutMcp,
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
  await waitFor(() => expect(screen.getAllByText('linear').length).toBeGreaterThan(0))
}

const asking = { name: 'linear', transport: 'http', status: 'signin', enabled: true, tools: [], error: 'linear needs you to sign in.', auth: { method: 'oauth', state: 'required' } }
const signedIn = { name: 'linear', transport: 'http', status: 'ok', enabled: true, tools: [], error: '', auth: { method: 'oauth', state: 'signed_in' } }
const AUTHORIZE = 'https://auth.example.com/authorize?response_type=code&client_id=dcr-1&state=s'

let opened: { opener: unknown; closed: boolean; location: { href: string }; close: ReturnType<typeof vi.fn> }
let openSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  startMcpSignIn.mockReset()
  signOutMcp.mockReset()
  notify.mockReset()
  confirm.mockReset()
  copyText.mockReset()
  copyText.mockResolvedValue(true)
  opened = { opener: window, closed: false, location: { href: '' }, close: vi.fn() }
  openSpy = vi.spyOn(window, 'open').mockImplementation(() => opened as unknown as Window)
})

afterEach(() => {
  openSpy.mockRestore()
  delete window.pclawDesktop
})

describe('a server that asks for a sign-in', () => {
  it('says so on its card and signs in through a tab cut off from this page', async () => {
    servers = [asking]
    let answer!: (v: unknown) => void
    startMcpSignIn.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    mockModules()
    await mount()
    expect(screen.getByText('sign-in needed')).toBeInTheDocument()
    expect(screen.getByText(/asks you to sign in before its tools can be used/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    // The tab opened in the click itself, before the gateway answered, and is cut off from this page.
    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(opened.opener).toBeNull()
    expect(startMcpSignIn).toHaveBeenCalledWith('linear', undefined)
    answer({ authorizationUrl: AUTHORIZE, redirectUri: 'http://127.0.0.1:10000/api/mcp/oauth/callback' })
    await waitFor(() => expect(opened.location.href).toBe(AUTHORIZE))
    expect(await screen.findByText(/Finish signing in to/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the sign-in page' }).getAttribute('href')).toBe(AUTHORIZE)

    // The other tab finishes; the next look finds it signed in, and the page says so.
    servers = [signedIn]
    window.dispatchEvent(new Event('focus'))
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Signed in to "linear".', 'success'))
    expect(await screen.findByText('signed in')).toBeInTheDocument()
    expect(screen.queryByText(/Finish signing in to/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Sign out of linear' })).toBeInTheDocument()
  })

  it('a sign-in that ended says so and offers it again', async () => {
    servers = [{ ...asking, error: 'You are signed out of linear.', auth: { method: 'oauth', state: 'signed_out' } }]
    mockModules()
    await mount()
    expect(screen.getByText(/You are signed out of/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in again' })).toBeInTheDocument()
  })

  it('asks for the app to register when the authorization server will not register PersonalClaw', async () => {
    servers = [asking]
    mockModules()
    const { ApiError } = await import('../../lib/api')
    startMcpSignIn.mockRejectedValueOnce(new ApiError(
      'auth.example.com does not let PersonalClaw register itself. Register an app there with the redirect URL http://127.0.0.1:10000/api/mcp/oauth/callback, then enter its client ID.',
      400, 'mcp_sign_in_needs_client_id',
      { redirectUri: 'http://127.0.0.1:10000/api/mcp/oauth/callback', issuer: 'https://auth.example.com' },
    ))
    startMcpSignIn.mockResolvedValueOnce({ authorizationUrl: AUTHORIZE, redirectUri: 'x' })
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('http://127.0.0.1:10000/api/mcp/oauth/callback')).toBeInTheDocument()
    expect(opened.close).toHaveBeenCalled()
    expect(notify).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'my-app' } })
    fireEvent.change(screen.getByLabelText('Client secret'), { target: { value: 'shh' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Sign in' }).at(-1)!)
    await waitFor(() => expect(startMcpSignIn).toHaveBeenLastCalledWith('linear', { clientId: 'my-app', clientSecret: 'shh' }))
    await waitFor(() => expect(opened.location.href).toBe(AUTHORIZE))
    await waitFor(() => expect(screen.queryByText('http://127.0.0.1:10000/api/mcp/oauth/callback')).toBeNull())
  })

  it('a Sign in the server does not have is refused, and the card then says why and offers none', async () => {
    // A server that takes a token answers like one that signs in. The gateway refuses the sign-in
    // and probes the server again; the page reads the list again, so the card stops offering Sign in
    // and says, in place, what the server wants. It used to keep "asks you to sign in" after a toast.
    servers = [asking]
    mockModules()
    const { ApiError } = await import('../../lib/api')
    const refusal = 'linear refused the connection but does not publish how to sign in: https://mcp.example.test serves no authorization server metadata. If it takes an API key, add it to the server as a header instead.'
    startMcpSignIn.mockRejectedValueOnce(new ApiError(refusal, 409, 'mcp_sign_in_not_offered'))
    await mount()
    servers = [{ name: 'linear', transport: 'http', status: 'error', enabled: true, tools: [], error: refusal }]
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('does not publish how to sign in'), 'error'))
    expect(opened.close).toHaveBeenCalled()
    expect(await screen.findByText(`Not connected — ${refusal}`)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull()
    expect(screen.queryByText(/asks you to sign in/)).toBeNull()
  })

  it('a sign-in that cannot start says why, and closes the tab it opened', async () => {
    servers = [asking]
    mockModules()
    const { ApiError } = await import('../../lib/api')
    startMcpSignIn.mockRejectedValueOnce(new ApiError('auth.example.com does not say it supports PKCE with S256.', 502, 'mcp_sign_in_failed'))
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('does not say it supports PKCE'), 'error'))
    expect(opened.close).toHaveBeenCalled()
  })
})

describe('a browser that gives the sign-in no tab', () => {
  it('says the sign-in page did not open, and offers it to open by hand', async () => {
    // A popup blocker answers `window.open` with no window at all.
    openSpy.mockImplementation(() => null)
    servers = [asking]
    startMcpSignIn.mockResolvedValue({ authorizationUrl: AUTHORIZE, redirectUri: 'http://127.0.0.1:10000/api/mcp/oauth/callback' })
    mockModules()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/did not open\. Open it to sign in; this page updates when you are done\./)).toBeInTheDocument()
    expect(screen.queryByText(/in the tab that opened/)).toBeNull()
    expect(screen.getByRole('link', { name: 'Open the sign-in page' }).getAttribute('href')).toBe(AUTHORIZE)
  })

  it('a tab closed before the sign-in page could load is no tab either', async () => {
    servers = [asking]
    let answer!: (v: unknown) => void
    startMcpSignIn.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    mockModules()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    opened.closed = true
    answer({ authorizationUrl: AUTHORIZE, redirectUri: 'x' })
    expect(await screen.findByText(/did not open\./)).toBeInTheDocument()
    expect(opened.location.href).toBe('')
    expect(screen.getByRole('link', { name: 'Open the sign-in page' }).getAttribute('href')).toBe(AUTHORIZE)
  })
})

describe('in the desktop app', () => {
  const systemOpen = vi.fn()
  beforeEach(() => {
    systemOpen.mockReset()
    // What the shell does with a blank window: it refuses it, and `window.open` gets nothing back.
    openSpy.mockImplementation(() => null)
    const bridge: Partial<DesktopBridge> = { systemBrowser: { open: systemOpen } }
    window.pclawDesktop = bridge as DesktopBridge
  })

  it('opens the sign-in page in the system browser, says so, and says when the sign-in is done', async () => {
    servers = [asking]
    let answer!: (v: unknown) => void
    startMcpSignIn.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    systemOpen.mockResolvedValue({ ok: true })
    mockModules()
    await mount()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    // No blank window first: the app's windows refuse one, so nothing would ever show the page.
    expect(openSpy).not.toHaveBeenCalled()
    expect(systemOpen).not.toHaveBeenCalled()
    answer({ authorizationUrl: AUTHORIZE, redirectUri: 'http://127.0.0.1:10000/api/mcp/oauth/callback' })
    await waitFor(() => expect(systemOpen).toHaveBeenCalledWith(AUTHORIZE))
    expect(await screen.findByText(/opened in your browser\. Finish signing in there; this page updates when you are done\./)).toBeInTheDocument()
    expect(screen.queryByText(/in the tab that opened/)).toBeNull()
    expect(screen.getByRole('link', { name: 'Open the sign-in page' }).getAttribute('href')).toBe(AUTHORIZE)

    // She finishes in the browser; the next look finds the server signed in, and the page says so.
    servers = [signedIn]
    window.dispatchEvent(new Event('focus'))
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Signed in to "linear".', 'success'))
    expect(await screen.findByText('signed in')).toBeInTheDocument()
    expect(screen.queryByText(/opened in your browser/)).toBeNull()
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('a browser the app could not open is said, with the sign-in link to copy', async () => {
    servers = [asking]
    startMcpSignIn.mockResolvedValue({ authorizationUrl: AUTHORIZE, redirectUri: 'x' })
    systemOpen.mockResolvedValue({ ok: false, reason: 'no application is set to open web pages.' })
    mockModules()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(
      /could not open your browser: no application is set to open web pages\. Copy the sign-in link and open it in a browser to sign in to/,
    )).toBeInTheDocument()
    expect(screen.queryByText(/opened in your browser/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Copy the sign-in link' }))
    await waitFor(() => expect(copyText).toHaveBeenCalledWith(AUTHORIZE, 'the sign-in link'))
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('an app that does not answer is not taken for an opened browser', async () => {
    servers = [asking]
    startMcpSignIn.mockResolvedValue({ authorizationUrl: AUTHORIZE, redirectUri: 'x' })
    systemOpen.mockRejectedValue(new Error('the desktop app stopped answering'))
    mockModules()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(/could not open your browser: the desktop app stopped answering\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy the sign-in link' })).toBeInTheDocument()
  })

  it('a sign-in that cannot start opens nothing at all', async () => {
    servers = [asking]
    mockModules()
    const { ApiError } = await import('../../lib/api')
    startMcpSignIn.mockRejectedValueOnce(new ApiError('auth.example.com does not say it supports PKCE with S256.', 502, 'mcp_sign_in_failed'))
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('does not say it supports PKCE'), 'error'))
    expect(systemOpen).not.toHaveBeenCalled()
    expect(openSpy).not.toHaveBeenCalled()
  })
})

describe('a signed-in server', () => {
  it('signs out after saying what signing out does', async () => {
    servers = [signedIn]
    confirm.mockResolvedValue(true)
    signOutMcp.mockResolvedValue(undefined)
    mockModules()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of linear' }))
    await waitFor(() => expect(signOutMcp).toHaveBeenCalledWith('linear'))
    const asked = confirm.mock.calls[0][0] as { body: string }
    expect(asked.body).toMatch(/deletes the tokens/)
    expect(asked.body).toMatch(/remove it there/)
  })

  it('draws no sign-in line for a server that never asked for one', async () => {
    servers = [{ name: 'linear', transport: 'http', status: 'ok', enabled: true, tools: [], error: '' }]
    mockModules()
    await mount()
    expect(screen.queryByRole('button', { name: /Sign in/ })).toBeNull()
    expect(screen.queryByText('signed in')).toBeNull()
  })
})
