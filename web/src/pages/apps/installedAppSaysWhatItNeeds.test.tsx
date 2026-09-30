// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import type { AppDisclosure, AppPrerequisite, AppSummary } from '../../lib/api'

// ── An installed app's panel says what it needs that PersonalClaw doesn't install ─────────────
//
// Install consent and the Store card name an app's `requires` (#3704): Local Image Generation's
// ComfyUI, what the app uses it for and how to have it. Once the app was in, nothing did. The
// Store card only says it for an app that is not installed, and the Library panel, where an owner
// goes when the app doesn't work, listed permissions and nothing it needs.
//
// Only the API is mocked; the Library and its detail panel are the shipped components.

const COMFYUI = {
  name: 'ComfyUI',
  why: 'Every image is made by a ComfyUI server running on this machine.',
  how: "Start ComfyUI on this machine and set its address on this app's Configure page.",
}

/** The installed copy's disclosure, as `GET /api/apps` carries it, naming *requires*. */
function disclosure(requires: AppPrerequisite[]): AppDisclosure {
  return {
    permissions: { network: true }, crons: [], pythonDependencies: [], sidecarDependencies: [],
    requires, hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '', providers: [],
    onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', cliSetup: '',
    cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
  }
}

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'local-image-gen', displayName: 'Local Image Generation', version: '0.1.2',
    description: 'Generate images with a ComfyUI server you run on your own machine.',
    enabled: true, origin: 'local', source: '/apps/local-image-gen', icon: '', hasBackend: false,
    hasUI: false, uiPages: [], isProvider: true, providerType: 'model', hasConfig: true,
    permissions: { network: true }, tags: [], backendRunning: false, backendPort: null,
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
  render(<AppsSection query={{ view: 'library', open: 'local-image-gen' }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Deactivate/ })).toBeTruthy())
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("the installed app's panel", () => {
  it('lists each prerequisite with what it is for and how to have it', async () => {
    await openInLibrary([app({ disclosure: disclosure([COMFYUI]) })])
    const row = screen.getByTestId('consent-requires')
    expect(within(row).getByText("What it needs that PersonalClaw doesn't install")).toBeTruthy()
    expect(row.textContent).toContain(`ComfyUI. ${COMFYUI.why}`)
    expect(row.textContent).toContain(COMFYUI.how)
  })

  it('says nothing about prerequisites for an app that needs none', async () => {
    await openInLibrary([app({ disclosure: disclosure([]) })])
    expect(screen.queryByText("What it needs that PersonalClaw doesn't install")).toBeNull()
  })
})
