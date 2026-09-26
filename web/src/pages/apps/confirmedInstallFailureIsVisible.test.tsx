// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import type { AppCatalogEntry, AppInstallResult } from '../../lib/api'

/** Issue #3540 — a CONFIRMED ("Install anyway") install that failed for a reason the scanner gate
 *  never anticipated (there: the python-dependency admission gate, #3538/#3539 — but the bug is
 *  general, not specific to that cause) left the consent modal up showing the stale scan, while
 *  the real, correct error rendered in the normal page flow BEHIND the modal's own opaque
 *  backdrop. "Install anyway" read as doing nothing.
 *
 *  🔑 THE ERROR BELONGS WHERE THE CLICK WAS. The one consent dialog shows a confirmed failure
 *  INSIDE itself, beside the button that caused it, with the server's reason verbatim. So this
 *  drives the real card → dialog → confirm path through a mocked API and asserts what a user
 *  would see: the reason is inside the dialog element, above its buttons, and the install is
 *  retriable rather than dead. jsdom implements no layout, so "inside the dialog" is the
 *  structural precondition; the real-browser drive checks the pixels with `elementFromPoint`. */

const SOURCE = 'https://github.com/PersonalClaw/PersonalClawApps.git#slack-channel'
const DIGEST = 'd'.repeat(64)

/** The server's review: a warning the user has to override, and the digest they consent to. */
const WARNING: AppInstallResult = {
  ok: false, name: 'slack-channel', needs_consent: true, error: '',
  scan: {
    verdict: 'warning', tier: 'community', signature: { state: 'unsigned', signer: '', reason: '' },
    findings: [{ surface: 'script', severity: 'warning', rule: 'reads_sensitive_path', path: 'slack_runtime/events.py', evidence: 'reads ~/.aws/credentials' }],
  },
  displayName: 'Slack Channel', version: '0.1.0', previous: null, consent: DIGEST,
  disclosure: {
    permissions: {}, crons: [], pythonDependencies: [{ spec: 'slack-sdk>=3.27,<4', coreOwned: false }],
    hasUI: false, uiComponents: '', hasBackend: false, onInstall: '', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
  },
}

// The exact shape #3538 produced: the confirmed attempt fails PAST the consent gate, so
// `needs_consent` is false and there is no terminal verdict either — a plain failure, and the
// one the old flow put behind the backdrop. The server composes it without a full stop.
const PACKAGING_REFUSAL: AppInstallResult = {
  ok: false, name: 'slack-channel', needs_consent: false, scan: WARNING.scan,
  error: "cannot verify app slack-channel's python dependencies against core's (No module named 'packaging'); refusing rather than risk moving a gateway dependency",
}

const INSTALLED: AppInstallResult = { ok: true, name: 'slack-channel', error: '', needs_consent: false, scan: null, displayName: 'Slack Channel' }

const previewApp = vi.fn()
const installApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: (...a: unknown[]) => installApp(...a),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView } from './AppsSection'

const entry: AppCatalogEntry = {
  name: 'slack-channel', displayName: 'Slack Channel', description: 'Slack workspace integration.',
  version: '0.1.0', icon: 'Slack', author: 'PersonalClaw', source: SOURCE,
  sourceKind: 'git', isProvider: true, providerType: 'channel', tags: ['channel', 'bundled'],
  consentKnown: true, permissions: {},
}

const catalog = {
  bundled: [], gitSources: [entry.source], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

const toasts: { level: string; message: string }[] = []
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail) }

beforeEach(() => {
  previewApp.mockReset().mockResolvedValue(WARNING)
  installApp.mockReset().mockResolvedValue(PACKAGING_REFUSAL)
  toasts.length = 0
  window.addEventListener('ne:toast', onToast)
})
afterEach(() => { window.removeEventListener('ne:toast', onToast); cleanup() })

function grid(onInstalled = vi.fn()) {
  render(
    <StoreView catalog={catalog} result={[{ ...entry, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={onInstalled} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
  return onInstalled
}

/** Card Install → the review → "Install anyway" → the confirmed attempt's failure. */
async function confirmAndFail() {
  fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan: warning/))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /Install anyway/ }))
  // The confirmed request fired, with the digest of what was reviewed (this was never the
  // failure mode — the request always reached the server and always got a real answer back).
  await waitFor(() => expect(installApp).toHaveBeenCalledWith(SOURCE, DIGEST))
  return within(screen.getByRole('dialog')).findByRole('alert')
}

describe('a confirmed install that fails past the consent gate (#3540)', () => {
  it('shows the real reason inside the dialog, above the button that caused it', async () => {
    const onInstalled = grid()
    const alert = await confirmAndFail()
    const dialog = screen.getByRole('dialog')

    // 🔴 THE BUG: the reason was on the page BEHIND the modal. It is inside it now…
    expect(dialog.contains(alert), 'the error must be inside the dialog, not behind its backdrop').toBe(true)
    expect(alert.textContent).toMatch(/cannot verify app slack-channel's python dependencies/)
    expect(alert.textContent).toMatch(/No module named 'packaging'/)
    // …as a sentence (the server's clause arrives unpunctuated)…
    expect(alert.textContent).toMatch(/moving a gateway dependency\.$/)
    // …and above the footer it explains, where the eye already is.
    const retry = within(dialog).getByRole('button', { name: /Install anyway/ })
    expect(alert.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    // Nothing claims success, and nothing leaves the page believing it installed.
    expect(onInstalled).not.toHaveBeenCalled()
    expect(toasts).toEqual([])
  })

  it('leaves the install retriable rather than dead: the same consent can be sent again', async () => {
    const onInstalled = grid()
    await confirmAndFail()
    installApp.mockResolvedValueOnce(INSTALLED)
    const retry = within(screen.getByRole('dialog')).getByRole('button', { name: /Install anyway/ })
    expect(retry).not.toBeDisabled()
    fireEvent.click(retry)
    await waitFor(() => expect(installApp).toHaveBeenCalledTimes(2))
    expect(installApp).toHaveBeenLastCalledWith(SOURCE, DIGEST)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(onInstalled).toHaveBeenCalledWith('slack-channel')
    expect(toasts).toContainEqual(expect.objectContaining({ level: 'success', message: 'Installed Slack Channel.' }))
  })

  it('and closing it leaves the card usable: a second click opens a fresh review', async () => {
    grid()
    await confirmAndFail()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Cancel$/ }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    // The card's Install button is not stuck `busy` forever — a user can act again.
    const installBtn = screen.getByRole('button', { name: /^Install$/ })
    expect(installBtn).not.toBeDisabled()
    fireEvent.click(installBtn)
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan: warning/))
    expect(previewApp).toHaveBeenCalledTimes(2)
    expect(within(screen.getByRole('dialog')).queryByRole('alert'), 'a fresh review carries no stale failure').toBeNull()
  })
})
