/**
 * The desktop shell's copy of the endpoint-registry contract (COMPANION-APPS T3.1/T3.3).
 *
 * 🔑 THE FORMAT IS NOT OWNED HERE. `web/src/lib/endpoints.ts` owns it, and names this shell as a
 * consumer by name: *"This module is what desktop (T4.1) and mobile import so that neither
 * re-decides the key format — two shells that disagree about the format are two shells that
 * cannot share a registry."*
 *
 * 🪤 WHY THIS IS A PORT AND NOT AN IMPORT. `endpoints.ts` is TypeScript inside the `web` Vite
 * bundle. `desktop/` is plain CommonJS loaded by Electron's main process with **no build step**:
 * `desktop/package.json`'s `build.files` copies the listed `.js` files verbatim, so there is
 * nothing that could transpile a `.ts` on the way in. `mobile/www/shell/registry.mjs` hit the
 * identical wall and answered with a text-scan parity rail.
 *
 * This file answers it more strongly: `test/endpointRegistry.test.js` **executes both
 * implementations** — Node ≥22 strips types from a `.ts` on import, so the test imports
 * `web/src/lib/endpoints.ts` for real and requires byte-identical output from every exported
 * function over a shared vector table. A text scan can only compare spellings; a differential
 * catches a behavioural divergence with matching spellings, which is the drift that actually
 * bleeds one gateway's state into another.
 *
 * 🚫 NO CREDENTIAL IS STORED HERE. A row is a URL, a label, a kind and a `device_session_ref`.
 * The session itself is an httponly cookie in the WebView's own jar — see `connectMode.js` for
 * why this shell never redeems a pairing code natively.
 */

// ── the contract's own vocabularies, for the parity rail to compare against ────────────────────

/** `endpoints.ts`'s `EndpointRegistry` field vocabulary. */
const REGISTRY_FIELDS = Object.freeze(["active", "endpoints"]);

/** `endpoints.ts`'s `CompanionEndpoint` field vocabulary, in its declared order. */
const ENDPOINT_FIELDS = Object.freeze(["id", "label", "base_url", "kind", "device_session_ref"]);

/** The one shell-global storage key. Must equal `endpoints.ts`'s `REGISTRY_STORAGE_KEY`. */
const REGISTRY_STORAGE_KEY = "companion:endpoints";

/** The zero value. Every failure mode of `parseRegistry` lands here or in something narrower. */
const EMPTY_REGISTRY = Object.freeze({ active: "", endpoints: [] });

/** Rhymes with `data/store.ts`'s `_SS_PREFIX = 'cache:'` — a short, colon-terminated namespace. */
const ENDPOINT_KEY_PREFIX = "ep:";

/** The `/api/ws` path every gateway serves its multiplexed event socket on. */
const WS_PATH = "/api/ws";

// ── ids ────────────────────────────────────────────────────────────────────────────────────────

const ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

function defaultRand() {
  const c = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (c && typeof c.getRandomValues === "function") {
    const buf = new Uint32Array(1);
    c.getRandomValues(buf);
    return buf[0] / 0x1_0000_0000;
  }
  return Math.random();
}

/**
 * Mint an endpoint id — `ep_` + 12 chars of `[a-z0-9]`.
 *
 * 🪤 NOT DERIVED FROM `base_url`. Two rows can share a host, and a URL changes (network move, port
 * reassign, Tailscale rename) without the gateway becoming a different brain. An id that tracked
 * the URL would orphan that endpoint's namespaced state on every such change and present a
 * re-paired gateway as a stranger.
 */
function newEndpointId(rand = defaultRand) {
  let out = "ep_";
  for (let i = 0; i < 12; i += 1) {
    out += ID_ALPHABET[Math.floor(rand() * ID_ALPHABET.length) % ID_ALPHABET.length];
  }
  return out;
}

// ── parse / serialize ──────────────────────────────────────────────────────────────────────────

function isRecord(v) {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v) {
  return typeof v === "string" ? v : undefined;
}

/** Coerce one raw row, or `undefined` if it cannot be one. An id-less row is DROPPED rather than
 *  assigned a fresh one (minting on read makes a parse nondeterministic). An unrecognized `kind`
 *  coerces to `remote`, the LESS privileged value. */
function coerceEndpoint(v) {
  if (!isRecord(v)) return undefined;
  const id = str(v.id);
  if (!id) return undefined;
  return {
    id,
    label: str(v.label) ?? "",
    base_url: str(v.base_url) ?? "",
    kind: v.kind === "local" ? "local" : "remote",
    device_session_ref: str(v.device_session_ref) ?? "",
  };
}

/** Drop unusable rows, drop duplicate ids (FIRST wins), guarantee `active` names a present id.
 *  Total by construction: a shell that throws on a corrupt registry cannot reach the switcher
 *  that would let the user fix it. */
function normalizeRegistry(value) {
  if (!isRecord(value)) return { active: "", endpoints: [] };
  const rawList = Array.isArray(value.endpoints) ? value.endpoints : [];
  const endpoints = [];
  const seen = new Set();
  for (const raw of rawList) {
    const ep = coerceEndpoint(raw);
    if (!ep || seen.has(ep.id)) continue;
    seen.add(ep.id);
    endpoints.push(ep);
  }
  const wanted = str(value.active) ?? "";
  const active = seen.has(wanted) ? wanted : endpoints[0] ? endpoints[0].id : "";
  return { active, endpoints };
}

function parseRegistry(raw) {
  if (raw === null || raw === undefined || raw === "") return { active: "", endpoints: [] };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { active: "", endpoints: [] };
  }
  return normalizeRegistry(parsed);
}

function serializeRegistry(reg) {
  return JSON.stringify(reg);
}

// ── reducers (pure; never touch storage) ───────────────────────────────────────────────────────

function findEndpoint(reg, id) {
  return reg.endpoints.find((e) => e.id === id);
}

function activeEndpoint(reg) {
  return findEndpoint(reg, reg.active);
}

/** Add a row and make it active — pairing a gateway is a switch to it. Re-adding an existing id
 *  REPLACES that row in place and keeps its position, so re-pairing after a URL change preserves
 *  the id and therefore the namespaced state. */
function addEndpoint(reg, entry) {
  const id = entry.id === undefined ? newEndpointId() : entry.id;
  const row = {
    id,
    label: entry.label,
    base_url: entry.base_url,
    kind: entry.kind,
    device_session_ref: entry.device_session_ref,
  };
  const at = reg.endpoints.findIndex((e) => e.id === id);
  const endpoints = at >= 0 ? reg.endpoints.map((e, i) => (i === at ? row : e)) : [...reg.endpoints, row];
  return { active: id, endpoints };
}

/** Remove a row; the active pointer falls to the first survivor. Deliberately does NOT purge that
 *  endpoint's namespaced state — forgetting an endpoint and wiping its caches are separate
 *  decisions. Callers meaning "forget it entirely" call `clearEndpointState` too. */
function removeEndpoint(reg, id) {
  const endpoints = reg.endpoints.filter((e) => e.id !== id);
  if (endpoints.length === reg.endpoints.length) return reg;
  const active = reg.active === id ? (endpoints[0] ? endpoints[0].id : "") : reg.active;
  return { active, endpoints };
}

/** Re-point `active`. A no-op for an unknown id: the switcher must not be able to strand the
 *  shell pointing at nothing. */
function setActive(reg, id) {
  if (!reg.endpoints.some((e) => e.id === id)) return reg;
  return { active: id, endpoints: reg.endpoints };
}

// ── per-endpoint storage namespacing ───────────────────────────────────────────────────────────

/**
 * Storage key for (endpoint id, logical key).
 *
 * 🔑 THE LENGTH FIELD IS THE WHOLE POINT. `id + ':' + key` is NOT injective: `{id:'a',
 * key:'b:c'}` and `{id:'a:b', key:'c'}` both render `a:b:c`, so two brains would share one slot —
 * the exact bleed this mechanism exists to prevent, hidden inside the prevention. Encoding
 * `id.length` first makes the split point data rather than a guess.
 */
function endpointKey(id, logicalKey) {
  return `${ENDPOINT_KEY_PREFIX}${id.length}:${id}:${logicalKey}`;
}

/** Inverse of `endpointKey`; `undefined` for anything not one of our keys. */
function parseEndpointKey(key) {
  if (!key.startsWith(ENDPOINT_KEY_PREFIX)) return undefined;
  const rest = key.slice(ENDPOINT_KEY_PREFIX.length);
  const colon = rest.indexOf(":");
  if (colon <= 0) return undefined;
  const lenText = rest.slice(0, colon);
  if (!/^\d+$/.test(lenText)) return undefined;
  const len = Number(lenText);
  const body = rest.slice(colon + 1);
  if (body.length < len + 1 || body[len] !== ":") return undefined;
  return { id: body.slice(0, len), logicalKey: body.slice(len + 1) };
}

/** All keys currently in `store`, snapshotted before any mutation (`store.key(i)` shifts under
 *  removal, so iterating and deleting in one pass skips entries). */
function snapshotKeys(store) {
  const out = [];
  for (let i = 0; i < store.length; i += 1) {
    const k = store.key(i);
    if (k !== null && k !== undefined) out.push(k);
  }
  return out;
}

/** A read/write handle bound to ONE endpoint id: the id is not one of its arguments, so it cannot
 *  address another endpoint's slot even by accident. */
function endpointScope(store, id) {
  return {
    id,
    get: (logicalKey) => store.getItem(endpointKey(id, logicalKey)),
    set: (logicalKey, value) => store.setItem(endpointKey(id, logicalKey), value),
    remove: (logicalKey) => store.removeItem(endpointKey(id, logicalKey)),
    logicalKeys: () =>
      snapshotKeys(store)
        .map(parseEndpointKey)
        .filter((p) => p && p.id === id)
        .map((p) => p.logicalKey),
    clear: () => clearEndpointState(store, id),
  };
}

/** Forget one endpoint's state. Used when a device session is revoked, so that revoking one
 *  gateway breaks only that entry (T4.4's acceptance bar). */
function clearEndpointState(store, id) {
  for (const k of snapshotKeys(store)) {
    const parsed = parseEndpointKey(k);
    if (parsed && parsed.id === id) store.removeItem(k);
  }
}

// ── storage-backed registry I/O ────────────────────────────────────────────────────────────────

function loadRegistry(store, key = REGISTRY_STORAGE_KEY) {
  let raw = null;
  try {
    raw = store.getItem(key);
  } catch {
    // A storage scope can throw outright. Same answer as corrupt JSON: the shell still has to
    // boot far enough to show its switcher.
    return { active: "", endpoints: [] };
  }
  return parseRegistry(raw);
}

function saveRegistry(store, reg, key = REGISTRY_STORAGE_KEY) {
  store.setItem(key, serializeRegistry(reg));
}

// ── the native socket URL (CA-7) ───────────────────────────────────────────────────────────────

/** `https:` → `wss:`, `http:` → `ws:`. Returns `undefined` — never a guess — for an unparseable
 *  `base_url`, a bare host, or a non-http scheme. */
function endpointSocketUrl(baseUrl, path = WS_PATH) {
  const raw = String(baseUrl === null || baseUrl === undefined ? "" : baseUrl).trim();
  if (!raw) return undefined;
  let url;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  const proto = url.protocol === "https:" ? "wss:" : url.protocol === "http:" ? "ws:" : "";
  if (!proto) return undefined;
  if (!url.host) return undefined;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${proto}//${url.host}${suffix}`;
}

function endpointSocket(endpoint, path = WS_PATH) {
  if (!endpoint) return undefined;
  return endpointSocketUrl(endpoint.base_url, path);
}

module.exports = {
  REGISTRY_FIELDS,
  ENDPOINT_FIELDS,
  REGISTRY_STORAGE_KEY,
  EMPTY_REGISTRY,
  ENDPOINT_KEY_PREFIX,
  WS_PATH,
  newEndpointId,
  normalizeRegistry,
  parseRegistry,
  serializeRegistry,
  findEndpoint,
  activeEndpoint,
  addEndpoint,
  removeEndpoint,
  setActive,
  endpointKey,
  parseEndpointKey,
  endpointScope,
  clearEndpointState,
  loadRegistry,
  saveRegistry,
  endpointSocketUrl,
  endpointSocket,
};
