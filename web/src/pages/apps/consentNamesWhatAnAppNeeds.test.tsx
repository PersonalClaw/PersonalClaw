// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { AppCatalog, AppCatalogEntry, AppDisclosure, AppInstallResult } from '../../lib/api'

// ── Install consent and the Store card say what an app needs that PersonalClaw doesn't install ──
//
// Local Image Generation needs a ComfyUI server. On main nothing on either surface could say so,
// and the owner learned it from the error after installing. The manifest's `requires` reaches both
// through the one disclosure, and so do the engine packages a sidecar app's Install engine puts in
// its own Python environment.

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
import { StoreView } from './AppsSection'

const COMFYUI = {
  name: 'ComfyUI',
  why: 'It generates the images; this app sends it the prompt.',
  how: 'Install ComfyUI, start it on this machine, and put its address in Configure.',
}

const NOTHING: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], sidecarDependencies: [], requires: [], launches: [], npmPackages: [], writes: [],
  hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '', providers: [],
  onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', cliSetup: '',
  cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
}

function review(grants: Partial<AppDisclosure>, over: Partial<AppInstallResult> = {}): AppInstallResult {
  return {
    ok: false, name: 'image-maker', error: '', needs_consent: true,
    scan: { verdict: 'clean', tier: 'community', findings: [], signature: null },
    displayName: 'Image Maker', version: '1.0.0', disclosure: { ...NOTHING, ...grants }, previous: null,
    consent: 'c'.repeat(64), ...over,
  }
}

async function dialogFor(r: AppInstallResult, update = false): Promise<string> {
  previewApp.mockResolvedValue(r)
  render(<InstallDialogHarness target={{ source: '/apps/image-maker', label: 'Image Maker', ...(update ? { update: 'image-maker' } : {}) }} />)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).not.toMatch(/Nothing is installed yet/))
  return (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')
}

beforeEach(() => previewApp.mockReset())
afterEach(cleanup)

describe('install consent', () => {
  it('names the prerequisite, what it is for and how to have it, before anything installs', async () => {
    const text = await dialogFor(review({ requires: [COMFYUI] }))
    expect(text).toContain("What it needs that PersonalClaw doesn't install")
    expect(text).toContain(`ComfyUI. ${COMFYUI.why}${COMFYUI.how}`)
    const row = screen.getByTestId('consent-requires')
    // First, because it decides whether installing is worth it at all.
    expect(screen.getByTestId('app-disclosure').firstElementChild).toBe(row)
  })

  it('names the engine packages Install engine will put in its own Python environment', async () => {
    const text = await dialogFor(review({
      providers: [{ type: 'tts', implementation: 'provider:create_provider', execution: 'sidecar' }],
      sidecarDependencies: ['omnivoice>=0.2', 'vocos'],
    }))
    expect(text).toContain(
      'When you choose Install engine, installs omnivoice>=0.2, vocos into its own Python environment, which that child process runs.')
  })

  it("an update that adds a prerequisite lists it among what it changes", async () => {
    const text = await dialogFor(review({ requires: [COMFYUI] }, { previous: { ...NOTHING } }), true)
    expect(screen.getByTestId('update-changes').textContent).toContain('+ Adds: Needs ComfyUI')
    expect(text).toContain('+ Adds: Needs ComfyUI')
  })
})

describe('the Store card', () => {
  const EMPTY: AppCatalog = {
    bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
    localSources: ['/srv/apps'], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
  }

  function card(over: Partial<AppCatalogEntry>) {
    const e: AppCatalogEntry = {
      name: 'image-maker', displayName: 'Image Maker', description: 'Makes images.', version: '1.0.0',
      icon: '', author: '', source: '/srv/apps/image-maker', sourceKind: 'local',
      isProvider: false, providerType: '', tags: [], permissions: {}, crons: [], consentKnown: true,
      ...over,
    }
    render(
      <StoreView catalog={{ ...EMPTY, localApps: [e] }} result={[{ ...e, installed: false, enabled: false, hasUI: false }]}
        totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
        onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
        onAction={(() => {}) as never} onOpenSources={() => {}} />,
    )
  }

  it('says what the app needs before the click that installs it', () => {
    card({ requires: [COMFYUI] })
    const line = screen.getByTestId('store-card-requires')
    expect(line.textContent).toBe('Needs ComfyUI')
    expect(line.getAttribute('title')).toBe(`ComfyUI: ${COMFYUI.why}`)
  })

  it('reads two prerequisites as a list, and says nothing for an app that needs nothing', () => {
    card({ requires: [COMFYUI, { name: 'Ollama', why: 'It writes the captions.', how: 'Install Ollama.' }] })
    expect(screen.getByTestId('store-card-requires').textContent).toBe('Needs ComfyUI and Ollama')
    cleanup()
    card({})
    expect(screen.queryByTestId('store-card-requires')).toBeNull()
  })
})
