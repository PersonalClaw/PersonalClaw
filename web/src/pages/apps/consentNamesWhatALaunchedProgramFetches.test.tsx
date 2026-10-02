// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { AppDisclosure, AppInstallResult, AppLaunchedProgram } from '../../lib/api'

// ── Install consent says what a program an app starts downloads and runs, and where it goes ──
//
// A marketplace app searched by running `npx -y <its package> find <query>` from its own code: npx
// fetched the newest version of that npm package and ran it as you on every search. Its review said
// "Permissions the gateway enforces: None", named no host, and listed a provider module. A launch now
// says which package npx downloads and runs, which hosts the program reaches, and, for a program you
// choose (a runbook action you wrote), that you name it.

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

const NPX: AppLaunchedProgram = {
  program: 'npx',
  why: 'Without an API key, each search you make runs its find command with what you typed',
  inherits: ['sign-in', 'settings'],
  inheritsWhile: null,
  npmPackage: 'example-finder',
  hosts: ['registry.npmjs.org', 'finder.example.com'],
}
const GIT: AppLaunchedProgram = {
  program: 'git',
  why: "It clones a listing's repository into a temporary folder to read it.",
  inherits: ['settings'],
  inheritsWhile: null,
  npmPackage: '',
  hosts: ['git.example.org'],
}
const YOURS: AppLaunchedProgram = {
  program: '*',
  why: 'Applying a fix runs the command an action in your own runbook declares, exactly as it is written.',
  inherits: ['sign-in', 'settings'],
  inheritsWhile: { setting: 'allow_apply', label: 'Allow gated remediation', value: true, default: false },
  npmPackage: '',
  hosts: [],
}

function review(grants: Partial<AppDisclosure>, over: Partial<AppInstallResult> = {}): AppInstallResult {
  return {
    ok: false, name: 'example-finder', error: '', needs_consent: true,
    scan: { verdict: 'warning', tier: 'community', findings: [], signature: null },
    displayName: 'Example Finder', version: '0.1.5', disclosure: { ...NOTHING, ...grants }, previous: null,
    consent: 'c'.repeat(64), ...over,
  }
}

async function dialogFor(r: AppInstallResult, update = false): Promise<string> {
  previewApp.mockResolvedValue(r)
  render(<InstallDialogHarness target={{ source: '/apps/example-finder', label: 'Example Finder', ...(update ? { update: 'example-finder' } : {}) }} />)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).not.toMatch(/Nothing is installed yet/))
  return (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')
}

beforeEach(() => previewApp.mockReset())
afterEach(cleanup)

describe('install consent for a program that downloads and runs a package', () => {
  it('says npx downloads the package and runs it as you each time, and names the hosts', async () => {
    const text = await dialogFor(review({ launches: [NPX] }))
    expect(text).toContain(
      'Starts the npx program installed on this machine, as you and outside PersonalClaw. '
      + 'Without an API key, each search you make runs its find command with what you typed. '
      + 'Each time, npx fetches the newest version of the npm package example-finder from the npm registry, '
      + 'unless npm already holds it, and runs it as you. '
      + 'npm runs the install scripts of that package and of every package it depends on. '
      + 'It reaches registry.npmjs.org and finder.example.com. '
      + 'It runs with your own npx sign-in and settings.')
  })

  it('names the one host a program reaches', async () => {
    const text = await dialogFor(review({ launches: [GIT] }))
    expect(text).toContain(
      "It clones a listing's repository into a temporary folder to read it. It reaches git.example.org. "
      + 'It runs with your own git settings.')
    expect(text).not.toContain('npm registry')
  })

  it('an update that reaches a new host or runs another package lists each among what it changes', async () => {
    const next: AppLaunchedProgram = { ...NPX, npmPackage: 'example-finder-next', hosts: [...NPX.hosts, 'mirror.example.net'] }
    await dialogFor(review({ launches: [next] }, { previous: { ...NOTHING, launches: [NPX] } }), true)
    const changes = (screen.getByTestId('update-changes').textContent ?? '').replace(/\s+/g, ' ')
    expect(changes).toContain(
      '+ Adds: Starts npx, which fetches and runs the npm package example-finder-next, reaching '
      + 'registry.npmjs.org, finder.example.com, mirror.example.net, with your own sign-in, settings')
    expect(changes).toContain(
      '− Drops: Starts npx, which fetches and runs the npm package example-finder, reaching '
      + 'registry.npmjs.org, finder.example.com, with your own sign-in, settings')
  })
})

describe('install consent for a program you name', () => {
  it('says you choose the program, and the setting that has to be on', async () => {
    const text = await dialogFor(review({ launches: [YOURS] }))
    expect(text).toContain(
      'Starts the programs you name for it, as you and outside PersonalClaw. '
      + 'Applying a fix runs the command an action in your own runbook declares, exactly as it is written. '
      + 'While Allow gated remediation is on, each program it starts runs with your own sign-in and settings for that program. '
      + 'Allow gated remediation is off until you turn it on.')
    expect(text).not.toContain('Starts the * program')
  })

  it('an update that adds one says so', async () => {
    await dialogFor(review({ launches: [YOURS] }, { previous: { ...NOTHING } }), true)
    const changes = (screen.getByTestId('update-changes').textContent ?? '').replace(/\s+/g, ' ')
    expect(changes).toContain(
      '+ Adds: Starts programs you name, with your own sign-in, settings while Allow gated remediation is on')
  })
})

describe('an installed app', () => {
  it('its panel names the package and the hosts as its install consent did', () => {
    render(<AppDisclosureView disclosure={{ ...NOTHING, launches: [NPX, GIT] }} action="installed" />)
    const runs = (screen.getByTestId('consent-runs').textContent ?? '').replace(/\s+/g, ' ')
    expect(runs).toContain('npx fetches the newest version of the npm package example-finder')
    expect(runs).toContain('It reaches registry.npmjs.org and finder.example.com.')
    expect(runs).toContain('It reaches git.example.org.')
  })
})
