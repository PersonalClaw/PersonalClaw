// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { AppDisclosure, AppInstallResult, AppLaunchedProgram } from '../../lib/api'

// ── Install consent says which programs an app starts, what npm installs for it, and where it writes ──
//
// The agent apps' dialog said "This is everything Claude Code gets" and listed the provider module and
// the scan. It never said that Claude Code's own CLI is started from this machine with its own sign-in
// (and, with Isolated Claude settings off, with your own auto-approve rules), nor that switching the app
// on npm-installs its ACP adapter into the PersonalClaw folder. The manifest now declares each, and the
// one disclosure carries them here, to the installed app's panel and to an update's list of changes.

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('this rail never confirms')),
    updateApp: () => Promise.reject(new Error('this rail never confirms')),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { InstallDialogHarness } from '../../test/installDialogHarness'
import { AppDisclosureView } from './installConsent'

const NOTHING: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], sidecarDependencies: [], requires: [],
  launches: [], npmPackages: [], writes: [],
  hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '', providers: [],
  onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', cliSetup: '',
  cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
}

const CLAUDE: AppLaunchedProgram = {
  program: 'claude',
  why: 'Claude Code does the work of each chat. Its ACP adapter starts it.',
  inherits: ['sign-in', 'settings', 'auto-approve-rules'],
  inheritsWhile: { setting: 'isolated_config', label: 'Isolated Claude settings', value: false, default: true },
}
const KIRO: AppLaunchedProgram = {
  program: 'kiro-cli',
  why: 'kiro-cli does the work of each chat, over ACP.',
  inherits: ['sign-in', 'settings', 'auto-approve-rules'],
  inheritsWhile: null,
}
const ADAPTER = '@agentclientprotocol/claude-agent-acp'

function review(grants: Partial<AppDisclosure>, over: Partial<AppInstallResult> = {}): AppInstallResult {
  return {
    ok: false, name: 'claude-code-agent', error: '', needs_consent: true,
    scan: { verdict: 'clean', tier: 'community', findings: [], signature: null },
    displayName: 'Claude Code', version: '0.2.4', disclosure: { ...NOTHING, ...grants }, previous: null,
    consent: 'c'.repeat(64), ...over,
  }
}

async function dialogFor(r: AppInstallResult, update = false): Promise<string> {
  previewApp.mockResolvedValue(r)
  render(<InstallDialogHarness target={{ source: '/apps/claude-code-agent', label: 'Claude Code', ...(update ? { update: 'claude-code-agent' } : {}) }} />)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).not.toMatch(/Nothing is installed yet/))
  return (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')
}

beforeEach(() => previewApp.mockReset())
afterEach(cleanup)

describe('install consent', () => {
  it('names the program it starts and what of yours it runs with, and the setting that decides it', async () => {
    const text = await dialogFor(review({ launches: [CLAUDE] }))
    expect(screen.getByTestId('consent-runs').contains(screen.getByTestId('consent-launch'))).toBe(true)
    expect(text).toContain(
      'Starts the claude program installed on this machine, as you and outside PersonalClaw. '
      + 'Claude Code does the work of each chat. Its ACP adapter starts it. '
      + 'While Isolated Claude settings is off, it runs with your own claude sign-in, settings and auto-approve rules. '
      + 'What those rules allow, it does without asking you here first. '
      + 'Isolated Claude settings is on until you turn it off.')
  })

  it('says it plainly when no setting decides it', async () => {
    const text = await dialogFor(review({ launches: [KIRO] }))
    expect(text).toContain(
      'kiro-cli does the work of each chat, over ACP. It runs with your own kiro-cli sign-in, settings and '
      + 'auto-approve rules. What those rules allow, it does without asking you here first.')
  })

  it('names the npm package it installs, where it goes, and that npm runs its scripts', async () => {
    const text = await dialogFor(review({ npmPackages: [ADAPTER] }))
    expect(text).toContain(
      `When it is installed or switched on, installs the npm package ${ADAPTER} into your PersonalClaw folder `
      + '(acp-adapters), unless a copy is already on this machine. npm runs the install scripts of that package '
      + 'and of every package it depends on. Until it is installed, npx fetches it each time PersonalClaw starts it.')
  })

  it('names each place outside its own folder it writes', async () => {
    const text = await dialogFor(review({
      writes: [
        { path: 'cc-config', why: "Claude Code's own config while Isolated Claude settings is on." },
        { path: '~/.example', why: 'Its cache.' },
      ],
    }))
    expect(screen.getByTestId('consent-writes')).toBeTruthy()
    expect(text).toContain("What it writes outside its own folder")
    expect(text).toContain("cc-config in your PersonalClaw folder: Claude Code's own config while Isolated Claude settings is on.")
    expect(text).toContain('~/.example in your home folder: Its cache.')
  })

  it('an update that adds a program or an npm package lists each among what it changes', async () => {
    await dialogFor(review({ launches: [CLAUDE], npmPackages: [ADAPTER] }, { previous: { ...NOTHING } }), true)
    const changes = screen.getByTestId('update-changes').textContent ?? ''
    expect(changes).toContain(
      '+ Adds: Starts claude, with your own sign-in, settings, auto-approve rules while Isolated Claude settings is off')
    expect(changes).toContain(`+ Adds: npm package ${ADAPTER}`)
  })

  it('an update that widens what a program runs with says so', async () => {
    await dialogFor(review({ launches: [KIRO] }, { previous: { ...NOTHING, launches: [{ ...KIRO, inherits: ['sign-in'] }] } }), true)
    const changes = screen.getByTestId('update-changes').textContent ?? ''
    expect(changes).toContain('+ Adds: Starts kiro-cli, with your own sign-in, settings, auto-approve rules')
    expect(changes).toContain('− Drops: Starts kiro-cli, with your own sign-in')
  })
})

describe('an installed app', () => {
  it('its panel says the same as its install consent did', () => {
    render(<AppDisclosureView disclosure={{ ...NOTHING, launches: [CLAUDE], npmPackages: [ADAPTER] }} action="installed" />)
    const runs = (screen.getByTestId('consent-runs').textContent ?? '').replace(/\s+/g, ' ')
    expect(runs).toContain('Starts the claude program installed on this machine')
    expect(runs).toContain(`installs the npm package ${ADAPTER}`)
  })
})
