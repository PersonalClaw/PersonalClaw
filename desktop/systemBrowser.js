"use strict";

/**
 * Where a page that is not the gateway's goes: the system's default browser, never the app's own
 * windows.
 *
 * The app's windows hold only the gateway's pages, on the origin the user confirmed, because the
 * view that shows them can carry the capability bridge. Every other page has one rule, and three
 * doors reach it:
 *
 *   - a window a page asks for (`window.open`, a link with `target=_blank`): `windowOpenHandler`
 *     lets one open only on the gateway's own origin and denies the rest, a blank window included,
 *     handing an http or https page to the system browser;
 *   - a navigation away from the gateway (a link, a `location` assignment, a server's redirect):
 *     `navigationGuard` cancels it and hands the page to the system browser;
 *   - the bridge's `systemBrowser.open(url)`: the same rule as a call that answers, for a page that
 *     has to know whether the browser opened. A remote tool server's sign-in is one: the Tools page
 *     waits for it to finish, and a blank window opened first to point at it later is exactly what
 *     the first door refuses.
 *
 * Only an http or https page is handed on. `shell.openExternal` gives its argument to the OS, which
 * opens whatever an app registered for the scheme (a file, a settings pane, another app's handler),
 * so an address that came from a renderer is untrusted input, checked here in the main process. The
 * preload's coercion is a courtesy.
 *
 * Electron is injected, so a test drives every door against a fake and no browser opens.
 */

/** The longest address handed on. An authorization request with its PKCE challenge, `state` and
 * scopes is a few hundred characters; an address of megabytes is not a page anyone meant to open. */
const MAX_URL = 8192;
/** The longest reason passed back to the page, which shows it as text. */
const MAX_REASON = 200;

const NOT_A_WEB_PAGE = "only a web page (http or https) opens in your browser";

const clip = (value, max) => String(value ?? "").trim().slice(0, max);

/** `raw` as the address to open, or `""` when it is not an http or https page. What was checked is
 * what is opened: the parsed address, not the string that was sent. */
function systemBrowserUrl(raw) {
  if (typeof raw !== "string" || !raw || raw.length > MAX_URL) return "";
  let u;
  try {
    u = new URL(raw);
  } catch {
    return "";
  }
  return u.protocol === "http:" || u.protocol === "https:" ? u.href : "";
}

/**
 * @param {object} deps
 * @param {{openExternal: (url: string) => Promise<void>}} deps.shell  Electron's `shell`
 * @param {(msg: string) => void} [deps.log]
 */
function makeSystemBrowser({ shell, log = () => {} } = {}) {
  /**
   * Open one page in the system's default browser. Resolves `{ok, reason?}` and never rejects: a
   * door that hands a page on cannot crash on the answer, and `ok: false` is an answer the page can
   * act on (offer the link to open by hand), not a silence. The address is never logged: a sign-in
   * page's carries its single-use `state`.
   *
   * @param {unknown} raw
   * @returns {Promise<{ok: boolean, reason?: string}>}
   */
  async function open(raw) {
    const url = systemBrowserUrl(raw);
    if (!url) return { ok: false, reason: NOT_A_WEB_PAGE };
    try {
      await shell.openExternal(url);
      return { ok: true };
    } catch (err) {
      // The OS's words, without the address should they quote it.
      const said = String((err && err.message) || "").split(url).join("the page").split(raw).join("the page");
      const reason = clip(said, MAX_REASON) || "the system did not say why";
      log(`could not open a page in the system browser: ${reason}`);
      return { ok: false, reason };
    }
  }

  return { open };
}

/**
 * The handler for `webContents.setWindowOpenHandler`.
 *
 * A window on the active gateway's origin opens in the app. Everything else is denied: a blank
 * window, which a page would point somewhere later, never opens, and an http or https page goes to
 * the system browser instead.
 *
 * `allowedOrigin` is read on every call and is the ACTIVE origin, not the spawned gateway's: in
 * connect-mode the page being rendered belongs to the paired gateway, and a window it opens on its
 * own origin is as legitimate there as it is on loopback.
 *
 * @param {{allowedOrigin: () => string, systemBrowser: {open: (url: string) => Promise<object>}}} deps
 */
function windowOpenHandler({ allowedOrigin, systemBrowser }) {
  return ({ url }) => {
    const origin = allowedOrigin();
    let u = null;
    try {
      u = new URL(url);
    } catch {
      // Not an address at all: denied below, and `open` refuses it.
    }
    if (u && origin && u.origin === origin) return { action: "allow" };
    systemBrowser.open(url);
    return { action: "deny" };
  };
}

/**
 * 🔒 THE VIEW MAY NOT LEAVE THE ORIGIN THE USER CONFIRMED.
 *
 * The handler for `will-navigate` and `will-redirect`, and the guard that matters most once a
 * bridge exists. Without it, a link or a script in rendered content could navigate the view's
 * `WebContents` to any origin, and on the loopback path that `WebContents` carries `preload.js`,
 * so the microphone, hotkey and notification bridge would follow it there. Same-origin navigations
 * and the local loading document are allowed; everything else is handed to the system browser,
 * where it belongs.
 *
 * `will-navigate` covers link clicks and `location` assignments; `will-redirect` covers a server
 * 3xx, which is the shape the SSRF guidance singles out: a host that passes validation and then
 * points the client somewhere it would never have accepted.
 *
 * @param {{allowedOrigin: () => string, systemBrowser: {open: (url: string) => Promise<object>},
 *   log?: (msg: string) => void}} deps
 */
function navigationGuard({ allowedOrigin, systemBrowser, log = () => {} }) {
  return (event, url) => {
    let u;
    try {
      u = new URL(url);
    } catch {
      event.preventDefault();
      return;
    }
    if (u.protocol === "file:" || u.protocol === "about:") return; // loading.html, about:blank
    const origin = allowedOrigin();
    if (origin && u.origin === origin) return;
    event.preventDefault();
    log(`blocked in-app navigation to ${u.origin} (allowed: ${origin || "none"})`);
    systemBrowser.open(url);
  };
}

/**
 * The bridge's main-process half of `systemBrowser.open`.
 *
 * On its own channel rather than folded into `registerCapabilityIpc`, whose channel set is
 * ratcheted to exactly probe/request/snapshot (`test/capabilities.test.js`): opening a page is not
 * a question about a capability. It opens nothing a page could not already reach through a link or
 * `window.open`, which the first door hands to the same `open`; what it adds is the answer.
 */
function registerSystemBrowserIpc(ipcMain, systemBrowser, channels) {
  ipcMain.handle(channels.systemBrowserOpen, (_e, url) => systemBrowser.open(url));
}

module.exports = {
  MAX_URL,
  MAX_REASON,
  makeSystemBrowser,
  navigationGuard,
  registerSystemBrowserIpc,
  systemBrowserUrl,
  windowOpenHandler,
};
