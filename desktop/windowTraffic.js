"use strict";

/**
 * What the app's main process does with each request its windows make: cancel it, and set, keep or
 * strip its pair header (`decide`).
 *
 * Two rules keep this app's sign-in with its own gateway:
 *
 * - **Cancel.** While the windows' sign-in is withheld (the gateway was lost and is not proven back),
 *   nothing reaches its origin. Nothing ever reaches an origin this run withheld or left (`retired`),
 *   or a loopback origin that is neither the held one nor a gateway the owner added (`paired`).
 * - **The pair.** A pair header never crosses origins: it comes off a request no page made, a
 *   sandboxed frame's (`"null"`), or one whose page is another origin's, a redirect's next hop
 *   included (`strip`). Once the gateway gives the window session a pair, it rides as
 *   `X-PersonalClaw-Pair` on the held origin's own pages' fetches, XHRs, EventSources and socket
 *   upgrades (all reach webRequest as `xhr`, and the upgrade as `webSocket`), and on nothing else: no
 *   navigation, no service worker (`set`). While the window's cookie holds the held session, it goes
 *   over whatever pair the page sent, as a header or as its socket's subprotocol (the gateway reads
 *   the header first). A page keeps its own pair on its own origin (`keep`): a gateway the owner
 *   added, whose pages sign themselves in, and a held page whose cookie holds another sign-in. Main's
 *   own scripts in a page of a gateway the owner added send that page's (`PAGE_OWN_PAIR_HEADERS`).
 *
 * Origins are compared parsed (scheme, host, port, with ws read as http and wss as https), never as
 * strings. Pure and Electron-free (`test/windowTraffic.test.js`); `main.js` registers it once.
 */

const PAIR_HEADER = "X-PersonalClaw-Pair";
/** The event a page is told its sign-in's pair changed by, on its `document` (`web/src/lib/pair.ts`). */
const PAIR_CHANGED = "personalclaw:pair-changed";
/** The kinds of request the pair rides on. */
const PAIRED_TYPES = Object.freeze(["xhr", "webSocket"]);
/** Where a page keeps its own pair, and the pair's shape (the dashboard's `web/src/lib/pair.ts`). */
const PAGE_PAIR_KEY = "pc_pair";
const PAIR_SHAPE = /^[A-Za-z0-9_-]{43}$/;
/**
 * A JavaScript expression for a script main runs in a page: the headers that carry the page's own
 * pair, read from its storage when the script runs, as the page reads it (`pairHeader`), or none.
 * A page of the held origin keeps none: the window session's is set on its requests (`decide`).
 * It is written out whole, never put together from values (a script built with `JSON.stringify` is
 * code made from a value: CodeQL's js/bad-code-sanitization), and its test holds it to
 * `PAGE_PAIR_KEY`, `PAIR_SHAPE` and `PAIR_HEADER`.
 */
const PAGE_OWN_PAIR_HEADERS =
  '(() => { try { const v = window.localStorage.getItem("pc_pair"); return /^[A-Za-z0-9_-]{43}$/.test(v || "") ? { "X-PersonalClaw-Pair": v } : {}; } catch { return {}; } })()';

const DEFAULT_PORTS = Object.freeze({ "http:": "80", "https:": "443" });
const SCHEME_OF = Object.freeze({ "http:": "http:", "https:": "https:", "ws:": "http:", "wss:": "https:" });

/**
 * The origin *value* names, as one comparable string (`http://127.0.0.1:8080`, `https://[::1]:443`),
 * with ws read as http, wss as https, the default port spelled out, and a trailing dot dropped; or
 * "" for anything that is not an http(s) or ws(s) origin (`file:`, `about:`, `data:`, `"null"`).
 */
function originKey(value) {
  if (typeof value !== "string" || !value || value === "null") return "";
  let u;
  try {
    u = new URL(value);
  } catch {
    return "";
  }
  const scheme = SCHEME_OF[u.protocol];
  if (!scheme || !u.hostname) return "";
  const host = u.hostname.replace(/\.$/, "");
  const port = u.port || DEFAULT_PORTS[scheme];
  return `${scheme}//${host}:${port}`;
}

/** The host of an origin key, brackets kept for IPv6. */
function hostOf(key) {
  return key.slice(key.indexOf("//") + 2, key.lastIndexOf(":"));
}

/**
 * Whether *key*'s host is this computer: 127.0.0.0/8, `::1` (and an IPv4-mapped 127/8 address),
 * `localhost` and any `*.localhost`, and the unspecified addresses `0.0.0.0` and `::`, which reach
 * this computer's listeners too. The URL parser has already lowercased the host and normalised
 * IPv4 and IPv6 literals.
 */
function isLoopbackOrigin(key) {
  if (!key) return false;
  const host = hostOf(key);
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (/^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host) || host === "0.0.0.0") return true;
  if (host === "[::1]" || host === "[::]") return true;
  return /^\[::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}\]$/.test(host);
}

/** *headers* without header *name*, matched without regard to case. */
function withoutHeader(headers, name) {
  const wanted = name.toLowerCase();
  const kept = {};
  for (const key of Object.keys(headers || {})) {
    if (key.toLowerCase() !== wanted) kept[key] = headers[key];
  }
  return kept;
}

/**
 * What to do with *request*, given the windows' sign-in *state*.
 *
 * @param {{url: string, resourceType: string, initiatorOrigin?: string, requestHeaders?: object,
 *   hasWebContents: boolean}} request - what webRequest says about one request.
 * @param {{held: ({origin: string, token: string, pair: (string|null)}|null), withheld: boolean,
 *   retired: Set<string>, paired: Set<string>, cookieFollows: boolean}} state - `retired` and
 *   `paired` hold origin keys (`originKey`).
 * @returns {{cancel: boolean, header: ("set"|"keep"|"strip"), pair: (string|null)}} - what is done
 *   with the request's `X-PersonalClaw-Pair`, and `pair`, the value `set` puts there.
 */
function decide(request, state) {
  const target = originKey(request.url);
  const held = state.held ? originKey(state.held.origin) : "";
  const from = originKey(request.initiatorOrigin);
  const strip = { cancel: false, header: "strip", pair: null };
  if (!target) return strip;

  let cancel = false;
  if (isLoopbackOrigin(target)) {
    if (target === held) cancel = state.withheld;
    else if (state.retired.has(target)) cancel = true;
    else cancel = !state.paired.has(target);
  }
  if (cancel) return { cancel: true, header: "strip", pair: null };
  // A pair never crosses origins: not on a request no page made, a sandboxed frame's, or one whose
  // page is another origin's.
  if (!from || from !== target) return strip;
  const keep = { cancel: false, header: "keep", pair: null };
  if (target !== held) return keep;

  const sets =
    Boolean(state.held && state.held.pair) &&
    !state.withheld &&
    state.cookieFollows !== false &&
    request.hasWebContents === true &&
    PAIRED_TYPES.includes(request.resourceType);
  return sets ? { cancel: false, header: "set", pair: state.held.pair } : keep;
}

/**
 * The headers *request* goes out with once *decision* is applied: the pair set over whatever the page
 * sent, kept, or taken off, and the Referer dropped as the app has always done.
 */
function headersFor(request, decision) {
  const headers = withoutHeader(request.requestHeaders, "Referer");
  if (decision.header === "set") return { ...withoutHeader(headers, PAIR_HEADER), [PAIR_HEADER]: decision.pair };
  if (decision.header === "strip") return withoutHeader(headers, PAIR_HEADER);
  return headers;
}

/** What `decide` reads of one webRequest record. */
function trafficRequest(details) {
  return {
    url: details.url,
    resourceType: details.resourceType,
    initiatorOrigin: details.initiatorOrigin,
    requestHeaders: details.requestHeaders || {},
    hasWebContents: Boolean(details.webContents) || Number.isInteger(details.webContentsId),
  };
}

/**
 * Register `decide` on *appSession* (Electron's, or anything with its `webRequest` and `cookies`
 * shape), once, with no filter, so no kind of request goes around it, reading *record*'s state
 * (`localSignIn.trafficState`) on every request. A cookie change in the jar is handed to the record
 * (`cookieChanged`), so a window signed in with another session carries no pair of this one's.
 */
function registerWindowTraffic(appSession, record) {
  appSession.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: decide(trafficRequest(details), record.trafficState()).cancel });
  });
  appSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const request = trafficRequest(details);
    const decision = decide(request, record.trafficState());
    if (decision.cancel) return callback({ cancel: true });
    callback({ requestHeaders: headersFor(request, decision) });
  });
  appSession.cookies.on("changed", (_event, cookie, _cause, removed) => record.cookieChanged(cookie, removed));
}

module.exports = {
  PAGE_OWN_PAIR_HEADERS,
  PAGE_PAIR_KEY,
  PAIR_SHAPE,
  PAIRED_TYPES,
  PAIR_CHANGED,
  PAIR_HEADER,
  decide,
  headersFor,
  isLoopbackOrigin,
  originKey,
  registerWindowTraffic,
  trafficRequest,
};
