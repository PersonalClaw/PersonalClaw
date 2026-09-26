// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import type { AppDisclosure } from '../../lib/api'
import { AppDisclosureView, permissionRows } from './installConsent'

// ── Install consent names every kind of code an app brings, and says it runs as you ──────────────
//
// #3608's "What it runs on this machine" row named a server process, the install (or update) hook
// and the MCP servers. An app's provider module — imported into the gateway itself — its switch-on,
// switch-off and uninstall hooks, its `personalclaw setup`/`doctor` steps and its source parsers
// appeared nowhere, so an app that shipped only a provider showed no row at all, and nothing on the
// screen said this code runs under your account (`docs/security/limitations.md` §7). The skills an
// app installs for your agents were not listed either, and a skill is instructions they follow.
//
// The sentence is the gateway's (`apps/disclosure._runs_as_you`), shown verbatim, so the dialog and
// the server cannot describe the code two ways.

afterEach(cleanup)

const text = () => (document.body.innerText || document.body.textContent || '').replace(/\s+/g, ' ')

/** What `apps/disclosure._runs_as_you` composes for `BRINGS_CODE`'s facts, copied from its output. */
const RUNS_AS_YOU =
  'Its server, its 2 provider modules, its MCP server, the commands it runs at install, update, ' +
  'enable, disable and uninstall, the step it adds to personalclaw setup and its source parser run ' +
  'as you on this machine. They can read and change your files, your PersonalClaw settings ' +
  'included, and the permissions listed here limit what the app asks the gateway for, not what ' +
  'this code does.'

const BRINGS_CODE: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], hasUI: false, uiComponents: '',
  hasBackend: true, backendSandbox: '',
  providers: [
    { type: 'model', implementation: 'provider.main:make', execution: 'in-process' },
    { type: 'memory', implementation: 'store.main:make', execution: 'sidecar' },
  ],
  onInstall: 'bash install.sh', onUpdate: 'bash migrate.sh', onEnable: 'bash on.sh',
  onDisable: 'bash off.sh', onUninstall: 'bash remove.sh',
  cliSetup: 'steps:setup', cliDoctor: '',
  sources: [{ name: 'repo-issues', script: 'parse_issues.py' }],
  mcpServers: [{ name: 'notes', launches: 'python mcp.py' }],
  skills: ['deploy-site', 'release-notes'],
  runsAsYou: RUNS_AS_YOU,
}

const NOTHING: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], hasUI: false, uiComponents: '',
  hasBackend: false, backendSandbox: '', providers: [], onInstall: '', onUpdate: '',
  onEnable: '', onDisable: '', onUninstall: '', cliSetup: '', cliDoctor: '', sources: [],
  mcpServers: [], skills: [], runsAsYou: '',
}

describe('install consent says what runs as you', () => {
  it('leads with the gateway’s sentence, verbatim', () => {
    render(<AppDisclosureView disclosure={BRINGS_CODE} action="install" />)
    expect(screen.getByTestId('consent-runs-as-you').textContent).toBe(RUNS_AS_YOU)
  })

  it('names each provider module and where it runs', () => {
    render(<AppDisclosureView disclosure={BRINGS_CODE} action="install" />)
    expect(text()).toContain("Loads its model provider provider.main:make into the gateway's own process.")
    expect(text()).toContain('Runs its memory provider store.main:make in a child process of the gateway.')
  })

  it('names every lifecycle hook with when it runs', () => {
    render(<AppDisclosureView disclosure={BRINGS_CODE} action="install" />)
    const shown = text()
    expect(shown).toContain("Runs bash install.sh in the app's folder during the install.")
    expect(shown).toContain("Runs bash on.sh in the app's folder each time the app is switched on.")
    expect(shown).toContain("Runs bash off.sh in the app's folder each time the app is switched off.")
    expect(shown).toContain("Runs bash remove.sh in the app's folder before the app is removed.")
    expect(shown).toContain('Runs steps:setup when you run personalclaw setup.')
    expect(shown).toContain('Runs parse_issues.py to read what its repo-issues source fetches, with no network access.')
  })

  it('an update shows the update hook, and the later hooks still', () => {
    render(<AppDisclosureView disclosure={BRINGS_CODE} action="update" />)
    const shown = text()
    expect(shown).toContain("Runs bash migrate.sh in the app's folder during the update.")
    expect(shown).not.toContain('bash install.sh')
    expect(shown).toContain('before the app is removed')
  })

  it('an app that ships only a provider module gets the row', () => {
    render(
      <AppDisclosureView
        disclosure={{
          ...NOTHING,
          providers: [{ type: 'model', implementation: 'm:make', execution: 'in-process' }],
          runsAsYou: 'Its provider module runs as you on this machine.',
        }}
        action="install"
      />,
    )
    expect(screen.getByTestId('consent-runs')).toBeTruthy()
    expect(screen.getByTestId('consent-runs-as-you').textContent).toContain('runs as you')
  })

  it('a server in a sandbox tier says where it runs', () => {
    render(<AppDisclosureView disclosure={{ ...NOTHING, hasBackend: true, backendSandbox: 'docker' }} action="install" />)
    expect(text()).toContain('Starts its own server process inside the docker sandbox')
    expect(screen.queryByTestId('consent-runs-as-you')).toBeNull()
  })

  it('an app that brings no code shows no such row', () => {
    render(<AppDisclosureView disclosure={NOTHING} action="install" />)
    expect(screen.queryByTestId('consent-runs')).toBeNull()
    expect(screen.queryByTestId('consent-skills')).toBeNull()
  })
})

describe('install consent lists what an app teaches your agents', () => {
  it('names each skill it installs', () => {
    render(<AppDisclosureView disclosure={BRINGS_CODE} action="install" />)
    const row = screen.getByTestId('consent-skills')
    expect(row.textContent).toContain('What it teaches your agents')
    expect(row.textContent).toContain('Adds 2 skills your agents can load and follow as instructions: deploy-site, release-notes.')
  })

  it('the memory grant says it reaches what your agents recall and the lessons they follow', () => {
    expect(permissionRows({ memory: true })).toContain(
      'Read and change your memory — what your agents recall, including the lessons they follow',
    )
  })
})
