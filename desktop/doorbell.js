/**
 * The doorbell (#3832): the main process's one socket to the gateway this shell started, so the
 * menu bar's counts and the buddy hear a change the moment it happens, and nothing polls while it
 * is connected.
 *
 * It is the owner's own socket, signed in with the shell's session (`localSignIn.authorization()`)
 * and presenting the gateway's own origin, the one this shell's windows load from, which the
 * gateway's socket door admits (`dashboard/ws.py`, `_check_ws_origin`). It is a doorbell, not a
 * feed: it reads nothing off a frame but its type (and an `update_progress` frame's step). A ring (`running_changed`, `waiting_changed`,
 * `buddy_policy_changed`, `listening`) is one read of the lists through the shell's own HTTP reads,
 * and a burst of rings within `COALESCE_MS` is one read. `update_progress` with step `restarting`
 * says the gateway announced a restart.
 *
 * While the socket is down the doorbell retries with backoff, and reads the lists every `pollMs`,
 * so the menu bar keeps counting; once it is back, the poll stops. A gateway restart, which moves
 * the gateway to a new port with a new session, is `connect` again.
 *
 * Electron-free: the socket, the reads and the timers are injected (`test/doorbell.test.js`).
 */

/** The frames that say something the lists hold has changed. */
const RINGS = new Set(["running_changed", "waiting_changed", "buddy_policy_changed", "listening"]);

/** A burst of rings inside this window is one read. */
const COALESCE_MS = 100;

/** The waits between attempts to reconnect, the last one repeated. */
const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];

/** The `ws://` address of the gateway at *gatewayUrl*'s socket. */
function socketUrl(gatewayUrl) {
  const u = new URL("/api/ws", gatewayUrl);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}

/** The frame's type, or "" for anything that is not a frame. */
function frameType(data) {
  try {
    const m = JSON.parse(typeof data === "string" ? data : String(data));
    return m && typeof m.type === "string" ? m.type : "";
  } catch {
    return "";
  }
}

/** Whether a frame is the gateway announcing a restart: `update_progress` at step `restarting`. */
function announcesRestart(data) {
  try {
    const m = JSON.parse(typeof data === "string" ? data : String(data));
    return m?.type === "update_progress" && m?.data?.step === "restarting";
  } catch {
    return false;
  }
}

/**
 * The socket to the gateway at *gatewayUrl*, signed in with *authorization()* (the shell's Bearer
 * session) and presenting the gateway's own origin. `WebSocket` is the runtime's (Node's and
 * Electron's main process have one); injected for the tests.
 */
function openGatewaySocket(url, gatewayUrl, authorization, WebSocketCtor = globalThis.WebSocket) {
  return new WebSocketCtor(url, { headers: { ...authorization(), Origin: new URL(gatewayUrl).origin } });
}

/**
 * - `openSocket(url, gatewayUrl)` opens the socket: a WebSocket-shaped object with `onopen`,
 *   `onmessage`, `onclose`, `onerror` and `close()`.
 * - `read()` reads the levels, resolving whatever `onLevels` takes (null on a failure).
 * - `onLevels(levels)`: one read's result.
 * - `onConnection(state)`: `up`, `lost` or `restarting`, once per change.
 */
function makeDoorbell({ openSocket, read, onLevels, onConnection = () => {}, setTimer = setTimeout, clearTimer = clearTimeout, pollMs = 5000, log = () => {} }) {
  let gateway = null;
  let socket = null;
  let attempt = 0;
  let retry = null;
  let poll = null;
  let rung = null;
  let reading = false;
  let again = false;
  let state = null;

  const say = (next) => {
    if (next === state) return;
    state = next;
    onConnection(next);
  };

  async function readNow() {
    if (reading) {
      again = true;
      return;
    }
    reading = true;
    try {
      do {
        again = false;
        const levels = await read();
        if (gateway) onLevels(levels);
      } while (again);
    } finally {
      reading = false;
    }
  }

  /** A ring: one read for the burst it starts. */
  function ring() {
    if (rung || !gateway) return;
    rung = setTimer(() => {
      rung = null;
      void readNow();
    }, COALESCE_MS);
  }

  function startPoll() {
    if (poll || !gateway) return;
    const tick = () => {
      poll = setTimer(tick, pollMs);
      void readNow();
    };
    poll = setTimer(tick, pollMs);
  }

  function stopPoll() {
    if (poll) clearTimer(poll);
    poll = null;
  }

  function drop() {
    if (!socket) return;
    const s = socket;
    socket = null;
    s.onopen = s.onmessage = s.onclose = s.onerror = null;
    try {
      s.close();
    } catch {
      /* already closed */
    }
  }

  function down() {
    drop();
    if (!gateway) return;
    if (state !== "restarting") say("lost");
    startPoll();
    if (retry) return;
    const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    retry = setTimer(() => {
      retry = null;
      open();
    }, wait);
  }

  function open() {
    if (!gateway || socket) return;
    let s;
    try {
      s = openSocket(socketUrl(gateway), gateway);
    } catch (err) {
      log(`doorbell: could not open the socket: ${err.message}`);
      down();
      return;
    }
    socket = s;
    s.onopen = () => {
      attempt = 0;
      stopPoll();
      say("up");
      ring();
    };
    s.onmessage = (ev) => {
      const data = ev && ev.data;
      if (RINGS.has(frameType(data))) ring();
      else if (announcesRestart(data)) say("restarting");
    };
    s.onclose = () => down();
    s.onerror = () => down();
  }

  function stop() {
    gateway = null;
    drop();
    for (const t of [retry, poll, rung]) if (t) clearTimer(t);
    retry = poll = rung = null;
    state = null;
  }

  return {
    /** Ring at the gateway at *gatewayUrl*: its first start, or the new address a restart moved to. */
    connect(gatewayUrl) {
      stop();
      if (!gatewayUrl) return;
      gateway = gatewayUrl;
      attempt = 0;
      void readNow();
      open();
    },
    /** Read the lists now, outside a ring (the buddy window just came up). */
    ring,
    stop,
    get connected() {
      return state === "up";
    },
  };
}

/** Whether *waiting* (`GET /api/waiting`) holds an item that may interrupt you now: the gateway's own
 *  decision on each item (`waiting.interrupt_for`), so the buddy's window is raised only for what
 *  every other surface would ping you for. */
function mayRaise(waiting) {
  const items = waiting && Array.isArray(waiting.waiting) ? waiting.waiting : [];
  return items.some((w) => w && w.interrupt === "deliver" && w.current !== false);
}

module.exports = { BACKOFF_MS, COALESCE_MS, RINGS, makeDoorbell, mayRaise, openGatewaySocket, socketUrl };
