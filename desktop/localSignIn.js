/**
 * How the shell signs its own windows in to the gateway it spawned.
 *
 * The gateway asks every request for a sign-in, as every install does. When it starts it prints
 * one ready line, `PERSONALCLAW_READY:{"port":…,"token":…}`, on the stdout pipe only this process
 * reads, and the token in it is an owner session the gateway minted for this start. That token is
 * the shell's credential, and it is held in ONE place: this module's memory, in the main process.
 *
 * - The windows carry it as the gateway's session cookie, `pc_token_<port>` for the port the ready
 *   line names, set on the app's own session for the gateway's origin only. HttpOnly, so no page
 *   script can read it; SameSite=Lax; Path=/; no Domain, so no other host receives it; and NO
 *   EXPIRY, which makes it a session cookie: the browser keeps it in memory and never writes it to
 *   disk, and the sign-in ends with the app.
 * - The shell's own requests (the menu bar's counts, the capability registration) carry it as
 *   `Authorization: Bearer`, which sets no cookie and keeps it out of every URL.
 * - It never appears in a URL, a log line, a file or a message to a renderer. Opening the
 *   gateway's `/?token=` link instead would have left it in the window's history and a 30-day
 *   cookie in the app's cookie file.
 *
 * A restart (Settings → Restart, an applied update) prints a NEW ready line from the same process,
 * with a new port and a new token: `adopt` swaps the cookie over and hands back the session it
 * replaced, so the shell can sign that one out, and `movedUrl` says where each window that showed
 * the old address goes. Quitting signs the session out (`signOutRequest`) and drops it (`release`).
 *
 * Electron's cookie store is injected, so this is testable without Electron
 * (`test/localSignIn.test.js`).
 */

const { WINDOW_DOOR_FIELD } = require("./homeGateway");
const { PAIR_HEADER, originKey } = require("./windowTraffic");

const READY_PREFIX = "PERSONALCLAW_READY:";

/** The route that ends the session its own cookie carries (`handlers/auth.api_auth_logout`). */
const SIGN_OUT_PATH = "/api/auth/logout";

/**
 * The ready line's `{port, token, url, windowDoor}`, or null for any other line. A ready line without a token
 * still names the port, with `token: ""`: the window then meets the gateway's sign-in page, which
 * says what to do, rather than a window that never loads.
 *
 * The url is the address the gateway listens on, `127.0.0.1`.
 */
function parseReadyLine(line) {
  if (typeof line !== "string" || !line.startsWith(READY_PREFIX)) return null;
  let payload;
  try {
    payload = JSON.parse(line.slice(READY_PREFIX.length));
  } catch {
    return null;
  }
  const port = payload && payload.port;
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  const token = typeof payload.token === "string" ? payload.token : "";
  // Whether this build has the window's own door (`homeGateway.WINDOW_DOOR_FIELD`): a ready line
  // without it is today's gateway, and its windows are signed in as they always were.
  const windowDoor = payload[WINDOW_DOOR_FIELD] === true;
  return { port, token, url: `http://127.0.0.1:${port}`, windowDoor };
}

/** The gateway's session cookie for a gateway serving on *port*. */
function sessionCookieName(port) {
  return `pc_token_${port}`;
}

/** What `cookies.set` is given to sign the windows in to *session*'s gateway. */
function windowCookie(session) {
  return {
    url: session.url,
    name: sessionCookieName(session.port),
    value: session.token,
    path: "/",
    httpOnly: true,
    secure: false,
    sameSite: "lax",
  };
}

/**
 * The request that ends *session* at the gateway serving on *port*, presented to the route that
 * revokes the session that authorized it. *port* is the gateway being asked, which after a restart
 * is not the port the session was handed out on, and *windowDoor* says whether that gateway has the
 * window's own door: one that hasn't takes the session's own cookie, as it always did; one that has
 * takes it as a Bearer, with the session's own pair when it has one, never another session's.
 */
function signOutRequest(session, port, { windowDoor = false } = {}) {
  if (!windowDoor) return { path: SIGN_OUT_PATH, headers: { Cookie: `${sessionCookieName(port)}=${session.token}` } };
  const headers = { Authorization: `Bearer ${session.token}` };
  if (session.pair) headers[PAIR_HEADER] = session.pair;
  return { path: SIGN_OUT_PATH, headers };
}

/**
 * Where a window showing *url* goes once the gateway at *previousOrigin* restarted at *nextBase*,
 * or null when that window shows something else. The route (path and hash) is kept and the query
 * is dropped: the dashboard routes in the hash, and a query is the one place a sign-in link would
 * carry a token.
 */
function movedUrl(url, previousOrigin, nextBase) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (!previousOrigin || u.origin !== previousOrigin) return null;
  return `${nextBase}${u.pathname}${u.hash}`;
}

/**
 * Whether a window showing *url* moves to the restarted gateway's address: a page that loads on the
 * window's cookie alone (the dashboard at `/`, an app's own UI under `/apps/<name>/ui/`). Anything
 * else (an address a page signed for its sign-in, a download) is not carried to a new start: the
 * main window goes to the dashboard, and any other window closes.
 */
function movesOnRestart(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  return u.pathname === "/" || /^\/apps\/[^/]+\/ui\//.test(u.pathname);
}

/**
 * The shell's sign-in to its own gateway, and the one record every rule about the windows' traffic
 * reads (`windowTraffic.decide`, the shell's refusals).
 *
 * - `held`: the session the windows use, `{url, origin, port, token, pair, windowDoor, start}`.
 *   `pair` is the window session's own, once the gateway's window door gave one, and null on a
 *   gateway that has none. `start` is the gateway start it came from, `{pid, port, secretDigest}`:
 *   a lift writes it back only for a proof of that start. On a gateway with the window's door, only
 *   a session with its pair goes in the jar.
 * - `withheld`: nothing carries the sign-in to the held origin, while the gateway is lost and not
 *   proven back. The windows' cookie is out of the jar for as long as it is.
 * - `gen`: bumped at every sign-in, loss and release, so an answer or a cookie write that arrives
 *   after one acts on nothing (`stamp`, `isCurrent`).
 * - `retired`: the origins this run withheld for good or left. Nothing ever reaches one again, and
 *   no request carries a retired session. An origin held again leaves it.
 * - `paired`: the origins of the gateways the owner added, which windows may load.
 * - `signedOut`: the gateway ended the held session for the shell (`shellRefusal`). Nothing the
 *   shell sends carries it again, and nothing mints a new one on a refusal.
 *
 * @param {object} deps
 * @param {{set: Function, remove: Function}} deps.cookies - the app session's cookie store.
 * @param {(msg: string) => void} [deps.log]
 */
function makeLocalSignIn({ cookies, log = () => {} }) {
  let held = null;
  let withheld = false;
  let gen = 0;
  let signedOut = false;
  let cookieFollows = true;
  const retired = new Set();
  let paired = new Set();

  async function forget(session) {
    try {
      await cookies.remove(session.url, sessionCookieName(session.port));
    } catch (err) {
      log(`could not remove the window's sign-in for ${session.url}: ${err.message}`);
    }
  }
  // A ready line without a token still names the gateway the windows load: its origin is held, so
  // the window meets that gateway's sign-in page, and nothing carries a sign-in.
  function sessionOf(ready, pair, start) {
    if (!ready || !ready.url) return null;
    const token = ready.token || "";
    return {
      url: ready.url,
      origin: originKey(ready.url),
      port: ready.port,
      token,
      pair: token ? pair || null : null,
      windowDoor: Boolean(ready.windowDoor),
      start: start ? { pid: start.pid, port: start.port, secretDigest: start.secretDigest || "" } : null,
    };
  }
  /** Whether *session*'s cookie goes in the jar: one with a token, but for the shell's own hold, and on
   *  a gateway with the window's door, only the door's own session, with its pair. */
  const writesCookie = (session) =>
    Boolean(session && session.token) && !session.forShell && (!session.windowDoor || Boolean(session.pair));
  /** Whether *start* is the gateway start *session* came from. A session that records its start is
   *  lifted only for a proof that names that start; one that records none, for any. */
  const fromStart = (session, start) =>
    !session.start ||
    (Boolean(start) && start.pid === session.start.pid && start.port === session.start.port && (start.secretDigest || "") === session.start.secretDigest);
  /** After a write that saw the generation move: take *session*'s cookie back out unless the session
   *  held now names the same cookie, whose own write or removal comes after this one's. */
  async function settleMovedWrite(session) {
    if (!held || held.port !== session.port) await forget(session);
  }
  const isRetired = (session) => Boolean(session) && retired.has(session.origin);
  const isCurrent = (stamp) =>
    Boolean(held) && Boolean(stamp) && stamp.token === held.token && stamp.gen === gen && !isRetired(held);

  return {
    /**
     * Sign the windows in with the session *ready* carries, and *pair* when the gateway's window
     * door gave one. Returns the session it replaced (a restart's previous one), for the caller to
     * sign out, or null. A cookie the store refuses is logged, never thrown, so the session it
     * replaced is still handed back to be signed out. *start* is the gateway start the session came
     * from. Nothing is written once the generation moved on before the write (a loss, a newer
     * sign-in), and on a gateway with the window's door a session without its pair keeps the windows
     * out, with nothing in the jar.
     */
    async adopt(ready, { pair = null, start = null } = {}) {
      const previous = held;
      gen += 1;
      const mine = gen;
      held = sessionOf(ready, pair, start);
      signedOut = false;
      cookieFollows = true;
      if (held) retired.delete(held.origin);
      if (previous && (!held || previous.port !== held.port)) await forget(previous);
      const replaced = previous && previous.token && (!held || previous.token !== held.token) ? previous : null;
      if (gen !== mine) return replaced;
      const session = held;
      if (writesCookie(session)) {
        try {
          await cookies.set(windowCookie(session));
          if (gen === mine) withheld = false;
          else await settleMovedWrite(session);
        } catch (err) {
          log(`could not sign the window in to ${session.url}: ${err.message}`);
        }
      } else if (session && session.token) {
        // The windows are out before anything is awaited; a session this one replaced on the same port
        // still has its cookie in the jar, and leaves it.
        withheld = true;
        log(`the gateway on ${session.url} gave this sign-in no pair; its windows stay out`);
        if (previous && previous.token && previous.port === session.port) await forget(previous);
      } else if (ready) {
        withheld = false;
        log(`the gateway on ${ready.url} handed out no sign-in; its windows will be asked for one`);
      }
      return replaced;
    },
    /**
     * Hold *ready*'s session for the shell's own requests while the windows stay out: a gateway
     * with the window's door signs the windows in with a session of their own (`adopt` with its
     * pair), and until then no window cookie is written and the windows are withheld, a lift
     * included (`forShell`). An earlier start's cookie leaves the jar, and its session is handed
     * back to be signed out.
     */
    async holdForShell(ready, { start = null } = {}) {
      const previous = held;
      gen += 1;
      held = sessionOf(ready, null, start);
      if (held) held.forShell = true;
      withheld = true;
      signedOut = false;
      cookieFollows = true;
      if (held) retired.delete(held.origin);
      if (previous && previous.token) await forget(previous);
      return previous && previous.token && (!held || previous.token !== held.token) ? previous : null;
    },
    /**
     * The gateway was lost. In this order, with nothing awaited before the second step: the sign-in
     * is withheld and the generation moves on; *closeAll* ends every connection the windows hold;
     * then the windows' cookie leaves the jar. The session stays held, so a proof that the same
     * start is back can write it again (`lift`); *retire* also retires its origin, for a gateway
     * the app didn't start, whose next sign-in is a new one. Resolves the new generation.
     */
    async withhold(closeAll = () => {}, { retire = false } = {}) {
      withheld = true;
      gen += 1;
      const mine = gen;
      if (retire && held) retired.add(held.origin);
      closeAll();
      if (held && held.token) await forget(held);
      return mine;
    },
    /**
     * The same start is proven back: write the held session's cookie again, nothing minted. Acts
     * only while the generation is still *expected*, and clears the withhold only once the write
     * has resolved, so a loss that arrives during it keeps the windows out. Only a proof of the
     * session's own *start* lifts it. Over the shell's own hold (`holdForShell`), or a session
     * without its pair on a gateway with the window's door, nothing is written and the windows stay
     * withheld: only the shell's own credentials come back. A hold without a token lets the windows
     * back to the gateway's sign-in page. Nothing is lifted while signed out.
     */
    async lift(expected, { unretire = false, start = null } = {}) {
      if (signedOut) return false;
      if (!held || gen !== expected || !fromStart(held, start)) return false;
      if (unretire) retired.delete(held.origin);
      if (isRetired(held)) return false;
      if (held.forShell || (held.token && !writesCookie(held))) return true;
      if (!held.token) {
        withheld = false;
        return true;
      }
      const session = held;
      try {
        await cookies.set(windowCookie(session));
      } catch (err) {
        log(`could not sign the window in to ${session.url} again: ${err.message}`);
        return false;
      }
      if (gen !== expected) {
        await settleMovedWrite(session);
        return false;
      }
      cookieFollows = true;
      withheld = false;
      return true;
    },
    /** Retire *url*'s origin, one the windows' sign-in left for another (a restart on a new port). */
    retireOrigin(url) {
      const key = originKey(url);
      if (key && (!held || held.origin !== key)) retired.add(key);
    },
    /** What a request the shell sends now is sent on: the held token and the generation. */
    stamp() {
      return { token: held ? held.token : "", gen };
    },
    /** Whether the sign-in *stamp* names is still the one held, unretired, at the same generation. */
    isCurrent(stamp) {
      return isCurrent(stamp);
    },
    /**
     * The gateway ended the sign-in a shell request carried (`shellRefusal`). Acts once, only on the
     * current sign-in and only while it has a pair: a gateway that gives none is handled as today.
     * *defect* (a refused pair on the shell's own request) also takes the cookie and the pair away.
     * Returns whether it ended the sign-in.
     */
    async end(stamp, { defect = false, retiredToo = false } = {}) {
      if (signedOut || !held || !held.pair) return false;
      // *retiredToo*: the one read made before a found gateway's same-start lift, whose origin the
      // loss retired, is still the held session's own.
      const current = retiredToo ? Boolean(stamp) && stamp.token === held.token && stamp.gen === gen : isCurrent(stamp);
      if (!current) return false;
      signedOut = true;
      if (defect) {
        log(`the gateway refused this app's own pair on its sign-in for ${held.url}: a defect in this app`);
        held = { ...held, pair: null };
        await forget(held);
      }
      return true;
    },
    /** The held session as a Bearer, with its pair, for the one read before a found gateway's
     *  same-start lift, while its origin is retired and its windows withheld; none once signed out. */
    heldAuthorization() {
      if (!held || !held.token || signedOut) return {};
      const headers = { Authorization: `Bearer ${held.token}` };
      if (held.pair) headers[PAIR_HEADER] = held.pair;
      return headers;
    },
    /** The headers the shell's own requests carry: the held session as a Bearer, with its pair. */
    authorization() {
      if (!held || !held.token || signedOut || isRetired(held)) return {};
      const headers = { Authorization: `Bearer ${held.token}` };
      if (held.pair) headers[PAIR_HEADER] = held.pair;
      return headers;
    },
    /** A cookie in the app's jar changed: a window signed in with another session drops the pair. */
    cookieChanged(cookie, removed) {
      if (!held || !cookie || cookie.name !== sessionCookieName(held.port) || removed) return;
      if (String(cookie.domain || "").replace(/^\./, "") !== new URL(held.url).hostname) return;
      if ((cookie.path || "/") !== "/") return;
      cookieFollows = cookie.value === held.token;
    },
    /** The origins of the gateways the owner added (every registry row but the local one). */
    setPaired(urls) {
      paired = new Set((urls || []).map(originKey).filter(Boolean));
    },
    /** What `windowTraffic.decide` reads. `paired` never holds the held origin or a retired one. */
    trafficState() {
      const origin = held ? held.origin : "";
      const open = new Set([...paired].filter((key) => key !== origin && !retired.has(key)));
      return {
        held: held ? { origin: held.url, token: held.token, pair: held.pair } : null,
        withheld,
        retired: new Set(retired),
        paired: open,
        cookieFollows,
      };
    },
    get withheld() {
      return withheld;
    },
    get gen() {
      return gen;
    },
    get signedOut() {
      return signedOut;
    },
    /** The gateway start the held session came from, `{pid, port, secretDigest}`, or null. */
    get start() {
      return held && held.start ? { ...held.start } : null;
    },
    /** Whether the held session is the shell's own hold, the windows having none of their own yet. */
    get forShell() {
      return Boolean(held && held.forShell);
    },
    /** The held session's `{url, port, pair}`, never its token, or null. */
    get held() {
      return held ? { url: held.url, port: held.port, pair: held.pair } : null;
    },
    /** Drop the sign-in and its window cookie (quitting). Returns the session it held, or null. */
    async release() {
      const session = held && held.token ? held : null;
      held = null;
      gen += 1;
      if (session) await forget(session);
      return session;
    },
  };
}

module.exports = {
  READY_PREFIX,
  SIGN_OUT_PATH,
  makeLocalSignIn,
  movedUrl,
  movesOnRestart,
  parseReadyLine,
  sessionCookieName,
  signOutRequest,
  windowCookie,
};
