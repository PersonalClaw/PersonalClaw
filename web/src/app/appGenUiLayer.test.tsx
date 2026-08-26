/** The L1 genui component layer: an app contributes components, and loses them on disable.
 *
 *  AMBIENT-SURFACES §5.1 / atom AS-6: *"an installed app registers a genui component that
 *  appears in generated UIs and is removed on disable, and an attempt to shadow a core
 *  component name is refused at register time."*
 *
 *  The loader is driven with a REAL module object (only the fetch is stubbed), so the app's
 *  own `register(sdk, ctx)` call really runs through the gated SDK entry point and the
 *  registry's real refusals apply. Two properties get their own negative:
 *
 *  * an app WITHOUT the `generative-component` capability registers nothing — and the
 *    capability it does have (`generative-widget`) must not be enough, or the two declarations
 *    are one declaration wearing two names;
 *  * DISABLING the app removes its components on the same pass that would have loaded them,
 *    so the load and the removal cannot drift apart. */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { AppSummary } from '../lib/api'
import {
  componentsModuleUrl,
  contributesComponents,
  loadAppComponents,
  resetAppGenUiLayer,
  syncAppGenUiComponents,
} from './appGenUiLayer'
import { allComponents, getComponent, library } from '../ui/genui/registry'
import { registerCoreGenUiComponents } from '../ui/genui/components'

vi.mock('../lib/api', () => ({ api: { apps: vi.fn(async () => []) } }))

/** Only the FETCH is stubbed: `register()` below is the app's real code path, and
 *  `registerComponent` is the real gated SDK function with the real registry behind it. */
const moduleStub: { register?: unknown } = {}
vi.mock('./appSdk', async (importOriginal) => {
  const real = await importOriginal<typeof import('./appSdk')>()
  return { ...real, loadContributedModule: vi.fn(async () => moduleStub) }
})

registerCoreGenUiComponents()

const GAUGE = {
  name: 'AcmeGauge',
  group: 'Data' as const,
  description: 'gauge',
  args: [{ key: 'value', type: 'number' as const, required: true }],
  component: () => null,
}

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'acme',
    displayName: 'Acme',
    version: '1.0.0',
    description: '',
    enabled: true,
    origin: 'path',
    icon: '',
    hasBackend: false,
    hasUI: false,
    uiPages: [],
    uiComponents: 'genui.mjs',
    uiCapabilities: ['generative-component'],
    isProvider: false,
    providerType: '',
    hasConfig: false,
    permissions: {},
    tags: [],
    backendRunning: false,
    backendPort: null,
    ...over,
  } as AppSummary
}

/** What a well-behaved app's module exports. */
function registersGauge(name = 'AcmeGauge') {
  return {
    register: (sdk: { registerComponent: (ctx: unknown, def: unknown) => unknown }, ctx: unknown) =>
      sdk.registerComponent(ctx as never, { ...GAUGE, name } as never),
  }
}

beforeEach(() => {
  resetAppGenUiLayer()
  delete moduleStub.register
  window.location.hash = '#/dashboard'
})

describe('eligibility', () => {
  it('needs enabled + a module + the capability, all three', () => {
    expect(contributesComponents(app())).toBe(true)
    expect(contributesComponents(app({ enabled: false }))).toBe(false)
    expect(contributesComponents(app({ uiComponents: '' }))).toBe(false)
    expect(contributesComponents(app({ uiCapabilities: [] }))).toBe(false)
  })

  it('is NOT granted by the widget capability', () => {
    // Supplying a DSL body and extending the component vocabulary are different trust edges.
    expect(contributesComponents(app({ uiCapabilities: ['generative-widget'] }))).toBe(false)
  })

  it('serves the module off the app-ui asset route', () => {
    expect(componentsModuleUrl('acme', 'genui.mjs')).toBe('/apps/acme/ui/genui.mjs')
    expect(componentsModuleUrl('acme', '/genui.mjs')).toBe('/apps/acme/ui/genui.mjs')
  })
})

describe('loading an app component layer', () => {
  it('registers what the module registers, and it shows up in the prompt', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(1)
    expect(getComponent('AcmeGauge')?.source).toBe('acme')
    expect(library.prompt()).toContain('AcmeGauge')
  })

  it('registers NOTHING for an app that never declared the capability', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app({ uiCapabilities: ['generative-widget'] }))).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('registers nothing in safe mode', async () => {
    window.location.hash = '#/dashboard?safe=1'
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('survives a module with no register() export', async () => {
    expect(await loadAppComponents(app())).toBe(0)
  })

  it('counts only the registrations that were ACCEPTED', async () => {
    // An app that tries to take a core name gets a refusal for that one and keeps the rest.
    const spy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    moduleStub.register = (sdk: { registerComponent: (c: unknown, d: unknown) => unknown }, ctx: unknown) => {
      sdk.registerComponent(ctx as never, { ...GAUGE, name: 'Table' } as never)
      sdk.registerComponent(ctx as never, { ...GAUGE, name: 'AcmeGauge' } as never)
    }
    expect(await loadAppComponents(app())).toBe(1)
    expect(getComponent('Table')?.source).toBe('')
    expect(getComponent('AcmeGauge')?.source).toBe('acme')
    spy.mockRestore()
  })

  it('does not re-run a module it already loaded', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(1)
    expect(await loadAppComponents(app())).toBe(0)
  })
})

describe('the sync pass', () => {
  it('removes a DISABLED app’s components', async () => {
    Object.assign(moduleStub, registersGauge())
    await loadAppComponents(app())
    expect(getComponent('AcmeGauge')).toBeTruthy()

    // The same pass that loads is the pass that removes — so the two halves cannot drift.
    await syncAppGenUiComponents([app({ enabled: false })])
    expect(getComponent('AcmeGauge')).toBeUndefined()
    expect(library.prompt()).not.toContain('AcmeGauge')
  })

  it('removes them when the capability is revoked, not only when the app is off', async () => {
    Object.assign(moduleStub, registersGauge())
    await loadAppComponents(app())
    await syncAppGenUiComponents([app({ uiCapabilities: [] })])
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('leaves the core set alone', async () => {
    const core = allComponents().filter((c) => c.layer === 0).length
    Object.assign(moduleStub, registersGauge())
    await syncAppGenUiComponents([app()])
    await syncAppGenUiComponents([app({ enabled: false })])
    expect(allComponents().filter((c) => c.layer === 0).length).toBe(core)
    expect(getComponent('Table')).toBeTruthy()
  })

  it('does nothing at all in safe mode', async () => {
    window.location.hash = '#/dashboard?safe=1'
    Object.assign(moduleStub, registersGauge())
    expect(await syncAppGenUiComponents([app()])).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })
})
