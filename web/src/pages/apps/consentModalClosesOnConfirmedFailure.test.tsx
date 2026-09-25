// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AppCatalogEntry, AppInstallResult } from '../../lib/api'

/** Issue #3540 — the install-consent modal never closed and never showed its own error
 *  when a CONFIRMED ("Install anyway") re-attempt failed for a reason the scanner gate
 *  never anticipated (here: the python-dependency admission gate, #3538/#3539 — but the
 *  bug is general, not specific to that cause).
 *
 *  🔑 A UNIT TEST THAT THE HANDLER FIRES IS NOT EVIDENCE THE DIALOG CLOSES. The bug was
 *  never that `confirmInstall` failed to run — the confirmed `POST /api/apps` fired and
 *  came back with a real, correct error every time. The defect is that `useGuardedInstall`
 *  left `blocked` holding the FIRST attempt's warning, so `ConsentModal` (gated on
 *  `blocked` alone by every caller) stayed mounted showing the stale scan report, while
 *  `guarded.error` — set correctly — rendered in the normal page flow behind the modal's
 *  own opaque backdrop. So this test does NOT mock `useGuardedInstall` (unlike
 *  `cardInstallDiscloses.test.tsx`, which mocks it to pin what the CALLER supplies): it
 *  drives the real hook through a mocked `api.installApp`, and asserts the DOM a user
 *  would actually see — the dialog is gone, and the real error text is on screen. */

const WARNING = {
  ok: false, name: 'slack-channel', needs_consent: true, error: 'install needs consent: scanner raised warnings',
  scan: { verdict: 'warning', tier: 'community', findings: [
    { surface: 'script', severity: 'warning', rule: 'reads_sensitive_path', path: 'slack_runtime/events.py', evidence: 'reads ~/.aws/credentials' },
  ], signature: { state: 'unsigned', signer: '', reason: '' } },
} satisfies AppInstallResult

// The exact shape #3538 produced: the confirmed attempt fails PAST the scan gate, so
// `needs_consent` is false and there is no terminal (dangerous/invalid-signature) verdict
// either — `isBlockingResult` reads this as a plain error, on purpose, because it is one.
const PACKAGING_REFUSAL = {
  ok: false, name: 'slack-channel', needs_consent: false,
  error: "cannot verify app slack-channel's python dependencies against core's (No module named 'packaging'); refusing rather than risk moving a gateway dependency",
  scan: WARNING.scan, // the SAME stale scan report a naive read of `blocked` would keep showing
} satisfies AppInstallResult

let call = 0
vi.mock('../../lib/api', () => ({
  api: {
    installApp: (_source: string, confirm: boolean) => {
      call += 1
      return Promise.resolve(confirm ? PACKAGING_REFUSAL : WARNING)
    },
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView } from './AppsSection'

const entry: AppCatalogEntry = {
  name: 'slack-channel', displayName: 'Slack Channel', description: 'Slack workspace integration.',
  version: '0.1.0', icon: 'Slack', author: 'PersonalClaw', source: 'https://github.com/PersonalClaw/PersonalClawApps.git#slack-channel',
  sourceKind: 'git', isProvider: true, providerType: 'channel', tags: ['channel', 'bundled'],
  consentKnown: true, permissions: {},
}

const catalog = {
  bundled: [], gitSources: [entry.source], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

function grid() {
  return render(
    <StoreView catalog={catalog} result={[{ ...entry, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

describe('a confirmed install that fails past the scan gate (#3540)', () => {
  it('closes the stale consent modal instead of leaving it open with no visible reason', async () => {
    call = 0
    grid()

    fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    expect(screen.getByRole('dialog').textContent).toMatch(/Security scan: warning/)

    fireEvent.click(screen.getByRole('button', { name: /install anyway/i }))

    // The confirmed request actually fired (this was never the failure mode — the request
    // always reached the server and always got a real answer back).
    await waitFor(() => expect(call).toBe(2))

    // 🔴 THE BUG: this modal must be GONE, not merely re-rendered with the same content.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    // And the real reason must be the thing on screen now — not silence.
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toMatch(/cannot verify app slack-channel's python dependencies/)
    expect(alert.textContent).toMatch(/No module named 'packaging'/)
  })

  it('leaves the install retriable rather than dead: a second click re-opens a fresh dialog', async () => {
    call = 0
    grid()
    fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /install anyway/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    // The card's Install button is not stuck `busy` forever — a user can act again.
    const installBtn = screen.getByRole('button', { name: /^Install$/ })
    expect(installBtn).not.toBeDisabled()
    fireEvent.click(installBtn)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    expect(screen.getByRole('dialog').textContent).toMatch(/Security scan: warning/)
  })
})
