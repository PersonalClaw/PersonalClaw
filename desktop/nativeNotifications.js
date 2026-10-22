/**
 * Native OS notifications — the shell half of the `native` delivery target.
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

/** The buddy's alerts (#3897): the only notes raised silent, with the kind's still orb as their
 * icon. The gateway marks a note with one (`notification_kinds.alert_of`). Any other value is no
 * alert, and the note sounds as every native note does. */
const ALERT_KINDS = Object.freeze(["approval", "question", "error"]);
/** The longest icon accepted, as a data URL: a still orb of 96 px at 2x is a few kilobytes. */
const MAX_ICON = 48 * 1024;
const ICON_RE = /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/;

/**
 * The icon, or "" for anything that is not a bounded PNG data URL.
 *
 * A data URL only: the shell never reads a path or fetches a URL for a banner, so a note cannot
 * name a file on this machine or a host to reach. Anything else is dropped, and the banner shows
 * without an icon (its line says what changed on its own).
 */
function normalizeIcon(raw) {
  const icon = typeof raw === "string" ? raw : "";
  return icon.length <= MAX_ICON && ICON_RE.test(icon) ? icon : "";
}

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
 * @param {(dataUrl: string) => object | null} [deps.createIcon]  An alert's icon as an image
 *   (`nativeImage.createFromDataURL`), or null for one that does not read as an image.
 * @param {() => boolean} [deps.openAlert]  A tap on an alert: open the buddy on it, true when the
 *   buddy is on and its window could be rung. False ⇒ the app opens on the alert's route instead.
 * @param {(msg: string) => void} [deps.log]
 */
function makeNativeNotifications({
  Notification,
  focusWindow = () => false,
  sendToRenderer = () => {},
  createIcon = () => null,
  openAlert = () => false,
  log = () => {},
} = {}) {
  const supported =
    !!Notification &&
    (typeof Notification.isSupported !== "function" || Boolean(Notification.isSupported()));

  /**
   * Raise one OS notification.
   *
   * A buddy alert (`alert` one of {@link ALERT_KINDS}) is silent, and carries its still orb as
   * its icon when `icon` is a PNG data URL. No banner carries actions: a tap opens, and nothing
   * on the banner answers.
   *
   * @param {{title?: string, body?: string, route?: string, alert?: string, icon?: string}} note
   * @returns {{ok: boolean, route: string, reason?: string}}
   */
  function show(note) {
    const payload = note && typeof note === "object" ? note : {};
    const title = clip(payload.title, MAX_TITLE);
    const body = clip(payload.body, MAX_BODY);
    const route = normalizeRoute(payload.route);
    const alert = ALERT_KINDS.includes(payload.alert) ? payload.alert : "";

    if (!supported) {
      return { ok: false, route, reason: "the OS does not support notifications" };
    }
    if (!title) {
      // A banner with no title is an unattributable interruption. Refusing is the honest
      // answer, and the renderer still has the in-app bell.
      return { ok: false, route, reason: "a notification needs a title" };
    }

    // Silent for a buddy alert, in v0.3.0 always (its sound is later work, #3900); every other
    // native note keeps the sound it always had.
    const opts = { title, body, silent: Boolean(alert) };
    const iconUrl = alert ? normalizeIcon(payload.icon) : "";
    if (iconUrl) {
      try {
        const icon = createIcon(iconUrl);
        if (icon) opts.icon = icon;
      } catch (err) {
        log(`native notification icon unreadable: ${err.message}`);
      }
    }

    let notification;
    try {
      notification = new Notification(opts);
    } catch (err) {
      log(`native notification failed: ${err.message}`);
      return { ok: false, route, reason: err.message };
    }

    try {
      notification.on("click", () => {
        // A buddy alert opens the buddy on it while Buddy status is on (#3897): the buddy shows
        // the alert with its brief. Otherwise, and for every other note, the app opens.
        if (alert && buddyOpened()) return;
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

  /** Whether the buddy took the tap. A buddy that cannot be rung leaves it to the app. */
  function buddyOpened() {
    try {
      return openAlert() === true;
    } catch (err) {
      log(`native notification: the buddy could not open: ${err.message}`);
      return false;
    }
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
  normalizeIcon,
  ALERT_KINDS,
  MAX_TITLE,
  MAX_BODY,
  MAX_ICON,
};
