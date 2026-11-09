"use strict";

/**
 * The window's title bar, traffic lights and colour.
 *
 * On macOS the window uses the inset hidden title bar, and the traffic lights sit in the strip
 * above the page (`TAB_BAR_HEIGHT`, the native tab bar's padding, which the drag view covers).
 * Their place is `TRAFFIC_LIGHTS` and nowhere else, so moving them into a toolbar row later is a
 * change to that one constant.
 *
 * The window is opaque on every platform, in the canvas of the appearance the shell keeps
 * (`windowAppearance.js`), so the sidebar is the web UI's own rail in light and in dark, and a new
 * window or tab opens in the colour in force when it opens.
 */

/** The strip above the page, in px: the native tab bar's padding, covered by the drag view. */
const TAB_BAR_HEIGHT = 28;

/** The traffic lights' height, in px, so they can be centred in the strip. */
const TRAFFIC_LIGHT_HEIGHT = 14;

/** Where the traffic lights sit: centred in the strip, over the sidebar. The one place they move. */
const TRAFFIC_LIGHTS = Object.freeze({ x: 12, y: (TAB_BAR_HEIGHT - TRAFFIC_LIGHT_HEIGHT) / 2 });

/**
 * @param {{ platform: string, canvas: () => string }} deps `canvas` is the appearance's colour now.
 */
function makeWindowChrome({ platform, canvas }) {
  const mac = platform === "darwin";

  function options() {
    const backgroundColor = canvas();
    return mac
      ? { titleBarStyle: "hiddenInset", trafficLightPosition: TRAFFIC_LIGHTS, backgroundColor }
      : { titleBarStyle: "hidden", backgroundColor };
  }

  return { options };
}

module.exports = {
  TAB_BAR_HEIGHT,
  TRAFFIC_LIGHT_HEIGHT,
  TRAFFIC_LIGHTS,
  makeWindowChrome,
};
