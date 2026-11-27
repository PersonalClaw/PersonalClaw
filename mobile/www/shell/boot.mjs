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

import {
  companionUrl,
  GatewayUrlError,
  normalizeGatewayUrl,
  opensOutsideTheApp,
  outsideTheAppNotice,
  pairingTargetFromScan,
} from './network.mjs'
import { activeBaseUrl, forgetActiveGateway, readRegistry, rememberGateway } from './registry.mjs'
import { watchSafeAreaInsets } from './safeArea.mjs'

/** The remembered active gateway origin, or `''`. */
export function readStoredGateway(storage) {
  return activeBaseUrl(readRegistry(storage))
}

/** The platform the shell runs on, as Capacitor names it: `android`, `ios`, or `web` in a browser. */
export function platformOf(view) {
  try {
    return view?.Capacitor?.getPlatform?.() || 'web'
  } catch {
    return 'web'
  }
}

/**
 * What a connect answers when the app on this platform would open the gateway outside itself
 * (`network.mjs`, `opensOutsideTheApp`): the gateway is remembered, but the WebView is not
 * handed off. The shell says why first, and goes only when the owner chooses to.
 */
function outside(gatewayUrl, target) {
  return { ok: false, code: 'OPENS_OUTSIDE', message: outsideTheAppNotice(gatewayUrl), gatewayUrl, target }
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
export function connect({ raw, storage, location, platform = 'web' }) {
  try {
    const origin = normalizeGatewayUrl(raw)
    // The URL is computed before anything is persisted: an address that cannot produce a
    // companion URL must not leave a row behind.
    const target = companionUrl(origin)
    rememberGateway(storage, { baseUrl: origin })
    if (opensOutsideTheApp(origin, platform)) return outside(origin, target)
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
export function connectFromScan({ scanned, storage, location, platform = 'web' }) {
  try {
    const { gatewayUrl, target } = pairingTargetFromScan(scanned)
    rememberGateway(storage, { baseUrl: gatewayUrl })
    if (opensOutsideTheApp(gatewayUrl, platform)) return outside(gatewayUrl, target)
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
  const away = doc.getElementById('open-outside')
  const platform = platformOf(view)

  const fail = (message) => {
    if (status) {
      status.textContent = message
      status.hidden = false
    }
  }

  // A gateway this platform opens outside the app is never handed off unasked: the shell says
  // why, and the WebView goes only on the owner's Open in browser.
  let pending = ''
  const offer = (result) => {
    fail(result.message)
    pending = result.target
    if (away) away.hidden = false
  }
  away?.addEventListener?.('click', () => {
    if (pending) handOff(location, pending)
  })
  const answer = (result) => {
    if (result.code === 'OPENS_OUTSIDE') offer(result)
    else fail(result.message)
  }

  const remembered = readStoredGateway(storage)
  if (remembered) {
    if (field) field.value = remembered
    const result = connect({ raw: remembered, storage, location, platform })
    if (result.ok) return
    if (result.code === 'OPENS_OUTSIDE') {
      offer(result)
    } else {
      // A remembered address that no longer validates (the shell's allowed hosts changed under
      // it, say) must not wedge the app on a blank screen — drop the row and ask again.
      forgetActiveGateway(storage)
      fail(result.message)
    }
  }

  form?.addEventListener?.('submit', (event) => {
    event.preventDefault?.()
    if (status) status.hidden = true
    if (away) away.hidden = true
    pending = ''
    const result = connect({ raw: field?.value, storage, location, platform })
    if (!result.ok) answer(result)
  })
}

// Only in a real document; importing this module from a test must not touch anything.
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  start({ doc: document, view: window, storage: window.localStorage, location: window.location })
}
