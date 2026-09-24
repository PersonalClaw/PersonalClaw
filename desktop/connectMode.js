/**
 * Connect-to-a-gateway-you-did-not-spawn (COMPANION-APPS S4 / T4.1 + T4.4, `CA-8`).
 *
 * Before this module the shell had exactly one connection model: spawn a private gateway on
 * loopback and load it. That model is **unchanged and still the default** — every decision here is
 * additive, and `describeStartup` falls back to it for any reason it cannot fully justify doing
 * something else.
 *
 * No Electron import: everything is injected. That is not tidiness, it is the only way the trust
 * decisions below can be tested against real sockets and real files rather than asserted in prose.
 *
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 * WHAT THE SHELL HANDS A GATEWAY IT HAS NOT ESTABLISHED IS REAL — AND THE ANSWER IS "NOTHING"
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 *
 * 1. **The reachability probe carries no credential.** `probeEndpoint` GETs `/api/healthz` — the
 *    one route in `token_auth._BYPASS_EXACT` that is auth-exempt *and* returns no secret
 *    (`handlers_system.py:api_healthz` answers `{status, version}`) — with no cookie, no
 *    `Authorization`, no `X-Local-Secret`, and it does not follow redirects. A hostile LAN host
 *    that answers it learns that something asked, and nothing else.
 * 2. **The pairing code is never held by this process.** `pair/complete` replies with an httponly
 *    `Set-Cookie`, so the session has to land in the WebView's own jar; a shell that redeemed the
 *    code natively would hold a session the page could not use. So the shell hands the WebView
 *    `<origin>/pair?code=…` and lets the served page do the exchange — the same ruling
 *    `mobile/www/shell/network.mjs:pairingTargetFromScan` already records.
 * 3. **`.local_secret` and the shell token never leave loopback.** They authenticate the
 *    capability bridge, whose whole claim is "I am a process on THIS machine". `main.js` binds
 *    every call that carries them to the spawned gateway's URL, and `assertLoopbackTarget` below
 *    is the assertion that makes that a rail rather than a convention.
 * 4. **The capability bridge is not attached to a non-loopback origin at all** — see
 *    `shouldAttachBridge`. A tampered registry row therefore cannot get attacker-served JS a
 *    microphone, a notification or a global hotkey; the worst it can reach is an ordinary web page
 *    in a window.
 *
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 * RECONNECT IS NOT A CREDENTIAL RETRY LOOP, BY CONSTRUCTION
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 *
 * `isRetryable`/`nextReconnectStep` split failures into RETRYABLE (the host is not answering) and
 * TERMINAL (the host answered and refused). An auth refusal — the shape a revoked device session produces — is
 * terminal with **zero** retries: the row is marked `needs_pairing` and the automated path stops
 * until the user acts. Retryable failures back off on the SPA's own published curve
 * (`250 * 2 ** attempt`, ceiling 6 — `web/src/lib/useChatSocket.ts:45-46`) and are **bounded**;
 * after `MAX_RECONNECT_ATTEMPTS` the policy says `give_up` and waits for a human. Nothing here
 * ever re-presents a credential, because the probe never presents one in the first place.
 */

const http = require("http");
const https = require("https");
const dns = require("dns");

const {
  TRUST_LOOPBACK,
  TRUST_REFUSED,
  parseGatewayUrl,
  parsePairingUrl,
  transportPolicy,
  resolveHostTrust,
  addressFingerprint,
} = require("./gatewayUrl");

const {
  activeEndpoint,
  addEndpoint,
  endpointScope,
  clearEndpointState,
  findEndpoint,
  loadRegistry,
  newEndpointId,
  removeEndpoint,
  saveRegistry,
  setActive,
} = require("./endpointRegistry");

// ── constants ──────────────────────────────────────────────────────────────────────────────────

/**
 * The reserved row for the gateway this shell spawns.
 *
 * It is a registry row like any other so the switcher can list "this computer" beside the paired
 * gateways and switch back to it — but its `base_url` is rewritten from the READY line on every
 * launch, because the port is OS-assigned. That is exactly why `endpoints.ts` forbids deriving an
 * id from a URL: this row's URL changes every single launch and its namespaced state must not.
 */
const LOCAL_ENDPOINT_ID = "ep_local";
const LOCAL_ENDPOINT_LABEL = "This computer";

/** The logical key (inside the endpoint's own namespace) holding its confirmation record. */
const CONFIRM_KEY = "connect.confirmed";
/**
 * Where the row's label came from: `"gateway"` (adoptable — the shell filled it in) or `"user"`
 * (the person typed it, and nothing overwrites it).
 *
 * The contract's item 8 is "label endpoints from `companion.instance_name`, falling back to the
 * hostname, and let the user override locally". Three states, so two of them cannot share a slot: a
 * label the shell guessed from the hostname must be replaceable by the gateway's own name, and a
 * label the person chose must not be.
 */
const LABEL_SOURCE_KEY = "connect.labelSource";
const LABEL_MAX = 64;
/** Schema version of that record. A record without this exact version is treated as absent. */
const CONFIRM_VERSION = 1;

/** The credential-free identity probe. Auth-exempt and secret-free by construction. */
const HEALTH_PATH = "/api/healthz";

/** Probe timeout. Short: the question is "is anything there", not "is it fast". */
const PROBE_TIMEOUT_MS = 4000;

/** The SPA's own backoff ceiling and base (`web/src/lib/useChatSocket.ts:45-46`). Reused rather
 *  than re-chosen so the shell's row status and the page's indicator move together. */
const BACKOFF_BASE_MS = 250;
const BACKOFF_CEILING = 6;
/** After this many retryable failures the shell stops and asks for a human. */
const MAX_RECONNECT_ATTEMPTS = 8;

// ── probe outcomes (closed set) ────────────────────────────────────────────────────────────────

const HEALTH_UNKNOWN = "unknown"; // never probed — NOT the same as unreachable
const HEALTH_REACHABLE = "reachable";
const HEALTH_UNREACHABLE = "unreachable"; // DNS or TCP failure
const HEALTH_TIMEOUT = "timeout";
const HEALTH_NEEDS_PAIRING = "needs_pairing"; // answered 401/403 — the device session is gone
const HEALTH_NOT_A_GATEWAY = "not_a_gateway"; // answered, but not a PersonalClaw gateway
const HEALTH_HTTP_ERROR = "http_error";
const HEALTH_REDIRECTED = "redirected"; // answered 3xx — never followed
const HEALTH_REFUSED_BY_POLICY = "refused_by_policy"; // the shell will not dial this row at all

const HEALTH_STATES = Object.freeze([
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_TIMEOUT,
  HEALTH_NEEDS_PAIRING,
  HEALTH_NOT_A_GATEWAY,
  HEALTH_HTTP_ERROR,
  HEALTH_REDIRECTED,
  HEALTH_REFUSED_BY_POLICY,
]);

/** Outcomes the shell may retry on its own. Everything else is a human's decision. */
const RETRYABLE = Object.freeze([HEALTH_UNREACHABLE, HEALTH_TIMEOUT, HEALTH_HTTP_ERROR]);

// ── the loopback rail for secret-bearing calls ─────────────────────────────────────────────────

/**
 * Throw unless `url` is loopback.
 *
 * `main.js` calls this before every request that carries `.local_secret` or the shell token. It
 * exists because connect-mode introduces a second URL into a file that used to have exactly one:
 * the day someone points a capability-registration call at `activeUrl` instead of the spawned
 * gateway's URL, the shell POSTs the machine's local secret to a host on the network. A thrown
 * error is a crash in a `try` block; a silent send is a credential leak.
 */
function assertLoopbackTarget(url, what = "request") {
  const parsed = parseGatewayUrl(String(url || ""));
  if (!parsed.ok || parsed.trust !== TRUST_LOOPBACK) {
    throw new Error(
      `refusing to send ${what} to a non-loopback target (${String(url)}): this call carries a ` +
        `machine-local credential and is only ever valid against the gateway this shell spawned`
    );
  }
  return true;
}

/**
 * May the capability bridge (`preload.js`) be attached to a view loading `url`?
 *
 * Loopback only. The bridge reaches the microphone, global hotkeys, native notifications and the
 * login item; the gateway it is registered against had to prove same-machine access with
 * `.local_secret`. A remote gateway cannot make that claim, so the honest answer for its origin is
 * that the bridge is absent — which is exactly what `web/src/lib/desktopBridge.ts:123` already
 * handles (`window.pclawDesktop` missing → `null` → every capability reports unavailable).
 */
function shouldAttachBridge(url) {
  const parsed = parseGatewayUrl(String(url || ""));
  return Boolean(parsed.ok && parsed.trust === TRUST_LOOPBACK);
}

// ── confirmation records ───────────────────────────────────────────────────────────────────────

function readConfirmation(store, id) {
  const raw = endpointScope(store, id).get(CONFIRM_KEY);
  if (raw === null || raw === undefined || raw === "") return { present: false, reason: "absent" };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // A confirmation that will not parse is NOT a confirmation, and is also not the same fact as
    // never having confirmed — the reason distinguishes them so the UI can say which.
    return { present: false, reason: "unparseable" };
  }
  if (!parsed || typeof parsed !== "object" || parsed.v !== CONFIRM_VERSION) {
    return { present: false, reason: "wrong_version" };
  }
  return {
    present: true,
    reason: "",
    origin: typeof parsed.origin === "string" ? parsed.origin : "",
    trust: typeof parsed.trust === "string" ? parsed.trust : "",
    scheme: typeof parsed.scheme === "string" ? parsed.scheme : "",
    addresses: typeof parsed.addresses === "string" ? parsed.addresses : "",
    at: typeof parsed.at === "number" ? parsed.at : 0,
  };
}

function writeConfirmation(store, id, { origin, trust, scheme, addresses, now = Date.now() }) {
  endpointScope(store, id).set(
    CONFIRM_KEY,
    JSON.stringify({
      v: CONFIRM_VERSION,
      origin,
      trust,
      scheme,
      addresses: addresses || "",
      at: Math.floor(now / 1000),
    })
  );
}

/**
 * Is this row's confirmation still the one the user gave?
 *
 * `{ok, reason}`. The origin must match character for character, and — for a row whose host is a
 * NAME — the resolved address set must still fingerprint the same. The second half is the
 * time-of-check/time-of-use half: the user confirmed a machine, and a name that now points
 * somewhere else is a different machine wearing the same label. `currentFingerprint` is passed in
 * because resolving is the caller's async job.
 */
function confirmationHolds(record, { origin, currentFingerprint }) {
  if (!record.present) return { ok: false, reason: `unconfirmed:${record.reason}` };
  if (record.origin !== origin) return { ok: false, reason: "origin_changed" };
  if (record.addresses && currentFingerprint && record.addresses !== currentFingerprint) {
    return { ok: false, reason: "host_moved" };
  }
  return { ok: true, reason: "" };
}

// ── startup: which model does the shell use this launch? ────────────────────────────────────────

/**
 * Decide spawn-local vs connect, and say exactly why.
 *
 * Every path that is not "connect to a fully justified endpoint" returns `spawn-local` with a
 * `reason`, and the reasons are separate strings rather than one "no endpoint" catch-all. That is
 * the absent-vs-declared-false discipline applied to startup: "the user has never paired
 * anything", "the store is corrupt", "the store is world-writable" and "the confirmed host has
 * moved" want four different sentences and, for two of them, a warning the user can act on.
 */
function describeStartup({ store, registry, now = Date.now(), currentFingerprint = "" } = {}) {
  const reg = registry || loadRegistry(store);
  const warnings = [];
  const spawn = (reason) => ({ mode: "spawn-local", endpointId: LOCAL_ENDPOINT_ID, reason, warnings });

  if (store && store.permissions && !store.permissions.safe && store.permissions.exists) {
    // Loose permissions do not stop the shell; they stop it from TRUSTING what it reads.
    warnings.push({
      code: "store_permissions",
      detail: store.permissions.reason,
      message:
        "Your saved gateway list is readable or writable by more than your own account, so the " +
        "app will ask you to confirm a gateway again before connecting to it.",
    });
    return spawn("store_not_owner_only");
  }
  if (store && store.status === "unparseable") {
    warnings.push({
      code: "store_unparseable",
      detail: store.storeReason,
      message:
        "Your saved gateway list could not be read. It has been left exactly as it is — nothing " +
        "was overwritten — and the app started its own local gateway instead.",
    });
    return spawn("store_unparseable");
  }
  if (store && store.status === "unreadable") {
    warnings.push({
      code: "store_unreadable",
      detail: store.storeReason,
      message: "Your saved gateway list could not be opened. The app started its own local gateway instead.",
    });
    return spawn("store_unreadable");
  }

  const active = activeEndpoint(reg);
  if (!active) {
    // THREE different facts, deliberately not one. `absent` is a first launch; `empty` is a store
    // that exists and holds nothing; a dangling `active` was already repaired by the parser.
    if (!reg.endpoints.length) {
      const storeAbsent = !store || store.status === "absent";
      return spawn(storeAbsent ? "no_saved_endpoints" : "registry_empty");
    }
    return spawn("no_active_endpoint");
  }
  if (active.id === LOCAL_ENDPOINT_ID || active.kind === "local") return spawn("active_is_local");
  if (!active.base_url) return spawn("active_endpoint_has_no_url");

  const parsed = parseGatewayUrl(active.base_url);
  if (!parsed.ok) {
    warnings.push({
      code: "active_endpoint_unusable",
      detail: parsed.code,
      message: `The saved gateway "${active.label || active.base_url}" has an address the app will not use.`,
    });
    return spawn("active_endpoint_unparseable");
  }
  const policy = transportPolicy({ scheme: parsed.scheme, trust: parsed.trust });
  if (!policy.allowed) {
    warnings.push({
      code: "active_endpoint_refused",
      detail: policy.code,
      message: policy.warning || `The app will not connect to ${parsed.host}.`,
    });
    return spawn("active_endpoint_refused_by_policy");
  }

  const held = confirmationHolds(readConfirmation(store, active.id), {
    origin: parsed.origin,
    currentFingerprint,
  });
  if (!held.ok) {
    warnings.push({
      code: "active_endpoint_needs_confirmation",
      detail: held.reason,
      message:
        held.reason === "host_moved"
          ? `"${active.label || parsed.host}" now points at a different machine. Confirm it again before connecting.`
          : `Confirm "${active.label || parsed.host}" again before connecting.`,
    });
    return spawn("active_endpoint_unconfirmed");
  }

  return {
    mode: "connect",
    endpointId: active.id,
    endpoint: active,
    origin: parsed.origin,
    trust: parsed.trust,
    reason: "confirmed_active_endpoint",
    warnings,
    at: now,
  };
}

// ── adding an endpoint ─────────────────────────────────────────────────────────────────────────

/**
 * Validate what the user typed or pasted and produce the plan the confirm step will act on.
 *
 * Never mutates anything: the whole point is that the user sees the host the shell resolved, in
 * its own words, BEFORE the shell dials it or hands the WebView anything.
 *
 * `{ok, origin, host, trust, effectiveTrust, addresses, fingerprint, policy, code?, pairTarget?}`
 * or `{ok:false, code, message}`.
 */
async function prepareEndpoint(input, { lookup = dns.promises.lookup } = {}) {
  const text = String(input === null || input === undefined ? "" : input).trim();
  if (!text) return { ok: false, code: "EMPTY", message: "Paste a pairing link or type your gateway's address." };

  // A pairing link is the preferred input because its host came from the gateway rather than from
  // a keyboard. Recognised by shape, so a user pasting one into the address field still gets the
  // better path instead of an error about the `?code=`.
  const looksLikePairing = /^https?:\/\/[^\s]*\/pair(\?|$)/i.test(text);
  const parsed = looksLikePairing ? parsePairingUrl(text) : parseGatewayUrl(text);
  if (!parsed.ok) return parsed;

  const resolution = await resolveHostTrust(parsed.hostname, { lookup });
  if (!resolution.ok) {
    return {
      ok: false,
      code: `RESOLUTION:${resolution.reason}`,
      message: resolutionMessage(resolution.reason, parsed.hostname),
      addresses: resolution.addresses,
    };
  }
  // The class used for every privilege decision is the RESOLVED one, floored at `private` for a
  // name — a spelling never promotes itself to loopback, and a name that resolves into RFC1918 is
  // honestly private rather than "public internet".
  const effectiveTrust = resolution.effective;
  if (effectiveTrust === TRUST_REFUSED) {
    return { ok: false, code: `RESOLUTION:${resolution.reason}`, message: resolutionMessage(resolution.reason, parsed.hostname) };
  }
  const policy = transportPolicy({ scheme: parsed.scheme, trust: effectiveTrust });
  if (!policy.allowed) {
    return { ok: false, code: policy.code, message: policy.warning, trust: effectiveTrust, host: parsed.host };
  }

  return {
    ok: true,
    origin: parsed.origin,
    scheme: parsed.scheme,
    host: parsed.host,
    hostname: parsed.hostname,
    literalTrust: parsed.trust,
    trust: effectiveTrust,
    resolvedTrust: resolution.resolved,
    resolutionReason: resolution.reason,
    addresses: resolution.addresses,
    fingerprint: addressFingerprint(resolution.addresses),
    policy,
    schemeAssumed: Boolean(parsed.schemeAssumed),
    pairingCode: parsed.pairingCode || "",
    pairTarget: parsed.pairTarget || "",
    /** What the shell will navigate to once confirmed: the pairing page if a code came with the
     *  input, otherwise the origin itself (an already-paired gateway). */
    navigateTo: parsed.pairTarget || parsed.origin,
  };
}

function resolutionMessage(reason, host) {
  switch (reason) {
    case "dns_failed":
      return `${host} could not be looked up. Check the name, or use the IP address.`;
    case "dns_empty":
      return `${host} has no addresses.`;
    case "mixed_resolution":
      return `${host} answers with a mix of private and public addresses, so the app cannot tell you which network it is on.`;
    case "resolves_to_refused_range":
      return `${host} points at an address range the app will not connect to.`;
    default:
      return `${host} could not be resolved to something to connect to.`;
  }
}

/**
 * Record the user's confirmation and add (or update) the row.
 *
 * Two writes, in this order: the confirmation record into the endpoint's OWN namespace, then the
 * registry. The confirmation is namespaced rather than added as a registry field on purpose —
 * the registry shape is `endpoints.ts`'s and adding a field to it would be a second contract, and
 * "everything the shell itself persists is keyed by endpoint id" is exactly what the namespacing
 * rule is for.
 */
function confirmEndpoint(store, plan, { label = "", id, kind = "remote", now = Date.now(), mintId = newEndpointId } = {}) {
  const reg = loadRegistry(store);
  const existing = id ? findEndpoint(reg, id) : reg.endpoints.find((e) => e.base_url === plan.origin && e.id !== LOCAL_ENDPOINT_ID);
  const rowId = (existing && existing.id) || id || mintId();
  const chosen = sanitizeLabel(label);
  const next = addEndpoint(reg, {
    id: rowId,
    label: chosen || (existing && existing.label) || plan.host,
    base_url: plan.origin,
    kind,
    // Left empty deliberately: `pair/complete` answers with `device_id` and an httponly cookie,
    // never the nonce this field names, and the shell hands the redemption to the served `/pair`
    // page so the cookie lands in the WebView's jar. Filling it would need something that can
    // observe the redemption, which this process is not. See the report note on §C1.
    device_session_ref: (existing && existing.device_session_ref) || "",
  });
  writeConfirmation(store, rowId, {
    origin: plan.origin,
    trust: plan.trust,
    scheme: plan.scheme,
    addresses: plan.fingerprint,
    now,
  });
  // A label the shell guessed from the hostname stays adoptable; one the person typed does not.
  // Only ever set to `user`, never back to `gateway`: a person who names a gateway should not have
  // that undone by re-pairing it.
  if (chosen) endpointScope(store, rowId).set(LABEL_SOURCE_KEY, "user");
  saveRegistry(store, next);
  return { registry: next, id: rowId };
}

/**
 * Clean a label that came from somewhere else.
 *
 * `companion.instance_name` is adopted from a gateway the shell has confirmed but does not control,
 * so the string is untrusted input: control characters out, whitespace collapsed, clamped to
 * `LABEL_MAX`. The switcher renders with `textContent` so this is not the only guard, but a label
 * ends up in a window title and a menu too, and a 5,000-character "name" is a UI bug wherever it
 * lands.
 */
function sanitizeLabel(text) {
  const raw = String(text === null || text === undefined ? "" : text);
  let out = "";
  for (const ch of raw) {
    const cp = ch.codePointAt(0);
    // 🪤 TAB, LF AND CR ARE WHITESPACE, NOT HOSTILE CONTROL CHARACTERS. Dropping them outright — as
    // this did first — turns "Living room\tMac" into "Living roomMac", welding two words together.
    // They become a space and then collapse; everything else below 0x20 (and DEL) is deleted.
    if (cp === 0x09 || cp === 0x0a || cp === 0x0d) {
      out += " ";
      continue;
    }
    if (cp < 0x20 || cp === 0x7f) continue;
    out += ch;
  }
  return out.replace(/\s+/g, " ").trim().slice(0, LABEL_MAX);
}

/**
 * Adopt a gateway's own `companion.instance_name` as the row's label (contract item 8).
 *
 * 🔑 THE NAME IS READ BY THE PAGE, NOT BY THIS PROCESS, and that is forced rather than chosen.
 * `GET /api/companion/discovery` needs a session, and the session for a paired gateway is an
 * httponly cookie in the WebView's jar — the main process has no way to present it and should not
 * acquire one. So `main.js` asks the loaded page to fetch its own gateway's name and hands the
 * answer here.
 *
 * A row whose label the user typed is left alone; only a shell-guessed hostname is replaced.
 */
function adoptGatewayLabel(store, id, instanceName) {
  const label = sanitizeLabel(instanceName);
  if (!label) return { changed: false, reason: "empty_name" };
  const scope = endpointScope(store, id);
  if (scope.get(LABEL_SOURCE_KEY) === "user") return { changed: false, reason: "user_named_it" };
  const reg = loadRegistry(store);
  const row = findEndpoint(reg, id);
  if (!row) return { changed: false, reason: "unknown_endpoint" };
  if (row.label === label) return { changed: false, reason: "already_current" };
  saveRegistry(store, addEndpointPreservingActive(reg, { ...row, label }));
  return { changed: true, label };
}

/** `addEndpoint` makes its row active by design; a bookkeeping update must not. */
function addEndpointPreservingActive(reg, row) {
  const previousActive = reg.active;
  const added = addEndpoint(reg, row);
  return previousActive ? setActive(added, previousActive) : added;
}

/** Register/refresh the reserved spawn-local row WITHOUT stealing the active pointer. */
function rememberLocalGateway(store, baseUrl, { label = LOCAL_ENDPOINT_LABEL } = {}) {
  const reg = loadRegistry(store);
  const next = addEndpointPreservingActive(reg, {
    id: LOCAL_ENDPOINT_ID,
    label,
    base_url: String(baseUrl || ""),
    kind: "local",
    device_session_ref: "",
  });
  saveRegistry(store, next);
  return next;
}

/**
 * Forget an endpoint: drop the row AND sweep its namespaced state.
 *
 * The reducer deliberately does not sweep (a re-pair should find its state again), so "forget it
 * entirely" is two calls, and this is the place that means it. The sweep is what makes "revoking
 * one gateway breaks only that entry" true of the shell's own storage too: `clearEndpointState`
 * addresses one owner and cannot reach another's slot.
 */
function forgetEndpoint(store, id) {
  if (id === LOCAL_ENDPOINT_ID) return { ok: false, reason: "local_endpoint_is_not_removable" };
  const reg = loadRegistry(store);
  if (!findEndpoint(reg, id)) return { ok: false, reason: "unknown_endpoint" };
  const next = removeEndpoint(reg, id);
  clearEndpointState(store, id);
  saveRegistry(store, next);
  return { ok: true, registry: next };
}

// ── switching ──────────────────────────────────────────────────────────────────────────────────

/**
 * Re-point `active` and say where to navigate. Two steps, in the guide's order.
 *
 * Returns `{ok, registry, endpoint, navigateTo, attachBridge, needsConfirmation}`. It carries NO
 * state from the outgoing endpoint — the switcher's contract is a navigation, and anything handed
 * across would be the bleed the namespacing exists to prevent.
 */
function switchTo(store, id, { currentFingerprint = "", localBaseUrl = "" } = {}) {
  const reg = loadRegistry(store);
  const row = findEndpoint(reg, id);
  if (!row) return { ok: false, reason: "unknown_endpoint" };

  if (row.id === LOCAL_ENDPOINT_ID || row.kind === "local") {
    const url = localBaseUrl || row.base_url;
    if (!url) return { ok: false, reason: "local_gateway_not_running" };
    const next = setActive(reg, row.id);
    saveRegistry(store, next);
    return { ok: true, registry: next, endpoint: row, navigateTo: url, attachBridge: shouldAttachBridge(url), needsConfirmation: false };
  }

  const parsed = parseGatewayUrl(row.base_url);
  if (!parsed.ok) return { ok: false, reason: `unusable_url:${parsed.code}` };
  const policy = transportPolicy({ scheme: parsed.scheme, trust: parsed.trust });
  if (!policy.allowed) return { ok: false, reason: `refused_by_policy:${policy.code}` };

  const held = confirmationHolds(readConfirmation(store, row.id), { origin: parsed.origin, currentFingerprint });
  if (!held.ok) {
    // Switching does NOT silently re-confirm. The user confirmed a machine; if that is no longer
    // what the row points at, the switcher says so rather than connecting and hoping.
    return { ok: false, reason: `needs_confirmation:${held.reason}`, needsConfirmation: true, endpoint: row };
  }

  const next = setActive(reg, row.id);
  saveRegistry(store, next);
  return {
    ok: true,
    registry: next,
    endpoint: row,
    navigateTo: parsed.origin,
    attachBridge: shouldAttachBridge(parsed.origin),
    needsConfirmation: false,
  };
}

// ── health probing ─────────────────────────────────────────────────────────────────────────────

/**
 * One credential-free GET of `/api/healthz`, with redirects refused rather than followed.
 *
 * `{status, httpStatus, version, detail}` where `status` is one of `HEALTH_STATES`. `requestMod`
 * is injectable, but the default is Node's own `http`/`https` and the tests drive it against real
 * listeners — including listeners they then destroy — because a reconnect test against a fake
 * that returns an error object proves nothing about a socket that actually died.
 */
function probeEndpoint(baseUrl, { timeoutMs = PROBE_TIMEOUT_MS, httpMod = http, httpsMod = https } = {}) {
  return new Promise((resolve) => {
    const parsed = parseGatewayUrl(String(baseUrl || ""));
    if (!parsed.ok) {
      return resolve({ status: HEALTH_REFUSED_BY_POLICY, httpStatus: 0, version: "", detail: parsed.code });
    }
    const policy = transportPolicy({ scheme: parsed.scheme, trust: parsed.trust });
    if (!policy.allowed) {
      return resolve({ status: HEALTH_REFUSED_BY_POLICY, httpStatus: 0, version: "", detail: policy.code });
    }

    let url;
    try {
      url = new URL(HEALTH_PATH, parsed.origin);
    } catch {
      return resolve({ status: HEALTH_REFUSED_BY_POLICY, httpStatus: 0, version: "", detail: "BAD_ORIGIN" });
    }
    const mod = url.protocol === "https:" ? httpsMod : httpMod;
    let settled = false;
    const done = (payload) => {
      if (settled) return;
      settled = true;
      resolve(payload);
    };

    const req = mod.get(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname,
        timeout: timeoutMs,
        // 🪤 `agent: false` — MEASURED, NOT STYLE. Node's global agent has `keepAlive: true` since
        // v19, so a second probe reuses the pooled socket from the first. Against a gateway that has
        // since gone away that surfaces as `ECONNRESET` on a stale connection rather than
        // `ECONNREFUSED` against the port — the same `unreachable` verdict, but arrived at by
        // asking a socket instead of asking the host. A liveness probe that can be answered by a
        // connection opened before the outage is not a liveness probe. Caught by the drop test,
        // which asserts the error code and not just the verdict.
        agent: false,
        // No cookie jar, no Authorization, no X-Local-Secret, and an honest UA. The probe is
        // deliberately the least the shell can say.
        headers: { Accept: "application/json", "User-Agent": "PersonalClaw-Desktop-Probe" },
      },
      (res) => {
        const code = res.statusCode || 0;
        // NEVER follow a redirect. A host that passes validation and then 302s the client
        // somewhere else is the documented way to walk a validator; revalidating would be the
        // only safe alternative and there is no reason a health probe should need one.
        if (code >= 300 && code < 400) {
          res.resume();
          return done({ status: HEALTH_REDIRECTED, httpStatus: code, version: "", detail: "not_followed" });
        }
        if (code === 401 || code === 403) {
          res.resume();
          return done({ status: HEALTH_NEEDS_PAIRING, httpStatus: code, version: "", detail: "" });
        }
        let buf = "";
        res.setEncoding("utf8");
        // Bounded read: a hostile endpoint must not be able to stream this process out of memory.
        res.on("data", (c) => {
          if (buf.length < 4096) buf += c;
        });
        res.on("end", () => {
          if (code < 200 || code >= 300) {
            return done({ status: HEALTH_HTTP_ERROR, httpStatus: code, version: "", detail: buf.slice(0, 120) });
          }
          let body;
          try {
            body = JSON.parse(buf);
          } catch {
            return done({ status: HEALTH_NOT_A_GATEWAY, httpStatus: code, version: "", detail: "body_not_json" });
          }
          // `handlers_system.py:api_healthz` answers exactly `{status: "ok", version: <str>}`.
          // Anything else answered on this path is not a gateway, and saying so is better than
          // treating a 200 from a random web server as a healthy PersonalClaw.
          if (!body || typeof body !== "object" || body.status !== "ok" || typeof body.version !== "string") {
            return done({ status: HEALTH_NOT_A_GATEWAY, httpStatus: code, version: "", detail: "unexpected_shape" });
          }
          return done({ status: HEALTH_REACHABLE, httpStatus: code, version: body.version, detail: "" });
        });
        res.on("error", (err) => done({ status: HEALTH_UNREACHABLE, httpStatus: code, version: "", detail: err && err.code ? String(err.code) : "stream_error" }));
      }
    );
    req.on("timeout", () => {
      req.destroy();
      done({ status: HEALTH_TIMEOUT, httpStatus: 0, version: "", detail: `${timeoutMs}ms` });
    });
    req.on("error", (err) => {
      const code = err && err.code ? String(err.code) : "";
      // ENOTFOUND/EAI_AGAIN are DNS; ECONNREFUSED/ECONNRESET/EHOSTUNREACH are TCP. All of them
      // mean "the machine is not answering", which is the one class the shell may retry.
      done({ status: HEALTH_UNREACHABLE, httpStatus: 0, version: "", detail: code });
    });
  });
}

/** Is this outcome one the shell may retry by itself? */
function isRetryable(status) {
  return RETRYABLE.includes(status);
}

/**
 * What to do after a probe. `{action, delayMs, attempt, reason}` where `action` is
 * `stay` | `retry` | `give_up` | `needs_pairing` | `stop`.
 *
 * 🔑 `needs_pairing` GETS ZERO RETRIES. An endpoint that answered 401/403 has refused a
 * credential; probing it again on a timer is the credential-retry loop this function exists to
 * make impossible, and it would also drive the gateway's own per-IP lockout against its owner.
 * `not_a_gateway`, `redirected` and `refused_by_policy` stop for the same reason: the host
 * answered, and the answer was not "try later".
 */
function nextReconnectStep({ status, attempt = 0, maxAttempts = MAX_RECONNECT_ATTEMPTS } = {}) {
  if (status === HEALTH_REACHABLE) return { action: "stay", delayMs: 0, attempt: 0, reason: "reachable" };
  if (status === HEALTH_NEEDS_PAIRING) {
    return { action: "needs_pairing", delayMs: 0, attempt, reason: "auth_refused_terminal" };
  }
  if (!isRetryable(status)) return { action: "stop", delayMs: 0, attempt, reason: `terminal:${status}` };
  const next = attempt + 1;
  if (next > maxAttempts) {
    return { action: "give_up", delayMs: 0, attempt, reason: "attempts_exhausted" };
  }
  const capped = Math.min(next, BACKOFF_CEILING);
  return { action: "retry", delayMs: BACKOFF_BASE_MS * 2 ** capped, attempt: next, reason: `retryable:${status}` };
}

/**
 * Probe every row and return a per-row health map.
 *
 * One row's outcome never touches another's — the map is built independently per row and the
 * registry is never mutated here. That is T4.4's "revoking one gateway's device session breaks
 * only that entry" expressed as code: a `needs_pairing` on one row leaves every other row's
 * status, confirmation record and namespaced state exactly as it was.
 */
async function probeAll(registry, { probe = probeEndpoint, localBaseUrl = "" } = {}) {
  const rows = registry.endpoints || [];
  const results = await Promise.all(
    rows.map(async (row) => {
      const url = row.id === LOCAL_ENDPOINT_ID || row.kind === "local" ? localBaseUrl || row.base_url : row.base_url;
      if (!url) {
        // "This row has no URL" is not "this row is unreachable". Kept apart on purpose.
        return [row.id, { status: HEALTH_UNKNOWN, httpStatus: 0, version: "", detail: "no_url" }];
      }
      return [row.id, await probe(url)];
    })
  );
  return Object.fromEntries(results);
}

/**
 * Resolve an endpoint's host NOW and fingerprint the answer, for comparison with what the user
 * confirmed.
 *
 * 🔑 WITHOUT A CALLER, THE TIME-OF-CHECK/TIME-OF-USE GUARD IS DEAD CODE. `confirmationHolds` only
 * compares fingerprints when it is *given* a current one, so a caller that omits it silently skips
 * the host-moved check — which is exactly what `describeStartup` and `switchTo` did until this
 * function existed and `main.js` started awaiting it. Every site that decides whether to connect
 * resolves through here first.
 *
 * An IP literal fingerprints to ITSELF, which is the right answer rather than a special case: it
 * matches the fingerprint `confirmEndpoint` recorded, and a change to the literal is already caught
 * by the origin comparison. Nothing to detect, so nothing to detect it.
 *
 * A FAILED lookup answers `""`, deliberately. An empty fingerprint makes `confirmationHolds` skip the
 * comparison, so a resolver outage leaves the endpoint usable; treating it as a move would demand a
 * re-confirmation the user has no way to give while DNS is down. A real move is a DIFFERENT
 * non-empty answer, which is the only thing that reds the check.
 */
async function currentFingerprintFor(baseUrl, { lookup = dns.promises.lookup } = {}) {
  const parsed = parseGatewayUrl(String(baseUrl || ""));
  if (!parsed.ok) return "";
  const resolution = await resolveHostTrust(parsed.hostname, { lookup });
  if (!resolution.ok) return "";
  return addressFingerprint(resolution.addresses);
}

module.exports = {
  LOCAL_ENDPOINT_ID,
  LOCAL_ENDPOINT_LABEL,
  CONFIRM_KEY,
  CONFIRM_VERSION,
  LABEL_SOURCE_KEY,
  LABEL_MAX,
  HEALTH_PATH,
  PROBE_TIMEOUT_MS,
  BACKOFF_BASE_MS,
  BACKOFF_CEILING,
  MAX_RECONNECT_ATTEMPTS,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_TIMEOUT,
  HEALTH_NEEDS_PAIRING,
  HEALTH_NOT_A_GATEWAY,
  HEALTH_HTTP_ERROR,
  HEALTH_REDIRECTED,
  HEALTH_REFUSED_BY_POLICY,
  HEALTH_STATES,
  RETRYABLE,
  assertLoopbackTarget,
  shouldAttachBridge,
  readConfirmation,
  writeConfirmation,
  confirmationHolds,
  describeStartup,
  prepareEndpoint,
  currentFingerprintFor,
  confirmEndpoint,
  sanitizeLabel,
  adoptGatewayLabel,
  rememberLocalGateway,
  forgetEndpoint,
  switchTo,
  probeEndpoint,
  isRetryable,
  nextReconnectStep,
  probeAll,
};
