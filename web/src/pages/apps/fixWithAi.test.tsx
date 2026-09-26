// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { AppInstallResult } from '../../lib/api'

// APE-8 "Fix with AI". A failed app install that captured a build/hook log gets a button that
// opens a chat pre-filled with the FENCED install log. Two halves of the FE contract: a confirmed
// install that fails with a `fix_prompt` offers the button beside its error, inside the consent
// dialog — the one surface every install fails on — and the button renders ONLY when there is a
// fix prompt, passing it to launchChat unchanged.

// launchChat dispatches the `ne:launch-chat` event the host listens for; the button must forward
// the (backend-fenced) prompt to it unchanged and never auto-send.
const launchChat = vi.fn()
vi.mock('../../app/appSdk', () => ({ launchChat: (...a: unknown[]) => launchChat(...a) }))
const previewApp = vi.fn()
const installApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: (...a: unknown[]) => installApp(...a),
    updateApp: () => Promise.reject(new Error('no update in this file')),
  },
}))

// Imported after the mocks are registered so the components bind them.
import { FixWithAiButton } from './installConsent'
import { InstallDialogHarness } from '../../test/installDialogHarness'

const FENCED = '<untrusted_content source=app_install_log:broken source_type=app_install_log>\nboom\n</untrusted_content>'

const REVIEW: AppInstallResult = {
  ok: false, name: 'broken', error: '', needs_consent: true,
  scan: { verdict: 'clean', tier: 'community', findings: [], signature: null },
  displayName: 'Broken', version: '1.0.0', previous: null, consent: 'b'.repeat(64),
  disclosure: {
    permissions: {}, crons: [], pythonDependencies: [], hasUI: false, uiComponents: '',
    hasBackend: false, onInstall: 'make setup', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
  },
}

/** Open the dialog on the review, confirm, and wait for the failure it answers with. */
async function installAndFail(failure: AppInstallResult) {
  previewApp.mockResolvedValue(REVIEW)
  installApp.mockResolvedValue(failure)
  render(<InstallDialogHarness target={{ source: '/apps/broken', label: 'broken' }} />)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan:/))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Install$/ }))
  return within(screen.getByRole('dialog')).findByTestId('install-failure')
}

beforeEach(() => {
  launchChat.mockReset()
  previewApp.mockReset()
  installApp.mockReset()
})

describe('a failed install hands its fix prompt to the button beside the error', () => {
  it("offers Fix with AI inside the dialog and passes the failed install's prompt through unchanged", async () => {
    const prompt = `debug this:\n\n${FENCED}`
    const failure = await installAndFail({
      ok: false, name: 'broken', error: 'onInstall hook exited 7', needs_consent: false,
      scan: null, log_excerpt: 'boom', fix_prompt: prompt,
    })
    expect(failure.textContent).toMatch(/onInstall hook exited 7\./)
    fireEvent.click(within(failure).getByRole('button', { name: /fix with ai/i }))
    expect(launchChat).toHaveBeenCalledTimes(1)
    expect(launchChat).toHaveBeenCalledWith({ prompt })
  })

  it('offers nothing to fix when the failure carried no log', async () => {
    const failure = await installAndFail({
      ok: false, name: 'broken', error: "app 'broken' already installed (use update)", needs_consent: false,
      scan: null, log_excerpt: '', fix_prompt: '',
    })
    expect(failure.textContent).toMatch(/already installed/)
    expect(within(failure).queryByRole('button', { name: /fix with ai/i })).toBeNull()
  })
})

describe('FixWithAiButton', () => {
  it('renders nothing when there is no fix prompt', () => {
    const { container } = render(<FixWithAiButton fixPrompt={null} />)
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders the button and passes the fenced prompt to launchChat on click', () => {
    const prompt = `debug this:\n\n${FENCED}`
    const { getByRole } = render(<FixWithAiButton fixPrompt={prompt} />)
    const btn = getByRole('button', { name: /fix with ai/i })
    fireEvent.click(btn)
    expect(launchChat).toHaveBeenCalledTimes(1)
    expect(launchChat).toHaveBeenCalledWith({ prompt })
  })
})
