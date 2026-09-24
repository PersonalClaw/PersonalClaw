/** The L1 genui component layer: app-contributed components (AMBIENT-SURFACES §5.1/§6).
 *
 *  Loaded at the SHELL, once, for every ENABLED app that declares both a components
 *  module (`ui.components`) and the `generative-component` UI capability. Deliberately
 *  not at the app's page mount: a chat-born widget can name an app component without the
 *  user ever having opened that app, and a registry that only fills in after a page visit
 *  would drop the same line with `unknown-component` depending on where you had been.
 *
 *  Every load is error-boundaried in the value sense — one app's broken module is caught,
 *  logged and skipped; the remaining apps still register. Safe mode (`maxLayer = 0`)
 *  refuses the whole pass without fetching anything.
 *
 *  The contract an app's module exports:
 *
 *      export function register(sdk, ctx) {
 *        sdk.registerComponent(ctx, { name: 'AcmeGauge', group: 'Data', … })
 *      }
 *
 *  `sdk` is the host's `@personalclaw/app-sdk/genui` module (so the app calls the SAME
 *  gated entry point a bundled page would) and `ctx` is the app identity the gate reads. */
import { api, type AppSummary } from '../lib/api'
import { loadContributedModule, registerAppGenUiComponent, unregisterAppGenUiComponents, type AppContext } from './appSdk'
import { LAYER_APP, maxSurfaceLayer } from '../ui/surfaces/layers'

/** The capability that grants component registration. */
const CAP = 'generative-component'

/** Apps whose module has already been loaded this session, so a re-run (an app was
 *  enabled, the list refetched) does not re-import a module that already registered. */
const loaded = new Set<string>()

/** What one app's module receives as its `sdk` argument. Narrow on purpose: the L1
 *  loader hands over the registration entry point, not the whole SDK. */
export interface GenUiRegistrarSdk {
  registerComponent: typeof registerAppGenUiComponent
}

/** Whether this app should contribute components right now. */
export function contributesComponents(app: Pick<AppSummary, 'enabled' | 'uiComponents' | 'uiCapabilities'>): boolean {
  if (!app.enabled) return false
  if (!(app.uiComponents || '').trim()) return false
  return (app.uiCapabilities || []).includes(CAP)
}

/** The URL an app's components module is served at (the existing app-ui asset route). */
export function componentsModuleUrl(name: string, module: string): string {
  const clean = module.replace(/^\/+/, '')
  return `/apps/${encodeURIComponent(name)}/ui/${clean}`
}

/** Load + register one app's components. Returns how many registered, and never throws:
 *  a module that fails, exports nothing usable, or tries to shadow a core name leaves the
 *  registry exactly as it was. */
export async function loadAppComponents(app: AppSummary): Promise<number> {
  if (maxSurfaceLayer() < LAYER_APP) return 0
  if (!contributesComponents(app)) return 0
  if (loaded.has(app.name)) return 0
  loaded.add(app.name)
  const ctx: AppContext = {
    name: app.name,
    permissions: {},
    uiCapabilities: app.uiCapabilities || [],
  }
  let registered = 0
  try {
    const mod = await loadContributedModule(componentsModuleUrl(app.name, app.uiComponents || ''), ctx)
    const register = mod.register as
      | ((sdk: GenUiRegistrarSdk, ctx: AppContext) => void)
      | undefined
    if (typeof register !== 'function') {
      // eslint-disable-next-line no-console
      console.warn(`[surfaces] app "${app.name}" components module has no register() export`)
      return 0
    }
    const sdk: GenUiRegistrarSdk = {
      registerComponent: (a, def) => {
        const r = registerAppGenUiComponent(a, def)
        // A refusal is LOGGED, not thrown: an app that tried to shadow `Table` should
        // still get its other components, and the user should be able to see why one
        // is missing.
        if (r.ok) registered += 1
        // eslint-disable-next-line no-console
        else console.warn(`[surfaces] app "${app.name}" component refused: ${r.message}`)
        return r
      },
    }
    register(sdk, ctx)
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error(`[surfaces] app "${app.name}" components module failed to load`, e)
    // Failed load ⇒ nothing of this app's is in the registry, so allow a later retry.
    loaded.delete(app.name)
    unregisterAppGenUiComponents(app.name)
    return 0
  }
  return registered
}

/** Load the component layer for every eligible installed app. Safe to call more than
 *  once (idempotent per app); a disabled app's components are REMOVED on the same pass,
 *  so toggling an app off in Settings takes its components out of the next generated UI. */
export async function syncAppGenUiComponents(known?: AppSummary[]): Promise<number> {
  if (maxSurfaceLayer() < LAYER_APP) return 0
  let apps: AppSummary[] = known ?? []
  if (!known) {
    try {
      apps = await api.apps()
    } catch {
      return 0 // no app list ⇒ no L1 layer; core still renders everything it owns.
    }
  }
  let total = 0
  for (const app of apps) {
    if (contributesComponents(app)) {
      total += await loadAppComponents(app)
      continue
    }
    // Not eligible any more (disabled, capability revoked, module removed) — drop
    // whatever it had registered. This is the "removed on disable" half, and it lives
    // in the SAME pass as the load so the two cannot drift.
    if (loaded.has(app.name)) {
      loaded.delete(app.name)
      unregisterAppGenUiComponents(app.name)
    }
  }
  return total
}

/** Test seam: forget which modules were loaded. */
export function resetAppGenUiLayer(): void {
  for (const name of loaded) unregisterAppGenUiComponents(name)
  loaded.clear()
}
