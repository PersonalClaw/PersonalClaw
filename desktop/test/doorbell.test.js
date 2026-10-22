/**
 * The doorbell (#3832): one socket in the main process; a burst of rings is one read; while the
 * socket is down it retries with backoff and the menu bar's poll carries on, and once it is back
 * nothing polls; a restart is said as restarting, not lost; and the buddy's window is raised only for
 * an item that may interrupt you now.
 */

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { BACKOFF_MS, COALESCE_MS, makeDoorbell, mayRaise, openGatewaySocket, socketUrl } = require("../doorbell");

/** A clock and a scheduler of the test's own. */
function clock() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  return {
    setTimer: (fire, ms) => {
      const id = ++seq;
      timers.set(id, { at: now + ms, fire });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    pending: () => timers.size,
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fire();
        await settle();
      }
      now = until;
      await settle();
    },
  };
}

const settle = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

/** A socket the test drives. */
function fakeSockets() {
  const opened = [];
  return {
    opened,
    openSocket(url, gateway) {
      const s = { url, gateway, closed: false, close() { this.closed = true } };
      opened.push(s);
      return s;
    },
    get last() {
      return opened[opened.length - 1];
    },
  };
}

function world({ pollMs = 5000 } = {}) {
  const c = clock();
  const sockets = fakeSockets();
  const reads = [];
  const levels = [];
  const states = [];
  const bell = makeDoorbell({
    openSocket: sockets.openSocket,
    read: async () => {
      reads.push(reads.length);
      return { n: reads.length };
    },
    onLevels: (l) => levels.push(l),
    onConnection: (s) => states.push(s),
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
    pollMs,
  });
  return { c, sockets, reads, levels, states, bell };
}

const frame = (type, data = {}) => ({ data: JSON.stringify({ type, data }) });

describe("the doorbell", () => {
  test("opens one socket at the gateway's own address", () => {
    const w = world();
    w.bell.connect("http://127.0.0.1:8123");
    assert.equal(w.sockets.opened.length, 1);
    assert.equal(w.sockets.last.url, "ws://127.0.0.1:8123/api/ws");
    assert.equal(socketUrl("https://h.example:9/x"), "wss://h.example:9/api/ws");
  });

  test("ten change frames in a burst are one read of the lists", async () => {
    const w = world();
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onopen();
    await w.c.advance(COALESCE_MS);
    const before = w.reads.length;
    for (let i = 0; i < 10; i++) w.sockets.last.onmessage(frame(i % 2 ? "running_changed" : "waiting_changed"));
    await w.c.advance(COALESCE_MS);
    assert.equal(w.reads.length - before, 1);
    assert.deepEqual(w.states, ["up"]);
  });

  test("a frame that is no ring reads nothing: a reply streaming, a session list", async () => {
    const w = world();
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onopen();
    await w.c.advance(COALESCE_MS);
    const before = w.reads.length;
    for (const t of ["chat_chunk", "sessions", "work_phase", "approval"]) w.sockets.last.onmessage(frame(t));
    w.sockets.last.onmessage({ data: "not json" });
    await w.c.advance(COALESCE_MS * 5);
    assert.equal(w.reads.length, before);
  });

  test("connected, nothing polls", async () => {
    const w = world({ pollMs: 5000 });
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onopen();
    await w.c.advance(COALESCE_MS);
    const before = w.reads.length;
    await w.c.advance(60_000);
    assert.equal(w.reads.length, before);
    assert.equal(w.c.pending(), 0);
  });

  test("down, it says lost, polls the lists, and retries with backoff until it is back", async () => {
    const w = world({ pollMs: 5000 });
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onopen();
    await w.c.advance(COALESCE_MS);
    w.sockets.last.onclose();
    assert.deepEqual(w.states, ["up", "lost"]);
    // Each attempt fails at once; the waits between them grow.
    const opens = [];
    for (let i = 0; i < 3; i++) {
      const n = w.sockets.opened.length;
      await w.c.advance(BACKOFF_MS[i]);
      assert.equal(w.sockets.opened.length, n + 1, `attempt ${i + 1} after ${BACKOFF_MS[i]} ms`);
      opens.push(w.sockets.opened.length);
      w.sockets.last.onerror();
    }
    const polled = w.reads.length;
    await w.c.advance(5000);
    assert.ok(w.reads.length > polled, "the menu bar's counts are read while the socket is down");
    // Back: the poll stops.
    await w.c.advance(BACKOFF_MS[3]);
    w.sockets.last.onopen();
    await w.c.advance(COALESCE_MS);
    const after = w.reads.length;
    await w.c.advance(60_000);
    assert.equal(w.reads.length, after);
    assert.equal(w.states.at(-1), "up");
  });

  test("an announced restart is restarting, not lost, and a new address is a new socket", async () => {
    const w = world();
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onopen();
    w.sockets.last.onmessage(frame("update_progress", { step: "restarting" }));
    const first = w.sockets.last;
    first.onclose?.();
    assert.deepEqual(w.states, ["up", "restarting"]);
    w.bell.connect("http://127.0.0.1:9001");
    assert.equal(first.closed, true);
    assert.equal(w.sockets.last.url, "ws://127.0.0.1:9001/api/ws");
    w.sockets.last.onopen();
    assert.equal(w.states.at(-1), "up");
  });

  test("stopped, it holds no timer and no socket", async () => {
    const w = world();
    w.bell.connect("http://127.0.0.1:8123");
    w.sockets.last.onclose();
    w.bell.stop();
    assert.equal(w.c.pending(), 0);
    assert.equal(w.sockets.last.closed, true);
  });
});

describe("what raises the buddy's window", () => {
  const item = (interrupt, over = {}) => ({ id: `approval:${interrupt}`, kind: "approval", interrupt, current: true, ...over });

  test("only an item the gateway says may interrupt you now", () => {
    assert.equal(mayRaise({ waiting: [item("deliver")] }), true);
    for (const i of ["quiet", "hush", "drop"]) assert.equal(mayRaise({ waiting: [item(i)] }), false, i);
    assert.equal(mayRaise({ waiting: [item("deliver", { current: false })] }), false, "past answering");
    assert.equal(mayRaise({ waiting: [] }), false);
    assert.equal(mayRaise(null), false, "a read that failed raises nothing");
  });
});

describe("the socket the shell opens", () => {
  test("carries the shell's session and the gateway's own origin", async (t) => {
    if (typeof globalThis.WebSocket !== "function") return t.skip("no WebSocket in this runtime");
    const seen = await new Promise((resolve) => {
      const srv = http.createServer();
      srv.on("upgrade", (req, sock) => {
        resolve({ origin: req.headers.origin, auth: req.headers.authorization, path: req.url });
        sock.destroy();
        srv.close();
      });
      srv.listen(0, "127.0.0.1", () => {
        const gateway = `http://127.0.0.1:${srv.address().port}`;
        const ws = openGatewaySocket(socketUrl(gateway), gateway, () => ({ Authorization: "Bearer shell-session" }));
        ws.onerror = () => {};
      });
    });
    assert.equal(seen.auth, "Bearer shell-session");
    assert.match(seen.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(seen.path, "/api/ws");
  });
});
