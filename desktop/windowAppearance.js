"use strict";

/**
 * The appearance the shell keeps: the theme the page asked for, the mode in force and its canvas.
 *
 * A switch, from the page's corner control, its Settings or macOS, reaches the window at once
 * (`apply`): its colour, so the strip above the page is the page's canvas, and the app's
 * `nativeTheme.themeSource`, which the native tab bar, menus and the page's `prefers-color-scheme`
 * follow. It comes in two ways to one function: the bridge's `appearance.changed` from an app
 * window (`changed`, which takes only a well-formed report from an app window's own page), and a
 * fixed read of the page (`READ_SCRIPT`, then `fromPageRead`) when the view's theme colour
 * changes, on every load and on focus, which also reaches a paired gateway's view, which has no
 * bridge. At launch (`launch`) the stored appearance sets `themeSource` before the first window
 * opens, so the app opens in the owner's theme.
 */

/** The canvas tokens (`web/src/design/tokens.css`): the page's ground in each mode. */
const DARK_CANVAS = "#0f0f0f";
const LIGHT_CANVAS = "#f0f4f8";

/** The shell store's item that keeps the appearance across launches. */
const STORE_KEY = "appearance";

/** The store writes its file on every set, so a run of switches is written at most this often. */
const STORE_EVERY_MS = 2000;

const PREFERENCES = new Set(["dark", "light", "auto"]);
const MODES = new Set(["dark", "light"]);
const CANVAS = /^#[0-9a-f]{6}$/;

/** The canvas token of `mode`. */
function canvasOf(mode) {
  return mode === "light" ? LIGHT_CANVAS : DARK_CANVAS;
}

/** The `themeSource` a preference asks for: auto follows the system. */
function themeSourceFor(preference) {
  return preference === "dark" || preference === "light" ? preference : "system";
}

/** `value` as an appearance, or null unless its preference, mode and canvas are each well formed. */
function parseAppearance(value) {
  if (!value || typeof value !== "object") return null;
  const { preference, mode, canvas } = value;
  if (!PREFERENCES.has(preference) || !MODES.has(mode)) return null;
  if (typeof canvas !== "string" || !CANVAS.test(canvas)) return null;
  return { preference, mode, canvas };
}

/** How a page is read: fixed text that carries no input. Its answer goes to `fromPageRead`. */
const READ_SCRIPT = `(() => {
  const root = document.documentElement;
  const body = document.body;
  return {
    preference: root.dataset.modePreference || "",
    mode: root.dataset.mode || "",
    canvas: body ? getComputedStyle(body).backgroundColor : "",
  };
})()`;

/** A computed colour (`rgb(r, g, b)`, or an opaque `rgba`) or a `#rrggbb` as `#rrggbb`; else "". */
function hexOf(colour) {
  if (typeof colour !== "string") return "";
  const text = colour.trim().toLowerCase();
  if (CANVAS.test(text)) return text;
  const m = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(1|1\.0+)\s*)?\)$/.exec(text);
  if (!m) return "";
  const parts = m.slice(1, 4).map(Number);
  if (parts.some((n) => n > 255)) return "";
  return `#${parts.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * What a read of the page says, or null. A page that names its preference is that appearance. An
 * older page names none: its mode is taken as a pinned preference, as the shell took it before,
 * and is `pinned`, so it is never stored. A page with no dark or light mode (the loading page)
 * says nothing. The canvas is the body's colour, or the mode's token when it can't be read.
 */
function fromPageRead(read) {
  if (!read || typeof read !== "object" || !MODES.has(read.mode)) return null;
  const canvas = hexOf(read.canvas) || canvasOf(read.mode);
  if (PREFERENCES.has(read.preference)) return { preference: read.preference, mode: read.mode, canvas };
  return { preference: read.mode, mode: read.mode, canvas, pinned: true };
}

/**
 * The `themeSource` to set before the first window and the colour it opens in, from the stored
 * item and the mode the system is in. Nothing stored, or anything that doesn't parse, follows the
 * system, so the window and the loading page agree.
 */
function launch({ stored, systemDark }) {
  let kept = null;
  try {
    kept = parseAppearance(JSON.parse(stored));
  } catch {
    kept = null;
  }
  const systemMode = systemDark ? "dark" : "light";
  if (!kept) return { themeSource: "system", canvas: canvasOf(systemMode) };
  const mode = kept.preference === "auto" ? systemMode : kept.preference;
  return { themeSource: themeSourceFor(kept.preference), canvas: mode === kept.mode ? kept.canvas : canvasOf(mode) };
}

/**
 * @param {{
 *   nativeTheme: { themeSource: string },
 *   store?: { getItem(key: string): string | null, setItem(key: string, value: string): void } | null,
 *   canvas: string,
 *   focusedAppWindow: () => object | null,
 *   now?: () => number,
 *   setTimer?: (fn: () => void, ms: number) => unknown,
 *   clearTimer?: (timer: unknown) => void,
 * }} deps `canvas` is the colour the app opened in; `focusedAppWindow` the focused app window.
 */
function makeWindowAppearance({
  nativeTheme,
  store = null,
  canvas,
  focusedAppWindow,
  now = Date.now,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let current = canvas;
  let stored = store ? store.getItem(STORE_KEY) : null;
  let pending = null;
  let timer = null;
  let lastWrite = -Infinity;

  function write() {
    timer = null;
    if (pending !== null && pending !== stored) {
      store.setItem(STORE_KEY, pending);
      stored = pending;
      lastWrite = now();
    }
    pending = null;
  }

  function keep(a) {
    if (!store) return;
    const text = JSON.stringify({ preference: a.preference, mode: a.mode, canvas: a.canvas });
    if (text === stored && timer === null) return;
    pending = text;
    if (timer !== null) return;
    const wait = STORE_EVERY_MS - (now() - lastWrite);
    if (wait <= 0) write();
    else timer = setTimer(write, wait);
  }

  /**
   * Bring `win` to the appearance `a`: its colour always; the app's `themeSource` only when `win`
   * is focused or no app window is, so two windows on gateways in different themes can't take
   * turns over it; and the appearance kept for the next launch when it set `themeSource` and names
   * a preference of its own. True when it set `themeSource`.
   */
  function apply(win, a) {
    if (!win || (typeof win.isDestroyed === "function" && win.isDestroyed()) || !a) return false;
    win.setBackgroundColor(a.canvas);
    const focused = focusedAppWindow();
    if (focused && focused !== win) return false;
    current = a.canvas;
    const source = themeSourceFor(a.preference);
    if (nativeTheme.themeSource !== source) nativeTheme.themeSource = source;
    if (!a.pinned) keep(a);
    return true;
  }

  /**
   * Colour `win` in `mode`'s canvas, for a page with no mode of its own (the loading or offline
   * page), which follows `themeSource` by itself. The app's colour follows only when `win` is
   * focused or no app window is; `themeSource` and the store are never touched.
   */
  function paint(win, mode) {
    if (!win || (typeof win.isDestroyed === "function" && win.isDestroyed()) || !MODES.has(mode)) return;
    win.setBackgroundColor(canvasOf(mode));
    const focused = focusedAppWindow();
    if (!focused || focused === win) current = canvasOf(mode);
  }

  /** The preference kept for the next launch, a switch still waiting to be written included, or
   *  "" with none: handed to a new page of the app's own, whose origin may keep nothing. */
  function preference() {
    const text = pending ?? stored;
    try {
      return parseAppearance(JSON.parse(text))?.preference ?? "";
    } catch {
      return "";
    }
  }

  /**
   * The bridge's report from `sender`: applied to the app window whose page sent it, and refused
   * from any other sender (`appWindowOf` gives none) or when it isn't well formed.
   */
  function changed(sender, payload, appWindowOf) {
    const win = appWindowOf(sender);
    const a = parseAppearance(payload);
    if (!win || !a) return false;
    apply(win, a);
    return true;
  }

  /** Write what is waiting, as the app quits. */
  function flush() {
    if (timer !== null) clearTimer(timer);
    write();
  }

  return { canvas: () => current, apply, paint, preference, changed, flush };
}

/** The focused app window among `getAllWindows()`: one built around the app's page view, so never
 *  the buddy or a dialog; null when none is focused. */
function focusedAppWindow(getAllWindows) {
  return getAllWindows().find((w) => !w.isDestroyed() && w._pcView && w.isFocused()) || null;
}

/** The app window among `getAllWindows()` whose bridged page is `sender`, or null. */
function appWindowOfSender(getAllWindows, sender) {
  return (
    getAllWindows().find((w) => !w.isDestroyed() && w._pcBridgeAttached === true && w._pcView && w.webContents === sender) ||
    null
  );
}

module.exports = {
  DARK_CANVAS,
  LIGHT_CANVAS,
  STORE_KEY,
  STORE_EVERY_MS,
  READ_SCRIPT,
  canvasOf,
  themeSourceFor,
  parseAppearance,
  hexOf,
  fromPageRead,
  launch,
  makeWindowAppearance,
  focusedAppWindow,
  appWindowOfSender,
};
