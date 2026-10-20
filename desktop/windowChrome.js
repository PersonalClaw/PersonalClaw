"use strict";

/**
 * The window's title bar, traffic lights and sidebar material.
 *
 * On macOS the window uses the inset hidden title bar, and the traffic lights sit in the strip
 * above the page (`TAB_BAR_HEIGHT`, the native tab bar's padding, which the drag view covers).
 * Their place is `TRAFFIC_LIGHTS` and nowhere else, so moving them into a toolbar row later is a
 * change to that one constant.
 *
 * The sidebar takes the system's translucent material (`vibrancy: "under-window"` on a clear
 * window), and the page is told so through `data-window-material` on its root, so it leaves only
 * the sidebar clear and keeps the reading surfaces opaque. The window stays opaque, with no
 * material, on other platforms and while the system asks for reduced transparency; a change to
 * that setting reaches every open window.
 */

/** The strip above the page, in px: the native tab bar's padding, covered by the drag view. */
const TAB_BAR_HEIGHT = 28;

/** The traffic lights' height, in px, so they can be centred in the strip. */
const TRAFFIC_LIGHT_HEIGHT = 14;

/** Where the traffic lights sit: centred in the strip, over the sidebar. The one place they move. */
const TRAFFIC_LIGHTS = Object.freeze({ x: 12, y: (TAB_BAR_HEIGHT - TRAFFIC_LIGHT_HEIGHT) / 2 });

/** The opaque window colour: the dark canvas, which the window opened on before the material. */
const OPAQUE_BACKGROUND = "#0f0f0f";

/** A clear window, so the material shows where the page leaves the sidebar clear. */
const CLEAR_BACKGROUND = "#00000000";

const MATERIAL = "under-window";

/** The script that tells the page whether the material shows. Fixed text, carrying no input. */
function materialScript(on) {
  return on === true
    ? 'document.documentElement.dataset.windowMaterial = "sidebar";'
    : "delete document.documentElement.dataset.windowMaterial;";
}

/**
 * @param {{ platform: string, nativeTheme: { prefersReducedTransparency?: boolean,
 *   on: (event: string, cb: () => void) => void } }} deps
 */
function makeWindowChrome({ platform, nativeTheme }) {
  const mac = platform === "darwin";
  // A theme that gives no answer reads as "reduce": opaque is the safe side.
  const shows = () => mac && nativeTheme.prefersReducedTransparency === false;

  function options() {
    if (!mac) return { titleBarStyle: "hidden", backgroundColor: OPAQUE_BACKGROUND };
    return shows()
      ? { titleBarStyle: "hiddenInset", trafficLightPosition: TRAFFIC_LIGHTS, vibrancy: MATERIAL, backgroundColor: CLEAR_BACKGROUND }
      : { titleBarStyle: "hiddenInset", trafficLightPosition: TRAFFIC_LIGHTS, backgroundColor: OPAQUE_BACKGROUND };
  }

  /** Follow the system setting in every window `windows()` returns. macOS only. */
  function watch(windows) {
    if (!mac) return;
    let last = shows();
    nativeTheme.on("updated", () => {
      const now = shows();
      if (now === last) return;
      last = now;
      for (const win of windows()) {
        if (!win || win.isDestroyed()) continue;
        win.setVibrancy(now ? MATERIAL : null);
        win.setBackgroundColor(now ? CLEAR_BACKGROUND : OPAQUE_BACKGROUND);
        const wc = win.webContents;
        if (wc && !(wc.isDestroyed && wc.isDestroyed())) {
          Promise.resolve(wc.executeJavaScript(materialScript(now))).catch(() => {});
        }
      }
    });
  }

  return { options, shows, watch, script: () => materialScript(shows()) };
}

module.exports = {
  TAB_BAR_HEIGHT,
  TRAFFIC_LIGHT_HEIGHT,
  TRAFFIC_LIGHTS,
  OPAQUE_BACKGROUND,
  CLEAR_BACKGROUND,
  materialScript,
  makeWindowChrome,
};
