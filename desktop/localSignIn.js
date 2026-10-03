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

const READY_PREFIX = "PERSONALCLAW_READY:";

/** The route that ends the session its own cookie carries (`handlers/auth.api_auth_logout`). */
const SIGN_OUT_PATH = "/api/auth/logout";

/**
 * The ready line's `{port, token, url}`, or null for any other line. A ready line without a token
 * still names the port, with `token: ""`: the window then meets the gateway's sign-in page, which
 * says what to do, rather than a window that never loads.
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
  return { port, token, url: `http://localhost:${port}` };
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
 * The request that ends *session* at the gateway serving on *port*: its own cookie, presented to
 * the route that revokes the session its cookie carries. *port* is the gateway being asked, which
 * after a restart is not the port the session was handed out on.
 */
function signOutRequest(session, port) {
  return { path: SIGN_OUT_PATH, headers: { Cookie: `${sessionCookieName(port)}=${session.token}` } };
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
 * The shell's sign-in to its own gateway.
 *
 * @param {object} deps
 * @param {{set: Function, remove: Function}} deps.cookies - the app session's cookie store.
 * @param {(msg: string) => void} [deps.log]
 */
function makeLocalSignIn({ cookies, log = () => {} }) {
  /** `{url, port, token}` for this start, or null. Never returned to a caller but `adopt`/`release`. */
  let held = null;

  async function forget(session) {
    try {
      await cookies.remove(session.url, sessionCookieName(session.port));
    } catch (err) {
      log(`could not remove the window's sign-in for ${session.url}: ${err.message}`);
    }
  }

  return {
    /**
     * Sign the windows in with the session *ready* carries. Returns the session it replaced (a
     * restart's previous one), for the caller to sign out, or null.
     */
    async adopt(ready) {
      const previous = held;
      held = ready && ready.token ? { url: ready.url, port: ready.port, token: ready.token } : null;
      if (previous && (!held || previous.port !== held.port)) await forget(previous);
      if (held) {
        await cookies.set(windowCookie(held));
      } else if (ready) {
        log(`the gateway on ${ready.url} handed out no sign-in; its windows will be asked for one`);
      }
      return previous && (!held || previous.token !== held.token) ? previous : null;
    },

    /** The header the shell's own requests to its gateway carry, or none before it has a sign-in. */
    authorization() {
      return held ? { Authorization: `Bearer ${held.token}` } : {};
    },

    /** Drop the sign-in and its window cookie (quitting). Returns the session it held, or null. */
    async release() {
      const session = held;
      held = null;
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
  parseReadyLine,
  sessionCookieName,
  signOutRequest,
  windowCookie,
};
