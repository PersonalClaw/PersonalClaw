/**
 * The one place that knows WHERE the companion lives and WHICH hosts the shell may reach.
 *
 * This module is the shell's whole reason to exist. The companion UI is **served** by the
 * gateway at `#/companion` (`web/src/app/App.tsx`) — the shell does not contain a copy of it,
 * a re-implementation of it, or a build of it. It computes one URL and hands the WebView to
 * it. If a future change makes this file import anything out of `web/`, the shell has stopped
 * being a wrapper and become a fork.
 *
 * **Why the gateway URL is runtime state and not a build constant.** Capacitor's canonical
 * "wrap a remote site" recipe is `server.url` in `capacitor.config.json`, which is baked at
 * build time. Every PersonalClaw gateway lives at a different private address, so a baked URL
 * would mean one store build per user. The shell instead ships a tiny local bootstrap screen
 * whose only job is to learn the gateway origin once, remember it, and navigate away to it.
 *
 * **Why `allowNavigation` is a private-network rail and not `*`.** Capacitor keeps in-WebView
 * navigation to hosts on `server.allowNavigation` and kicks everything else to the system
 * browser. The companion is a LAN/tailnet surface, so the default list is exactly the private
 * ranges plus MagicDNS — a shell that cannot be steered onto a public phishing origin by a
 * link in rendered content. `capacitor.config.json` must carry every pattern below (asserted
 * by `tests/test_mobile_shell.py`); an operator fronting the gateway with a public reverse
 * proxy adds their own host to that list, deliberately, in a diff.
 */

/** The SPA hash route the gateway serves the companion at. Not a copy of it — a pointer. */
export const COMPANION_ROUTE = '#/companion'

/** The gateway's device-pairing page (`GET /pair`, `handlers/devices.py:pair_page`). */
export const PAIR_ROUTE = '/pair'

/**
 * Hostnames the shell may navigate to, as Capacitor `allowNavigation` globs (`*` = any run of
 * characters). RFC1918 is spelled out one `172.x` octet at a time because a glob cannot say
 * "16 through 31", and `172.*` would hand the shell most of a public /8.
 *
 * Tailscale is covered by `*.ts.net` (MagicDNS) rather than its 100.64/10 CGNAT range, for
 * the same reason: `100.*` would also match the public 100.0–100.63 space.
 *
 * Written out one literal at a time rather than generated with a loop, so that
 * `tests/test_mobile_shell.py` can compare this list against `capacitor.config.json` with a
 * dumb line scan — the Python suite parses this file, it does not execute node (the convention
 * `tests/test_desktop_seam.py` set). A generated list would need the rail to re-implement the
 * generator, which is how the two sides would come to disagree while both looking right.
 */
export const PRIVATE_HOST_PATTERNS = Object.freeze([
  'localhost',
  '127.0.0.1',
  '*.local',
  '10.*',
  '192.168.*',
  '172.16.*',
  '172.17.*',
  '172.18.*',
  '172.19.*',
  '172.20.*',
  '172.21.*',
  '172.22.*',
  '172.23.*',
  '172.24.*',
  '172.25.*',
  '172.26.*',
  '172.27.*',
  '172.28.*',
  '172.29.*',
  '172.30.*',
  '172.31.*',
  '*.ts.net',
])

/** Schemes a gateway can be reached over. */
const ALLOWED_PROTOCOLS = Object.freeze(['http:', 'https:'])

/** A rejection the bootstrap UI can turn into a sentence, rather than a bare `Error`. */
export class GatewayUrlError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'GatewayUrlError'
    this.code = code
  }
}

/**
 * Does `host` match one Capacitor-style glob?
 *
 * Anchored at both ends on purpose: an unanchored match would let
 * `evil.com/?x=192.168.1.1` style hostnames through on a substring hit.
 */
export function matchesHostPattern(host, pattern) {
  if (!host || !pattern) return false
  const escaped = String(pattern).replace(/[.+?^${}()|[\]\\]/g, '\\$&')
  const rx = new RegExp(`^${escaped.replace(/\*/g, '.*')}$`, 'i')
  return rx.test(String(host))
}

/** Is `host` on the private-network rail? */
export function isPrivateGatewayHost(host) {
  return PRIVATE_HOST_PATTERNS.some((pattern) => matchesHostPattern(host, pattern))
}

/**
 * Turn whatever the owner typed or scanned into a gateway **origin**.
 *
 * Accepts a bare `host:port` (what someone reads off `personalclaw status`) as well as a full
 * URL, and throws a coded `GatewayUrlError` for everything it will not accept. Returns only
 * the origin: any path, query or fragment the input carried is dropped, because the shell —
 * not the input — decides which route to open.
 */
export function normalizeGatewayUrl(raw) {
  const text = String(raw ?? '').trim()
  if (!text) throw new GatewayUrlError('EMPTY', 'Enter your gateway address.')

  // No scheme means someone typed `192.168.1.5:10000`. Default to http: rather than https:,
  // because a gateway on a private address is overwhelmingly plain HTTP.
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `http://${text}`

  let url
  try {
    url = new URL(candidate)
  } catch {
    throw new GatewayUrlError('BAD_URL', `${text} is not an address.`)
  }
  if (!ALLOWED_PROTOCOLS.includes(url.protocol)) {
    throw new GatewayUrlError('BAD_SCHEME', 'A gateway is reached over http or https.')
  }
  if (!url.hostname) throw new GatewayUrlError('BAD_URL', `${text} is not an address.`)
  if (!isPrivateGatewayHost(url.hostname)) {
    throw new GatewayUrlError(
      'NOT_PRIVATE',
      `${url.hostname} is not on your private network. The companion is reached over your ` +
        `LAN or tailnet; a public address has to be added to the shell's allowed hosts.`,
    )
  }
  return url.origin
}

/**
 * The served companion URL for a gateway — the single thing this shell wraps.
 */
export function companionUrl(rawGatewayUrl) {
  return `${normalizeGatewayUrl(rawGatewayUrl)}/${COMPANION_ROUTE}`
}

/**
 * Read a scanned pairing QR (`MC-8` renders it; `pair/start` composes it) into somewhere to go.
 *
 * The payload is one URL — `<base>/pair?code=XXXX-XXXX` — and `base` is resolved server-side
 * precisely so the scanning phone gets the LAN address rather than the `127.0.0.1` a browser
 * would have composed. So a scan configures the gateway URL *and* delivers the code: the shell
 * never asks the owner to type an address it was just handed.
 *
 * The shell deliberately does NOT redeem the code itself. `POST /api/devices/pair/complete`
 * answers with a `Set-Cookie` (httponly, SameSite=Lax, `pc_token_{port}`), so the session must
 * land in the WebView's own cookie jar — which happens by letting the served `/pair` page do
 * the exchange inside that WebView. A shell that redeemed the code natively would hold a
 * session the WebView could not use, and would be a second device-session contract.
 */
export function pairingTargetFromScan(scanned) {
  const text = String(scanned ?? '').trim()
  if (!text) throw new GatewayUrlError('EMPTY', 'Nothing was scanned.')

  let url
  try {
    url = new URL(text)
  } catch {
    throw new GatewayUrlError('BAD_URL', 'That QR code is not a pairing link.')
  }
  const origin = normalizeGatewayUrl(url.origin)
  if (url.pathname.replace(/\/+$/, '') !== PAIR_ROUTE) {
    throw new GatewayUrlError('NOT_PAIRING', 'That QR code is not a pairing link.')
  }
  const code = (url.searchParams.get('code') ?? '').trim()
  if (!code) throw new GatewayUrlError('NO_CODE', 'That pairing link carries no code.')

  return {
    gatewayUrl: origin,
    target: `${origin}${PAIR_ROUTE}?code=${encodeURIComponent(code)}`,
  }
}
