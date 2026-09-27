import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'

// ── The ceilings section says when Max memory and Max processes do nothing ─────────────────────
//
// On macOS the kernel refuses any finite memory limit on a child, so Max memory is never applied,
// and Max processes counts every process the user runs rather than the child's. The gateway logged
// a warning about it once per process; the Security panel offered both fields with nothing said.
// The sentence comes from the SERVER (`/api/security/stats` → `child_ceilings`), because the
// browser can be on another machine than the gateway whose children these are.

const MAC_NOTE =
  "On this Mac, Max memory is never applied, because macOS refuses a memory limit on a child process, and Max processes counts every process you run rather than the child's. Neither contains a runaway child. Max open files is enforced."

async function mount(childCeilings: unknown) {
  vi.resetModules()
  sessionStorage.clear()
  const stats = vi.fn(() => Promise.resolve({
    denied_commands: 112, suspicious_patterns: 20, tool_schemas: 30, redaction_paths: 5,
    ...(childCeilings === undefined ? {} : { child_ceilings: childCeilings }),
  }))
  const patchConfig = vi.fn(() => Promise.resolve({}))
  vi.doMock('../../lib/api', () => ({
    api: {
      // The signed-in devices summary (ledger 255) renders inside this SAME panel; a total
      // mock with no `devices` read would throw before the section under test renders.
      devices: () => Promise.resolve([]),
      securityStats: stats,
      deniedCommands: () => Promise.resolve({
        builtin: [], user: [], user_additions: 0,
        baseline: { version: 1, sha256: 'x', count: 0, verified: true, detail: '' },
      }),
      securityEgress: () => Promise.resolve({ value: { allow_hosts: [], deny_hosts: [], allow_private: false }, revision: 'r1' }),
      addDeniedCommand: () => Promise.resolve({}),
      removeDeniedCommand: () => Promise.resolve({}),
      outsideHome: () => Promise.resolve({ places: [], allowed: [] }),
      setSecurityEgress: () => Promise.resolve({}),
      desktopState: () => Promise.resolve({
        connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
      }),
      credentialStore: () => Promise.resolve({
        migration: 'credentials_to_keychain', backend: 'dotenv', requested: 'dotenv',
        blocked: true, pending_keys: [], pending: 0, keychain_keys: 0,
        rollback_available: false, snapshot_name: '.env.pre-keychain', verified: true,
        verification: { checked: 0, missing: [], still_in_dotenv: [] },
      }),
      personalclawConfig: () => Promise.resolve({
        sandbox: { nofile: 4096, max_pids: 0, max_rss_mb: 0, cgroup_scopes: false, env_passthrough: [] },
      }),
      patchConfig,
    },
  }))
  const { SecurityPanel } = await import('./SecurityPanel')
  await act(async () => {
    render(<SecurityPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  await screen.findByText('Max memory (MB)')
  return { stats, patchConfig }
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the child process ceilings on a host where two of them do nothing', () => {
  it('says so above the fields, in the words the gateway sent', async () => {
    await mount({ contained: false, note: MAC_NOTE })
    const note = screen.getByText(MAC_NOTE)
    const field = screen.getByText('Max memory (MB)')
    // Before the fields, so it is read before a number is typed into one.
    expect(note.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('says nothing when the ceilings contain a child', async () => {
    await mount({ contained: true, note: '' })
    expect(screen.queryByText(/never applied/)).toBeNull()
    expect(screen.queryByText(/Neither contains/)).toBeNull()
  })

  it('renders from a response cached before the field existed', async () => {
    await mount(undefined)
    expect(screen.getByText('Max processes')).toBeTruthy()
    expect(screen.queryByText(/never applied/)).toBeNull()
  })

  it('reads the note again once Cgroup scopes is saved, since the note depends on it', async () => {
    const { stats, patchConfig } = await mount({ contained: false, note: MAC_NOTE })
    const before = stats.mock.calls.length
    fireEvent.click(screen.getByRole('switch', { name: /Cgroup scopes/ }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('sandbox.cgroup_scopes', true))
    await waitFor(() => expect(stats.mock.calls.length).toBeGreaterThan(before))
  })
})
