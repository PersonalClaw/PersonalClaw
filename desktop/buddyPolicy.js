/**
 * Buddy status, as the shell follows it (#3896, #3901). The owner's switch is the gateway's
 * `buddy.show`, one value every window and the phone honour, and the gateway serves what it means
 * at `GET /api/buddy/policy`: whether the buddy shows, and on which surfaces (the window, the
 * tray's stills, the dock), each already off while `show` is.
 *
 * The shell keeps the last answer it read under `buddy:enabled` in its own store, as whether the
 * window was on, so a start with no gateway yet, or a gateway too old to answer, does what the
 * owner last chose. The menu bar's Show Buddy writes the gateway's value, and only when the gateway
 * cannot be reached does it change the kept one alone.
 */

const { ENABLED_KEY } = require("./buddyWindow");

const SURFACES = Object.freeze(["window", "tray", "dock", "notifications"]);

/** The policy route's answer as the shell uses it, or null for one it cannot read: no answer, an
 *  old gateway's 404, or a shape that is not the policy. A surface is on only while `show` is. */
function readPolicy(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.show !== "boolean") return null;
  const given = raw.surfaces && typeof raw.surfaces === "object" ? raw.surfaces : {};
  const surfaces = Object.fromEntries(SURFACES.map((k) => [k, raw.show && given[k] === true]));
  return { show: raw.show, surfaces, cached: false };
}

/** The last answer the shell kept, for a start with no gateway: every surface as the window was. */
function cachedPolicy(store) {
  const on = Boolean(store) && store.getItem(ENABLED_KEY) === "1";
  return { show: on, surfaces: Object.fromEntries(SURFACES.map((k) => [k, on])), cached: true };
}

/** Keep what the window should be, for the next start with no gateway. */
function keepPolicy(store, policy) {
  if (store && policy) store.setItem(ENABLED_KEY, policy.surfaces.window ? "1" : "0");
}

/** The write the menu bar's Show Buddy makes: the one config path, as Settings writes it. */
function showWrite(on) {
  return { path: "buddy.show", value: on === true };
}

module.exports = { SURFACES, readPolicy, cachedPolicy, keepPolicy, showWrite };
