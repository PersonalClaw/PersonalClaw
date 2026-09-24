/**
 * The bootstrap screen's wiring: learn the gateway origin once, then leave.
 *
 * Everything the owner sees after `handOff` is the gateway's own document. This file is the
 * shell's entire UI, and it exists only because Capacitor's `server.url` is a build-time
 * constant while a gateway address is per-owner runtime state (see `network.mjs`).
 *
 * What it persists is the registry `web/src/lib/endpoints.ts` declares, in the shell's own
 * storage scope — which that module argues is the only scope that can hold it. See
 * `registry.mjs` for why this is a parity rail rather than an import.
 */

import { companionUrl, GatewayUrlError, normalizeGatewayUrl, pairingTargetFromScan } from './network.mjs'
import { activeBaseUrl, forgetActiveGateway, readRegistry, rememberGateway } from './registry.mjs'
import { watchSafeAreaInsets } from './safeArea.mjs'

/** The remembered active gateway origin, or `''`. */
export function readStoredGateway(storage) {
  return activeBaseUrl(readRegistry(storage))
}

/**
 * Navigate the WebView to the served companion. `replace`, not `assign`: the bootstrap must not
 * sit in the history stack, or Android's back gesture out of the companion lands on it.
 */
export function handOff(location, url) {
  location.replace(url)
}

/**
 * Connect to `raw`, recording it as the active endpoint on success. Returns the error message on
 * failure so the caller can render it; throws nothing a UI has to unwrap.
 */
export function connect({ raw, storage, location }) {
  try {
    const origin = normalizeGatewayUrl(raw)
    // The URL is computed before anything is persisted: an address that cannot produce a
    // companion URL must not leave a row behind.
    const target = companionUrl(origin)
    rememberGateway(storage, { baseUrl: origin })
    handOff(location, target)
    return { ok: true, gatewayUrl: origin }
  } catch (err) {
    if (err instanceof GatewayUrlError) return { ok: false, code: err.code, message: err.message }
    throw err
  }
}

/**
 * Redeem a scanned pairing QR: record the origin the QR resolved to, then hand the WebView to the
 * gateway's own `/pair` page so the exchange — and its `Set-Cookie` — happens in the jar the
 * companion will read from.
 *
 * The shell deliberately does not call `POST /api/devices/pair/complete` itself. That route
 * answers with an httponly cookie, so a native redemption would hold a session the WebView could
 * not use, and would be a second device-session contract beside the one on `main`.
 */
export function connectFromScan({ scanned, storage, location }) {
  try {
    const { gatewayUrl, target } = pairingTargetFromScan(scanned)
    rememberGateway(storage, { baseUrl: gatewayUrl })
    handOff(location, target)
    return { ok: true, gatewayUrl }
  } catch (err) {
    if (err instanceof GatewayUrlError) return { ok: false, code: err.code, message: err.message }
    throw err
  }
}

/**
 * Wire the document. Idempotent enough to call once from an inline module script.
 */
export function start({ doc, view, storage, location }) {
  const shell = doc.getElementById('shell')
  if (shell) watchSafeAreaInsets(doc, view, shell)

  const form = doc.getElementById('connect')
  const field = doc.getElementById('gateway')
  const status = doc.getElementById('status')

  const fail = (message) => {
    if (status) {
      status.textContent = message
      status.hidden = false
    }
  }

  const remembered = readStoredGateway(storage)
  if (remembered) {
    if (field) field.value = remembered
    const result = connect({ raw: remembered, storage, location })
    // A remembered address that no longer validates (the shell's allowed hosts changed under it,
    // say) must not wedge the app on a blank screen — drop the row and ask again.
    if (!result.ok) {
      forgetActiveGateway(storage)
      fail(result.message)
    }
    return
  }

  form?.addEventListener?.('submit', (event) => {
    event.preventDefault?.()
    if (status) status.hidden = true
    const result = connect({ raw: field?.value, storage, location })
    if (!result.ok) fail(result.message)
  })
}

// Only in a real document; importing this module from a test must not touch anything.
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  start({ doc: document, view: window, storage: window.localStorage, location: window.location })
}
