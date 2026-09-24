/** The layered surface overlay (AMBIENT-SURFACES §6) — L0 / L1 / L2 + safe mode.
 *
 *  Once agents can generate UI, the failure to prevent is an agent-rewritten surface
 *  bricking the app. The overlay makes that structurally impossible by naming which
 *  layer a surface came from and giving the recovery route to the layer nothing can
 *  touch:
 *
 *    L0 — CORE: the shipped `web/dist` bundle. Immutable at runtime; the build owns it.
 *    L1 — APP: app-contributed pages + genui components, loaded through the appSdk
 *         host map, removable by disabling the app.
 *    L2 — USER/AGENT: user-customized or agent-rewritten surface overrides.
 *
 *  Resolution is replace-vs-compose per surface kind: component registrations COMPOSE
 *  (a higher layer may ADD, never SHADOW a lower layer's name — refused at register
 *  time), while skins/skeletons REPLACE (highest layer wins).
 *
 *  Safe mode forces `maxLayer = 0` — pure L0, no app modules, no overlays. Two ways
 *  in, deliberately: `#/dashboard?safe=1` (the URL a user can be told over the phone)
 *  and the `--safe-surfaces` gateway flag (the one that survives a client that cannot
 *  render its own address bar). Because L0 is immutable and the safe route is part of
 *  L0, the recovery path never routes through anything an agent can touch. */

/** 0 = core, 1 = app, 2 = user/agent. */
export type SurfaceLayer = 0 | 1 | 2

export const LAYER_CORE: SurfaceLayer = 0
export const LAYER_APP: SurfaceLayer = 1
export const LAYER_USER: SurfaceLayer = 2

/** The gateway's `--safe-surfaces` flag. A SERVER-side latch: it must not be clearable
 *  by editing the URL, or the flag would be advice rather than a recovery mode. */
let serverSafe = false

/** The meta tag the gateway stamps into `index.html` under `--safe-surfaces`.
 *  Read from the DOCUMENT, not from a fetch: the layer ceiling has to be known before
 *  the first app module loads, and a bootstrap request that resolved after that would
 *  make the flag advisory. Keep in sync with `surface_layers.SAFE_META_NAME`. */
const SAFE_META_NAME = 'personalclaw-safe-surfaces'

function metaSafeSurfaces(): boolean {
  if (typeof document === 'undefined') return false
  const el = document.querySelector(`meta[name="${SAFE_META_NAME}"]`)
  return el?.getAttribute('content') === '1'
}

/** Force safe mode on from the client side (the server flag's test seam, and the hook
 *  a future in-app "reload in safe mode" control would use). One-way: a later `false`
 *  cannot clear it, because the operator's recovery decision is not a per-call opinion. */
export function setServerSafeSurfaces(on: boolean): void {
  if (on) serverSafe = true
}

/** True when THIS page load asked for safe mode via the hash route's query.
 *
 *  Read from the hash (not `location.search`) because the app is hash-routed:
 *  `#/dashboard?safe=1`. Any truthy-but-not-"0" value counts — a user typing
 *  `safe=true` in a recovery situation must not be told their escape hatch was
 *  spelled wrong. */
export function safeModeInUrl(hash: string = window.location.hash): boolean {
  const q = hash.indexOf('?')
  if (q < 0) return false
  const params = new URLSearchParams(hash.slice(q + 1))
  const v = params.get('safe')
  if (v === null) return false
  return v !== '0' && v.toLowerCase() !== 'false'
}

/** Whether safe mode is active from EITHER lever. */
export function safeMode(hash?: string): boolean {
  return serverSafe || metaSafeSurfaces() || safeModeInUrl(hash)
}

/** The highest surface layer this page load may resolve. `0` in safe mode — pure L0.
 *
 *  Every layer consumer asks THIS, so adding a layer producer later cannot forget the
 *  gate: an app-module loader, a component registration and an overlay resolver all
 *  refuse the same way. */
export function maxSurfaceLayer(hash?: string): SurfaceLayer {
  return safeMode(hash) ? LAYER_CORE : LAYER_USER
}

/** Human name for a layer, for refusal messages and the safe-mode notice. */
export function layerName(layer: SurfaceLayer): string {
  return layer === LAYER_CORE ? 'core' : layer === LAYER_APP ? 'app' : 'user'
}
