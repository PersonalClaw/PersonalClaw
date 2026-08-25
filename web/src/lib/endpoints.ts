/** Multi-gateway endpoint registry + per-endpoint storage namespacing (COMPANION-APPS T3.1/T3.3).
 *
 * A companion shell (desktop today, mobile next) holds N *paired gateways* — N independent
 * PersonalClaw brains — and a switcher that re-points `active` and reloads the same served SPA
 * from the new origin. This module owns the registry shape and, more importantly, the one
 * mechanism that keeps two brains from bleeding into each other.
 *
 * 🔑 WHY THIS LIVES IN THE SHELL'S STORAGE SCOPE, NOT THE SPA'S. The served SPA cannot hold this
 * registry, for two independently sufficient reasons, both measured rather than assumed:
 *
 *   1. The SPA is re-downloaded from whichever gateway is active. `desktop/main.js:768` does
 *      `wc.loadURL(backendUrl)` — the shell loads the dashboard *from the gateway's own origin* —
 *      and `backendUrl` (`desktop/main.js:143`) is a single string resolved from the spawned
 *      gateway's READY line. A registry of N gateways has nowhere to live in a bundle that is
 *      itself one of the N.
 *   2. The SPA's storage is ALREADY partitioned, for free, by browser origin. `grep -n partition`
 *      over `desktop/main.js` finds nothing, so the default session partition applies and
 *      per-origin isolation holds. Nothing in the SPA reaches across gateways either:
 *      `useChatSocket.ts:32` opens `${proto}://${location.host}/api/ws` — origin-relative — and
 *      `lib/api.ts` uses relative URLs only (it has no `base_url`/`API_BASE` at all).
 *
 * So the ONLY storage scope that spans all N gateways is the shell's own. That is precisely where
 * bleed is possible, and therefore the only place namespacing is load-bearing. This module is what
 * desktop (T4.1) and mobile import so that neither re-decides the key format — two shells that
 * disagree about the format are two shells that cannot share a registry.
 *
 * 🚫 NO HUB, NO GATEWAY-TO-GATEWAY. N endpoints are N client-side rows. Gateways never learn about
 * each other; the client fans out. (The multi-instance hub is permanently vetoed.)
 */

/** `local` = a gateway this shell spawned and controls the lifecycle of; `remote` = anything else. */
export type EndpointKind = 'local' | 'remote'

/** One saved gateway. Shape fixed by the owner amendment: `{id, label, base_url, kind,
 *  device_session_ref}`. `snake_case` on `base_url`/`device_session_ref` is deliberate — these
 *  rows are persisted JSON and mirror the wire/config naming, not a TS convention. */
export interface CompanionEndpoint {
  /** Opaque, client-minted, stable for the life of the pairing. See `newEndpointId`. */
  id: string
  /** Human label; seeded from `companion.instance_name`, user-editable. */
  label: string
  base_url: string
  kind: EndpointKind
  /** Nonce naming a REMOTE-USER-AUTH `sessions.json` device row. Not a token; not a secret. */
  device_session_ref: string
}

/** `{active, endpoints[]}` — a list plus an active-gateway pointer. */
export interface EndpointRegistry {
  /** The `id` of the active endpoint, or `''` when there are none. Never dangles after a parse. */
  active: string
  endpoints: CompanionEndpoint[]
}

/** The zero value. Every failure mode of `parseRegistry` lands here or in something narrower. */
export const EMPTY_REGISTRY: EndpointRegistry = { active: '', endpoints: [] }

/** The one shell-global storage key. Deliberately NOT under `ENDPOINT_KEY_PREFIX`: the registry
 *  is the thing that spans all endpoints, so namespacing it by endpoint would be circular. */
export const REGISTRY_STORAGE_KEY = 'companion:endpoints'

// ── ids ──────────────────────────────────────────────────────────────────────────────────────

const ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

/** Mint an endpoint id.
 *
 * 🪤 THE ID IS NOT DERIVED FROM `base_url`, AND MUST NOT BE. Two rows can legitimately share a
 * host (two gateways behind one reverse proxy, or `localhost` on two ports at different times),
 * so a URL is not unique. And a URL *changes* — a laptop moves networks, a port is reassigned,
 * `claw.local` becomes a Tailscale name — without the gateway becoming a different brain. If the
 * id were the URL, every such change would silently orphan that endpoint's namespaced state and
 * present a re-paired gateway as a stranger. The id is minted once at pair time and never
 * recomputed; `base_url` is mutable data hanging off it.
 *
 * The alphabet is `[a-z0-9]` so a minted id never needs escaping in a storage key. (The key
 * encoder in `endpointKey` is nonetheless total over arbitrary ids — a registry can be
 * hand-edited, or written by an older shell.)
 */
export function newEndpointId(rand: () => number = defaultRand): string {
  let out = 'ep_'
  for (let i = 0; i < 12; i++) out += ID_ALPHABET[Math.floor(rand() * ID_ALPHABET.length) % ID_ALPHABET.length]
  return out
}

function defaultRand(): number {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined
  if (c && typeof c.getRandomValues === 'function') {
    const buf = new Uint32Array(1)
    c.getRandomValues(buf)
    return buf[0] / 0x1_0000_0000
  }
  return Math.random()
}

// ── parse / serialize ────────────────────────────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

/** Coerce one raw row, or `undefined` if it cannot be one.
 *
 * A row with no `id` is DROPPED rather than assigned a fresh one: minting here would make a parse
 * nondeterministic, and an id-less row has no namespaced state to rescue anyway.
 *
 * An unrecognized `kind` coerces to `'remote'`, the LESS privileged of the two. `local` means "a
 * gateway this shell owns the lifecycle of"; defaulting an unknown value to `local` would hand
 * lifecycle authority to a row that never claimed it. */
function coerceEndpoint(v: unknown): CompanionEndpoint | undefined {
  if (!isRecord(v)) return undefined
  const id = str(v.id)
  if (!id) return undefined
  return {
    id,
    label: str(v.label) ?? '',
    base_url: str(v.base_url) ?? '',
    kind: v.kind === 'local' ? 'local' : 'remote',
    device_session_ref: str(v.device_session_ref) ?? '',
  }
}

/** Normalize a candidate registry: drop unusable rows, drop duplicate ids (FIRST wins — it is the
 *  one whose namespaced state was presumably written), and guarantee `active` names a present id.
 *
 *  Total by construction: every input resolves to a registry. A shell that throws on a corrupt
 *  registry cannot even reach the switcher that would let the user fix it. */
export function normalizeRegistry(value: unknown): EndpointRegistry {
  if (!isRecord(value)) return EMPTY_REGISTRY
  const rawList = Array.isArray(value.endpoints) ? value.endpoints : []
  const endpoints: CompanionEndpoint[] = []
  const seen = new Set<string>()
  for (const raw of rawList) {
    const ep = coerceEndpoint(raw)
    if (!ep || seen.has(ep.id)) continue
    seen.add(ep.id)
    endpoints.push(ep)
  }
  const wanted = str(value.active) ?? ''
  const active = seen.has(wanted) ? wanted : (endpoints[0]?.id ?? '')
  return { active, endpoints }
}

/** Parse persisted JSON. Malformed JSON, a missing `active`, an `active` naming an absent id, and
 *  duplicate ids each resolve to a defined value instead of throwing. */
export function parseRegistry(raw: string | null | undefined): EndpointRegistry {
  if (raw == null || raw === '') return EMPTY_REGISTRY
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return EMPTY_REGISTRY
  }
  return normalizeRegistry(parsed)
}

export function serializeRegistry(reg: EndpointRegistry): string {
  return JSON.stringify(reg)
}

// ── registry reducers (pure; never touch storage) ────────────────────────────────────────────

export function findEndpoint(reg: EndpointRegistry, id: string): CompanionEndpoint | undefined {
  return reg.endpoints.find((e) => e.id === id)
}

export function activeEndpoint(reg: EndpointRegistry): CompanionEndpoint | undefined {
  return findEndpoint(reg, reg.active)
}

/** Add a row (minting an id when the caller does not supply one) and make it active — pairing a
 *  gateway is a switch to it. Re-adding an existing id REPLACES that row's data in place and keeps
 *  its position, so re-pairing after a URL change preserves the id and therefore the namespaced
 *  state (see the `newEndpointId` note on why the id is not the URL). */
export function addEndpoint(
  reg: EndpointRegistry,
  entry: Omit<CompanionEndpoint, 'id'> & { id?: string },
): EndpointRegistry {
  const id = entry.id ?? newEndpointId()
  const row: CompanionEndpoint = {
    id,
    label: entry.label,
    base_url: entry.base_url,
    kind: entry.kind,
    device_session_ref: entry.device_session_ref,
  }
  const at = reg.endpoints.findIndex((e) => e.id === id)
  const endpoints = at >= 0 ? reg.endpoints.map((e, i) => (i === at ? row : e)) : [...reg.endpoints, row]
  return { active: id, endpoints }
}

/** Remove a row. When the ACTIVE row is removed the pointer falls to the first survivor (`''` if
 *  none) — the invariant "`active` never dangles" holds after every reducer, not just after a parse.
 *
 *  Deliberately does NOT purge that endpoint's namespaced state: forgetting an endpoint and wiping
 *  its caches are separate decisions (a re-pair should be able to find its state again). Callers
 *  that mean "forget it entirely" call `clearEndpointState` too. */
export function removeEndpoint(reg: EndpointRegistry, id: string): EndpointRegistry {
  const endpoints = reg.endpoints.filter((e) => e.id !== id)
  if (endpoints.length === reg.endpoints.length) return reg
  const active = reg.active === id ? (endpoints[0]?.id ?? '') : reg.active
  return { active, endpoints }
}

/** Re-point `active`. A no-op for an unknown id: the switcher must not be able to strand the shell
 *  pointing at nothing. */
export function setActive(reg: EndpointRegistry, id: string): EndpointRegistry {
  if (!reg.endpoints.some((e) => e.id === id)) return reg
  return { ...reg, active: id }
}

// ── per-endpoint storage namespacing ─────────────────────────────────────────────────────────

/** Rhymes with `data/store.ts`'s `_SS_PREFIX = 'cache:'` — a short, colon-terminated namespace. */
export const ENDPOINT_KEY_PREFIX = 'ep:'

/** Storage key for (endpoint id, logical key).
 *
 * 🔑 THE LENGTH FIELD IS THE WHOLE POINT. The naive `id + ':' + key` is NOT injective, because `:`
 * can occur inside an id: `{id:'a', key:'b:c'}` and `{id:'a:b', key:'c'}` both render `a:b:c`, so
 * two different brains would share one slot — the exact bleed this module exists to prevent, hidden
 * inside the mechanism meant to prevent it. Encoding `id.length` before the id makes the split
 * point data rather than a guess, so the encoding is injective for ANY id and any logical key,
 * including ids containing the separator. Choosing "no separator may appear in an id" instead would
 * be a validation rule the registry cannot enforce over hand-edited or older-shell input; this is a
 * property of the encoding, which needs no cooperation.
 *
 *     ep:1:a:b:c   ← id 'a',   key 'b:c'
 *     ep:3:a:b:c   ← id 'a:b', key 'c'
 */
export function endpointKey(id: string, logicalKey: string): string {
  return `${ENDPOINT_KEY_PREFIX}${id.length}:${id}:${logicalKey}`
}

/** Inverse of `endpointKey`; `undefined` for anything not one of our keys. Proves the encoding is
 *  injective in the direction that matters, and lets `clearEndpointState` sweep by owner. */
export function parseEndpointKey(key: string): { id: string; logicalKey: string } | undefined {
  if (!key.startsWith(ENDPOINT_KEY_PREFIX)) return undefined
  const rest = key.slice(ENDPOINT_KEY_PREFIX.length)
  const colon = rest.indexOf(':')
  if (colon <= 0) return undefined
  const lenText = rest.slice(0, colon)
  if (!/^\d+$/.test(lenText)) return undefined
  const len = Number(lenText)
  const body = rest.slice(colon + 1)
  if (body.length < len + 1 || body[len] !== ':') return undefined
  return { id: body.slice(0, len), logicalKey: body.slice(len + 1) }
}

/** The subset of `Storage` this module needs. Narrow on purpose: a test's fake implements five
 *  members instead of the whole DOM interface, and a real `localStorage`/`sessionStorage` satisfies
 *  it structurally. Nothing here ever reaches for a global — the caller injects the scope. */
export interface KeyValueStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  readonly length: number
  key(index: number): string | null
}

/** All keys currently in `store`, snapshotted before any mutation (`store.key(i)` shifts under
 *  removal, so iterating and deleting in one pass skips entries). */
function snapshotKeys(store: KeyValueStore): string[] {
  const out: string[] = []
  for (let i = 0; i < store.length; i++) {
    const k = store.key(i)
    if (k != null) out.push(k)
  }
  return out
}

/** A read/write handle bound to ONE endpoint id. Hand this to a cache, a socket bookkeeper, or a
 *  prefs reader and it cannot address another endpoint's slot even by accident — the id is not one
 *  of its arguments. */
export interface EndpointScope {
  readonly id: string
  get(logicalKey: string): string | null
  set(logicalKey: string, value: string): void
  remove(logicalKey: string): void
  /** The logical keys this endpoint owns in `store` (not the encoded storage keys). */
  logicalKeys(): string[]
  /** Drop every key this endpoint owns; leaves every other endpoint untouched. */
  clear(): void
}

export function endpointScope(store: KeyValueStore, id: string): EndpointScope {
  return {
    id,
    get: (logicalKey) => store.getItem(endpointKey(id, logicalKey)),
    set: (logicalKey, value) => store.setItem(endpointKey(id, logicalKey), value),
    remove: (logicalKey) => store.removeItem(endpointKey(id, logicalKey)),
    logicalKeys: () =>
      snapshotKeys(store)
        .map(parseEndpointKey)
        .filter((p): p is { id: string; logicalKey: string } => p?.id === id)
        .map((p) => p.logicalKey),
    clear: () => clearEndpointState(store, id),
  }
}

/** Forget one endpoint's state. Used when a device session is revoked, so that revoking one gateway
 *  breaks only that entry (COMPANION-APPS T4.4's acceptance bar). */
export function clearEndpointState(store: KeyValueStore, id: string): void {
  for (const k of snapshotKeys(store)) {
    if (parseEndpointKey(k)?.id === id) store.removeItem(k)
  }
}

// ── storage-backed registry I/O ──────────────────────────────────────────────────────────────

export function loadRegistry(store: KeyValueStore, key: string = REGISTRY_STORAGE_KEY): EndpointRegistry {
  let raw: string | null = null
  try {
    raw = store.getItem(key)
  } catch {
    // A storage scope can throw outright (disabled/quota-exceeded/partitioned). Same answer as
    // corrupt JSON: the shell still has to boot far enough to show its switcher.
    return EMPTY_REGISTRY
  }
  return parseRegistry(raw)
}

export function saveRegistry(
  store: KeyValueStore,
  reg: EndpointRegistry,
  key: string = REGISTRY_STORAGE_KEY,
): void {
  store.setItem(key, serializeRegistry(reg))
}

// ── the native socket URL (CA-7) ─────────────────────────────────────────────────────────────

/** The `/api/ws` path every gateway serves its multiplexed event socket on. */
export const WS_PATH = '/api/ws'

/** Turn an endpoint's `base_url` into the WebSocket URL a NATIVE client opens.
 *
 * 🪤 THIS IS THE ONE PLACE A `base_url` IS LEGITIMATELY PREPENDED TO A PATH, and it does not
 * contradict the "load `base_url` as an origin, never prepend it" rule in the companion guide.
 * That rule is about a **WebView**: it loads the served SPA, and the SPA's own socket is
 * origin-relative (`useChatSocket.ts` builds `${proto}://${location.host}/api/ws`), so a shell
 * that also prepends would produce a second, wrong URL. A **native** client has no document and
 * therefore no `location` to be relative to — it has only the string in the registry row. So it
 * must build the URL, and building it in one shared place is the difference between one
 * implementation and one per platform.
 *
 * 🔑 THE SCHEME MAP IS THE WHOLE POINT. `https:` → `wss:` and `http:` → `ws:`. Getting this wrong
 * is not a cosmetic bug: opening `ws://` against a TLS-terminating tunnel fails the handshake,
 * and opening `wss://` against a plain-http LAN gateway fails it the other way. Because the map
 * is derived from the endpoint's OWN scheme, a `remote` row reached over the owner's tunnel gets
 * `wss://` automatically — nothing has to remember that remote implies TLS.
 *
 * Returns `undefined` — never a guess — when `base_url` is unparseable or carries a scheme that
 * is not http/https. A `file:`, `data:` or bare-host row is a broken registry entry, and a
 * shell that gets `undefined` can say "this endpoint is misconfigured" on that row. Coercing it
 * would produce a socket that dials somewhere unintended, which is strictly worse than a refusal.
 *
 * The URL carries no credential: the device session rides as the session cookie, which is why
 * the companion guide forbids the `?token=` query parameter (it IP-binds and a phone changes IP).
 */
export function endpointSocketUrl(baseUrl: string, path: string = WS_PATH): string | undefined {
  const raw = String(baseUrl ?? '').trim()
  if (!raw) return undefined
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    // No base is supplied on purpose: a bare host like `claw.local:10000` parses as the `claw.local:`
    // SCHEME with an opaque path, so guessing a scheme for it would silently invent a protocol.
    return undefined
  }
  const proto = url.protocol === 'https:' ? 'wss:' : url.protocol === 'http:' ? 'ws:' : ''
  if (!proto) return undefined
  if (!url.host) return undefined
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${proto}//${url.host}${suffix}`
}

/** `endpointSocketUrl` for a registry row. Convenience only — same rules, same refusals. */
export function endpointSocket(
  endpoint: CompanionEndpoint | undefined,
  path: string = WS_PATH,
): string | undefined {
  if (!endpoint) return undefined
  return endpointSocketUrl(endpoint.base_url, path)
}
