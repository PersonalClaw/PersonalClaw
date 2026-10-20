// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import type { AppProcessExit, AppSummary } from '../../lib/api'

// ── An app's panel says why a process it runs is not running ─────────────────────────────────
//
// A backend or a background worker that crashed showed "not running" and nothing else: what it
// printed was discarded, so the panel, the log and the Doctor had no clue between them. The
// gateway now keeps how each process's last run ended and the last lines it printed (masked
// before they leave it), and the panel shows them: the end in a sentence, the line that says why,
// and the lines themselves folded away beneath it.
//
// Only the API is mocked; the Library and its detail panel are the shipped components.

const CRASH: AppProcessExit = {
  pid: 4242,
  exitCode: 1,
  ended: 'exited with code 1',
  endedAt: '2026-10-02T19:20:05+00:00',
  cause: "ModuleNotFoundError: No module named 'feedparser'",
  lines: [
    'Traceback (most recent call last):',
    '  File "backend/server.py", line 3, in <module>',
    "ModuleNotFoundError: No module named 'feedparser'",
  ],
}

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'fixture-feeds', displayName: 'Fixture Feeds', version: '0.1.0',
    description: 'Reads feeds.',
    enabled: true, origin: 'local', source: '/apps/fixture-feeds', icon: '', hasBackend: true,
    hasUI: false, uiPages: [], isProvider: false, providerType: '', hasConfig: false,
    permissions: {}, tags: [], backendRunning: false, backendPort: null, disclosure: null,
    ...over,
  }
}

async function openInLibrary(apps: AppSummary[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([...apps]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
    },
  }))
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'library', open: apps[0].name }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Deactivate/ })).toBeTruthy())
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("an app's panel, for a process that is not running", () => {
  it('says how the backend last ended, the line that says why, and the last lines it printed', async () => {
    await openInLibrary([app({ backendExit: CRASH })])
    const backend = screen.getByRole('region', { name: 'Backend' })
    expect(backend.textContent).toContain('not running')
    expect(backend.textContent).toMatch(/Its last run exited with code 1 at .+\./)
    expect(within(backend).getByText(CRASH.cause)).toBeTruthy()
    const time = backend.querySelector('time')
    expect(time?.getAttribute('dateTime')).toBe('2026-10-02T19:20:05.000Z')

    const summary = within(backend).getByText('The last 3 lines it printed')
    expect(summary.closest('details')?.open, 'the traceback is folded away until asked for').toBe(false)
    const printed = within(backend).getByLabelText('The last lines it printed')
    expect(printed.textContent).toBe(CRASH.lines.join('\n'))
  })

  it('says only that it runs, and where, while it runs', async () => {
    await openInLibrary([app({ backendRunning: true, backendPort: 8798, backendExit: null })])
    const backend = screen.getByRole('region', { name: 'Backend' })
    expect(backend.textContent).toContain('running on port 8798')
    expect(backend.textContent).not.toContain('Its last run')
  })

  it('says why a worker was given up on, and how its last run ended', async () => {
    await openInLibrary([
      app({
        hasBackend: false,
        workers: [
          { name: 'worker', state: 'failed', running: false, reason: 'crash-loop: gave up after 5 restarts, none of which stayed up 60s', exit: CRASH },
        ],
      }),
    ])
    const worker = screen.getByRole('region', { name: 'Background worker' })
    expect(worker.textContent).toContain('stopped: crash-loop: gave up after 5 restarts')
    expect(worker.textContent).toMatch(/Its last run exited with code 1 at /)
    expect(within(worker).getByLabelText('The last lines it printed').textContent).toContain('Traceback')
    expect(screen.queryByRole('region', { name: 'Backend' }), 'the app has no backend').toBeNull()
  })

  it('says why a paused worker waits, with no last run to show', async () => {
    await openInLibrary([
      app({
        hasBackend: false,
        workers: [{ name: 'worker', state: 'paused', running: false, reason: 'incident mode is active — unattended work is suspended', exit: null }],
      }),
    ])
    const paused = screen.getByRole('region', { name: 'Background worker' })
    expect(paused.textContent).toContain('paused: incident mode is active — unattended work is suspended')
    expect(paused.textContent).not.toContain('Its last run')
  })
})
