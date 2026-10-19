/**
 * `personalclaw://` links.
 *
 * A link is input from any app or web page on the machine, so it is held to one rule: it
 * NAVIGATES and never ACTS. It names one of the dashboard's own routes, optionally a thread or
 * another sub-path, and a few state parameters; it becomes a hash route; and the hash reaches the
 * page as a quoted string, through the same hash assignment the menu-bar item uses. Nothing a link
 * carries is run, sent, approved or answered: a prompt rides only in a new thread's address, as
 * data for the page to show in its composer, and only the person can send it.
 *
 *   personalclaw://<route>[/<segment>...][?<parameter>=<value>&...]
 *
 * - `<route>` is one of the app's routes by its own name (`ROUTES`, kept equal to the dashboard's
 *   list by `test/deepLinks.test.js`), or one of two aliases: `threads` (the chat route) and
 *   `automations` (the triggers route). Lower case only.
 * - Up to four `<segment>`s follow: a thread's key, a settings panel, an item's id. Each is
 *   percent-decoded and must then be made of letters, digits and `. _ : @ ~ -`, so an encoded
 *   `/`, `#`, `?`, quote or backslash cannot become structure on the other side.
 * - The parameters are a closed set (`PARAMETERS`), each with its own shape and length cap, each
 *   given once. Any other key is refused, which is what keeps out every key that would act.
 *
 * Anything else is refused, and a refused link opens the app without navigating. The parser is
 * written out rather than built on `URL`, because `URL` resolves `.` and `..` path segments and
 * would hand over a different link from the one it was given.
 */

const SCHEME = "personalclaw";

/** The dashboard's routes (`web/src/app/App.tsx` `ROUTABLE`). Its shell-only routes stay out. */
const ROUTES = new Set([
  "dashboard",
  "chat",
  "projects",
  "knowledge",
  "tasks",
  "inbox",
  "triggers",
  "files",
  "artifacts",
  "terminal",
  "agents",
  "tools",
  "skills",
  "learning",
  "prompts",
  "workflows",
  "apps",
  "settings",
  "notifications",
  "discover",
  "loop",
  "loops",
  "code",
  "app",
  "mission-control",
]);

/** Names a link may use for a route, beside the routes' own names. */
const ALIASES = Object.freeze({ threads: "chat", automations: "triggers" });

const LIMITS = Object.freeze({
  /** The whole link, checked before anything else is read. */
  link: 40000,
  /** Sub-path segments after the route. */
  segments: 4,
  /** One decoded segment. */
  segment: 128,
  /** An id-shaped value (project). An artifact is a page of its own, `artifacts/<slug>`. */
  id: 128,
  /** A word-shaped value (mode, pane). */
  word: 32,
  /** A file path. */
  file: 1024,
  /** A prompt, in UTF-16 code units. */
  prompt: 4000,
});

const SEGMENT = /^[A-Za-z0-9._:@~-]+$/;
const ID = /^[A-Za-z0-9._:-]+$/;
const WORD = /^[a-z][a-z0-9-]*$/;
/** C0 controls (bar tab, line feed, carriage return), DEL, C1 controls, and the direction overrides and isolates. */
const UNSAFE_TEXT = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/;
const UNSAFE_PATH = /[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/;

/**
 * A review file is a path inside the thread's folder: relative, `/`-separated, with no `..`
 * segment, no home or drive prefix and no backslash, so a link cannot point the review pane
 * anywhere else on the machine.
 */
function isRelativePath(v) {
  if (/^[/~]/.test(v) || /^[A-Za-z]:/.test(v) || v.includes("\\")) return false;
  return v.split("/").every((part) => part !== "..");
}

/**
 * The parameters a link may carry, in the order the hash writes them. Every one is state the
 * page shows; none is a command.
 */
const PARAMETERS = [
  ["project", (v) => v.length <= LIMITS.id && ID.test(v)],
  ["mode", (v) => v.length <= LIMITS.word && WORD.test(v)],
  ["pane", (v) => v.length <= LIMITS.word && WORD.test(v)],
  ["file", (v) => v.length <= LIMITS.file && !UNSAFE_PATH.test(v) && isRelativePath(v)],
  ["prompt", (v) => v.length <= LIMITS.prompt && !UNSAFE_TEXT.test(v)],
];
const PARAMETER_CHECKS = new Map(PARAMETERS);

const refuse = (reason) => ({ ok: false, reason });

function decode(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return null;
  }
}

/**
 * Parse a `personalclaw://` link into the hash route it opens.
 *
 * @param {unknown} raw
 * @returns {{ok: true, route: string, hash: string} | {ok: false, reason: string}}
 */
function parseDeepLink(raw) {
  if (typeof raw !== "string" || raw === "") return refuse("not a link");
  if (raw.length > LIMITS.link) return refuse("the link is too long");
  const prefix = `${SCHEME}://`;
  if (raw.slice(0, prefix.length).toLowerCase() !== prefix) return refuse(`not a ${SCHEME}:// link`);
  if (raw.includes("#")) return refuse("a link carries no fragment");

  const rest = raw.slice(prefix.length);
  const qAt = rest.indexOf("?");
  const pathPart = qAt >= 0 ? rest.slice(0, qAt) : rest;
  const queryPart = qAt >= 0 ? rest.slice(qAt + 1) : "";

  const parts = pathPart.split("/");
  const name = parts[0];
  // The first part is the route's name, nothing more: the lookup below refuses credentials, a
  // port, any encoding and any capital, because no route's name holds one.
  const route = Object.prototype.hasOwnProperty.call(ALIASES, name) ? ALIASES[name] : name;
  if (!ROUTES.has(route)) return refuse("no route the app has");

  let rawSegments = parts.slice(1);
  if (rawSegments.length === 1 && rawSegments[0] === "") rawSegments = [];
  if (rawSegments.length > LIMITS.segments) return refuse("too many path segments");
  const segments = [];
  for (const piece of rawSegments) {
    const seg = decode(piece);
    if (seg === null || seg === "" || seg === "." || seg === "..") return refuse("a path segment is not one");
    if (seg.length > LIMITS.segment) return refuse("a path segment is too long");
    if (!SEGMENT.test(seg)) return refuse("a path segment holds a character a link may not carry");
    segments.push(seg);
  }

  const given = new Map();
  if (queryPart !== "") {
    for (const pair of queryPart.split("&")) {
      const eq = pair.indexOf("=");
      if (eq <= 0) return refuse("a parameter has no value");
      const key = decode(pair.slice(0, eq).replace(/\+/g, " "));
      const value = decode(pair.slice(eq + 1).replace(/\+/g, " "));
      if (key === null || value === null) return refuse("a parameter is not encoded as one");
      if (!PARAMETER_CHECKS.has(key)) return refuse("a parameter the app does not take");
      if (given.has(key)) return refuse("a parameter is given twice");
      if (value === "") return refuse("a parameter has no value");
      if (key === "prompt" && !(route === "chat" && segments.length === 1 && segments[0] === "new")) {
        return refuse("a prompt fills only a new thread's composer");
      }
      if (!PARAMETER_CHECKS.get(key)(value)) return refuse(`the ${key} parameter is not one`);
      given.set(key, value);
    }
  }

  const query = new URLSearchParams();
  for (const [key] of PARAMETERS) if (given.has(key)) query.set(key, given.get(key));
  const path = [route, ...segments.map(encodeURIComponent)].join("/");
  const qs = query.toString();
  return { ok: true, route, hash: `#/${path}${qs ? `?${qs}` : ""}` };
}

/** A hash route the shell may hand the page: a route, encoded segments, an encoded query. */
const HASH = /^#\/[a-z][a-z-]*(\/[A-Za-z0-9%._~:@!'()*-]+)*(\?[A-Za-z0-9%._~*+=&!'()-]+)?$/;

/**
 * The script that moves the page to `hash`. The ONE way the shell writes a hash: the value is
 * checked against the hash grammar and then quoted, so the page receives a string, never code.
 */
function hashAssignmentScript(hash) {
  if (typeof hash !== "string" || !HASH.test(hash)) throw new Error("not a hash route");
  return `window.location.hash = ${JSON.stringify(hash)};`;
}

/** The link in a launch's arguments: the first one on this scheme, or null. */
function linkFromArgv(argv) {
  if (!Array.isArray(argv)) return null;
  const found = argv.find((a) => typeof a === "string" && a.slice(0, SCHEME.length + 1).toLowerCase() === `${SCHEME}:`);
  return found || null;
}

/**
 * Where links wait until the dashboard can take them.
 *
 * A link can arrive before the window exists or while it shows the loading page. The newest
 * accepted link waits, and is delivered once, when the dashboard has loaded. A refused link brings
 * the app forward and changes nothing, so an earlier link still waiting is kept. The log names a
 * refusal's reason or an accepted link's route, never the link, which may hold a prompt.
 */
function makeDeepLinkInbox({ canDeliver, deliver, show = () => {}, log = () => {} }) {
  let pending = null;

  function flush() {
    if (pending === null || !canDeliver()) return false;
    const hash = pending;
    pending = null;
    deliver(hash);
    return true;
  }

  function receive(raw) {
    const result = parseDeepLink(raw);
    show();
    if (!result.ok) {
      log(`deep link refused: ${result.reason}`);
      return false;
    }
    log(`deep link opens the ${result.route} route`);
    pending = result.hash;
    flush();
    return true;
  }

  return { receive, flush };
}

/** The origin of an http(s) address, or "" for anything else: a local file, `about:`, `data:`, `blob:`. */
function webOrigin(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.origin : "";
  } catch {
    return "";
  }
}

/**
 * Whether `pageUrl` is the dashboard of the gateway at `gatewayUrl`: an http(s) page on the
 * gateway's origin, at its root. A gateway sits at the root of its origin (a gateway's address
 * carries no path) and serves the dashboard at `/`; its other pages, such as the sign-in and the
 * pairing pages, are not the dashboard.
 */
function isDashboardPage(pageUrl, gatewayUrl) {
  const expected = webOrigin(gatewayUrl);
  if (expected === "" || webOrigin(pageUrl) !== expected) return false;
  return new URL(pageUrl).pathname === "/";
}

/**
 * The inbox of the app's window. A link goes only to the dashboard of the gateway the window is
 * pointed at, once it has finished loading: never to the loading page or the gateway switcher,
 * which are local files, never to another page of the gateway, and never to another origin. It
 * goes through `hashAssignmentScript`.
 *
 * @param {{contents: () => any, gatewayUrl: () => string | null, show?: () => void, log?: (m: string) => void}} deps
 *   `contents` is the active tab's web contents, or null; `gatewayUrl` the gateway it is pointed at.
 */
function makeWindowDeepLinkInbox({ contents, gatewayUrl, show = () => {}, log = () => {} }) {
  return makeDeepLinkInbox({
    canDeliver: () => {
      const wc = contents();
      if (!wc || wc.isDestroyed() || wc.isLoading()) return false;
      return isDashboardPage(wc.getURL(), gatewayUrl());
    },
    deliver: (hash) => {
      try {
        Promise.resolve(contents().executeJavaScript(hashAssignmentScript(hash))).catch(() => {});
      } catch (err) {
        log(`deep link not delivered: ${err.message}`);
      }
    },
    show,
    log,
  });
}

/**
 * Claim the scheme and listen for links. Called before the app is ready: macOS hands a link that
 * launched the app to `open-url` early in the start. A development run leaves the scheme with the
 * installed app, whose registration it would otherwise take over. A link that is held, because it
 * came while the window showed the loading page, is offered again each time a page finishes
 * loading, so it goes once the dashboard has.
 */
function registerDeepLinks({ app, inbox, isPackaged, argv, log = () => {} }) {
  if (isPackaged) app.setAsDefaultProtocolClient(SCHEME);
  else log(`a development run does not claim ${SCHEME}:// links`);

  app.on("web-contents-created", (_event, wc) => {
    wc.on("did-finish-load", () => inbox.flush());
  });

  app.on("open-url", (event, url) => {
    event.preventDefault();
    inbox.receive(url);
  });
  // Linux (and Windows) hand a link to a second launch, which forwards its arguments here.
  app.on("second-instance", (_event, secondArgv) => {
    const link = linkFromArgv(secondArgv);
    if (link) inbox.receive(link);
  });

  const first = linkFromArgv(argv);
  if (first) inbox.receive(first);
}

module.exports = {
  SCHEME,
  ROUTES,
  ALIASES,
  LIMITS,
  PARAMETERS,
  parseDeepLink,
  hashAssignmentScript,
  linkFromArgv,
  makeDeepLinkInbox,
  makeWindowDeepLinkInbox,
  registerDeepLinks,
};
