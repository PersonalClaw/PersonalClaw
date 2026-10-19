// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { AppDisclosure, AppInstallResult } from '../../lib/api'

// ── Install consent names the replyStyles grant, and an update that adds it says so ───────────
//
// An app's reply styles load only while it holds `replyStyles` (`apps/prompt_seed`), and their text
// reaches every turn that chooses one, so the grant is a capability the owner weighs at install. It
// is listed among the enforced permissions on its own line, and an app whose only grant it is does
// not read as granted nothing.

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('this rail never confirms')),
    updateApp: () => Promise.reject(new Error('this rail never confirms')),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { InstallDialogHarness } from '../../test/installDialogHarness'
import { PermissionList, permissionRows } from './installConsent'

const LINE = 'Add reply styles your chats can choose, as tone guidance only'

const NOTHING: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], sidecarDependencies: [], requires: [], launches: [], npmPackages: [], writes: [],
  hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '', providers: [],
  onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', cliSetup: '',
  cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
}

function review(previous: AppDisclosure | null): AppInstallResult {
  return {
    ok: false, name: 'acme', error: '', needs_consent: true,
    scan: { verdict: 'clean', tier: 'community', findings: [], signature: null },
    displayName: 'Acme', version: '1.1.0', disclosure: { ...NOTHING, permissions: { replyStyles: true } },
    previous, consent: 'c'.repeat(64),
  }
}

beforeEach(() => previewApp.mockReset())
afterEach(cleanup)

describe('the replyStyles grant at install', () => {
  it('is its own row among the permissions the gateway enforces', () => {
    expect(permissionRows({ replyStyles: true })).toContain(LINE)
    expect(permissionRows({})).not.toContain(LINE)
    expect(permissionRows({ replyStyles: false })).not.toContain(LINE)
  })

  it('an app whose only grant it is does not read as granted nothing', () => {
    const { container } = render(<PermissionList perms={{ replyStyles: true }} />)
    const rows = Array.from(container.querySelectorAll('li')).map((li) => li.textContent ?? '')
    expect(rows).toEqual([`• ${LINE}`])
    expect(container.textContent ?? '').not.toMatch(/None — this app is granted no gateway capability/)
  })

  it('an update that adds the grant lists it among what it changes', async () => {
    previewApp.mockResolvedValue(review({ ...NOTHING }))
    render(<InstallDialogHarness target={{ source: '/apps/acme', label: 'Acme', update: 'acme' }} />)
    await waitFor(() => expect(screen.getByTestId('update-changes')).toBeTruthy())
    expect(screen.getByTestId('update-changes').textContent).toContain(`+ Adds: ${LINE}`)
  })
})
