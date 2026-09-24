/**
 * The shell's persisted gateway registry — the SAME contract `web/src/lib/endpoints.ts` declares.
 *
 * 🔑 THE FORMAT IS NOT OWNED HERE. `web/src/lib/endpoints.ts` owns it, and says so by name:
 * *"This module is what desktop (T4.1) and mobile import so that neither re-decides the key
 * format — two shells that disagree about the format are two shells that cannot share a
 * registry."* Before this file existed that module had **zero** production importers; the mobile
 * shell is its first consumer, and the shell storing a bespoke `personalclaw.gatewayUrl` instead
 * would have been the second registry contract it exists to prevent.
 *
 * 🪤 WHY THIS IS A PARITY RAIL AND NOT AN IMPORT. `endpoints.ts` is TypeScript inside the `web`
 * Vite bundle. This bootstrap is a plain ES module served from the shell's own local origin with
 * no bundler and no build step at all — so it cannot import that file, in the same way
 * `desktop/`'s shell-side modules cannot import core's Python. The repo's existing answer to that
 * shape is a vocabulary rail (`tests/test_desktop_seam.py`), so this file keeps its
 * implementation as small as possible and `tests/test_mobile_shell.py` asserts the storage key
 * and both field vocabularies still match `endpoints.ts` character for character. Anything
 * beyond one row — the switcher, per-endpoint storage namespacing, `endpointSocketUrl` — is
 * deliberately NOT reimplemented here; it belongs to whichever atom grows the shell to N
 * gateways, and it will grow it by importing `endpoints.ts` through a real build.
 *
 * 🚫 NO CREDENTIAL IS EVER STORED. `endpoints.ts` records why: *"The URL carries no credential:
 * the device session rides as the session cookie"* — `pair/complete` answers with an httponly
 * `Set-Cookie` (`pc_token_{port}`), so the session lives in the WebView's cookie jar where script
 * cannot read it, and the companion guide forbids a `?token=` query parameter because it IP-binds
 * and a phone changes IP. Everything in this file is a URL and a label.
 */

/** The one shell-global storage key. Must equal `endpoints.ts`'s `REGISTRY_STORAGE_KEY`. */
export const REGISTRY_STORAGE_KEY = 'companion:endpoints'

/** `endpoints.ts`'s `EndpointRegistry` field vocabulary. */
export const REGISTRY_FIELDS = Object.freeze(['active', 'endpoints'])

/** `endpoints.ts`'s `CompanionEndpoint` field vocabulary, in its declared order. */
export const ENDPOINT_FIELDS = Object.freeze(['id', 'label', 'base_url', 'kind', 'device_session_ref'])

/** The zero value — `endpoints.ts`'s `EMPTY_REGISTRY`. */
export const EMPTY_REGISTRY = Object.freeze({ active: '', endpoints: [] })

const ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

/**
 * Mint an endpoint id — `ep_` + 12 chars of `[a-z0-9]`, per `endpoints.ts`'s `newEndpointId`.
 *
 * Deliberately NOT derived from `base_url`: a gateway that moves networks or changes port is the
 * same brain, and an id that tracked its URL would orphan that endpoint's state on every move.
 */
export function newEndpointId(rand = defaultRand) {
  let out = 'ep_'
  for (let i = 0; i < 12; i += 1) {
    out += ID_ALPHABET[Math.floor(rand() * ID_ALPHABET.length) % ID_ALPHABET.length]
  }
  return out
}

function defaultRand() {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined
  if (c && typeof c.getRandomValues === 'function') {
    const buf = new Uint32Array(1)
    c.getRandomValues(buf)
    return buf[0] / 0x1_0000_0000
  }
  return Math.random()
}

/**
 * The stored registry, or the zero value.
 *
 * Total by construction, for the reason `endpoints.ts` gives: a shell that throws on a corrupt
 * registry is a shell that cannot be recovered from without clearing storage by hand.
 */
export function readRegistry(storage) {
  let raw = ''
  try {
    raw = String(storage?.getItem?.(REGISTRY_STORAGE_KEY) ?? '')
  } catch {
    return { active: '', endpoints: [] }
  }
  if (!raw) return { active: '', endpoints: [] }

  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { active: '', endpoints: [] }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { active: '', endpoints: [] }
  }

  const rows = Array.isArray(parsed.endpoints) ? parsed.endpoints : []
  const endpoints = []
  for (const row of rows) {
    if (typeof row !== 'object' || row === null || Array.isArray(row)) continue
    // An id-less row is DROPPED rather than assigned a fresh one — minting on read would make a
    // parse nondeterministic, and it has no namespaced state to rescue anyway (`endpoints.ts`).
    if (typeof row.id !== 'string' || !row.id) continue
    if (endpoints.some((existing) => existing.id === row.id)) continue // first wins
    endpoints.push({
      id: row.id,
      label: typeof row.label === 'string' ? row.label : '',
      base_url: typeof row.base_url === 'string' ? row.base_url : '',
      // An unrecognized kind coerces to the LESS privileged of the two: `local` means "a gateway
      // this shell owns the lifecycle of", which a row must claim rather than fall into.
      kind: row.kind === 'local' ? 'local' : 'remote',
      device_session_ref: typeof row.device_session_ref === 'string' ? row.device_session_ref : '',
    })
  }
  const active = typeof parsed.active === 'string' ? parsed.active : ''
  return {
    // `active` must never dangle after a parse.
    active: endpoints.some((e) => e.id === active) ? active : endpoints[0]?.id ?? '',
    endpoints,
  }
}

export function writeRegistry(storage, registry) {
  try {
    storage?.setItem?.(REGISTRY_STORAGE_KEY, JSON.stringify(registry))
    return true
  } catch {
    // A WebView with storage denied can still reach the companion this launch; it just has to be
    // told the address again next time. Losing the shortcut is not worth losing the session.
    return false
  }
}

/** The active row, or `undefined`. */
export function activeEndpoint(registry) {
  return registry?.endpoints?.find((e) => e.id === registry.active)
}

/** The active row's `base_url`, or `''`. */
export function activeBaseUrl(registry) {
  return activeEndpoint(registry)?.base_url ?? ''
}

/**
 * Record `baseUrl` as the active gateway, reusing an existing row for the same origin.
 *
 * Matching an existing row by `base_url` is not the same mistake as *deriving the id* from it:
 * the id stays whatever was minted at pair time, so an endpoint keeps its identity (and its
 * namespaced state) across relaunches instead of accumulating one row per launch.
 *
 * `kind` is always `remote` from a mobile shell: `local` means "a gateway this shell spawned and
 * controls the lifecycle of", which a phone never does.
 *
 * `device_session_ref` is left empty on a row this shell creates, and never overwritten on one it
 * finds. The shell cannot know the nonce it names — `pair/complete` returns `device_id` and sets
 * the session as an httponly cookie, and the shell hands the redemption to the served `/pair`
 * page precisely so the cookie lands in the WebView's jar. Filling that field needs something
 * that can observe the redemption, which is not this file.
 */
export function rememberGateway(storage, { baseUrl, label = '', mintId = newEndpointId } = {}) {
  const registry = readRegistry(storage)
  const existing = registry.endpoints.find((e) => e.base_url === baseUrl)
  if (existing) {
    if (label) existing.label = label
    registry.active = existing.id
  } else {
    const row = {
      id: mintId(),
      label,
      base_url: baseUrl,
      kind: 'remote',
      device_session_ref: '',
    }
    registry.endpoints.push(row)
    registry.active = row.id
  }
  const stored = writeRegistry(storage, registry)
  return { registry, stored }
}

/** Drop the active row — an address that no longer validates must not wedge the next launch. */
export function forgetActiveGateway(storage) {
  const registry = readRegistry(storage)
  const endpoints = registry.endpoints.filter((e) => e.id !== registry.active)
  const next = { active: endpoints[0]?.id ?? '', endpoints }
  writeRegistry(storage, next)
  return next
}
