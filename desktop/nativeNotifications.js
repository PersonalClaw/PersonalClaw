/**
 * Native OS notifications (DC-5 T4.2) — the shell half of plan-42's `native` target.
 *
 * The gateway decides WHETHER a note is a native one (a rule naming the `native` target,
 * plus a connected shell whose `native_notifications` capability is available — see
 * `notification_rules.native_delivery`). This module is the only thing that can act on
 * that decision, because raising an OS notification is a main-process capability and the
 * gateway is a plain HTTP server that may equally be serving a browser tab.
 *
 * The route the note travels is therefore: gateway → WS note (`native.deliver: true`) →
 * renderer (`web/src/lib/nativeNotifications.ts`) → this module → `new Notification()`.
 * The renderer is in the middle on purpose: it already holds the multiplexed WS (the main
 * process holds no socket to the gateway at all, only outbound POSTs), and it — not Python
 * — owns the SPA's route vocabulary, so the "tap focuses the relevant surface" half is
 * decided by the process that knows what surfaces exist.
 *
 * Narrowness, in the same spirit as `loginItem.js`:
 *
 *  - **Nothing here fires on its own.** `show()` is reachable only from an IPC call the
 *    renderer makes in response to a gateway note. Construction raises nothing;
 *    `test/nativeNotifications.test.js` asserts a ZERO-notification construction.
 *  - **It cannot become a spam channel.** Every string is coerced and truncated here, in
 *    the process that owns the boundary — the preload check is a courtesy. An empty title
 *    is refused rather than shown as a blank banner.
 *  - **The tap is inert without a route.** A click focuses the window (always safe) and
 *    forwards `{route}` to the renderer, which navigates. This module never builds a URL,
 *    never calls `loadURL`, and never opens an external link — so a hostile `route` can at
 *    worst name a hash the SPA does not recognize.
 *  - **A failed notification is reported, not fatal.** `Notification.isSupported()` false,
 *    a throwing constructor, no window to focus: each returns `{ok: false, reason}` so the
 *    renderer can fall back to the in-app bell rather than lose the note.
 *
 * Electron is injected rather than required, so the whole contract is testable against a
 * fake and no test can raise a real banner.
 */

/** Longest strings we hand to the OS. A notification is a one-line interruption; the
 * platform truncates anyway, and an unbounded body from a gateway note is a way to make a
 * banner cover the screen. */
const MAX_TITLE = 120;
const MAX_BODY = 400;
/** A route is an SPA hash id (`inbox`, `loops`), not a URL. Bounded and sanitized so
 * nothing that reaches `location.hash` can carry a scheme, a host or a path. */
const MAX_ROUTE = 64;
const ROUTE_RE = /^[a-z0-9][a-z0-9/-]*$/;

const clip = (value, max) => String(value ?? "").trim().slice(0, max);

/**
 * The route, or "" when the caller gave nothing usable.
 *
 * Rejects rather than repairs: a half-cleaned route would deep-link somewhere the user did
 * not mean, and "" is a perfectly good answer (the tap then just focuses the window).
 */
function normalizeRoute(raw) {
  const route = clip(raw, MAX_ROUTE).replace(/^#\/?/, "");
  return ROUTE_RE.test(route) ? route : "";
}

/**
 * @param {object} deps
 * @param {{new (opts: object): object, isSupported?: () => boolean}} deps.Notification
 * @param {() => boolean} [deps.focusWindow]  Raise the app window. False ⇒ no window.
 * @param {(payload: object) => void} [deps.sendToRenderer]  Forward the tap's route.
 * @param {(msg: string) => void} [deps.log]
 */
function makeNativeNotifications({
  Notification,
  focusWindow = () => false,
  sendToRenderer = () => {},
  log = () => {},
} = {}) {
  const supported =
    !!Notification &&
    (typeof Notification.isSupported !== "function" || Boolean(Notification.isSupported()));

  /**
   * Raise one OS notification.
   *
   * @param {{title?: string, body?: string, route?: string}} note
   * @returns {{ok: boolean, route: string, reason?: string}}
   */
  function show(note) {
    const payload = note && typeof note === "object" ? note : {};
    const title = clip(payload.title, MAX_TITLE);
    const body = clip(payload.body, MAX_BODY);
    const route = normalizeRoute(payload.route);

    if (!supported) {
      return { ok: false, route, reason: "the OS does not support notifications" };
    }
    if (!title) {
      // A banner with no title is an unattributable interruption. Refusing is the honest
      // answer, and the renderer still has the in-app bell.
      return { ok: false, route, reason: "a notification needs a title" };
    }

    let notification;
    try {
      notification = new Notification({ title, body, silent: false });
    } catch (err) {
      log(`native notification failed: ${err.message}`);
      return { ok: false, route, reason: err.message };
    }

    try {
      notification.on("click", () => {
        // Focus first and unconditionally: raising the app is useful even when the note
        // named no surface, and it is the half a user actually expects from a tap.
        const focused = focusWindow();
        if (!focused) log("native notification tapped with no window to focus");
        if (route) sendToRenderer({ route });
      });
      notification.show();
    } catch (err) {
      log(`native notification failed to show: ${err.message}`);
      return { ok: false, route, reason: err.message };
    }
    return { ok: true, route };
  }

  return { supported, show };
}

/**
 * The bridge's main-process half.
 *
 * On its own channel rather than folded into `registerCapabilityIpc`, whose channel set is
 * ratcheted to exactly probe/request/snapshot (`test/capabilities.test.js`) — showing a
 * notification is a USE of a capability, not a question about one.
 */
function registerNativeNotificationIpc(ipcMain, native, channels) {
  ipcMain.handle(channels.notify, (_e, note) => native.show(note));
}

module.exports = {
  makeNativeNotifications,
  registerNativeNotificationIpc,
  normalizeRoute,
  MAX_TITLE,
  MAX_BODY,
};
