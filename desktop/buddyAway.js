/**
 * Whether the owner is away, and whether they hid the buddy (#3832). Both live in the main process,
 * for the buddy and the menu bar alike.
 *
 * **Away** is the system's idle time against the buddy policy's `away_after_secs`
 * (`GET /api/buddy/policy`, #3901; 180 s until it is read), checked every `CHECK_MS` while the
 * buddy's window exists, and at once when the screen locks or the machine sleeps (away) or wakes
 * (checked again). A docked buddy hides while you are away, and a floating one dozes; the page
 * keeps what happened meanwhile for the return summary.
 *
 * **Hidden by the owner** ("Hide for an hour", "Hide until tomorrow") is kept in the shell's store as
 * the moment it ends (`buddy:hiddenUntil`), so it outlasts a restart of the app, and "tomorrow" is the
 * next local midnight. One timeout ends it; "Show Buddy now" ends it early. While it holds, the
 * buddy shows nothing but its listening face, and nothing waiting raises its window.
 *
 * Electron-free: the idle time, the store and the timers are injected (`test/buddyAway.test.js`).
 */

/** How often the idle time is read while the buddy's window exists. */
const CHECK_MS = 15000;

/** The absence time until the policy says otherwise (`buddy.away_after_secs`'s default). */
const DEFAULT_AWAY_SECS = 180;

/** The store key the end of an owner's hide is kept under (epoch ms). */
const HIDDEN_KEY = "buddy:hiddenUntil";

const HOUR_MS = 60 * 60 * 1000;

/**
 * - `idleSeconds()`: the system's idle time (`powerMonitor.getSystemIdleTime()`).
 * - `awayAfterSecs()`: the policy's `away_after_secs`.
 * - `onChange(away)`: once per change.
 */
function makeAbsence({ idleSeconds, awayAfterSecs = () => DEFAULT_AWAY_SECS, onChange, setTimer = setTimeout, clearTimer = clearTimeout, checkMs = CHECK_MS }) {
  let away = false;
  let locked = false;
  let timer = null;
  const say = (next) => {
    if (next === away) return;
    away = next;
    onChange(next);
  };
  const limit = () => {
    const secs = Number(awayAfterSecs());
    return Number.isFinite(secs) && secs > 0 ? secs : DEFAULT_AWAY_SECS;
  };
  const check = () => {
    let idle = 0;
    try {
      idle = Number(idleSeconds()) || 0;
    } catch {
      idle = 0;
    }
    say(locked || idle >= limit());
  };
  const tick = () => {
    timer = setTimer(tick, checkMs);
    check();
  };
  return {
    /** Read the idle time from now on, while the buddy's window exists. */
    start() {
      if (timer) return;
      timer = setTimer(tick, checkMs);
      check();
    },
    stop() {
      if (timer) clearTimer(timer);
      timer = null;
    },
    /** The screen locked, or the machine is going to sleep: away at once. */
    lock() {
      locked = true;
      say(true);
    },
    /** The screen unlocked, or the machine woke: read the idle time again. */
    unlock() {
      locked = false;
      check();
    },
    get away() {
      return away;
    },
  };
}

/** The next local midnight after *now* (epoch ms). */
function nextMidnight(now) {
  const d = new Date(now);
  d.setHours(24, 0, 0, 0);
  return d.getTime();
}

/**
 * The owner's hide. `onChange(hidden)` once per change, including the hide ending on its own.
 */
function makeBuddyHide({ store, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout, onChange = () => {} }) {
  let timer = null;
  const until = () => {
    const v = Number(store.getItem(HIDDEN_KEY));
    return Number.isFinite(v) && v > now() ? v : 0;
  };
  const arm = () => {
    if (timer) clearTimer(timer);
    timer = null;
    const end = until();
    if (!end) return;
    timer = setTimer(() => {
      timer = null;
      store.setItem(HIDDEN_KEY, "0");
      onChange(false);
    }, end - now());
  };
  const hideUntil = (end) => {
    store.setItem(HIDDEN_KEY, String(Math.round(end)));
    arm();
    onChange(true);
  };
  return {
    /** Pick up a hide a previous run of the app left, and end it when it is due. */
    start: arm,
    stop() {
      if (timer) clearTimer(timer);
      timer = null;
    },
    get hidden() {
      return until() > 0;
    },
    get until() {
      return until();
    },
    hideForAnHour: () => hideUntil(now() + HOUR_MS),
    hideUntilTomorrow: () => hideUntil(nextMidnight(now())),
    /** "Show Buddy now": the hide ends early. */
    showNow() {
      const was = until() > 0;
      store.setItem(HIDDEN_KEY, "0");
      arm();
      if (was) onChange(false);
    },
  };
}

module.exports = { CHECK_MS, DEFAULT_AWAY_SECS, HIDDEN_KEY, makeAbsence, makeBuddyHide, nextMidnight };
