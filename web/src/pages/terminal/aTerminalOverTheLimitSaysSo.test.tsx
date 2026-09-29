import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Profiler } from 'react'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'

// ── A terminal refused for the limit says the limit, and New session says it too ─────────────
//
// Measured in the terminal drawer with three sessions open: New session → POST 429
// `{"error": "Max 3 sessions"}`, twice, and NOTHING on screen — no message, no tab, the button still
// enabled; the only trace was the console's "Failed to load resource". The drawer rendered its error
// only while no session was open. Now the gateway answers `terminal_session_limit` with a sentence
// naming the limit and what to do; the drawer and the Terminal page both show it, and keep New
// session unavailable with it as the reason until a session closes.
//
// Each commit is recorded through a `Profiler`, whose `onRender` React calls as it commits, with the
// DOM already updated. That makes "the sentence goes when the session does" a claim about every
// render, not about whenever a poll happens to look: clearing it in an effect on the tab count left
// one committed render with the tab gone and the refusal still up, and a read after `waitFor` saw it
// only when that effect had not run yet, which a loaded machine made likely.

const LIMIT = '3 terminals are already open, the most this gateway runs at once. Close one to open another.'

const created: string[] = []

function mockDeps() {
  created.length = 0
  vi.doMock('./TerminalView', () => ({ TerminalView: () => <div data-testid="pty" /> }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    const open = new Set<string>()
    return {
      ...real,
      api: {
        createTerminal: () => {
          if (open.size >= 3) {
            created.push('refused')
            return Promise.reject(new real.ApiError(LIMIT, 429, 'terminal_session_limit', undefined))
          }
          const id = `s${created.length + 1}@none`
          created.push(id)
          open.add(id)
          return Promise.resolve({ session_id: id, cwd: '/w', shell: '/bin/bash' })
        },
        deleteTerminal: (id: string) => { open.delete(id); return Promise.resolve({ ok: true }) },
        terminalSessions: () => Promise.resolve({ enabled: true, persist_available: false, sessions: [] }),
        personalclawConfig: () => Promise.resolve({ dashboard: { terminal: { persist: false } } }),
        sandboxProviders: () => Promise.resolve({ providers: [] }),
        patchConfig: () => Promise.resolve({}),
      },
    }
  })
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); localStorage.clear(); mockDeps() })

describe('the terminal drawer at its limit', () => {
  it('says the limit where the tabs are, and New session says it until a session closes', async () => {
    const { TerminalDrawer } = await import('./TerminalDrawer')
    const commits: Array<{ tabs: number; refusal: boolean; unavailable: boolean }> = []
    const recordCommit = () => commits.push({
      tabs: document.querySelectorAll('[role="tab"]').length,
      refusal: document.querySelector('[role="alert"]') !== null,
      unavailable: document.querySelector('button[aria-label="New session"][aria-disabled="true"]') !== null,
    })
    render(
      <Profiler id="drawer" onRender={recordCommit}>
        <TerminalDrawer open onClose={() => {}} onOpenFull={() => {}} />
      </Profiler>,
    )
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(1))  // the first opens by itself
    const newSession = () => screen.getByRole('button', { name: 'New session' })
    fireEvent.click(newSession())
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(2))
    fireEvent.click(newSession())
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(3))

    fireEvent.click(newSession())
    const said = await screen.findByRole('alert')
    expect(said.textContent).toContain(LIMIT)
    expect(newSession()).toHaveAttribute('aria-disabled', 'true')
    expect(newSession().getAttribute('title') ?? '').toContain(LIMIT)
    // Pressing it again asks nothing: the answer would be the same refusal.
    fireEvent.click(newSession())
    expect(created.filter((c) => c === 'refused')).toHaveLength(1)

    // Closing one frees a place: the sentence goes, and New session works again, in the very render
    // that drops the tab.
    const closedAt = commits.length
    fireEvent.click(screen.getByRole('button', { name: 'Close Session 3' }))
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(2))
    const afterClose = commits.slice(closedAt).filter((c) => c.tabs === 2)
    expect(afterClose.length, 'vacuity floor: no commit dropped the tab').toBeGreaterThan(0)
    expect(afterClose.filter((c) => c.refusal || c.unavailable), 'a render dropped the tab and kept the refusal').toEqual([])
    expect(screen.queryByRole('alert')).toBeNull()
    expect(newSession()).not.toHaveAttribute('aria-disabled')
    fireEvent.click(newSession())
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(3))
  })
})

describe('the Terminal page at its limit', () => {
  it('says the limit, and its New terminal session control carries it as the reason', async () => {
    const { TerminalPage } = await import('./TerminalPage')
    render(<TerminalPage query={{}} setQuery={() => {}} />)
    const control = async () => (await screen.findAllByRole('button', { name: 'New terminal session' }))[0]
    for (let open = 1; open <= 3; open++) {
      fireEvent.click(await control())
      await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(open))
    }
    fireEvent.click(await control())
    const said = await screen.findByRole('alert')
    expect(within(said).getByText(LIMIT)).toBeTruthy()
    await waitFor(async () => expect(await control()).toHaveAttribute('aria-disabled', 'true'))
    expect((await control()).getAttribute('title') ?? '').toContain(LIMIT)
  })
})

describe('a refusal stands while the sessions it was said against are open', () => {
  it('a rename keeps it, and a session closed or opened since leaves it unsaid', async () => {
    const { ApiError } = await import('../../lib/api')
    const { openRefusal, standing } = await import('./openRefusal')
    const open = [{ id: 's1@none' }, { id: 's2@none' }, { id: 's3@none' }]
    const said = openRefusal(new ApiError(LIMIT, 429, 'terminal_session_limit', undefined), open)
    expect(said).toMatchObject({ text: LIMIT, limit: true })
    expect(standing(said, open.map((t) => ({ ...t, label: 'Build' })))).toBe(said)
    expect(standing(said, open.slice(0, 2))).toBeNull()
    expect(standing(said, [...open.slice(0, 2), { id: 's4@none' }])).toBeNull()
    expect(openRefusal(new Error('The gateway did not answer.'), open).limit).toBe(false)
  })
})
