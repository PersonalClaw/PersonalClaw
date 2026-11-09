/**
 * The windows' sign-in across a lost gateway, a restart, a refusal and the window's door, as `main.js`
 * has it: its own functions and its `gatewayWatch` wiring, run in a context that gives them the real
 * record (`localSignIn.js`), the real watch (`gatewayWatch.js`) and the real traffic decision
 * (`windowTraffic.js`), with fakes for the gateways, the cookie jar, the windows and the clock.
 *
 * One invariant holds after every step of every sequence: while the windows' sign-in is withheld, no
 * loopback session cookie is in the jar, and every request to the held origin or a retired one is
 * cancelled.
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { makeLocalSignIn, movedUrl, movesOnRestart, parseReadyLine, signOutRequest } = require("../localSignIn");
const { makeGatewayWatch } = require("../gatewayWatch");
const { RUNTIME_FILE, VERSION_SHAPE } = require("../homeGateway");
const { decide, originKey } = require("../windowTraffic");
const { classify: classifyRefusal } = require("../shellRefusal");
const { LOCAL_STATES, describeLocal } = require("../connectDialog");
const { compareVersions } = require("../appUpdate");
const { behindSentence, unusableSentence } = require("../gatewayLost");
const gatewayFirst = require("../gatewayFirst");

const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");

const fnSource = (name) => {
  const at = MAIN.search(new RegExp(`^(async )?function ${name}\\(`, "m"));
  assert.ok(at >= 0, `${name} is gone — this rail is now blind`);
  return MAIN.slice(at, MAIN.indexOf("\n}\n", at) + 2);
};
const statement = (head) => {
  const at = MAIN.indexOf(head);
  assert.ok(at >= 0, `${head} is gone — this rail is now blind`);
  return MAIN.slice(at, MAIN.indexOf("\n});\n", at) + 4);
};

const FUNCTIONS = [
  "originOf",
  "credentialsWithheld",
  "loseGateway",
  "takeBackAdoptedGateway",
  "followMovedGateway",
  "findHomeGateway",
  "resumeGateway",
  "withholdForGood",
  "adoptedGone",
  "gatewayLost",
  "adoptLocalGateway",
  "takeWindowDoor",
  "followRestartedGateway",
  "adoptHomeGateway",
  "park",
  "loadOrPark",
  "reloadParkedWindows",
  "noteShellRefusal",
  "parseJson",
  "shellSignedOut",
  "signInAgain",
  "askWindowDoor",
  "startOf",
  "spendDoorAsk",
  "spendDoorAsks",
  "doorAsksLeft",
  "doorGaveUp",
  "keepToSignOut",
  "signOutKept",
  "readHeldSessionAt",
  "noteDoorbellFailure",
  "stopDoorbellForStart",
  "watchRecord",
  "noteRecordLoss",
  "readyLineIsStale",
  "stopRecordWatch",
  "rowBackFromSignedOut",
  "reloadAfterSignInAgain",
  "dashboardAt",
  "dispatchPairChanged",
  "moveOffExempt",
  "takeReadyLine",
  "doorbellConnection",
  "startPresenceRefresh",
  "stopPresenceRefresh",
  "postGateway",
  "signOutLocalSession",
  "watchWindowTraffic",
  "syncPaired",
  "purgeStaleLocalCookies",
  "windowLoadFailed",
  "buddyUrl",
  "startLocalHere",
  "bringUpLocalGateway",
  "socketGateway",
  "getGateway",
  "reachHomeGateway",
  "useRunningGateway",
  "ownGatewayLives",
  "shapedVersion",
  "mayUseFoundGateway",
  "setBehind",
  "refusePairless",
  "signOutAtPort",
  "sawGateway",
  "trashPreviousOnce",
  "showNotConnected",
  "endRefusal",
  "refusalEnded",
  "startFromRow",
  "useRunningHere",
  "updateFoundGateway",
  "updateFoundGatewayNow",
  "holdUpdate",
  "updateSession",
  "endUpdateSession",
  "signOutUpdateSessions",
  "askOver",
  "sendToFoundGateway",
  "pressUpdateTheGateway",
  "confirmGatewayUpdate",
];

/** Every session and pair a fake handed out or the jar was given, and every window made: what the
 *  last scan looks for (`the credentials stay out of what main logs and loads`). */
const SECRETS = new Set();
const ALL_WINDOWS = [];
const noteSecret = (value) => {
  if (typeof value === "string" && value) SECRETS.add(value);
};

const TOKEN = "ready-session-token";
const NEXT = "next-start-token";
const WINDOW = "window-session-token";
const PAIR = "pair-of-the-window-session";
const ready = (port, token, extra = {}) => (noteSecret(token), parseReadyLine(`PERSONALCLAW_READY:${JSON.stringify({ port, token, ...extra })}`));

/** A cookie jar that keeps what is set, by name, and writes each write into *did*. */
function jar(did = []) {
  const cookies = new Map();
  return {
    cookies,
    async set(details) {
      did.push(`set:${details.name}`);
      noteSecret(details.value);
      cookies.set(details.name, details.value);
    },
    async remove(_url, name) {
      cookies.delete(name);
    },
  };
}

/** A window: its webContents, the URL it shows, what it loaded. */
function windowAt(id, url) {
  const wc = {
    id,
    url,
    loads: [],
    isDestroyed: () => false,
    getURL: () => wc.url,
    loadURL(next) {
      wc.loads.push(next);
      wc.url = next;
    },
    loadFile(file, opts) {
      wc.loads.push(`file:${path.basename(file)}${opts && opts.query ? `?${new URLSearchParams(opts.query)}` : ""}`);
      wc.url = "file:///app/loading.html";
    },
    reload() {
      wc.loads.push("reload");
    },
    scripts: [],
    executeJavaScript(js) {
      wc.scripts.push(js);
      return Promise.resolve();
    },
  };
  ALL_WINDOWS.push(wc);
  return wc;
}

/** main.js's functions in a context of their own, wired to fakes. */
/** Every world the sequences made, scanned at the end (`the credentials stay out of the logs`). */
const WORLDS = [];

function world({ windowDoor = false } = {}) {
  const did = [];
  /** What main logged, every line. */
  const said = [];
  /** Timers the test runs by hand (`runLater`). */
  const later = [];
  /** The held session's reads before a found gateway's same-start lift. */
  const reads = [];
  /** Every request main's http sent. */
  const requests = [];
  /** What main asked of the Gateways window and its chooser. */
  const dialog = [];
  /** The shell store's registry (`loadRegistry`): the local row, and any the test adds. */
  const registry = { endpoints: [{ id: "local", kind: "local", base_url: "http://127.0.0.1:40000" }] };
  const tokenOf = (headers) => {
    const bearer = /^Bearer (.+)$/.exec(headers.Authorization || "");
    if (bearer) return bearer[1];
    const cookie = /^pc_token_\d+=(.+)$/.exec(headers.Cookie || "");
    return cookie ? cookie[1] : "";
  };
  const timers = [];
  const cookieJar = jar(did);
  const localSignIn = makeLocalSignIn({ cookies: cookieJar, log: (msg) => said.push(String(msg)) });
  // Every session and pair the record is handed, by main or by a test.
  for (const name of ["adopt", "holdForShell"]) {
    const real = localSignIn[name];
    localSignIn[name] = (r, options = {}) => {
      noteSecret(r && r.token);
      noteSecret(options.pair);
      return real.call(localSignIn, r, options);
    };
  }
  // main's console: every argument of every line is kept, and a test that swaps a level only listens.
  const mainConsole = {};
  for (const level of ["log", "info", "warn", "error"]) {
    let listener = null;
    Object.defineProperty(mainConsole, level, {
      get: () => (...args) => {
        said.push(args.map(String).join(" "));
        if (listener) listener(...args);
      },
      set: (fn) => {
        listener = fn;
      },
    });
  }
  const windows = [windowAt(1, "about:blank")];
  const gateways = { proof: null, signIn: null, record: null, digest: "digest-of-start-1", reaches: 0, deadPid: null };
  /** More reaches than any sequence needs: a look that never stops fails by assertion, not by the
   *  runner's timeout. */
  const REACH_BUDGET = 50;
  const box = {
    console: mainConsole,
    __dirname: path.join(__dirname, ".."),
    path,
    URL,
    Promise,
    // A retry's wait runs at once; the record's read while the doorbell is up waits for the test,
    // and a cleared one never runs.
    setTimeout: (fn, ms = 0) => {
      if (!(ms > 0)) return (fn(), 0);
      const handle = { fn };
      later.push(handle);
      return handle;
    },
    clearTimeout: (handle) => {
      const at = later.indexOf(handle);
      if (at >= 0) later.splice(at, 1);
    },
    localSignIn,
    decide,
    originKey,
    movedUrl,
    movesOnRestart,
    signOutRequest,
    classifyRefusal,
    PERSONALCLAW_HOME: "/home/someone/.personalclaw",
    HOME_NEWER_EXIT: 75,
    HOME_SERVED_EXIT: 3,
    ERR_BLOCKED_BY_CLIENT: -20,
    WINDOW_DOOR_ATTEMPTS: 3,
    WINDOW_DOOR_RETRY_MS: 0,
    DOORBELL_FAILURES: 2,
    RECORD_WATCH_MS: 2000,
    recordWatch: null,
    doorAsks: { key: "", spent: 0 },
    doorbellBound: { key: "", failures: 0, stopped: false },
    keptSessions: [],
    signedInAfresh: null,
    latchedSinceEnd: false,
    recordLossNoted: "",
    startKey: (start) => `${start ? start.pid : ""}:${start ? start.secretDigest || "" : ""}`,
    watchedKey: () => (box.gatewayWatch.start ? box.startKey(box.gatewayWatch.start) : ""),
    PAIR_CHANGED: "personalclaw:pair-changed",
    buddy: null,
    pushToBuddy: () => {},
    doorbell: { connect: () => did.push("doorbell"), stop: () => did.push("doorbell-stop") },
    // The doorbell's socket, opened with what the record says to carry.
    openGatewaySocket: (_url, _gateway, auth) => (did.push(`socket:${tokenOf(auth())}`), {}),
    // A start of the app's own gateway, and its stop: each test that starts one says what they do.
    startGateway: async () => {},
    stopGateway: async () => {
      box.gatewayProcess = null;
    },
    sendStatus: () => {},
    setUpFirstGateway: async () => {},
    // Every request main sends, by its own http: answered from *gateways.answers* by path (a
    // sign-out revokes, anything else is {}), and a sign-out written into *did* with the session it
    // carried, so the order of a sequence reads in one list.
    http: {
      request: (opts, onAnswer) => {
        const sent = { port: Number(opts.port), path: opts.path, method: opts.method, headers: { ...opts.headers } };
        requests.push(sent);
        if (opts.path === "/api/status" && opts.method === "GET") reads.push(sent);
        if (opts.path === "/api/auth/logout") did.push(`signout:${tokenOf(sent.headers)}`);
        const answer =
          (opts.path === "/api/auth/logout" && gateways.logout && gateways.logout(sent.headers)) ||
          (gateways.ask && gateways.ask(sent)) ||
          (gateways.answers && gateways.answers[opts.path]) ||
          (opts.path === "/api/status" && gateways.read) ||
          { status: 200, body: opts.path === "/api/auth/logout" ? '{"revoked":true}' : "{}" };
        const req = {
          on: () => req,
          write: () => {},
          destroy: () => {},
          end: () => {
            if (answer === "silent") return;
            // An answer marked closeOnly ends on a close alone, as a socket torn down mid-answer does.
            const res = { statusCode: answer.status, on: (event, fn) => {
              if (event === "data") fn(answer.body);
              if (event === (answer.closeOnly ? "close" : "end")) Promise.resolve().then(fn);
            } };
            Promise.resolve().then(() => onAnswer(res));
          },
        };
        return req;
      },
    },
    Buffer,
    assertLoopbackTarget: (url) => {
      if (!/^http:\/\/127\.0\.0\.1:\d+/.test(String(url))) throw new Error(`not loopback: ${url}`);
    },
    registerWindowTraffic: (appSession, record) => {
      appSession.webRequest.onBeforeRequest(() => {});
      appSession.webRequest.onBeforeSendHeaders(() => {});
      appSession.cookies.on("changed", () => record);
    },
    noteSessionEvent: async () => {},
    scheduleReachProbe: () => did.push("probe"),
    loadRegistry: () => registry,
    LOCAL_ENDPOINT_ID: "local",
    SIGNED_OUT_SENTENCE: "This computer's gateway is running, but this app is signed out of it.",
    NOT_SIGNED_IN_AGAIN_SENTENCE: "This app can't sign in to this computer's gateway again from here.",
    GATEWAY_CHANGED_SENTENCE: "This computer's gateway changed while this app signed in. Try again.",
    localGatewayUrl: null,
    activeUrl: null,
    adoptedGateway: null,
    gatewayGone: false,
    gatewayStarts: 0,
    startFailure: null,
    shellToken: "shell-token",
    lostAt: 0,
    windowDoorInUse: false,
    localState: { state: "own", sentence: "" },
    mainWindow: null,
    shellStore: null,
    gatewayProcess: { pid: 700 },
    parked: new Map(),
    trayPresence: { available: false, setRestarting: () => {} },
    session: { defaultSession: { closeAllConnections: () => did.push("closeAll") } },
    webContents: {
      getAllWebContents: () => windows,
      fromId: (id) => windows.find((w) => w.id === id) || null,
    },
    BaseWindow: { getAllWindows: () => [] },
    appWindows: () => windows.map((webContents) => ({ webContents })),
    homeGateway: {
      reach: async () => {
        gateways.reaches += 1;
        if (gateways.reaches > REACH_BUDGET) throw new Error(`more than ${REACH_BUDGET} reaches: a look that never stops`);
        return gateways.proof || { ok: false, reason: "none" };
      },
      RUNTIME_FILE,
      readRecord: () => gateways.record,
      pidIsAlive: (pid) => pid !== gateways.deadPid,
      startDigest: () => gateways.digest,
      VERSION_SHAPE,
    },
    makeGatewayWatch: (opts) => makeGatewayWatch({ ...opts, setTimer: (fn) => (timers.push(fn), timers.length), clearTimer: () => {} }),
    registerWithGateway: async () => {
      did.push(`register:${box.credentialsWithheld() ? "withheld" : "open"}`);
      return true;
    },

    rememberLocalGateway: () => {},
    setLocalState: (state, sentence = "") => {
      box.localState = { state, sentence };
      did.push(`row:${state}`);
    },
    stoppedSentence: () => "stopped",
    adoptedGoneSentence: () => "gone",
    setOlder: () => {},
    // The Gateways window and its chooser: what main asks of them, in *dialog*.
    connectDialog: { openChooser: () => dialog.push("openChooser"), closeChooser: () => dialog.push("closeChooser"), changed: () => {} },
    // The chooser's press for this computer's row, once the gateway answers: the window loads it.
    navigateToEndpoint: async (win, url) => {
      if (!win || win.isDestroyed()) return false;
      box.activeUrl = url;
      win.webContents.loadURL(url);
      return true;
    },
    summarizePresence: () => ({}),
    // The app's version, and the updater main tells which gateway it uses (`sawGateway`).
    app: { getVersion: () => box.appVersion },
    appVersion: "0.3.8",
    compareVersions,
    behindSentence,
    unusableSentence,
    appUpdater: { noted: [], noteGateway: (told) => box.appUpdater.noted.push(told), trashPrevious: () => did.push("trash") },
    appUpdaterStarted: false,
    previousTrashed: false,
    refusedStart: "",
    autoUseRunning: () => false,
    foundSentence: () => "found",
    notStartedSentence: () => "did not start",
    // Update the Gateway: its engine and sentences as they are, and its constants as main has them.
    ALLOWED_REQUESTS: gatewayFirst.ALLOWED_REQUESTS,
    planGatewayUpdate: gatewayFirst.plan,
    runGatewayUpdate: gatewayFirst.run,
    holdSentence: gatewayFirst.holdSentence,
    shownVersion: gatewayFirst.shownVersion,
    PAIR_HEADER: "X-PersonalClaw-Pair",
    PLAN_SESSION_TTL: "5m",
    UPDATE_SESSION_TTL: "30m",
    TTL_MS: { "5m": 5 * 60_000, "30m": 30 * 60_000 },
    gatewayUpdate: null,
    updateSessions: [],
    isQuitting: false,
    Date,
    Number,
    JSON,
    // The native confirm: the test's answer (0 is Update the Gateway), each one kept.
    confirms: [],
    confirmAnswer: 0,
    require: (name) => {
      if (name !== "electron") throw new Error(`main required ${name} in a test`);
      return { dialog: { showMessageBox: async (options) => (box.confirms.push(options), { response: box.confirmAnswer }) } };
    },
  };
  // The door's answers, whatever a test puts in its place: each session and pair it hands out is noted.
  let signIn = async () => gateways.signIn;
  Object.defineProperty(box.homeGateway, "signIn", {
    get: () => async (...args) => {
      const out = await signIn(...args);
      if (out && out.ok) {
        noteSecret(out.ready && out.ready.token);
        noteSecret(out.pair);
      }
      return out;
    },
    set: (fn) => {
      signIn = fn;
    },
  });
  vm.createContext(box);
  vm.runInContext(FUNCTIONS.map(fnSource).join("\n"), box);
  vm.runInContext(statement("const gatewayWatch = makeGatewayWatch({").replace("const gatewayWatch", "globalThis.gatewayWatch"), box);
  const tick = async (times = 1) => {
    for (let i = 0; i < times; i++) for (const fn of timers.splice(0)) await fn();
  };
  /** The invariant, checked after a step. */
  const holds = (step) => {
    const state = localSignIn.trafficState();
    // On a gateway with the window's door, the only session in the jar is the held one, with its pair.
    if (box.windowDoorInUse) {
      for (const [name, value] of cookieJar.cookies) {
        if (!name.startsWith("pc_token_")) continue;
        assert.ok(state.held && value === state.held.token && state.held.pair, `${step}: ${name} in the jar is not the held session with its pair`);
      }
    }
    if (!state.withheld) return;
    const loopbackCookies = [...cookieJar.cookies.keys()].filter((name) => name.startsWith("pc_token_"));
    assert.deepEqual(loopbackCookies, [], `${step}: a session cookie in the jar while withheld`);
    if (state.held) {
      const r = { url: `${state.held.origin}/api/chat`, resourceType: "xhr", initiatorOrigin: state.held.origin, requestHeaders: {}, hasWebContents: true };
      assert.equal(decide(r, state).cancel, true, `${step}: the held origin reached while withheld`);
    }
    for (const key of state.retired) {
      const r = { url: `${key}/`, resourceType: "mainFrame", initiatorOrigin: undefined, requestHeaders: {}, hasWebContents: true };
      assert.equal(decide(r, state).cancel, true, `${step}: a retired origin reached`);
    }
  };
  const runLater = () => {
    for (const handle of later.splice(0)) handle.fn();
  };
  const made = { box, did, dialog, said, reads, requests, registry, later, runLater, cookieJar, localSignIn, windows, gateways, tick, holds, windowDoor };
  WORLDS.push(made);
  return made;
}

/** The app's own gateway started: its ready line taken, and watched from it. */
async function ownStart(w, port = 51234, token = TOKEN, extra = {}) {
  const r = ready(port, token, extra);
  // main's own ready-line step: the start watched from it, then taken.
  await w.box.takeReadyLine({ pid: 700 }, r, false);
  w.gateways.record = { pid: 700, port };
  return r;
}

/** The This computer row reads *state*, with that state's own sentence. */
function rowIs(w, state, message = state) {
  assert.equal(w.box.localState.state, state, message);
  const sentence = { signed_out: w.box.SIGNED_OUT_SENTENCE, cant_sign_in: w.box.NOT_SIGNED_IN_AGAIN_SENTENCE }[state];
  if (sentence) assert.equal(w.box.localState.sentence, sentence, `${message}: the row's sentence`);
}

describe("the app's own gateway: a non-terminal loss, then a proof of the same start", () => {
  it("withholds and keeps the session, then writes the same one back, with nothing minted, before anything carries the credentials again", async () => {
    const w = world();
    await ownStart(w);
    w.holds("started");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN);
    w.box.loseGateway();
    w.holds("after loseGateway");
    assert.equal(w.box.credentialsWithheld(), true, "the credentials go on through the gap");
    assert.deepEqual(w.did, ["set:pc_token_51234", "closeAll"]);
    await new Promise((r) => setImmediate(r));
    w.holds("lost");
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: false } };
    await w.tick();
    w.holds("proven");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN, "the same session, nothing minted");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    assert.deepEqual(w.did, ["set:pc_token_51234", "closeAll", "set:pc_token_51234", "doorbell", "register:open"], "the doorbell rings before the registration");
    assert.equal(w.box.credentialsWithheld(), false);
  });

  it("keeps withholding for a new start on the same port, until its ready line rotates the session", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-2", windowDoor: false } };
    await w.tick(3);
    w.holds("a new start proven");
    assert.equal(w.localSignIn.withheld, true);
    assert.equal(w.cookieJar.cookies.size, 0);
    w.gateways.digest = "digest-of-start-2";
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(51234, NEXT), true);
    w.holds("rotated");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), NEXT);
    assert.ok(w.did.includes(`signout:${TOKEN}`), "the old start's session is signed out at the new one");
    assert.equal(w.localSignIn.trafficState().retired.size, 0, "the same origin is not retired");
  });

  it("retires the origin a restart left for another port, and nothing reaches it again", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    w.holds("moved");
    const state = w.localSignIn.trafficState();
    assert.equal(state.retired.has(originKey("http://127.0.0.1:51234")), true);
    assert.equal(w.cookieJar.cookies.has("pc_token_51234"), false);
    assert.equal(w.cookieJar.cookies.get("pc_token_52345"), NEXT);
  });

  it("an exit withholds for good: the origin is retired and the cookie leaves", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    await w.box.gatewayLost({ code: 1 });
    w.holds("after gatewayLost");
    await new Promise((r) => setImmediate(r));
    w.holds("exited");
    assert.equal(w.box.gatewayWatch.kind, null, "the watch outlived the exit");
    assert.equal(w.localSignIn.trafficState().retired.has(originKey("http://127.0.0.1:51234")), true);
  });
});

describe("a gateway the app found: a loss retires its session; the same start takes it back, a new start rotates", () => {
  // A gateway the app found is used only with the window's door, whose sign-in has a pair.
  const found = (port, token, digest) => ({ ok: true, gateway: { pid: 300, port, secretDigest: digest, windowDoor: true }, ready: ready(port, token, { window_door: true }), pair: PAIR });

  it("takes the same start back with the session it held, and its origin leaves the retired ones", async () => {
    const w = world();
    w.box.gatewayProcess = null;
    await w.box.adoptHomeGateway(found(5000, TOKEN, "adopted-start-1"));
    w.holds("after adoptHomeGateway");
    w.gateways.record = { pid: 300, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await new Promise((r) => setImmediate(r));
    w.holds("lost");
    assert.equal(w.localSignIn.trafficState().retired.size, 1);
    w.gateways.proof = { ok: true, gateway: { pid: 300, port: 5000, secretDigest: "adopted-start-1", windowDoor: true } };
    await w.tick();
    w.holds("proven");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), TOKEN);
    assert.equal(w.localSignIn.trafficState().retired.size, 0);
  });

  it("signs in to a new start on the same port, and signs the old session out there once it is back", async () => {
    const w = world();
    w.box.gatewayProcess = null;
    await w.box.adoptHomeGateway(found(5000, TOKEN, "adopted-start-1"));
    w.holds("after adoptHomeGateway");
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    w.windows[0].url = "http://127.0.0.1:5000/#/chat/abc";
    w.gateways.proof = { ok: true, gateway: { pid: 301, port: 5000, secretDigest: "adopted-start-2", windowDoor: true } };
    w.gateways.signIn = { ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: PAIR };
    await w.tick();
    w.holds("after tick");
    await new Promise((r) => setImmediate(r));
    w.holds("rotated");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), NEXT);
    const out = w.did.indexOf(`signout:${TOKEN}`);
    assert.ok(out >= 0 && out < w.did.indexOf("register:open"), w.did.join(", "));
    assert.deepEqual(w.windows[0].loads, [], "a page that stayed was reloaded");
    assert.deepEqual(w.windows[0].scripts, ['document.dispatchEvent(new Event("personalclaw:pair-changed"))'], "a page that stayed was not told its pair changed");
  });

  it("a gone gateway is withheld for good, and the watch's news names it", async () => {
    const w = world();
    w.box.gatewayProcess = null;
    await w.box.adoptHomeGateway(found(5000, TOKEN, "adopted-start-1"));
    w.holds("after adoptHomeGateway");
    w.box.adoptedGone({ pid: 300, port: 5000 });
    await new Promise((r) => setImmediate(r));
    w.holds("gone");
    assert.ok(w.did.includes("row:stopped"));
  });
});

describe("a gateway the app found: used only at this app's version or newer, with the window's door", () => {
  /** A proven answer of a found gateway's start. */
  const proven = ({ port = 5000, pid = 300, digest = "found-start-1", version = "0.3.8", door = true } = {}) => ({ pid, port, secretDigest: digest, windowDoor: door, version });
  const signedIn = (port, token) => ({ ok: true, ready: ready(port, token, { window_door: true }), pair: PAIR });
  /** A world whose door counts each sign-in it is asked for. */
  const counted = () => {
    const w = world();
    w.box.gatewayProcess = null;
    w.signIns = [];
    w.box.homeGateway.signIn = async (gateway) => (w.signIns.push(gateway.port), w.answer || signedIn(gateway.port, WINDOW));
    return w;
  };
  /** Each place the app takes up a gateway it found, run against *gateway*'s proof. */
  const SITES = {
    "the start's own look": async (w) => {
      w.box.autoUseRunning = () => true;
      w.box.startGateway = async () => Promise.reject(Object.assign(new Error("served"), { exit: { code: 3 } }));
      return w.box.bringUpLocalGateway();
    },
    "Use It": async (w) => (await w.box.useRunningGateway()).ok,
    "a move it follows": async (w) => w.box.followMovedGateway(),
    "a new start it takes back": async (w) => w.box.takeBackAdoptedGateway({ kind: "adopted", pid: 299, port: w.gateways.proof.gateway.port, secretDigest: "found-start-0" }, w.gateways.proof.gateway),
    "Sign In Again": async (w) => {
      w.box.adoptedGateway = { pid: 299, port: w.gateways.proof.gateway.port };
      w.box.windowDoorInUse = true;
      w.box.localGatewayUrl = `http://127.0.0.1:${w.gateways.proof.gateway.port}`;
      return (await w.box.signInAgain()).ok;
    },
  };
  const tryAt = async (site, gateway) => {
    const w = counted();
    w.gateways.proof = { ok: true, gateway };
    w.gateways.record = { pid: gateway.pid, port: gateway.port };
    const used = await SITES[site](w);
    return { w, used };
  };
  const wroteNoCookie = (w, what) => assert.deepEqual(w.did.filter((d) => d.startsWith("set:")), [], `${what}: a cookie was written`);

  it("is never signed in to at a version older than this app's, with the window's door or without it, at any of the five places, and the row says so", async () => {
    assert.equal(
      behindSentence({ port: 5000, version: "0.3.7", app: "0.3.8" }),
      "This home's gateway on port 5000 runs 0.3.7, older than this app (0.3.8), and the app needs its gateway at 0.3.8 or newer. Update the Gateway brings it to 0.3.8 or newer."
    );
    // An older gateway is behind whether it has the door or not: its version is asked first.
    // Compared as reported, which the compare trims: spaces round an older version leave it older.
    // A version spelled with a capital V reads as one too.
    for (const { version, door } of [{ version: "0.3.7", door: true }, { version: "0.3.7", door: false }, { version: "0.3.6", door: false }, { version: " 0.3.7 ", door: true }, { version: "V0.3.7", door: true }]) {
      for (const site of Object.keys(SITES)) {
        const what = `${site} at ${version}${door ? "" : " without the door"}`;
        const { w, used } = await tryAt(site, proven({ version, door }));
        assert.ok(!used, `${what}: used`);
        assert.deepEqual(w.signIns, [], `${what}: signed in`);
        wroteNoCookie(w, what);
        assert.equal(w.box.localState.state, "behind", what);
        assert.equal(w.box.localState.sentence, behindSentence({ port: 5000, version: version.trim(), app: "0.3.8" }), what);
        assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "none", version: "" }), `${what}: the updater still hears of a gateway in use`);
        assert.ok(!w.windows.some((win) => win.loads.some((url) => String(url).includes(":5000"))), `${what}: a window loaded it`);
        assert.ok(!w.did.some((d) => d.startsWith("register") || d === "doorbell"), `${what}: it registered or rang`);
      }
    }
  });

  it("is never signed in to without the window's door, whatever version it reports, and says which", async () => {
    const said = {
      "0.3.6+local": "This home's gateway on port 5000 reports version 0.3.6+local, which this app can't compare with its own (0.3.8): the app needs its gateway at 0.3.8 or newer. Update the gateway, then press Try Again.",
      "0.3.8": "This home's gateway on port 5000 reports 0.3.8, but this app can't use that build. Install the gateway again, then press Try Again.",
      "0.3.9": "This home's gateway on port 5000 reports 0.3.9, but this app can't use that build. Install the gateway again, then press Try Again.",
      "0.3.9<script>": "This home's gateway on port 5000 reports a version this app can't read, which it can't compare with its own (0.3.8): the app needs its gateway at 0.3.8 or newer. Update the gateway, then press Try Again.",
    };
    for (const [version, sentence] of Object.entries(said)) {
      for (const site of Object.keys(SITES)) {
        const { w, used } = await tryAt(site, proven({ version, door: false }));
        const what = `${site} at ${version}`;
        assert.ok(!used, `${what}: used`);
        assert.deepEqual(w.signIns, [], `${what}: /api/token/local was asked`);
        assert.deepEqual(w.requests, [], `${what}: a request was sent`);
        wroteNoCookie(w, what);
        assert.equal(w.box.localState.state, "unusable", what);
        assert.equal(w.box.localState.sentence, sentence, what);
        assert.ok(!w.said.some((line) => line.includes("<script>")), `${what}: an unread version reached the log`);
      }
    }
  });

  it("writes no cookie for a sign-in without its pair, and ends that session at the gateway it came from", async () => {
    for (const site of ["Use It", "a move it follows", "a new start it takes back"]) {
      const w = counted();
      const gateway = proven();
      w.gateways.proof = { ok: true, gateway };
      w.gateways.record = { pid: gateway.pid, port: gateway.port };
      w.answer = { ok: true, ready: ready(5000, NEXT), pair: null };
      const used = await SITES[site](w);
      assert.ok(!used, `${site}: used`);
      wroteNoCookie(w, site);
      assert.ok(w.did.includes(`signout:${NEXT}`), `${site}: the pairless session was left live: ${w.did.join(", ")}`);
      assert.equal(w.requests.filter((r) => r.path === "/api/auth/logout")[0].port, 5000, site);
      assert.equal(w.box.localState.state, "unusable", site);
      assert.ok(w.said.some((line) => line.includes("without a pair; that session was ended")), `${site}: the log didn't say the session ended`);
      assert.deepEqual(w.box.keptSessions, [], `${site}: an ended session was kept`);
    }
    // A sign-out that isn't answered 200 keeps the session to be signed out later, and says so after
    // it; a version this app can't read is said as one.
    const w = counted();
    const gateway = proven({ version: "0.3.9<script>" });
    w.gateways.proof = { ok: true, gateway };
    w.gateways.record = { pid: gateway.pid, port: gateway.port };
    w.gateways.answers = { "/api/auth/logout": { status: 500, body: "{}" } };
    w.answer = { ok: true, ready: ready(5000, NEXT), pair: null };
    assert.equal(await SITES["Use It"](w), false);
    assert.deepEqual(w.box.keptSessions.map((k) => k.token), [NEXT], "a session that didn't end there was dropped");
    const line = w.said.findIndex((l) => l.includes("without a pair; that session is kept to be signed out later"));
    assert.ok(line >= 0 && w.said.findIndex((l) => l.includes("a sign-out at port 5000 → HTTP 500")) < line, w.said.join(" | "));
    assert.equal(w.box.localState.sentence, unusableSentence({ port: 5000, version: "", app: "0.3.8", comparable: false }));
    assert.ok(!w.said.some((l) => l.includes("<script>")), "an unread version reached the log");
    // A version spelled with a capital V is said as it reads.
    const v = counted();
    v.gateways.proof = { ok: true, gateway: proven({ version: "V0.3.9" }) };
    v.gateways.record = { pid: 300, port: 5000 };
    v.answer = { ok: true, ready: ready(5000, NEXT), pair: null };
    assert.equal(await SITES["Use It"](v), false);
    assert.equal(v.box.localState.sentence, unusableSentence({ port: 5000, version: "V0.3.9", app: "0.3.8", comparable: true }));
  });

  it("uses one at this app's version or newer with the door, and a later start at it after a refusal, and tells the updater its version", async () => {
    for (const site of Object.keys(SITES)) {
      for (const version of ["0.3.8", "0.3.9", "0.3.9+local", "0.3.9<script>"]) {
        const { w, used } = await tryAt(site, proven({ version }));
        if (site === "Sign In Again") continue; // Sign In Again on a gateway in use is pinned beside it.
        const what = `${site} at ${version}`;
        assert.ok(used, `${what}: not used`);
        assert.deepEqual(w.signIns, [5000], what);
        // A version without a version's shape reaches neither the updater nor the log.
        const told = version === "0.3.9<script>" ? "" : version;
        assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "adopted", version: told }), `${what}: the updater wasn't told`);
        assert.ok(!w.said.some((line) => line.includes("<script>")), `${what}: an unread version reached the log`);
        if (version === "0.3.9+local") {
          assert.ok(w.said.some((line) => line.includes("reports version 0.3.9+local") && line.endsWith("it is used")), `${what}: no line says it is used`);
        }
        if (version === "0.3.9<script>") {
          assert.ok(w.said.some((line) => line.includes("reports a version this app can't read") && line.endsWith("it is used")), `${what}: no line says it is used`);
        }
      }
    }
    // Refused as older at a new start, then the same port's next start at the app's version.
    const w = counted();
    w.gateways.record = { pid: 300, port: 5000 };
    const was = { kind: "adopted", pid: 299, port: 5000, secretDigest: "found-start-0" };
    assert.equal(await w.box.takeBackAdoptedGateway(was, proven({ version: "0.3.7" })), false);
    assert.equal(await w.box.takeBackAdoptedGateway(was, proven({ pid: 301, digest: "found-start-2", version: "0.3.8" })), true);
    assert.deepEqual(w.signIns, [5000]);
  });

  /** A world using a found gateway at 0.3.8 on port 5000, its main window on it, and the watch's
   *  next look refusing a new start there at 0.3.7. */
  const refusedWhileInUse = async () => {
    const w = counted();
    const main = windowAt(9, "http://127.0.0.1:5000/#/chat");
    w.box.mainWindow = { isDestroyed: () => false, isMinimized: () => false, restore: () => {}, show: () => {}, webContents: main };
    await w.box.adoptHomeGateway({ ok: true, gateway: proven(), ready: ready(5000, TOKEN, { window_door: true }), pair: PAIR });
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    await new Promise((r) => setImmediate(r));
    w.gateways.proof = { ok: true, gateway: proven({ pid: 301, digest: "found-start-2", version: "0.3.7" }) };
    await w.tick();
    rowIs(w, "behind", "the older start is refused");
    assert.equal(main.loads.at(-1), "file:loading.html?state=offline", "the main window doesn't say nothing serves it");
    assert.ok(w.dialog.includes("openChooser"), "the chooser didn't open");
    return { w, main };
  };
  /** The watch's next look proves a new start at 0.3.8 on the same port. */
  const provenAgain = async (w) => {
    w.gateways.record = { pid: 302, port: 5000 };
    w.gateways.proof = { ok: true, gateway: proven({ pid: 302, digest: "found-start-3", version: "0.3.8" }) };
    await w.tick();
    await new Promise((r) => setImmediate(r));
  };

  it("ends a refusal once the watch takes back a start at this app's version: the row, the chooser and the main window come back", async () => {
    const { w, main } = await refusedWhileInUse();
    await provenAgain(w);
    rowIs(w, "adopted", "the row still says the gateway is refused");
    assert.equal(w.box.startFailure, null, "the refusal is still the start's failure");
    assert.equal(w.box.refusedStart, "");
    assert.ok(w.dialog.includes("closeChooser"), "the chooser stayed open");
    assert.notEqual(main.loads.at(-1), "file:loading.html?state=offline", "the main window stayed on the offline page");
    assert.equal(main.loads.at(-1), "http://127.0.0.1:5000");
    assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "adopted", version: "0.3.8" }));
  });

  it("leaves a found gateway's watch armed when Try Again is pressed on its refused start, answers with the row, and starts nothing", async () => {
    const { w } = await refusedWhileInUse();
    const starts = [];
    w.box.startGateway = async () => {
      starts.push("start");
    };
    // The row's own start, as the Gateways window and the chooser press it.
    const out = await w.box.startFromRow();
    assert.equal(JSON.stringify(out), JSON.stringify({ ok: false, code: "behind", message: behindSentence({ port: 5000, version: "0.3.7", app: "0.3.8" }) }));
    assert.deepEqual(starts, [], "Try Again started the app's own gateway");
    assert.equal(w.box.gatewayWatch.kind, "adopted", "Try Again dropped the watch of the gateway in use");
    assert.equal(w.box.gatewayWatch.withheld, true, "the watch stopped looking");
    rowIs(w, "behind", "the press changed the row");
    // The watch still takes the gateway back once a start this app may use is proven there.
    await provenAgain(w);
    rowIs(w, "adopted", "the watch didn't take the gateway back");
  });

  it("starts the app's own gateway from a stopped row while a found gateway is in use, letting go of its sign-in before the watch", async () => {
    // A found gateway in use that never answered the start's wait: the row says stopped, the watch is
    // neither withheld nor ended, so it never looks.
    const w = counted();
    await w.box.adoptHomeGateway({ ok: true, gateway: proven(), ready: ready(5000, TOKEN, { window_door: true }), pair: PAIR });
    w.box.startFailure = { unanswered: true };
    w.box.setLocalState("stopped", "PersonalClaw's gateway didn't answer.");
    const starts = [];
    w.box.startGateway = async () => {
      starts.push("start");
    };
    const watch = w.box.gatewayWatch;
    const drop = watch.drop;
    watch.drop = () => {
      w.did.push(`drop:${w.localSignIn.trafficState().withheld ? "withheld" : "open"}`);
      drop();
    };
    const out = await w.box.startFromRow();
    assert.equal(out.ok, true, JSON.stringify(out));
    assert.deepEqual(starts, ["start"], "Start a Gateway Here did nothing");
    assert.ok(w.did.includes("drop:withheld"), `the watch was dropped while the found gateway's session was live: ${w.did.join(", ")}`);
    assert.equal(w.box.adoptedGateway, null);
    assert.equal(w.box.gatewayGone, true);
    assert.ok(w.box.appUpdater.noted.some((n) => n.kind === "none"), "the updater still hears of the found gateway");
  });

  it("ends a refusal when the watch follows the gateway to a start this app may use on another port", async () => {
    const w = counted();
    const main = windowAt(9, "http://127.0.0.1:5000/#/chat");
    w.box.mainWindow = { isDestroyed: () => false, isMinimized: () => false, restore: () => {}, show: () => {}, webContents: main };
    await w.box.adoptHomeGateway({ ok: true, gateway: proven(), ready: ready(5000, TOKEN, { window_door: true }), pair: PAIR });
    w.box.loseGateway();
    await new Promise((r) => setImmediate(r));
    // The record moved to 5001, a start there at 0.3.7: refused, and the main window says so.
    w.gateways.record = { pid: 301, port: 5001 };
    w.gateways.proof = { ok: true, gateway: proven({ pid: 301, port: 5001, digest: "found-start-2", version: "0.3.7" }) };
    await w.tick();
    rowIs(w, "behind", "the moved start at 0.3.7 is refused");
    assert.equal(main.loads.at(-1), "file:loading.html?state=offline");
    // Then a start there at 0.3.8.
    w.gateways.record = { pid: 302, port: 5001 };
    w.gateways.proof = { ok: true, gateway: proven({ pid: 302, port: 5001, digest: "found-start-3", version: "0.3.8" }) };
    await w.tick();
    await new Promise((r) => setImmediate(r));
    rowIs(w, "adopted", "the follow left the refusal on the row");
    assert.equal(w.box.startFailure, null);
    assert.equal(w.box.refusedStart, "");
    assert.ok(w.dialog.includes("closeChooser"), "the chooser stayed open");
    assert.equal(main.loads.at(-1), "http://127.0.0.1:5001", "the main window stayed on the offline page");
  });

  it("ends a refusal when the record comes back to the adopted start's own port with that same start proven", async () => {
    const w = counted();
    const main = windowAt(9, "http://127.0.0.1:5000/#/chat");
    w.box.mainWindow = { isDestroyed: () => false, isMinimized: () => false, restore: () => {}, show: () => {}, webContents: main };
    await w.box.adoptHomeGateway({ ok: true, gateway: proven(), ready: ready(5000, TOKEN, { window_door: true }), pair: PAIR });
    w.box.loseGateway();
    await new Promise((r) => setImmediate(r));
    w.gateways.record = { pid: 301, port: 5001 };
    w.gateways.proof = { ok: true, gateway: proven({ pid: 301, port: 5001, digest: "found-start-2", version: "0.3.7" }) };
    await w.tick();
    rowIs(w, "behind");
    // The record back on 5000, and the start it adopted proven there again.
    w.gateways.record = { pid: 300, port: 5000 };
    w.gateways.proof = { ok: true, gateway: proven() };
    await w.tick();
    await new Promise((r) => setImmediate(r));
    rowIs(w, "adopted", "the same start's lift left the refusal on the row");
    assert.equal(w.box.startFailure, null);
    assert.equal(w.box.refusedStart, "");
    assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "adopted", version: "0.3.8" }));
  });

  it("ends a refusal at a Sign In Again that signs the windows in at a found gateway, so Try Again then starts", async () => {
    const { w } = await refusedWhileInUse();
    // The start at 0.3.8 proven where the windows are, and the owner's Sign In Again there.
    w.box.windowDoorInUse = true;
    w.gateways.record = { pid: 302, port: 5000 };
    w.gateways.proof = { ok: true, gateway: proven({ pid: 302, digest: "found-start-3", version: "0.3.8" }) };
    assert.equal((await w.box.signInAgain()).ok, true);
    assert.equal(w.box.startFailure, null, "the refusal outlived the sign-in");
    assert.equal(w.box.refusedStart, "");
    // The row's start no longer stands back for a refusal that has ended.
    const starts = [];
    w.box.startGateway = async () => {
      starts.push("start");
    };
    await w.box.startFromRow();
    assert.deepEqual(starts, ["start"]);
  });

  it("ends a refusal without moving a main window that shows a paired gateway", async () => {
    const { w, main } = await refusedWhileInUse();
    w.box.activeUrl = "https://gateway.example:8443";
    const loads = main.loads.length;
    await provenAgain(w);
    rowIs(w, "adopted");
    assert.equal(w.box.startFailure, null);
    assert.equal(main.loads.length, loads, `the main window was moved off the paired gateway: ${main.loads.slice(loads).join(", ")}`);
  });

  it("tells the updater the gateway it signs in to again, and clears it for its own gateway, one that stopped and one it refuses", async () => {
    const w = counted();
    w.gateways.proof = { ok: true, gateway: proven({ version: "0.3.9" }) };
    w.box.adoptedGateway = { pid: 300, port: 5000 };
    w.box.windowDoorInUse = true;
    w.box.localGatewayUrl = "http://127.0.0.1:5000";
    assert.equal((await w.box.signInAgain()).ok, true);
    assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "adopted", version: "0.3.9" }));
    w.box.adoptedGone({ pid: 300, port: 5000 });
    assert.equal(JSON.stringify(w.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "none", version: "" }));
    const own = world();
    await own.box.bringUpLocalGateway();
    assert.equal(JSON.stringify(own.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "own", version: "" }));
    await own.box.gatewayLost({ code: 1 });
    assert.equal(JSON.stringify(own.box.appUpdater.noted.at(-1)), JSON.stringify({ kind: "none", version: "" }));
  });

  it("moves the copy an update replaced to the Trash once the first gateway is in use in a run, at the start or later", async () => {
    const w = counted();
    w.gateways.proof = { ok: true, gateway: proven() };
    w.box.appUpdaterStarted = true;
    assert.equal((await w.box.useRunningGateway()).ok, true);
    assert.equal(w.did.filter((d) => d === "trash").length, 1);
    await w.box.adoptHomeGateway({ ok: true, gateway: proven({ pid: 302, digest: "found-start-3" }), ready: ready(5000, NEXT, { window_door: true }), pair: PAIR });
    assert.equal(w.did.filter((d) => d === "trash").length, 1, "trashed twice in a run");
  });
});

describe("Update the Gateway: a gateway the app found, older than itself, updated on a press and then used", () => {
  const OLD = { pid: 300, port: 5000, secretDigest: "found-start-1", version: "0.3.7" };
  const NEW = { pid: 300, port: 5000, secretDigest: "found-start-2", version: "0.3.8" };
  const CHECK = { kind: "pip", current: "0.3.7", latest: "0.3.8", update_available: true, channel: "stable", pin: "" };
  /** A world with a gateway the app found and doesn't use, older than it, on *door* or not. Its
   *  door counts each sign-in and the lifetime asked; the gateway answers the press's requests, and
   *  restarts on 0.3.8 *after* status reads once the update is asked for. */
  const older = ({ door = true, check = CHECK, update = { status: 200, body: JSON.stringify({ status: "updating" }) }, after = 1, comesBack = NEW, progress = "installing" } = {}) => {
    const w = world();
    w.box.gatewayProcess = null;
    w.box.setTimeout = (fn) => (fn(), 0);
    const proven = (g) => ({ ok: true, gateway: { ...g, windowDoor: door } });
    w.gateways.proof = proven(OLD);
    w.gateways.record = { pid: 300, port: 5000 };
    w.minted = [];
    // The door itself, kept for a test that wraps it.
    w.door = async (gateway, _home, options = {}) => {
      const token = `${gateway.secretDigest}-session-${w.minted.length}`;
      const pair = gateway.windowDoor ? `${"p".repeat(42)}${w.minted.length + 1}` : null;
      noteSecret(token);
      if (pair) noteSecret(pair);
      w.minted.push({ token, pair, ttl: options.ttl, start: gateway.secretDigest, door: Boolean(gateway.windowDoor) });
      return { ok: true, ready: ready(gateway.port, token, gateway.windowDoor ? { window_door: true } : {}), pair };
    };
    w.box.homeGateway.signIn = w.door;
    /** Whether *sent* carries a live session this door minted at the start proven now, with its pair. */
    const live = (sent) => {
      const bearer = /^Bearer (.+)$/.exec(sent.headers.Authorization || "");
      const m = bearer && w.minted.find((x) => x.token === bearer[1]);
      return Boolean(m) && !w.ended.includes(m.token) && m.start === w.gateways.proof.gateway.secretDigest && (m.pair || undefined) === sent.headers["X-PersonalClaw-Pair"];
    };
    // A start ends only a session of its own, sent as its door takes one: a Cookie without the door, a
    // Bearer with it. A session of a start that has gone is answered 401.
    w.ended = [];
    // Each sign-out, with the start proven when it came.
    w.logouts = [];
    w.gateways.logout = (headers) => {
      const bearer = /^Bearer (.+)$/.exec(headers.Authorization || "");
      const cookie = /^pc_token_\d+=(.+)$/.exec(headers.Cookie || "");
      const token = (bearer && bearer[1]) || (cookie && cookie[1]) || "";
      w.logouts.push({ token, start: w.gateways.proof && w.gateways.proof.gateway ? w.gateways.proof.gateway.secretDigest : "" });
      const minted = w.minted.find((m) => m.token === token);
      if (minted && minted.start !== w.gateways.proof.gateway.secretDigest) return { status: 401, body: "{}" };
      const door = minted ? minted.door : true;
      const carried = door ? Boolean(bearer) : Boolean(cookie);
      if (carried) w.ended.push(token);
      return { status: 200, body: JSON.stringify({ revoked: carried }) };
    };
    // Status reads counted from the update's POST: the one before it reads the progress as it stands.
    let reads = 0;
    let asked = false;
    w.gateways.ask = (sent) => {
      // The door's sessions only: anything else is answered 401.
      if (!live(sent)) return { status: 401, body: "{}" };
      if (sent.path === "/api/update/check") return { status: 200, body: JSON.stringify(check) };
      if (sent.path === "/api/system/restart?probe=1") return { status: 200, body: JSON.stringify({ ok: true, running_agents: 2, sessions: 1 }) };
      if (sent.path === "/api/update" && sent.method === "POST") {
        asked = true;
        return update;
      }
      if (sent.path === "/api/status") {
        if (!asked) return { status: 200, body: JSON.stringify({ update_progress: null }) };
        reads += 1;
        // The new start is a build with the window's door.
        if (reads >= after && comesBack) w.gateways.proof = { ok: true, gateway: { ...comesBack, windowDoor: true } };
        return { status: 200, body: JSON.stringify({ update_progress: progress ? { step: progress, detail: "" } : null }) };
      }
      return null;
    };
    // The row as the app's refusal leaves it (`setBehind`): the gateway is older, and not used.
    w.box.startFailure = { behind: true };
    w.box.setLocalState("behind", "older");
    return w;
  };
  const credentialed = (w) => w.requests.filter((r) => r.path !== "/api/auth/logout" && r.path !== "/api/healthz");

  it("signs only the shell in, for minutes, plans, confirms and updates, and loads the windows only once a new start is proven at the app's version", async () => {
    for (const door of [true, false]) {
      const w = older({ door });
      const out = await w.box.updateFoundGateway();
      assert.equal(out.ok, true, `door ${door}: ${JSON.stringify(out)}`);
      // Two short sessions for the press, then the use's own sign-in, at the new start.
      assert.deepEqual(w.minted.map((m) => m.ttl), ["5m", "30m", undefined], `door ${door}`);
      assert.equal(w.box.confirms.length, 1);
      assert.equal(w.box.confirms[0].message, "Update the gateway on port 5000 from 0.3.7 to 0.3.8?");
      assert.equal(w.box.confirms[0].detail, "It restarts, which interrupts 2 running agents and 1 live session. PersonalClaw uses it once it's back on 0.3.8.");
      assert.equal(JSON.stringify(w.box.confirms[0].buttons), JSON.stringify(["Update the Gateway", "Cancel"]));
      const keys = credentialed(w).map((r) => `${r.method} ${r.path}`);
      assert.deepEqual(keys.filter((k) => k === "POST /api/update"), ["POST /api/update"], `door ${door}: ${keys.join(", ")}`);
      // Before the confirm, the plan's session; after it, the update's, each with its own pair.
      const sentAt = credentialed(w);
      const afterBox = sentAt.slice(sentAt.findIndex((r) => r.path === "/api/update/check" && r.headers.Authorization === `Bearer ${w.minted[1].token}`));
      assert.ok(afterBox.length > 2, `door ${door}: the update sent nothing over its own session`);
      for (const r of afterBox) {
        assert.equal(r.headers.Authorization, `Bearer ${w.minted[1].token}`, `door ${door}: ${r.method} ${r.path} went without the update's session`);
        assert.equal(r.headers["X-PersonalClaw-Pair"], w.minted[1].pair || undefined, `door ${door}: ${r.method} ${r.path} went without its pair`);
      }
      assert.ok(sentAt.slice(0, sentAt.length - afterBox.length).every((r) => r.headers.Authorization === `Bearer ${w.minted[0].token}`), `door ${door}: the plan went over another session`);
      for (const r of credentialed(w)) {
        assert.ok(gatewayFirst.ALLOWED_REQUESTS.includes(`${r.method} ${r.path}`), `door ${door}: ${r.method} ${r.path} was sent`);
        assert.equal(Object.keys(r.headers).some((h) => h.toLowerCase() === "user-agent"), false, "a User-Agent went");
        assert.equal(r.headers.Cookie, undefined, "a press request carried a cookie");
      }
      // The plan's session was ended at its own start by its door's credential; the update's, at the
      // new start, which no longer knows it.
      assert.deepEqual(w.ended, [w.minted[0].token], `door ${door}`);
      assert.equal(w.box.updateSessions.length, 0);
      // Only the use's session is in the jar, and only after the new start was proven.
      for (const m of w.minted.slice(0, 2)) assert.ok(![...w.cookieJar.cookies.values()].includes(m.token), "a press session reached the jar");
      assert.ok(w.did.indexOf("row:updating") < w.did.lastIndexOf("row:adopted"), w.did.join(", "));
    }
  });

  it("asks nothing of a gateway it can't compare, at its version or without the window's door, and signs nothing in before the compare", async () => {
    for (const [version, door, row] of [["0.3.8", true, "found"], ["0.3.9+local", true, "found"], ["0.3.8", false, "unusable"], ["0.3.9", false, "unusable"], ["0.3.6+local", false, "unusable"]]) {
      const what = `${version}${door ? "" : " without the door"}`;
      const w = older();
      w.gateways.proof = { ok: true, gateway: { ...OLD, version, windowDoor: door } };
      const out = await w.box.updateFoundGateway();
      assert.equal(out.ok, false, what);
      assert.deepEqual(w.minted, [], what);
      assert.deepEqual(w.requests, [], `${what}: a request was sent`);
      // One with the door at its version, or a version it can't compare, is offered with Use It.
      assert.equal(w.box.localState.state, row, what);
    }
    // A stale press, on a row that no longer says the gateway is older, changes nothing.
    for (const state of ["adopted", "own", "found"]) {
      const w = older();
      w.box.setLocalState(state, "as it is");
      const out = await w.box.updateFoundGateway();
      assert.equal(out.ok, false, state);
      assert.equal(w.box.localState.state, state, `${state}: a stale press moved the row`);
      assert.deepEqual(w.minted, [], state);
      assert.equal(w.gateways.reaches, 0, `${state}: a stale press proved the gateway`);
    }
  });

  it("refuses a seventh request and a probe without its query before a socket opens", async () => {
    const w = older();
    const held = { session: { token: "t", pair: null }, port: 5000 };
    const ask = w.box.askOver(held, { pid: 300, port: 5000, secretDigest: "found-start-1" });
    for (const [method, route] of [["GET", "/api/config"], ["POST", "/api/system/restart"], ["POST", "/api/update?force=1"], ["DELETE", "/api/update"]]) {
      assert.equal(await ask(method, route), null, `${method} ${route}`);
    }
    assert.deepEqual(w.requests, []);
    // And nothing goes to a start that isn't the one it was made for.
    w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: false } };
    assert.equal(await ask("GET", "/api/status"), null);
    assert.deepEqual(w.requests, []);
  });

  it("signs out at Cancel, at a hold and at quit, and its sessions never reach the jar, a window, the row or the log", async () => {
    const cancelled = older();
    cancelled.box.confirmAnswer = 1;
    // Cancel is an answer, which the windows show no failure for.
    assert.equal(JSON.stringify(await cancelled.box.updateFoundGateway()), JSON.stringify({ ok: false, cancelled: true }));
    assert.equal(cancelled.box.localState.state, "behind", "Cancel moved the row");
    assert.deepEqual(cancelled.minted.map((m) => m.ttl), ["5m"], "a session was minted after Cancel");
    assert.deepEqual(cancelled.ended, [cancelled.minted[0].token]);
    assert.ok(!credentialed(cancelled).some((r) => r.path === "/api/update"), "Cancel updated the gateway");
    const holds = [
      [{ check: { ...CHECK, kind: "container" } }, "container"],
      [{ update: { status: 409, body: JSON.stringify({ error: "The working tree has local changes." }) }, progress: null }, "refused"],
      [{ comesBack: { ...NEW, version: "0.3.7" } }, "came_back_older"],
    ];
    for (const [over, why] of holds) {
      const w = older(over);
      const out = await w.box.updateFoundGateway();
      assert.equal(out.ok, false, why);
      assert.equal(w.box.localState.state, "behind", why);
      assert.equal(w.box.localState.sentence, out.message, why);
      assert.equal(w.box.updateSessions.length, 0, `${why}: a session was left live`);
      assert.ok(w.ended.includes(w.minted[0].token), why);
    }
    // At quit: a session kept for a start that wasn't proven is ended once one is.
    const quitting = older();
    quitting.box.updateSessions.push({ session: { token: "kept-session", pair: "q".repeat(43) }, port: 5000, start: { pid: OLD.pid, port: 5000, secretDigest: OLD.secretDigest }, until: Date.now() + 60_000, inUse: false, afterRestart: false });
    noteSecret("kept-session");
    await quitting.box.signOutUpdateSessions(1000);
    assert.deepEqual(quitting.ended, ["kept-session"]);
    for (const w of [cancelled, quitting]) {
      for (const m of w.minted) {
        assert.ok(!JSON.stringify(w.box.localState).includes(m.token), "a session reached the row");
        assert.ok(!w.said.some((line) => line.includes(m.token)), "a session reached the log");
        assert.ok(!w.windows.some((win) => win.loads.some((url) => String(url).includes(m.token))), "a session reached a window");
      }
    }
  });

  it("keeps a session whose sign-out wasn't revoked, holds the press before the box, and ends it on a later try", async () => {
    const w = older();
    w.gateways.logout = () => ({ status: 200, body: JSON.stringify({ revoked: false }) });
    const out = await w.box.updateFoundGateway();
    assert.equal(w.box.updateSessions.length, 1, "a 200 that didn't revoke counted as a sign-out");
    // Tried once more before the box; still live, so the box never opens.
    assert.equal(w.box.confirms.length, 0, "the box opened over a live session");
    assert.equal(out.message, gatewayFirst.holdSentence("not_ended", {}));
    assert.equal(w.box.localState.state, "behind");
    w.gateways.logout = (headers) => (w.ended.push(headers.Authorization), { status: 200, body: JSON.stringify({ revoked: true }) });
    await w.box.signOutUpdateSessions(1000);
    assert.equal(w.box.updateSessions.length, 0);
  });

  /** The sign-outs sent for *token*, each `{port, headers}` as it went. */
  const signOutsOf = (w, token) => w.requests.filter((r) => r.path === "/api/auth/logout" && (r.headers.Authorization === `Bearer ${token}` || String(r.headers.Cookie || "").endsWith(`=${token}`)));

  it("ends the update's session only at a new start once the gateway said it restarts, with that start's door's credential", async () => {
    // The gateway answers the POST that it restarts, with nothing to advance; its old start is still proven.
    const w = older({ update: { status: 200, body: JSON.stringify({ status: "restarting", detail: "already at the tag" }) }, comesBack: null });
    const out = await w.box.updateFoundGateway();
    assert.equal(out.ok, false);
    assert.match(out.message, /found nothing to move to 0\.3\.8 \(already at the tag\)/);
    const second = w.minted[1];
    assert.deepEqual(signOutsOf(w, second.token), [], "a sign-out reached the old start after it said it restarts");
    assert.equal(w.box.updateSessions.length, 1, "the update's session was dropped");
    assert.equal(w.box.updateSessions[0].afterRestart, true);
    // Still the old start: nothing goes.
    await w.box.signOutUpdateSessions();
    assert.deepEqual(signOutsOf(w, second.token), []);
    // The new start, with the window's door: the sign-out goes there, as a Bearer with its pair.
    w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
    await w.box.signOutUpdateSessions();
    const sent = signOutsOf(w, second.token);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].headers.Authorization, `Bearer ${second.token}`);
    assert.equal(sent[0].headers["X-PersonalClaw-Pair"], second.pair);
    assert.equal(w.box.updateSessions.length, 0);
  });

  it("tries a kept session again wherever a start is proven on its port, sends nothing for a lapsed one, and ends a door-less start's at a door start with a Bearer", async () => {
    const kept = (token, { minutes = 10, pair = "k".repeat(43), start = OLD } = {}) => {
      noteSecret(token);
      return { session: { token, pair }, port: 5000, start: { pid: start.pid, port: 5000, secretDigest: start.secretDigest }, until: Date.now() + minutes * 60_000, inUse: false, afterRestart: false };
    };
    // Proven by Use It, and by the watch's take-back of a new start.
    for (const [site, prove] of [
      ["Use It", (w) => w.box.useRunningGateway()],
      ["a new start it takes back", (w) => w.box.takeBackAdoptedGateway({ kind: "adopted", pid: 299, port: 5000, secretDigest: "found-start-0" }, w.gateways.proof.gateway)],
      ["a look of the app's start", (w) => {
        w.box.startGateway = async () => Promise.reject(Object.assign(new Error("served"), { exit: { code: 3 } }));
        return w.box.bringUpLocalGateway();
      }],
      ["a move it follows", (w) => w.box.followMovedGateway()],
    ]) {
      const w = older();
      w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
      w.box.updateSessions.push(kept(`kept-at-${site.length}`));
      await prove(w);
      assert.equal(signOutsOf(w, `kept-at-${site.length}`).length, 1, `${site}: the kept session wasn't tried`);
      assert.equal(w.box.updateSessions.length, 0, site);
    }
    // A lapsed one sends nothing and is dropped.
    const lapsed = older();
    lapsed.box.updateSessions.push(kept("lapsed-session", { minutes: -1 }));
    await lapsed.box.signOutUpdateSessions();
    assert.deepEqual(lapsed.requests, []);
    assert.equal(lapsed.box.updateSessions.length, 0);
    // A session kept at a start without the door, ended at one with it: a Bearer, and the fake ends
    // only a Bearer there.
    const w = older({ door: false });
    w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
    w.box.updateSessions.push(kept("doorless-session", { pair: null }));
    await w.box.signOutUpdateSessions();
    const sent = signOutsOf(w, "doorless-session");
    assert.equal(sent.length, 1);
    assert.equal(sent[0].headers.Authorization, "Bearer doorless-session");
    assert.equal(sent[0].headers.Cookie, undefined);
    assert.deepEqual(w.ended, ["doorless-session"]);
  });

  it("leaves the press's row to the press while the watch looks, and doesn't use a start the watch took back", async () => {
    const w = older();
    // In use at 0.3.8, then lost, and its next start on the port refused at 0.3.7 by the watch.
    w.gateways.proof = { ok: true, gateway: { pid: 299, port: 5000, secretDigest: "found-start-0", version: "0.3.8", windowDoor: true } };
    await w.box.adoptHomeGateway({ ok: true, gateway: w.gateways.proof.gateway, ready: ready(5000, "in-use-session", { window_door: true }), pair: PAIR });
    w.box.loseGateway();
    await new Promise((r) => setImmediate(r));
    w.gateways.proof = { ok: true, gateway: { ...OLD, windowDoor: true } };
    await w.tick();
    assert.equal(w.box.localState.state, "behind");
    // The watch looks at every wait of the press.
    w.box.setTimeout = (fn, ms) => {
      if (ms === gatewayFirst.POLL_MS) w.tick().then(fn);
      else fn();
      return 0;
    };
    const mark = w.did.length;
    const out = await w.box.updateFoundGateway();
    const rows = w.did.slice(mark).filter((d) => d.startsWith("row:"));
    assert.ok(rows.includes("row:updating"), rows.join(", "));
    assert.ok(!rows.slice(rows.indexOf("row:updating")).includes("row:behind"), `the watch wrote over the press's row: ${rows.join(", ")}`);
    assert.equal(w.box.localState.state, "adopted");
    // The watch took the new start back first: the press didn't sign in there a second time.
    assert.equal(JSON.stringify(out), JSON.stringify({ ok: true, inUse: true }));
    assert.equal(w.minted.filter((m) => m.start === NEW.secretDigest).length, 1, JSON.stringify(w.minted.map((m) => m.start)));
  });

  it("holds on an error partway through, ends what it holds there, and every transport settles on a close", async () => {
    const w = older();
    const real = w.box.homeGateway.reach;
    let after = 0;
    w.box.homeGateway.reach = async (...args) => {
      if (w.requests.some((r) => r.path === "/api/update" && r.method === "POST") && ++after === 2) throw new Error("the proof broke");
      return real(...args);
    };
    const out = await w.box.updateFoundGateway();
    assert.equal(out.ok, false);
    assert.equal(out.message, gatewayFirst.holdSentence("error", {}));
    assert.equal(w.box.localState.state, "behind");
    assert.equal(w.box.updateSessions.length, 0, "a session was left live after the error");
    assert.ok(w.ended.includes(w.minted[1].token), "the update's session wasn't ended");
    assert.ok(w.said.some((line) => line.includes("Update the Gateway stopped on an error")));
    // An answer that closes without an end still settles each transport.
    const c = world();
    c.gateways.answers = {
      "/api/auth/logout": { status: 200, body: '{"revoked":true}', closeOnly: true },
      "/api/status": { status: 200, body: "{}", closeOnly: true },
    };
    assert.equal(await c.box.signOutAtPort({ token: "closing-session", pair: null }, 5000, { windowDoor: true }), true);
    noteSecret("closing-session");
    const answer = await c.box.sendToFoundGateway({ session: { token: "closing-session", pair: null }, port: 5000 }, "GET", "/api/status", 1000);
    assert.equal(answer.status, 200);
  });

  it("joins a second press to the first: one confirm, and one pair of sessions", async () => {
    const w = older();
    const first = w.box.updateFoundGateway();
    const second = w.box.updateFoundGateway();
    assert.equal(first, second, "a second press started its own");
    assert.equal((await first).ok, true);
    assert.equal(w.box.confirms.length, 1);
    assert.deepEqual(w.minted.map((m) => m.ttl), ["5m", "30m", undefined]);
  });

  it("says a new start's version as it has a version's shape, in the row and the log", async () => {
    const w = older({ comesBack: { ...NEW, version: "0.3.9<script>" } });
    const out = await w.box.updateFoundGateway();
    assert.equal(out.ok, false);
    assert.equal(w.box.localState.state, "behind");
    assert.ok(w.box.localState.sentence.includes(gatewayFirst.UNREADABLE_VERSION), w.box.localState.sentence);
    assert.ok(!w.box.localState.sentence.includes("<script>"));
    assert.ok(!w.said.some((line) => line.includes("<script>")), "an unread version reached the log");
  });

  it("holds signed_out when the gateway refuses the plan's session, and ends no sign-in of the windows", async () => {
    const w = older();
    // The windows hold a session of their own, which a refusal read as theirs would end.
    await w.localSignIn.adopt(ready(5000, "window-held-session", { window_door: true }), { pair: PAIR });
    noteSecret("window-held-session");
    w.gateways.ask = () => ({ status: 401, body: "{}" });
    const out = await w.box.updateFoundGateway();
    assert.equal(out.message, gatewayFirst.holdSentence("signed_out", {}));
    assert.equal(w.box.localState.state, "behind");
    assert.equal(w.box.localSignIn.signedOut, false, "the press's refusal ended the windows' sign-in");
    assert.equal(w.localSignIn.stamp().token, "window-held-session", "the windows' session changed");
    // The restart's count refused the same way: the box never opens.
    const probe = older();
    const asked = probe.gateways.ask;
    probe.gateways.ask = (sent) => (sent.path === "/api/system/restart?probe=1" ? { status: 403, body: "{}" } : asked(sent));
    const held = await probe.box.updateFoundGateway();
    assert.equal(held.message, gatewayFirst.holdSentence("signed_out", {}));
    assert.equal(probe.box.confirms.length, 0, "the box opened without the restart's count");
    assert.ok(probe.ended.includes(probe.minted[0].token), "the plan's session was left live");
  });

  /** In use at 0.3.8 (pid 300), then lost, and its next start on the port refused at 0.3.7 by the
   *  watch: the row says behind, and the watch keeps looking. */
  const inUseThenRefused = async (w) => {
    const inUse = { pid: 300, port: 5000, secretDigest: "found-start-0", version: "0.3.8", windowDoor: true };
    w.gateways.proof = { ok: true, gateway: inUse };
    await w.box.adoptHomeGateway({ ok: true, gateway: inUse, ready: ready(5000, "in-use-session", { window_door: true }), pair: PAIR });
    noteSecret("in-use-session");
    w.box.loseGateway();
    await new Promise((r) => setImmediate(r));
    w.gateways.proof = { ok: true, gateway: { ...OLD, windowDoor: true } };
    await w.tick();
    assert.equal(w.box.localState.state, "behind");
    assert.equal(w.box.startFailure.behind, true);
  };
  /** Run *step* at the press's *n*th wait (its sleeps), then go on. */
  const atWait = (w, n, step) => {
    let waits = 0;
    w.box.setTimeout = (fn, ms) => {
      if (ms === gatewayFirst.POLL_MS && ++waits === n) Promise.resolve(step()).then(fn);
      else fn();
      return 0;
    };
  };

  it("keeps the update's session from the old start from the first sign it restarts: a quit or an error then signs nothing out there", async () => {
    // The gateway says it restarts; its old start is still proven.
    for (const end of ["quit", "error"]) {
      const w = older({ progress: "restarting", comesBack: null });
      if (end === "quit") {
        // The quit's own sign-out while the press waits, then the press stops.
        atWait(w, 2, async () => {
          await w.box.signOutUpdateSessions(1000, { all: true });
          w.box.isQuitting = true;
        });
      } else {
        // The first proof after a status read that said it restarts throws.
        const real = w.box.homeGateway.reach;
        let thrown = false;
        w.box.homeGateway.reach = async (...args) => {
          const reads = w.requests.filter((r) => r.path === "/api/status" && r.method === "GET").length;
          if (!thrown && reads >= 2) {
            thrown = true;
            throw new Error("the proof broke");
          }
          return real(...args);
        };
      }
      const out = await w.box.updateFoundGateway();
      assert.equal(out.ok, false, end);
      const second = w.minted[1];
      assert.deepEqual(w.logouts.filter((l) => l.token === second.token && l.start === OLD.secretDigest), [], `${end}: the update's session was signed out at the old start after it said it restarts`);
      assert.equal(w.box.updateSessions.length, 1, `${end}: the update's session wasn't kept`);
      // Once a new start is proven, the sign-out goes there.
      w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
      await w.box.signOutUpdateSessions();
      assert.equal(w.logouts.filter((l) => l.token === second.token && l.start === NEW.secretDigest).length, 1, `${end}: not signed out at the new start`);
    }
  });

  it("tries a kept session around a site's proof, never between it and the sign-in or the held session's read it carries", async () => {
    const kept = () => {
      noteSecret("kept-around");
      return { session: { token: "kept-around", pair: "k".repeat(43) }, port: 5000, start: { pid: OLD.pid, port: 5000, secretDigest: OLD.secretDigest }, until: Date.now() + 600_000, inUse: false, afterRestart: false };
    };
    /** The order of proofs and sign-ins; the retry's proof goes silent, as a gateway gone quiet does. */
    const traced = (w, silentAt) => {
      w.trace = [];
      const real = w.box.homeGateway.reach;
      let n = 0;
      w.box.homeGateway.reach = async (...args) => {
        n += 1;
        w.trace.push("proof");
        return n === silentAt ? { ok: false, reason: "silent", sentence: "silent" } : real(...args);
      };
      w.box.homeGateway.signIn = async (...args) => (w.trace.push("sign-in"), w.door(...args));
    };
    // Use It and the follow: the retry first (its proof silent), then the site's own proof and sign-in.
    for (const [site, run] of [["Use It", (w) => w.box.useRunningGateway()], ["a move it follows", (w) => w.box.followMovedGateway()]]) {
      const w = older();
      w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
      w.box.updateSessions.push(kept());
      traced(w, 1);
      await run(w);
      const at = w.trace.indexOf("sign-in");
      assert.ok(at > 0, `${site}: no sign-in after a silent retry: ${w.trace.join(", ")}`);
      assert.equal(w.trace[at - 1], "proof", `${site}: ${w.trace.join(", ")}`);
      assert.equal(w.trace.slice(0, at).length, 2, `${site}: something came between the proof and the sign-in: ${w.trace.join(", ")}`);
    }
    // The take-back: its own sign-in on the watch's proof, and the retry after it.
    const w = older();
    w.gateways.proof = { ok: true, gateway: { ...NEW, windowDoor: true } };
    w.box.updateSessions.push(kept());
    traced(w, 0);
    assert.equal(await w.box.takeBackAdoptedGateway({ kind: "adopted", pid: 299, port: 5000, secretDigest: "found-start-0" }, w.gateways.proof.gateway), true);
    assert.deepEqual(w.trace.slice(0, 2), ["sign-in", "proof"], `the take-back's retry came before its sign-in: ${w.trace.join(", ")}`);
  });

  it("leaves a row the watch settled while the press ran: in use again after the box, stopped once the gateway is gone", async () => {
    // The watch takes a start it may use back while the box is open; the press then holds plan_changed.
    const w = older();
    await inUseThenRefused(w);
    w.box.require = () => ({
      dialog: {
        showMessageBox: async (options) => {
          w.box.confirms.push(options);
          w.gateways.record = { pid: 301, port: 5000 };
          w.gateways.proof = { ok: true, gateway: { pid: 301, port: 5000, secretDigest: "found-start-3", version: "0.3.8", windowDoor: true } };
          await w.tick();
          await new Promise((r) => setImmediate(r));
          return { response: 0 };
        },
      },
    });
    const out = await w.box.updateFoundGateway();
    assert.equal(out.message, gatewayFirst.holdSentence("plan_changed", {}));
    assert.equal(w.box.localState.state, "adopted", "the hold wrote over the gateway in use");
    // The gateway's pid dies while the press waits, with the watch armed: the row says it stopped.
    const g = older();
    await inUseThenRefused(g);
    atWait(g, 1, async () => {
      g.gateways.deadPid = 300;
      g.gateways.record = null;
      g.gateways.proof = { ok: false, reason: "none" };
      await g.tick();
    });
    const gone = await g.box.updateFoundGateway();
    assert.equal(gone.ok, false);
    assert.equal(g.box.localState.state, "stopped", `the hold wrote over a stopped row: ${g.box.localState.state}`);
    assert.equal(JSON.stringify(g.box.startFailure), JSON.stringify({ gone: true }));
  });

  it("ends a gone hold the way it ends a refusal once the watch puts a start it may use in use, and never writes one over a gateway in use", async () => {
    // The take-back of a new start on 5000, the follow to one on 5001, and the same start proven again.
    const ways = {
      "a take-back": { record: { pid: 301, port: 5000 }, gateway: { pid: 301, port: 5000, secretDigest: "found-start-3", version: "0.3.8", windowDoor: true }, url: "http://127.0.0.1:5000" },
      "a follow": { record: { pid: 302, port: 5001 }, gateway: { pid: 302, port: 5001, secretDigest: "found-start-4", version: "0.3.8", windowDoor: true }, url: "http://127.0.0.1:5001" },
      "the same start": { record: { pid: 300, port: 5000 }, gateway: { pid: 300, port: 5000, secretDigest: "found-start-0", version: "0.3.8", windowDoor: true }, url: "http://127.0.0.1:5000" },
    };
    for (const [way, back] of Object.entries(ways)) {
      const w = older();
      const main = windowAt(9, "http://127.0.0.1:5000/#/chat");
      w.box.mainWindow = { isDestroyed: () => false, isMinimized: () => false, restore: () => {}, show: () => {}, webContents: main };
      await inUseThenRefused(w);
      // The pid dies at the press's first wait, with no record, no proof and no look of the watch.
      atWait(w, 1, () => {
        w.gateways.deadPid = 300;
        w.gateways.record = null;
        w.gateways.proof = { ok: false, reason: "none" };
      });
      const gone = await w.box.updateFoundGateway();
      assert.equal(gone.ok, false, way);
      rowIs(w, "stopped", `${way}: the press didn't hold gone`);
      w.gateways.deadPid = null;
      w.gateways.record = back.record;
      w.gateways.proof = { ok: true, gateway: back.gateway };
      await w.tick();
      await new Promise((r) => setImmediate(r));
      rowIs(w, "adopted", `${way} left the gone hold on the row`);
      assert.equal(w.box.startFailure, null, `${way}: the gone hold is still the start's failure`);
      assert.equal(w.box.refusedStart, "", way);
      assert.equal(main.loads.at(-1), back.url, `${way}: the main window stayed where the hold left it`);
    }
    // A gone hold said once the watch has a gateway in use again leaves the row and the failure.
    const w = older();
    await inUseThenRefused(w);
    w.gateways.record = ways["a take-back"].record;
    w.gateways.proof = { ok: true, gateway: ways["a take-back"].gateway };
    await w.tick();
    await new Promise((r) => setImmediate(r));
    rowIs(w, "adopted");
    assert.equal(w.box.startFailure, null);
    w.box.holdUpdate({ pid: 300, port: 5000 }, "gone", {});
    rowIs(w, "adopted", "the gone hold wrote over the gateway in use");
    assert.equal(w.box.startFailure, null);
  });

  it("keeps a hold's sentence on the row when the watch looks at the start it refused again", async () => {
    const w = older({ check: { ...CHECK, pin: "0.3.7" } });
    await inUseThenRefused(w);
    const held = await w.box.updateFoundGateway();
    assert.match(held.message, /pinned to 0\.3\.7, not 0\.3\.8/);
    rowIs(w, "behind");
    assert.equal(w.box.localState.sentence, held.message);
    await w.tick();
    rowIs(w, "behind");
    assert.equal(w.box.localState.sentence, held.message, "the watch's look wrote the plain sentence over the hold");
  });

  it("leaves the press's row alone when the watch's follow or take-back refuses a start while it runs", async () => {
    // The follow: the record moves to 5001, a start at 0.3.7 there.
    const w = older();
    await inUseThenRefused(w);
    let refusedThere = "";
    atWait(w, 1, async () => {
      const proof = w.gateways.proof;
      w.gateways.record = { pid: 302, port: 5001 };
      w.gateways.proof = { ok: true, gateway: { pid: 302, port: 5001, secretDigest: "found-start-4", version: "0.3.7", windowDoor: true } };
      await w.tick();
      refusedThere = w.box.refusedStart;
      w.gateways.record = { pid: 300, port: 5000 };
      w.gateways.proof = proof;
    });
    const mark = w.did.length;
    await w.box.updateFoundGateway();
    assert.match(refusedThere, /found-start-4/, "the follow didn't run");
    const rows = w.did.slice(mark).filter((d) => d.startsWith("row:"));
    assert.ok(rows.includes("row:updating"), rows.join(", "));
    assert.ok(!rows.slice(rows.indexOf("row:updating"), rows.length - 1).some((r) => r === "row:behind" || r === "row:unusable"), `the follow wrote over the press's row: ${rows.join(", ")}`);
    // The follow to a start at 0.3.8 with the door on 5001, whose sign-in comes back pairless.
    const f = older();
    await inUseThenRefused(f);
    let pairless = 0;
    atWait(f, 1, async () => {
      const proof = f.gateways.proof;
      const signIn = f.box.homeGateway.signIn;
      f.gateways.record = { pid: 304, port: 5001 };
      f.gateways.proof = { ok: true, gateway: { pid: 304, port: 5001, secretDigest: "found-start-6", version: "0.3.8", windowDoor: true } };
      f.box.homeGateway.signIn = async (gateway, home, options = {}) => {
        if (options.ttl) return f.door(gateway, home, options);
        pairless += 1;
        return { ok: true, ready: ready(5001, "pairless-follow-session"), pair: null };
      };
      noteSecret("pairless-follow-session");
      await f.tick();
      f.box.homeGateway.signIn = signIn;
      f.gateways.record = { pid: 300, port: 5000 };
      f.gateways.proof = proof;
    });
    const fmark = f.did.length;
    await f.box.updateFoundGateway();
    assert.equal(pairless, 1, "the follow didn't ask the moved start's door");
    const frows = f.did.slice(fmark).filter((d) => d.startsWith("row:"));
    assert.ok(frows.includes("row:updating"), frows.join(", "));
    assert.ok(!frows.slice(frows.indexOf("row:updating")).includes("row:unusable"), `the pairless follow wrote over the press's row: ${frows.join(", ")}`);
    // The take-back of a new start whose sign-in comes back without its pair.
    const t = older();
    await inUseThenRefused(t);
    atWait(t, 1, async () => {
      t.gateways.record = { pid: 303, port: 5000 };
      t.gateways.proof = { ok: true, gateway: { pid: 303, port: 5000, secretDigest: "found-start-5", version: "0.3.8", windowDoor: true } };
      t.box.homeGateway.signIn = async (gateway, home, options = {}) => (options.ttl ? t.door(gateway, home, options) : { ok: true, ready: ready(5000, "pairless-session"), pair: null });
      noteSecret("pairless-session");
      await t.tick();
      t.box.isQuitting = true;
    });
    const tmark = t.did.length;
    await t.box.updateFoundGateway();
    const trows = t.did.slice(tmark).filter((d) => d.startsWith("row:"));
    assert.ok(trows.includes("row:updating"), trows.join(", "));
    assert.ok(!trows.slice(trows.indexOf("row:updating")).includes("row:unusable"), `the pairless take-back wrote over the press's row: ${trows.join(", ")}`);
  });

  it("opens the box when the plan's session is ended on the try before it, and keeps nothing", async () => {
    const w = older();
    let first = true;
    const logout = w.gateways.logout;
    // The first sign-out answers without a revoke; the one before the box ends it.
    w.gateways.logout = (headers) => {
      if (first) {
        first = false;
        return { status: 200, body: JSON.stringify({ revoked: false }) };
      }
      return logout(headers);
    };
    const out = await w.box.updateFoundGateway();
    assert.equal(out.ok, true, JSON.stringify(out));
    assert.equal(w.box.confirms.length, 1);
    assert.equal(w.box.updateSessions.length, 0, "a session was kept");
  });
});

describe("a refusal of the shell's own request", () => {
  it("ends a sign-in that has a pair, once, and never one a later rotation replaced", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    await w.localSignIn.adopt(ready(51234, WINDOW), { pair: PAIR });
    w.holds("after adopt");
    const stale = { token: TOKEN, gen: 0 };
    w.box.noteShellRefusal(stale, 403, JSON.stringify({ error: { code: "session_signed_out" } }));
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    assert.equal(w.localSignIn.signedOut, false, "a read sent before the rotation ended the rotated session");
    const now = w.localSignIn.stamp();
    for (let i = 0; i < 3; i++) w.box.noteShellRefusal(now, 403, JSON.stringify({ error: { code: "session_signed_out" } }));
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    assert.equal(w.localSignIn.signedOut, true);
    assert.equal(w.did.filter((d) => d === "row:signed_out").length, 1, "three refusals counted as three");
    assert.equal(w.box.credentialsWithheld(), true, "the shell still carries an ended sign-in");
  });

  it("changes nothing on a gateway that gives no pair, or for a pair that only didn't arrive", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, JSON.stringify({ error: { code: "session_signed_out" } }));
    await w.localSignIn.adopt(ready(51234, WINDOW), { pair: PAIR });
    w.holds("after adopt");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, JSON.stringify({ error: { code: "session_required", detail: { reason: "pair_missing" } } }));
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    assert.equal(w.localSignIn.signedOut, false);
    assert.deepEqual(w.did.filter((d) => d.startsWith("row:")), []);
  });
});

describe("the window's door of the app's own gateway", () => {
  it("signs the windows in with a session of their own and its pair, and ends the ready session, never letting the ready one into the jar", async () => {
    const w = world();
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: true } };
    w.gateways.signIn = { ok: true, ready: ready(51234, WINDOW, { window_door: true }), pair: PAIR };
    const asked = [];
    w.box.homeGateway.signIn = async (_gateway, _home, options) => (asked.push(options), w.gateways.signIn);
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    assert.equal(JSON.stringify(asked), JSON.stringify([{ ttl: null }]), "the own gateway's door was asked for a lifetime");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), WINDOW);
    assert.ok(w.did.includes(`signout:${TOKEN}`), "the ready session is not signed out");
    assert.equal(w.localSignIn.held.pair, PAIR);
    assert.equal(w.localSignIn.withheld, false);
  });

  it("keeps the windows out, with no cookie, when the proven answer says there is no door, or the door gives no pair", async () => {
    for (const [proofDoor, signIn] of [
      [false, { ok: true, ready: ready(51234, WINDOW), pair: null }],
      [true, { ok: false, pairless: true, sentence: "no pair" }],
    ]) {
      const w = world();
      w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: proofDoor } };
      w.gateways.signIn = signIn;
      await ownStart(w, 51234, TOKEN, { window_door: true });
      w.holds("a failed ask");
      assert.equal(w.cookieJar.cookies.size, 0, "an unbound token reached the jar");
      assert.equal(w.localSignIn.withheld, true);
    }
  });

  it("asks only through a proof whose pid is the child's", async () => {
    const w = world();
    w.gateways.proof = { ok: true, gateway: { pid: 9999, port: 51234, secretDigest: "x", windowDoor: true } };
    let asked = 0;
    w.box.homeGateway.signIn = async () => {
      asked += 1;
      return { ok: true, ready: ready(51234, WINDOW), pair: PAIR };
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    assert.equal(asked, 0, "the door was asked of a gateway whose pid isn't the child's");
    assert.equal(w.cookieJar.cookies.size, 0);
  });

  it("on a gateway without the door, signs the windows in with the ready session exactly as today", async () => {
    const w = world();
    let reached = 0;
    w.box.homeGateway.reach = async () => {
      reached += 1;
      return { ok: false };
    };
    await ownStart(w);
    w.holds("after ownStart");
    assert.equal(reached, 0, "a proof was asked of a gateway with no door");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN);
  });
});

describe("windows during a withhold", () => {
  it("a window parked by a cancelled navigation goes back to its route once the same start is back", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    const [win] = w.windows;
    w.box.loadOrPark(win, "http://127.0.0.1:51234/#/chat/abc");
    w.holds("after loadOrPark");
    assert.deepEqual(win.loads, ["file:loading.html?state=offline"], "a window loaded the gateway while withheld");
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1" } };
    await w.tick();
    w.holds("after tick");
    await new Promise((r) => setImmediate(r));
    w.holds("after a step");
    assert.equal(win.loads.at(-1), "http://127.0.0.1:51234/#/chat/abc");
  });
});

describe("Sign In Again: offered only on a gateway with the window's door", () => {
  const AGAIN = "window-session-again";
  const refused = (code, reason) => JSON.stringify({ error: { code, ...(reason ? { detail: { reason } } : {}) } });
  const REFUSALS = [
    refused("auth_bearer_invalid"),
    refused("session_signed_out"),
    refused("session_expired"),
    refused("session_required", "pair_wrong"),
    refused("session_required", "unbound_cookie"),
    refused("session_required", "pair_missing"),
  ];
  const settle = () => new Promise((r) => setImmediate(r));

  it("a flagless own gateway's This computer row has no Sign In Again in any state", async () => {
    for (const state of LOCAL_STATES) {
      const action = describeLocal({ state }).action;
      assert.equal(Boolean(action) && action.id === "sign-in", state === "signed_out", `the ${state} row`);
    }
    const w = world();
    const seen = new Set();
    const everyRefusal = async (step) => {
      for (const body of REFUSALS) {
        w.box.noteShellRefusal(w.localSignIn.stamp(), 403, body);
        await settle();
        w.holds("after settle");
        seen.add(w.box.localState.state);
        w.holds(`${step}, ${body}`);
        const action = describeLocal(w.box.localState).action;
        assert.notEqual(action && action.id, "sign-in", `${step}, refused with ${body}: Sign In Again on a gateway without the door`);
      }
    };
    await ownStart(w);
    w.holds("after ownStart");
    await everyRefusal("started");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    await everyRefusal("lost");
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: false } };
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    await everyRefusal("the same start back");
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    w.holds("after takeReadyLine");
    await everyRefusal("restarted");
    await w.box.gatewayLost({ code: 1 });
    w.holds("after gatewayLost");
    await settle();
    w.holds("after settle");
    await everyRefusal("exited");
    assert.equal(w.localSignIn.signedOut, false);
    assert.ok(!w.did.includes("row:signed_out"), w.did.join(", "));
    assert.deepEqual([...seen].sort(), ["own", "stopped"], "the walk missed a state the row takes");
  });

  it("refuses a press on a gateway without the door, logged as a defect, before anything is asked", async () => {
    const w = world();
    const warned = [];
    w.box.console.warn = (msg) => warned.push(msg);
    let reached = 0;
    let asked = 0;
    w.box.homeGateway.reach = async () => ((reached += 1), w.gateways.proof);
    w.box.homeGateway.signIn = async () => ((asked += 1), { ok: true, ready: ready(51234, AGAIN), pair: null });
    await ownStart(w);
    w.holds("after ownStart");
    w.box.localState = { state: "signed_out", sentence: "" };
    for (const windowDoor of [false, true]) {
      w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor } };
      const out = await w.box.signInAgain();
      w.holds(`a refused press (proven door: ${windowDoor})`);
      assert.equal(out.ok, false, `signed in again on a held sign-in without the door (proven door: ${windowDoor})`);
      assert.equal(out.message, w.box.NOT_SIGNED_IN_AGAIN_SENTENCE);
    }
    assert.equal(reached + asked, 0, "the gateway was asked anything for a press the row can't offer");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN, "the jar changed");
    assert.equal(warned.filter((msg) => msg.includes("a defect in this app")).length, 2, warned.join("\n"));
  });

  it("signs the own gateway in again at its door, with no lifetime asked; a proven answer without the door is refused", async () => {
    const w = world();
    w.gateways.proof = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: true } };
    w.gateways.signIn = { ok: true, ready: ready(51234, WINDOW, { window_door: true }), pair: PAIR };
    const asked = [];
    w.box.homeGateway.signIn = async (_gateway, _home, options) => (asked.push(options), w.gateways.signIn);
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("started");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("signed out");
    rowIs(w, "signed_out");
    w.gateways.proof = { ok: true, gateway: { ...w.gateways.proof.gateway, windowDoor: false } };
    assert.equal((await w.box.signInAgain()).ok, false);
    w.holds("a press the proven answer refuses");
    assert.equal(asked.length, 1, "a door was asked of a gateway whose proven answer has none");
    w.gateways.proof = { ok: true, gateway: { ...w.gateways.proof.gateway, windowDoor: true } };
    w.gateways.signIn = { ok: true, ready: ready(51234, AGAIN, { window_door: true }), pair: "pair-again" };
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("signed in again");
    assert.equal(JSON.stringify(asked), JSON.stringify([{ ttl: null }, { ttl: null }]));
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), AGAIN);
    assert.equal(w.localSignIn.held.pair, "pair-again");
    assert.equal(w.box.localState.state, "own");
  });

  it("a gateway the app found is signed in again at its door for the door's 30 days, and a new start is held with its own door's flag", async () => {
    const w = world();
    w.box.gatewayProcess = null;
    await w.box.adoptHomeGateway({ ok: true, gateway: { pid: 300, port: 5000, secretDigest: "adopted-start-1", windowDoor: true }, ready: ready(5000, TOKEN, { window_door: true }), pair: PAIR });
    w.holds("after adoptHomeGateway");
    assert.equal(w.box.windowDoorInUse, true);
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    w.gateways.proof = { ok: true, gateway: { pid: 301, port: 5000, secretDigest: "adopted-start-2", windowDoor: true } };
    w.gateways.signIn = { ok: true, ready: ready(5000, WINDOW, { window_door: true }), pair: PAIR };
    const asked = [];
    w.box.homeGateway.signIn = async (_gateway, _home, options) => (asked.push(options), w.gateways.signIn);
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("a new start with the door");
    assert.equal(w.box.windowDoorInUse, true, "a new start with the door is held without its door's flag");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_expired"));
    await settle();
    w.holds("after settle");
    rowIs(w, "signed_out");
    w.gateways.signIn = { ok: true, ready: ready(5000, AGAIN, { window_door: true }), pair: "pair-again" };
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("the found gateway signed in again");
    assert.equal(JSON.stringify(asked), JSON.stringify([undefined, {}]), "the found gateway's asks");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), AGAIN);
    assert.equal(w.box.localState.state, "adopted");
  });
});

describe("the window's door after failed asks: the shell's own hold", () => {
  const settle = async (times = 6) => {
    for (let i = 0; i < times; i++) await new Promise((r) => setImmediate(r));
  };
  const DOOR = { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "digest-of-start-1", windowDoor: true } };
  const OPENED = () => ({ ok: true, ready: ready(51234, WINDOW, { window_door: true }), pair: PAIR });
  const loopbackCookies = (w) => [...w.cookieJar.cookies.keys()].filter((name) => name.startsWith("pc_token_"));
  /** A door that fails, and loses the gateway while its first ask is out. */
  const doorLosingAtFirst = (w, then = () => ({ ok: false, sentence: "the door gave nothing" })) => {
    const asked = [];
    w.box.homeGateway.signIn = async () => {
      asked.push(1);
      if (asked.length === 1) {
        w.box.loseGateway();
        w.holds("after loseGateway");
        return { ok: false, sentence: "the gateway went away" };
      }
      return then();
    };
    return asked;
  };

  it("a same-start proof after a failed ask lifts the shell's own credentials only: no window cookie, the windows withheld", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = { ok: false, sentence: "the door gave nothing" };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("the same start proven back");
    assert.deepEqual(loopbackCookies(w), [], "the ready line's unbound session reached the jar");
    assert.equal(w.localSignIn.withheld, true);
    assert.equal(w.box.credentialsWithheld(), false, "the shell's own credentials stayed withheld");
  });

  it("asks the door again once the shell is back, within the start's three asks, and lets the windows in", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    const asked = doorLosingAtFirst(w, OPENED);
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after settle");
    assert.equal(asked.length, 1, "the asks went on through the loss");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(asked.length, 2, "the door was not asked again once the same start was back");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), WINDOW);
    assert.equal(w.localSignIn.withheld, false);
    assert.ok(w.did.includes(`signout:${TOKEN}`), "the ready session is not signed out");
  });

  it("asks at most three times a start, across a loss, and the row then says signed out, with Sign In Again", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    const asked = doorLosingAtFirst(w);
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after settle");
    assert.notEqual(w.box.localState.state, "signed_out", "signed out with asks left");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(asked.length, 3, "the start's asks");
    rowIs(w, "signed_out");
    assert.equal(describeLocal(w.box.localState).action.id, "sign-in");
    assert.deepEqual(loopbackCookies(w), []);
    assert.equal(w.box.credentialsWithheld(), false, "the shell's own credentials");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(asked.length, 3, "a second proof asked past the start's three");
    assert.deepEqual(loopbackCookies(w), []);
  });

  it("Sign In Again after the last ask signs the windows in at the door, signs the ready session out and brings parked windows back", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = { ok: false, sentence: "the door gave nothing" };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    rowIs(w, "signed_out");
    w.box.parked.set(1, "http://127.0.0.1:51234/#/chat/abc");
    w.gateways.signIn = OPENED();
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("after signInAgain");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), WINDOW);
    assert.ok(w.did.includes(`signout:${TOKEN}`), "the ready session is not signed out");
    assert.equal(w.box.localState.state, "own");
    assert.deepEqual(w.windows[0].loads, ["http://127.0.0.1:51234/#/chat/abc"], "the parked window stayed parked");
  });

  it("a new start whose door gives the windows a session takes the row back from signed out", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = { ok: false, sentence: "the door gave nothing" };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    rowIs(w, "signed_out");
    w.gateways.signIn = OPENED();
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(51234, NEXT, { window_door: true }), true);
    w.holds("after takeReadyLine");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), WINDOW);
    assert.equal(w.box.localState.state, "own");
  });
});

describe("a gateway's start, the asks at its door, the doorbell's bound and an End", () => {
  const settle = async (times = 10) => {
    for (let i = 0; i < times; i++) await new Promise((r) => setImmediate(r));
  };
  const refused = (code, reason) => JSON.stringify({ error: { code, ...(reason ? { detail: { reason } } : {}) } });
  const AGAIN = "window-session-again";
  const FOUND = (pid, port, secretDigest, door = false) => ({ ok: true, gateway: { pid, port, secretDigest, ...(door ? { windowDoor: true } : {}) } });
  const DOOR = FOUND(700, 51234, "digest-of-start-1", true);
  const OPENED = (token = WINDOW, pair = PAIR) => ({ ok: true, ready: ready(51234, token, { window_door: true }), pair });
  const loopbackCookies = (w) => [...w.cookieJar.cookies.keys()].filter((name) => name.startsWith("pc_token_"));
  /** A gateway the app found, used: *door* says whether it has the window's door. */
  async function foundStart(w, { pid = 300, port = 5000, digest = "adopted-start-1", door = true, token = WINDOW, pair = PAIR } = {}) {
    w.box.gatewayProcess = null;
    w.gateways.record = { pid, port };
    await w.box.adoptHomeGateway({ ...FOUND(pid, port, digest, door), ready: ready(port, token, door ? { window_door: true } : {}), pair });
    w.holds("after adoptHomeGateway");
  }

  it("a found gateway's second start, lost and proven again, is the same start: nothing is minted, reloaded or told", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.holds("after foundStart");
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), { ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: PAIR });
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(asked, 1);
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), NEXT);
    const door = w.box.windowDoorInUse;
    w.windows[0].url = "http://127.0.0.1:5000/#/chat/abc";
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("the second start lost");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("the second start proven again");
    assert.equal(asked, 1, "the second start was signed in to afresh");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), NEXT, "the same session did not come back");
    assert.deepEqual(w.windows[0].loads, [], "a page reloaded");
    assert.deepEqual(w.windows[0].scripts, [], "a page was told its pair changed for the same start");
    assert.equal(w.box.windowDoorInUse, door);
    assert.equal(w.reads.at(-1).headers.Authorization, `Bearer ${NEXT}`, "the same start was lifted with no read first");
  });

  it("before a found gateway's same-start lift, one read with the held session: an ending answer lifts nothing", async () => {
    // A gateway the app found without the window's door is never used, so only one with it is lifted.
    for (const door of [true]) {
      const w = world();
      await foundStart(w, door ? { door: true, token: WINDOW, pair: PAIR } : {});
      w.holds("after foundStart");
      w.box.loseGateway();
      w.holds("after loseGateway");
      await settle();
      w.holds("after settle");
      w.gateways.proof = FOUND(300, 5000, "adopted-start-1", door);
      w.gateways.read = { status: 401, body: refused("session_expired") };
      await w.tick();
      w.holds("after tick");
      await settle();
      w.holds(`ended (door: ${door})`);
      assert.deepEqual(loopbackCookies(w), [], `an ended session was written back (door: ${door})`);
      rowIs(w, door ? "signed_out" : "cant_sign_in", `door: ${door}`);
      assert.equal(describeLocal(w.box.localState).action === null, !door);
    }
  });

  it("asks a found gateway's new start three times in all over five looks; the row is signed out after the third, and a press asks once", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.holds("after foundStart");
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    const asked = [];
    w.box.homeGateway.signIn = async (_g, _h, options) => (asked.push(options), { ok: false, status: 0, sentence: "no answer" });
    for (let look = 1; look <= 5; look++) {
      await w.tick();
      w.holds("after tick");
      await settle();
      w.holds(`look ${look}`);
      if (look < 3) assert.notEqual(w.box.localState.state, "signed_out", `signed out after ${look} asks`);
    }
    assert.equal(asked.length, 3, "a found start's asks");
    rowIs(w, "signed_out");
    assert.equal(w.box.windowDoorInUse, true);
    w.box.parked.set(1, "http://127.0.0.1:5000/#/chat/abc");
    w.box.homeGateway.signIn = async (_g, _h, options) => (asked.push(options), { ok: true, ready: ready(5000, AGAIN, { window_door: true }), pair: "pair-again" });
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("signed in again");
    assert.equal(asked.length, 4, "the press asked other than once");
    assert.deepEqual(w.windows[0].loads, ["http://127.0.0.1:5000/#/chat/abc"]);
    assert.equal(w.box.credentialsWithheld(), false, "the press left the watch withheld");
    assert.deepEqual(w.box.gatewayWatch.start, { pid: 301, port: 5000, secretDigest: "adopted-start-2" }, "the watch is not on the proven start");
  });

  it("a 403 at a found start's door spends its asks at once", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.holds("after foundStart");
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), { ok: false, status: 403, sentence: "refused" });
    for (let look = 0; look < 3; look++) {
      await w.tick();
      w.holds("after tick");
      await settle();
      w.holds("after settle");
    }
    assert.equal(asked, 1);
    rowIs(w, "signed_out");
  });

  it("the own gateway's door: three asks a start, and a disagreement leaves a row with no Sign In Again", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), { ok: false, pairless: true, status: 200, sentence: "broken" });
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("three failed asks");
    assert.equal(asked, 3);
    rowIs(w, "signed_out");
    const d = world();
    d.gateways.proof = FOUND(700, 51234, "digest-of-start-1", false);
    await ownStart(d, 51234, TOKEN, { window_door: true });
    d.holds("a disagreement");
    rowIs(d, "cant_sign_in");
    assert.equal(describeLocal(d.box.localState).action, null, "a row Sign In Again would be refused on offers it");
    assert.equal(d.box.windowDoorInUse, false);
  });

  it("Sign In Again at the app's own gateway asks nothing of a proof that names another pid", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after settle");
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), OPENED(AGAIN, "pair-again"));
    w.gateways.proof = FOUND(9999, 51234, "digest-of-start-1", true);
    assert.equal((await w.box.signInAgain()).ok, false);
    w.holds("after signInAgain");
    assert.equal(asked, 0, "the door was asked through a proof whose pid isn't the child's");
  });

  it("after an End, a loss and the same start proven again bring nothing back, and the windows' connections close once", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_expired"));
    await settle();
    w.holds("after settle");
    rowIs(w, "signed_out");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    for (let i = 0; i < 4; i++) {
      await w.tick();
      w.holds("after tick");
      await settle();
      w.holds(`tick ${i}`);
    }
    assert.deepEqual(loopbackCookies(w), [], "an ended session came back");
    assert.equal(w.did.filter((d) => d === "closeAll").length, 1);
  });

  it("the doorbell's bound: lost twice before up on the same start, the second lift starts no doorbell", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.startPresenceRefresh();
    w.holds("after the ring");
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after settle");
    w.gateways.proof = FOUND(700, 51234, "digest-of-start-1");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    const rings = () => w.did.filter((d) => d === "doorbell").length;
    const after = rings();
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after settle");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(rings(), after, "the second lift rang again");
    rowIs(w, "cant_sign_in");
    // The app's own gateway keeps its same-start lift after the trip: its record is still read.
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN, "the own gateway's same start was not lifted after the trip");
    assert.equal(w.box.credentialsWithheld(), false);
  });

  it("after an End at a found gateway, its next start signs the app in with no press, and its stop says stopped", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.holds("after foundStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after settle");
    rowIs(w, "signed_out");
    w.gateways.record = { pid: 302, port: 5000 };
    w.gateways.proof = FOUND(302, 5000, "adopted-start-3", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-of-the-next-start" });
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("the next start");
    assert.equal(w.box.localState.state, "adopted", "a new start left the row signed out");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), NEXT);
    w.gateways.record = null;
    w.box.homeGateway.pidIsAlive = () => false;
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.equal(w.box.localState.state, "stopped");
  });

  it("signs a session out on quitting after a defect, though the shell's own sign-in is ended", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_required", "pair_wrong"));
    await settle();
    w.holds("after settle");
    assert.equal(w.localSignIn.signedOut, true);
    const held = await w.localSignIn.release();
    w.holds("after release");
    await w.box.signOutLocalSession(held);
    w.holds("after signOutLocalSession");
    const out = w.requests.filter((r) => r.path === "/api/auth/logout").at(-1);
    assert.ok(out, "the quit sign-out was not sent");
    assert.equal(out.headers.Authorization, `Bearer ${WINDOW}`);
    assert.equal(out.headers["X-PersonalClaw-Pair"], undefined, "after a defect, the sign-out carries no pair");
  });

  it("a sign-in minted while the gateway was lost is signed out once the same start is back", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asks = 0;
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      if (asks === 1) {
        w.box.loseGateway();
        w.holds("after loseGateway");
        return OPENED("minted-while-lost", "pair-of-the-lost-ask");
      }
      return OPENED();
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    assert.ok(!w.did.includes("signout:minted-while-lost"), "signed out at an unproven port");
    await w.tick();
    w.holds("after tick");
    await settle();
    w.holds("after settle");
    assert.ok(w.did.includes("signout:minted-while-lost"), "the session minted on a moved generation stayed live");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), WINDOW);
  });

  it("Sign In Again reloads the exempt pages, sends the main window off them to the dashboard at its hash, and the buddy to its route", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after settle");
    const main = windowAt(2, "http://127.0.0.1:51234/files/report#/settings");
    const tab = windowAt(3, "http://127.0.0.1:51234/#/chat/abc");
    const other = windowAt(4, "http://127.0.0.1:51234/download/report");
    const buddyPage = windowAt(5, "http://127.0.0.1:51234/#/buddy");
    w.windows.push(main, tab, other, buddyPage);
    const owners = [main, tab, other].map((webContents) => ({ webContents, isDestroyed: () => false }));
    w.box.mainWindow = owners[0];
    w.box.appWindows = () => owners;
    w.box.buddy = { window: { isDestroyed: () => false, webContents: buddyPage } };
    w.box.homeGateway.signIn = async () => OPENED(AGAIN, "pair-again");
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("signed in again");
    assert.deepEqual(main.loads, ["http://127.0.0.1:51234/#/settings"]);
    assert.deepEqual(tab.loads, ["reload"]);
    assert.deepEqual(other.loads, [], "a window off the exempt paths reloaded");
    assert.deepEqual(buddyPage.loads, ["http://127.0.0.1:51234/#/buddy"]);
  });

  it("a restart to another port moves the exempt pages, sends the main window to the dashboard at its hash, and closes the rest", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    const main = windowAt(2, "http://127.0.0.1:51234/files/report#/settings");
    const exempt = windowAt(3, "http://127.0.0.1:51234/apps/notes/ui/index.html");
    const other = windowAt(4, "http://127.0.0.1:51234/download/x");
    w.windows.push(main, exempt, other);
    const closed = [];
    const owners = [main, exempt, other].map((webContents) => ({ webContents, isDestroyed: () => false, close: () => closed.push(webContents.id) }));
    w.box.mainWindow = owners[0];
    w.box.BaseWindow = { getAllWindows: () => owners };
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    w.holds("after takeReadyLine");
    assert.deepEqual(main.loads, ["http://127.0.0.1:52345/#/settings"]);
    assert.deepEqual(exempt.loads, ["http://127.0.0.1:52345/apps/notes/ui/index.html"]);
    assert.deepEqual(closed, [4]);
  });

  it("a parked window whose route doesn't load on the cookie alone follows the restart move's rule", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    const tab = windowAt(2, "file:///app/loading.html");
    w.windows.push(tab);
    const closed = [];
    w.box.BaseWindow = { getAllWindows: () => [{ webContents: tab, isDestroyed: () => false, close: () => closed.push(tab.id) }] };
    w.box.parked.set(2, "http://127.0.0.1:51234/download/x");
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    w.holds("after takeReadyLine");
    assert.deepEqual(closed, [2], "a parked non-exempt route was carried to the new start");
    assert.deepEqual(tab.loads, []);
  });

  it("a refusal of the old session's sign-out at a restart ends nothing of the new one", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.gateways.answers = { "/api/auth/logout": { status: 403, body: refused("auth_bearer_invalid") } };
    w.gateways.digest = "digest-of-start-2";
    w.gateways.proof = FOUND(700, 51234, "digest-of-start-2", true);
    w.gateways.signIn = OPENED(NEXT, "pair-of-the-second-start");
    const row = w.box.localState.state;
    await w.box.takeReadyLine({ pid: 700 }, ready(51234, "second-ready-token", { window_door: true }), true);
    w.holds("after takeReadyLine");
    await settle();
    w.holds("restarted");
    assert.ok(w.requests.some((r) => r.path === "/api/auth/logout"), "nothing was signed out");
    assert.equal(w.localSignIn.signedOut, false, "the old session's refusal ended the new one");
    assert.equal(w.box.localState.state, row);
  });

  it("on a gateway with the door, each ending or defect refusal signs the windows out once and asks nothing", async () => {
    for (const body of [refused("auth_bearer_invalid"), refused("session_signed_out"), refused("session_expired"), refused("session_required", "pair_wrong"), refused("session_required", "unbound_cookie")]) {
      const w = world();
      w.gateways.proof = DOOR;
      w.gateways.signIn = OPENED();
      await ownStart(w, 51234, TOKEN, { window_door: true });
      w.holds("after ownStart");
      let asked = 0;
      w.box.homeGateway.signIn = async () => ((asked += 1), OPENED(AGAIN, "pair-again"));
      w.box.noteShellRefusal(w.localSignIn.stamp(), 403, body);
      await settle();
      w.holds(body);
      assert.equal(w.localSignIn.signedOut, true, body);
      rowIs(w, "signed_out", body);
      assert.equal(w.did.filter((d) => d === "row:signed_out").length, 1, `${body}: the row was set more than once`);
      await w.tick(3);
      w.holds("after tick");
      await settle();
      w.holds("after settle");
      assert.equal(asked, 0, `${body}: asked after the refusal`);
    }
  });

  it("the door's own session goes in the jar before the ready session is signed out", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    const write = w.did.indexOf("set:pc_token_51234");
    const out = w.did.indexOf(`signout:${TOKEN}`);
    assert.ok(write >= 0 && out > write, w.did.join(", "));
  });

  it("a ready line, the restart that follows it and Sign In Again ring the doorbell before their sign-outs and registration", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    const first = w.did.indexOf("doorbell");
    assert.ok(first >= 0 && first < w.did.indexOf(`signout:${TOKEN}`), `the ready line's: ${w.did.join(", ")}`);
    w.gateways.digest = "digest-of-start-2";
    w.gateways.proof = FOUND(700, 51234, "digest-of-start-2", true);
    w.gateways.signIn = OPENED(NEXT, "pair-of-the-second-start");
    w.did.length = 0;
    await w.box.takeReadyLine({ pid: 700 }, ready(51234, "second-ready-token", { window_door: true }), true);
    w.holds("after takeReadyLine");
    await settle();
    w.holds("after settle");
    const ring = w.did.indexOf("doorbell");
    assert.ok(ring >= 0 && ring < w.did.indexOf("register:open") && ring < w.did.indexOf(`signout:${WINDOW}`), w.did.join(", "));
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after settle");
    w.did.length = 0;
    w.box.homeGateway.signIn = async () => OPENED(AGAIN, "pair-again");
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("after signInAgain");
    const again = w.did.indexOf("doorbell");
    assert.ok(again >= 0 && again < w.did.indexOf("register:open") && again < w.did.indexOf(`signout:${NEXT}`), w.did.join(", "));
  });

  it("removes the window cookies earlier versions left at localhost and [::1], but a localhost gateway the owner added keeps its own", async () => {
    const w = world();
    w.box.shellStore = {};
    w.registry.endpoints.push({ id: "lab", kind: "paired", base_url: "http://localhost:10000" });
    const stored = [
      { name: "pc_token", domain: "localhost" },
      { name: "pc_token_51234", domain: "localhost" },
      { name: "pc_token_10000", domain: "localhost" },
      { name: "pc_token_8080", domain: "[::1]" },
      { name: "pc_token_51234", domain: "127.0.0.1" },
      { name: "theme", domain: "localhost" },
    ];
    const removed = [];
    let flushed = 0;
    const appSession = { cookies: { get: async () => stored, remove: async (url, name) => removed.push(`${url} ${name}`), flushStore: async () => (flushed += 1) } };
    await w.box.purgeStaleLocalCookies(appSession);
    w.holds("after purgeStaleLocalCookies");
    assert.deepEqual(removed.sort(), ["http://[::1]/ pc_token_8080", "http://localhost/ pc_token", "http://localhost/ pc_token_51234"].sort());
    assert.equal(flushed, 1);
  });

  it("keeps a localhost gateway's cookie by the port its address names, or its scheme's, and logs a failed removal or flush without the cookie", async () => {
    const w = world();
    w.box.shellStore = {};
    w.registry.endpoints.push({ id: "tls", kind: "paired", base_url: "https://localhost" }, { id: "plain", kind: "paired", base_url: "http://[::1]" });
    const stored = [
      { name: "pc_token_443", domain: "localhost", value: "stale-session-443" },
      { name: "pc_token_80", domain: "localhost", value: "stale-session-80" },
      { name: "pc_token_80", domain: "[::1]", value: "stale-session-ipv6-80" },
      { name: "pc_token_443", domain: "[::1]", value: "stale-session-ipv6-443" },
    ];
    const removed = [];
    const appSession = {
      cookies: {
        get: async () => stored,
        remove: async (url, name) => {
          if (url === "http://[::1]/") throw new Error("the store refused");
          removed.push(`${url} ${name}`);
        },
        flushStore: async () => {
          throw new Error("the store could not flush");
        },
      },
    };
    await w.box.purgeStaleLocalCookies(appSession);
    w.holds("after purgeStaleLocalCookies");
    assert.deepEqual(removed, ["http://localhost/ pc_token_80"], "a localhost https gateway's own cookie went, or another stayed");
    const failed = w.said.filter((line) => line.includes("could not"));
    assert.equal(failed.length, 2, w.said.join("\n"));
    assert.ok(failed[0].includes("pc_token_443") && failed[0].includes("[::1]"), failed[0]);
    assert.ok(failed[1].includes("flush"), failed[1]);
    for (const line of w.said) for (const cookie of stored) assert.ok(!line.includes(cookie.value), `a log line names a cookie's value: ${line}`);
  });

  it("signs a session out with its own credential: the Cookie at a gateway without the door, a Bearer with its pair at one with it", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    await w.box.signOutLocalSession({ token: "old-session", port: 51234, pair: null });
    w.holds("after signOutLocalSession");
    const flagless = w.requests.filter((r) => r.path === "/api/auth/logout").at(-1);
    assert.equal(flagless.headers.Cookie, "pc_token_51234=old-session");
    assert.equal(flagless.headers.Authorization, undefined);
    const d = world();
    d.gateways.proof = DOOR;
    d.gateways.signIn = OPENED();
    await ownStart(d, 51234, TOKEN, { window_door: true });
    d.holds("after ownStart");
    await d.box.signOutLocalSession({ token: "door-session", port: 51234, pair: "its-own-pair" });
    d.holds("after signOutLocalSession");
    const door = d.requests.filter((r) => r.path === "/api/auth/logout").at(-1);
    assert.equal(door.headers.Authorization, "Bearer door-session");
    assert.equal(door.headers["X-PersonalClaw-Pair"], "its-own-pair");
    assert.equal(door.headers.Cookie, undefined);
  });

  it("registers the windows' traffic once, with no filter, on the app's session", () => {
    const w = world();
    const calls = [];
    const counted = (name) => (...args) => calls.push([name, args.length]);
    w.box.registerWindowTraffic = require("../windowTraffic").registerWindowTraffic;
    const appSession = {
      webRequest: { onBeforeRequest: counted("onBeforeRequest"), onBeforeSendHeaders: counted("onBeforeSendHeaders"), onCompleted: counted("onCompleted") },
      cookies: { on: (event) => calls.push(["cookies", event]) },
    };
    w.box.watchWindowTraffic(appSession);
    assert.deepEqual(calls, [["onBeforeRequest", 1], ["onBeforeSendHeaders", 1], ["cookies", "changed"], ["onCompleted", 1]]);
  });

  it("keeps the local row out of the gateways the owner added", () => {
    const w = world();
    w.box.shellStore = {};
    w.registry.endpoints.push({ id: "lab", kind: "paired", base_url: "http://127.0.0.1:10000" });
    w.box.syncPaired();
    assert.deepEqual([...w.localSignIn.trafficState().paired], ["http://127.0.0.1:10000"]);
  });

  it("parks a window whose load the windows' traffic cancelled, on any origin, and probes nothing", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.activeUrl = "http://127.0.0.1:10000";
    const wc = windowAt(2, "about:blank");
    for (const failedUrl of ["http://127.0.0.1:51234/#/chat", "http://127.0.0.1:40000/", "http://127.0.0.1:10001/"]) {
      w.box.windowLoadFailed({}, wc, { errorCode: -20, errorDescription: "ERR_BLOCKED_BY_CLIENT", failedUrl, isMainFrame: true });
      w.holds(`after the cancelled load of ${failedUrl}`);
    }
    assert.equal(w.did.filter((d) => d === "probe").length, 0, "a cancelled load was probed");
    assert.equal(w.box.parked.get(2), "http://127.0.0.1:10001/");
    w.box.windowLoadFailed({}, wc, { errorCode: -105, errorDescription: "ERR_NAME_NOT_RESOLVED", failedUrl: "http://127.0.0.1:10000/", isMainFrame: true });
    w.holds("after the failed load");
    assert.equal(w.did.filter((d) => d === "probe").length, 1);
  });

  it("gives the buddy no page while the windows' sign-in is withheld, and loads a gateway the owner added during one", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    assert.equal(w.box.buddyUrl(), "http://127.0.0.1:51234/#/buddy");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after settle");
    assert.equal(w.box.buddyUrl(), null);
    const wc = windowAt(2, "about:blank");
    assert.equal(w.box.loadOrPark(wc, "http://127.0.0.1:10000/#/chat"), true);
    assert.equal(w.box.loadOrPark(wc, "http://127.0.0.1:51234/#/chat"), false);
    assert.deepEqual(wc.loads.slice(0, 1), ["http://127.0.0.1:10000/#/chat"]);
  });
});

describe("a loss while the app signs in, a start the doorbell's bound stopped, and a start after one that timed out", () => {
  const settle = async (times = 10) => {
    for (let i = 0; i < times; i++) await new Promise((r) => setImmediate(r));
  };
  const refused = (code, reason) => JSON.stringify({ error: { code, ...(reason ? { detail: { reason } } : {}) } });
  const AGAIN = "window-session-again";
  const FOUND = (pid, port, secretDigest, door = false) => ({ ok: true, gateway: { pid, port, secretDigest, ...(door ? { windowDoor: true } : {}) } });
  const DOOR = FOUND(700, 51234, "digest-of-start-1", true);
  const OPENED = (token = WINDOW, pair = PAIR) => ({ ok: true, ready: ready(51234, token, { window_door: true }), pair });
  const loopbackCookies = (w) => [...w.cookieJar.cookies.keys()].filter((name) => name.startsWith("pc_token_"));
  /** A gateway the app found, used: *door* says whether it has the window's door. */
  async function foundStart(w, { pid = 300, port = 5000, digest = "adopted-start-1", door = true, token = WINDOW, pair = PAIR } = {}) {
    w.box.gatewayProcess = null;
    w.gateways.record = { pid, port };
    await w.box.adoptHomeGateway({ ...FOUND(pid, port, digest, door), ready: ready(port, token, door ? { window_door: true } : {}), pair });
    w.holds("after adoptHomeGateway");
  }
  /** What carried a credential in *w* since *from*: a ring, a socket, a registration, a sign-out. */
  const carried = (w, from) => w.did.slice(from).filter((d) => d === "doorbell" || d.startsWith("socket:") || d.startsWith("register:") || d.startsWith("signout:"));
  /** The app's own gateway with the window's door, signed in, then ended by the gateway. */
  async function endedAtTheDoor(w) {
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after the End");
    rowIs(w, "signed_out");
  }

  it("Sign In Again: a loss during its cookie write keeps the credentials withheld, and nothing rings, registers or signs out before a proof", async () => {
    const w = world();
    await endedAtTheDoor(w);
    w.gateways.signIn = OPENED(AGAIN, "pair-again");
    const set = w.cookieJar.set;
    let heard = 0;
    w.cookieJar.set = (details) => {
      // The press's write goes out, and on a later tick, before it resolves, the doorbell hears the
      // gateway restarting.
      const written = set(details);
      if (details.value !== AGAIN || heard++) return written;
      return Promise.resolve()
        .then(() => w.box.doorbellConnection("restarting"))
        .then(() => written);
    };
    const from = w.did.length;
    const sent = w.requests.length;
    const out = await w.box.signInAgain();
    w.holds("after signInAgain");
    await settle();
    w.holds("after settle");
    assert.equal(heard, 1, "the press wrote no cookie: this case is blind");
    assert.equal(w.box.credentialsWithheld(), true, "the press let the credentials go to a port unproven since the loss");
    assert.deepEqual(carried(w, from), [], w.did.slice(from).join(", "));
    assert.equal(w.requests.length, sent, "something was sent before a proof");
    assert.deepEqual(loopbackCookies(w), []);
    assert.equal(out.ok, false, "the press went on across a loss during its write");
    assert.equal(out.message, w.box.GATEWAY_CHANGED_SENTENCE);
  });

  it("Sign In Again: a ready line during its cookie write leaves the watch on the new start, and the press goes no further", async () => {
    const w = world();
    await endedAtTheDoor(w);
    w.gateways.signIn = OPENED(AGAIN, "pair-again");
    const set = w.cookieJar.set;
    let restarted = null;
    w.cookieJar.set = (details) => {
      // The press's write goes out, and on a later tick, before it resolves, a restart's ready line
      // arrives.
      const written = set(details);
      if (details.value !== AGAIN || restarted) return written;
      restarted = Promise.resolve().then(() => {
        w.gateways.digest = "digest-of-start-2";
        return w.box.takeReadyLine({ pid: 700 }, ready(51234, NEXT), true);
      });
      return restarted.then(() => written);
    };
    const out = await w.box.signInAgain();
    w.holds("after signInAgain");
    await restarted;
    await settle();
    w.holds("after the restart");
    assert.ok(restarted, "the press wrote no cookie: this case is blind");
    assert.deepEqual(w.box.gatewayWatch.start, { pid: 700, port: 51234, secretDigest: "digest-of-start-2" }, "the press armed the watch on the start it proved, over the new one");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), NEXT);
    assert.equal(out.ok, false, "the press went on across a ready line during its write");
    assert.equal(out.message, w.box.GATEWAY_CHANGED_SENTENCE);
  });

  it("Sign In Again: a loss while the press proves the gateway asks the door nothing", async () => {
    const w = world();
    await endedAtTheDoor(w);
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), OPENED(AGAIN, "pair-again"));
    const reach = w.box.homeGateway.reach;
    w.box.homeGateway.reach = async (...args) => {
      const found = await reach(...args);
      w.box.doorbellConnection("lost");
      return found;
    };
    const out = await w.box.signInAgain();
    w.holds("after signInAgain");
    assert.equal(out.ok, false);
    assert.equal(out.message, w.box.GATEWAY_CHANGED_SENTENCE);
    assert.equal(asked, 0, "the door was asked after a loss the press's proof didn't cover");
  });

  it("a found gateway's new start: a loss during its cookie write lets nothing resume, and the watch stays withheld", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: PAIR });
    const set = w.cookieJar.set;
    let heard = 0;
    w.cookieJar.set = (details) => {
      const written = set(details);
      if (details.value !== NEXT || heard++) return written;
      return Promise.resolve()
        .then(() => w.box.doorbellConnection("lost"))
        .then(() => written);
    };
    const from = w.did.length;
    await w.tick();
    await settle();
    w.holds("after the take-back");
    assert.equal(heard, 1, "the new start's cookie was not written: this case is blind");
    assert.equal(w.box.credentialsWithheld(), true, "the take-back cleared a withhold the loss during its write set");
    assert.deepEqual(carried(w, from), [], w.did.slice(from).join(", "));
    assert.deepEqual(loopbackCookies(w), []);
  });

  it("the window's door of the app's own gateway: a loss during the door session's write rings nothing and signs nothing out", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    const set = w.cookieJar.set;
    let heard = 0;
    w.cookieJar.set = (details) => {
      const written = set(details);
      if (details.value !== WINDOW || heard++) return written;
      return Promise.resolve()
        .then(() => w.box.doorbellConnection("lost"))
        .then(() => written);
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    await settle();
    w.holds("after ownStart");
    assert.equal(heard, 1, "the door's session was not written: this case is blind");
    assert.equal(w.box.credentialsWithheld(), true);
    assert.deepEqual(carried(w, 0), [], w.did.join(", "));
    assert.deepEqual(loopbackCookies(w), []);
  });

  it("Sign In Again after the shell's hold was lifted: a loss during its ask adopts nothing, and the watch stays withheld", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = { ok: false, sentence: "the door gave nothing" };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    rowIs(w, "signed_out");
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    await w.tick();
    await settle();
    w.holds("after the hold's lift");
    assert.equal(w.box.credentialsWithheld(), false, "the shell's own hold was not lifted: this case is blind");
    assert.equal(w.localSignIn.withheld, true);
    let asked = 0;
    w.box.homeGateway.signIn = async () => {
      asked += 1;
      w.box.doorbellConnection("lost");
      return OPENED(AGAIN, "pair-again");
    };
    const out = await w.box.signInAgain();
    w.holds("after signInAgain");
    await settle();
    w.holds("after settle");
    assert.equal(asked, 1);
    assert.deepEqual(loopbackCookies(w), [], "the press let the windows in across a loss");
    assert.equal(w.box.gatewayWatch.withheld, true, "the press cleared the watch's withhold");
    assert.notEqual(w.localSignIn.held.token, AGAIN, "the press adopted a session minted across a loss");
    assert.equal(out.ok, false, "the press went on across a loss during its ask");
    assert.equal(out.message, w.box.GATEWAY_CHANGED_SENTENCE);
  });

  it("the door asked again at a resume: a loss during that ask adopts nothing, and the watch stays withheld", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asks = 0;
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      if (asks === 1) {
        w.box.loseGateway();
        w.holds("after loseGateway");
        return { ok: false, sentence: "the gateway went away" };
      }
      // The resume's ask hears the gateway lost while it is out.
      w.box.doorbellConnection("lost");
      return OPENED(AGAIN, "pair-again");
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after settle");
    assert.equal(asks, 1);
    await w.tick();
    await settle();
    w.holds("after the resume's ask");
    assert.equal(asks, 2, "the resume asked nothing: this case is blind");
    assert.deepEqual(loopbackCookies(w), [], "the resume's ask let the windows in across a loss");
    assert.equal(w.box.gatewayWatch.withheld, true);
    assert.equal(w.localSignIn.withheld, true);
  });

  it("a loss while a resume registers: the door is not asked again", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asks = 0;
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      if (asks === 1) {
        w.box.loseGateway();
        w.holds("after loseGateway");
        return { ok: false, sentence: "the gateway went away" };
      }
      return OPENED(AGAIN, "pair-again");
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after settle");
    const register = w.box.registerWithGateway;
    let registered = 0;
    w.box.registerWithGateway = async () => {
      registered += 1;
      w.box.loseGateway();
      w.holds("after loseGateway");
      return register();
    };
    await w.tick();
    await settle();
    w.holds("after the resume");
    assert.equal(registered, 1, "the resume did not register: this case is blind");
    assert.equal(asks, 1, "the door was asked while the watch was withheld");
    assert.deepEqual(loopbackCookies(w), []);
  });

  it("once the doorbell's bound stops a found start, its same start proven again lets nothing in, and its stop says stopped", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.box.startPresenceRefresh();
    w.holds("after the ring");
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after the first failure");
    w.gateways.proof = FOUND(300, 5000, "adopted-start-1", true);
    await w.tick();
    await settle();
    w.holds("after the first lift");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), WINDOW, "the first lift let nothing back: this case is blind");
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after the bound's trip");
    rowIs(w, "signed_out");
    const sent = w.requests.length;
    const from = w.did.length;
    // The door's ask is the one sender of the local secret: none goes to the start the bound stopped.
    let asked = 0;
    w.box.homeGateway.signIn = async () => ((asked += 1), { ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-again" });
    for (let look = 0; look < 3; look++) {
      await w.tick();
      await settle();
      w.holds(`look ${look}`);
      assert.deepEqual(loopbackCookies(w), [], `look ${look}: a lift let the windows back in with nothing to hear a loss`);
      assert.deepEqual(carried(w, from), [], `look ${look}: ${w.did.slice(from).join(", ")}`);
    }
    assert.equal(w.requests.length, sent, "something was sent to the start the bound stopped");
    w.gateways.record = null;
    w.box.homeGateway.pidIsAlive = () => false;
    await w.tick();
    await settle();
    w.holds("after the stop");
    assert.deepEqual(loopbackCookies(w), []);
    assert.deepEqual(carried(w, from), [], w.did.slice(from).join(", "));
    assert.equal(w.requests.length, sent);
    assert.equal(asked, 0, "the door of the start the bound stopped was asked");
    rowIs(w, "stopped");
  });

  it("a found gateway that moved: its start there is asked three times over five looks, once on a 403 or a 400, and the row offers nothing after the last", async () => {
    for (const [status, asks] of [[0, 3], [403, 1], [400, 1]]) {
      const w = world();
      await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
      w.gateways.record = { pid: 301, port: 5001 };
      w.box.loseGateway();
      w.holds("after loseGateway");
      await settle();
      w.holds("after the loss");
      w.gateways.proof = FOUND(301, 5001, "moved-start", true);
      const asked = [];
      w.box.homeGateway.signIn = async () => (asked.push(1), { ok: false, status, sentence: "no answer" });
      for (let look = 1; look <= 5; look++) {
        await w.tick();
        await settle();
        w.holds(`look ${look}`);
        if (asked.length < asks) assert.equal(w.box.localState.state, "adopted", `status ${status}: the row was set with asks left`);
      }
      assert.equal(asked.length, asks, `status ${status}: the moved start's asks`);
      rowIs(w, "cant_sign_in", `status ${status}`);
      assert.equal(describeLocal(w.box.localState).action, null, `status ${status}: the row offers a press that can't reach the moved start`);
      assert.equal(w.box.windowDoorInUse, true, `status ${status}: the moved start's flag was taken for the gateway in use`);
    }
  });

  it("after an End at a found gateway, its new start reloads the pages that latched the sign-out, and the buddy", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    const tab = windowAt(2, "http://127.0.0.1:5000/#/chat/abc");
    const buddyPage = windowAt(3, "http://127.0.0.1:5000/#/buddy");
    w.windows.push(tab);
    w.box.appWindows = () => [{ webContents: tab, isDestroyed: () => false }];
    w.box.buddy = { window: { isDestroyed: () => false, webContents: buddyPage } };
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after the End");
    rowIs(w, "signed_out");
    w.gateways.record = { pid: 302, port: 5000 };
    w.gateways.proof = FOUND(302, 5000, "adopted-start-3", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-of-the-next-start" });
    await w.tick();
    await settle();
    w.holds("after the new start");
    assert.equal(w.box.localState.state, "adopted");
    assert.deepEqual(tab.loads, ["reload"], "a page that latched the sign-out was not reloaded");
    assert.deepEqual(buddyPage.loads, ["http://127.0.0.1:5000/#/buddy"], "the buddy stayed on its signed-out page");
    assert.deepEqual(tab.scripts, [], "a reloaded page was told its pair changed too");
  });

  it("pages that latched an End reload at a newer start's take-back, when the first start's resume was overtaken", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    const tab = windowAt(2, "http://127.0.0.1:5000/#/chat/abc");
    const buddyPage = windowAt(3, "http://127.0.0.1:5000/#/buddy");
    w.windows.push(tab);
    w.box.appWindows = () => [{ webContents: tab, isDestroyed: () => false }];
    w.box.buddy = { window: { isDestroyed: () => false, webContents: buddyPage } };
    w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
    await settle();
    w.holds("after the End");
    w.gateways.record = { pid: 302, port: 5000 };
    w.gateways.proof = FOUND(302, 5000, "adopted-start-3", true);
    w.box.homeGateway.signIn = async (gateway) =>
      gateway.pid === 303
        ? { ok: true, ready: ready(5000, "start-4-session", { window_door: true }), pair: "pair-of-start-4" }
        : { ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-of-the-next-start" };
    const register = w.box.registerWithGateway;
    let overtaken = false;
    w.box.registerWithGateway = async () => {
      if (!overtaken) {
        overtaken = true;
        // While the first start's resume registers: a loss, a newer start proven, and its take-back.
        w.box.loseGateway();
        w.holds("after loseGateway");
        w.gateways.record = { pid: 303, port: 5000 };
        w.gateways.proof = FOUND(303, 5000, "adopted-start-4", true);
        await w.tick();
        await settle();
        w.holds("after the newer take-back");
      }
      return register();
    };
    await w.tick();
    await settle(20);
    w.holds("after both resumes");
    assert.ok(overtaken, "the first resume was not overtaken: this case is blind");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), "start-4-session");
    assert.ok(tab.loads.includes("reload"), "a page that latched the End was not reloaded");
    assert.ok(buddyPage.loads.includes("http://127.0.0.1:5000/#/buddy"), "the buddy stayed on its signed-out page");
    assert.equal(w.box.latchedSinceEnd, false, "the latch outlived the reloads");
  });

  it("while the doorbell is up the record is read every 2 s: unchanged it is read again; gone, naming another pid or port, or a pid not running, it is a loss, once, said with its file and its cause", async () => {
    const record = "/home/someone/.personalclaw/gateway.runtime.json";
    for (const [what, change, cause] of [
      ["gone", null, `${record} is missing or unreadable`],
      ["another pid", { pid: 701, port: 51234 }, `${record} names pid 701 on port 51234, not this start's (pid 700, port 51234)`],
      ["another port", { pid: 700, port: 51235 }, `${record} names pid 700 on port 51235, not this start's (pid 700, port 51234)`],
      ["a dead pid", { pid: 700, port: 51234, dead: true }, `${record} names pid 700, which is not running`],
    ]) {
      const w = world();
      await ownStart(w);
      w.holds("after ownStart");
      w.box.doorbellConnection("up");
      w.holds("after up");
      assert.equal(w.later.length, 1, `${what}: the record is not read while the doorbell is up`);
      w.runLater();
      w.holds("after the record's first read");
      assert.equal(w.later.length, 1, `${what}: an unchanged record was not read again`);
      assert.equal(w.box.credentialsWithheld(), false, `${what}: an unchanged record was a loss`);
      if (change && change.dead) w.gateways.deadPid = change.pid;
      w.gateways.record = change && { pid: change.pid, port: change.port };
      w.runLater();
      await settle();
      w.holds(`after the record's read (${what})`);
      const told = w.said.filter((line) => line.includes("runtime record"));
      assert.deepEqual(told, [`desktop: the gateway's runtime record at ${cause}; the windows' sign-in is withheld until a proof brings it back`], `${what}: the loss did not say where and why`);
      w.box.noteRecordLoss(w.box.gatewayWatch.start, null, false);
      assert.equal(w.said.filter((line) => line.includes("runtime record")).length, 1, `${what}: a start's loss was said twice`);
      assert.equal(w.box.credentialsWithheld(), true, `${what}: the record's change was not a loss`);
      assert.ok(w.did.includes("doorbell-stop"), `${what}: the doorbell's socket stayed open across the record's loss`);
      assert.equal(w.did.filter((d) => d === "closeAll").length, 1, `${what}: the windows' connections`);
      assert.equal(w.later.length, 0, `${what}: the record was read again after its loss`);
    }
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.doorbellConnection("up");
    w.holds("after up");
    w.box.stopPresenceRefresh();
    w.holds("after stopPresenceRefresh");
    assert.equal(w.later.length, 0, "the doorbell's stop left the record's read scheduled");
  });

  it("a found gateway's new start signs the windows in with its own pair, whatever another session's cookie said before", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.localSignIn.cookieChanged({ name: "pc_token_5000", domain: "127.0.0.1", value: "another" }, false);
    assert.equal(w.localSignIn.trafficState().cookieFollows, false, "another session's cookie: this case is blind");
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-again" });
    await w.tick();
    await settle();
    w.holds("after the new start");
    const state = w.localSignIn.trafficState();
    assert.equal(state.cookieFollows, true, "the new start's session doesn't follow its own cookie");
    const xhr = { url: "http://127.0.0.1:5000/api/chat", resourceType: "xhr", initiatorOrigin: "http://127.0.0.1:5000", requestHeaders: { "X-PersonalClaw-Pair": PAIR }, hasWebContents: true };
    assert.deepEqual(decide(xhr, state), { cancel: false, header: "set", pair: "pair-again" });
  });

  it("a same-start lift with a mint kept from a moved generation tells no page its pair changed, and signs the mint out", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asks = 0;
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      if (asks === 1) {
        w.box.loseGateway();
        w.holds("after loseGateway");
        return OPENED("minted-while-lost", "pair-of-the-lost-ask");
      }
      return OPENED();
    };
    w.windows[0].url = "http://127.0.0.1:51234/#/chat/abc";
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await w.tick();
    await settle();
    w.holds("after the same start's lift");
    assert.ok(w.did.includes("signout:minted-while-lost"), "the kept mint was not signed out: this case is blind");
    assert.deepEqual(w.windows[0].scripts, [], "a page was told its pair changed by a same-start lift");
  });

  it("a resume moves only the parked routes on an origin this run retired; any other loads as it was", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.gateways.digest = "digest-of-start-2";
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    w.holds("after the restart");
    w.gateways.record = { pid: 700, port: 52345 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    const other = windowAt(2, "file:///app/loading.html");
    const left = windowAt(3, "file:///app/loading.html");
    w.windows.push(other, left);
    w.box.parked.set(2, "http://127.0.0.1:10001/#/chat/abc");
    w.box.parked.set(3, "http://127.0.0.1:51234/#/chat/def");
    w.gateways.proof = FOUND(700, 52345, "digest-of-start-2");
    await w.tick();
    await settle();
    w.holds("after the same start's lift");
    assert.deepEqual(other.loads, ["http://127.0.0.1:10001/#/chat/abc"], "a parked route on another origin was moved to the gateway's");
    assert.deepEqual(left.loads, ["http://127.0.0.1:52345/#/chat/def"], "a parked route on the retired origin stayed there");
  });

  it("Sign In Again sends a parked main window whose route doesn't load on the cookie alone to the dashboard at its hash", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = { ok: false, sentence: "the door gave nothing" };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    rowIs(w, "signed_out");
    const main = windowAt(2, "file:///app/loading.html");
    w.windows.push(main);
    const owners = [{ webContents: main, isDestroyed: () => false, close: () => {} }];
    w.box.mainWindow = owners[0];
    w.box.BaseWindow = { getAllWindows: () => owners };
    w.box.parked.set(2, "http://127.0.0.1:51234/api/files/raw?x#/settings");
    w.gateways.signIn = OPENED();
    assert.equal((await w.box.signInAgain()).ok, true);
    w.holds("after signInAgain");
    assert.deepEqual(main.loads, ["http://127.0.0.1:51234/#/settings"]);
  });

  it("guard: an End while a found start's sign-in afresh registers leaves nothing to reload, tell or row back after it", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    const tab = windowAt(2, "http://127.0.0.1:5000/#/chat/abc");
    w.windows.push(tab);
    w.box.appWindows = () => [{ webContents: tab, isDestroyed: () => false }];
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-of-the-next-start" });
    const register = w.box.registerWithGateway;
    w.box.registerWithGateway = async () => {
      // The gateway ends the new sign-in while the registration is out.
      w.box.noteShellRefusal(w.localSignIn.stamp(), 403, refused("session_signed_out"));
      return register();
    };
    await w.tick();
    await settle();
    w.holds("after the End");
    assert.equal(w.localSignIn.signedOut, true, "the End did not land: this case is blind");
    assert.deepEqual(tab.loads, [], "a page reloaded after the End");
    assert.deepEqual(tab.scripts, [], "a page was told its pair changed after the End");
    rowIs(w, "signed_out");
  });

  it("guard: a found start's sign-in afresh whose doorbell the bound stops during the resume keeps the row the bound set", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.gateways.record = { pid: 301, port: 5000 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    w.gateways.proof = FOUND(301, 5000, "adopted-start-2", true);
    w.box.homeGateway.signIn = async () => ({ ok: true, ready: ready(5000, NEXT, { window_door: true }), pair: "pair-of-the-next-start" });
    const register = w.box.registerWithGateway;
    w.box.registerWithGateway = async () => {
      // Two failures before up on the new start, counted by the bound alone, with no loss beside them.
      w.box.noteDoorbellFailure();
      w.box.noteDoorbellFailure();
      return register();
    };
    await w.tick();
    await settle();
    w.holds("after the resume");
    assert.equal(w.box.doorbellBound.stopped, true, "the bound did not trip: this case is blind");
    rowIs(w, "signed_out", "the resume took the row back over the bound's");
  });

  it("guard: a take-back's flag left from another generation reloads and tells nothing at a resume", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    const tab = windowAt(2, "http://127.0.0.1:5000/#/chat/abc");
    w.windows.push(tab);
    w.box.appWindows = () => [{ webContents: tab, isDestroyed: () => false }];
    w.box.loseGateway();
    w.holds("after the loss");
    await settle();
    w.holds("after settle");
    // A flag a take-back left at an earlier generation, whose hand-over never came (a guard pin: set by hand).
    w.box.signedInAfresh = { wasSignedOut: true, gen: w.localSignIn.gen - 1 };
    w.gateways.proof = FOUND(300, 5000, "adopted-start-1", true);
    await w.tick();
    await settle();
    w.holds("after the same start's lift");
    assert.equal(w.cookieJar.cookies.get("pc_token_5000"), WINDOW, "the same start was not lifted: this case is blind");
    assert.deepEqual(tab.loads, [], "a stale flag reloaded a page");
    assert.deepEqual(tab.scripts, [], "a stale flag told a page its pair changed");
    assert.equal(w.box.signedInAfresh, null);
  });

  it("guard: a hold with no session rings nothing, and the doorbell's socket is refused", async () => {
    for (const extra of [{}, { window_door: true }]) {
      const w = world();
      await ownStart(w, 51234, "", extra);
      w.holds("after ownStart");
      const rings = w.did.filter((d) => d === "doorbell").length;
      w.box.startPresenceRefresh();
      w.holds("after startPresenceRefresh");
      assert.equal(w.did.filter((d) => d === "doorbell").length, rings, `${JSON.stringify(extra)}: a hold with no session rang`);
      assert.throws(() => w.box.socketGateway("ws://127.0.0.1:51234/api/ws", "http://127.0.0.1:51234"), /no sign-in/);
      assert.ok(!w.did.some((d) => d.startsWith("socket:")));
    }
  });

  it("a start after one that timed out withholds the last start's session for good before it stops it, until the next ready line", async () => {
    const w = world();
    // The late ready line of a start that timed out: followed, while the row says it did not start.
    await w.box.takeReadyLine({ pid: 700 }, ready(51234, TOKEN), true);
    w.gateways.record = { pid: 700, port: 51234 };
    w.holds("after the late ready line");
    w.box.localState = { state: "stopped", sentence: "did not start" };
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN, "the late ready line was not followed: this case is blind");
    const seen = [];
    const carriesNothing = async (step) => {
      w.holds(step);
      assert.deepEqual(loopbackCookies(w), [], `${step}: the last start's session is in the jar`);
      assert.equal(w.localSignIn.authorization().Authorization, undefined, `${step}: the last start's session is still carried`);
      assert.throws(() => w.box.socketGateway("ws://127.0.0.1:51234/api/ws", "http://127.0.0.1:51234"), /not proven|no sign-in/, `${step}: a socket`);
      assert.equal(await w.box.getGateway("/api/status"), null, `${step}: a read`);
      assert.equal(await w.box.postGateway("/api/desktop/state", {}, {}), null, `${step}: a post`);
      const before = w.requests.length;
      assert.equal(await w.box.signOutLocalSession({ token: TOKEN, port: 51234, pair: null }), false, `${step}: a sign-out`);
      assert.equal(w.requests.length, before, `${step}: a sign-out was sent to the port the start left`);
      seen.push(step);
    };
    const sent = w.requests.length;
    w.box.stopGateway = async () => {
      w.box.gatewayProcess = null;
      await carriesNothing("while the last start stops");
    };
    w.box.startGateway = async () => {
      await carriesNothing("while the next start starts");
      w.box.gatewayProcess = { pid: 701 };
      w.gateways.digest = "digest-of-start-2";
      w.gateways.record = { pid: 701, port: 51234 };
      atNext = w.requests.length;
      await w.box.takeReadyLine({ pid: 701 }, ready(51234, NEXT), false);
    };
    let atNext = -1;
    assert.equal(await w.box.startLocalHere(), true);
    w.holds("after the next ready line");
    assert.deepEqual(seen, ["while the last start stops", "while the next start starts"]);
    const carries = (r) => JSON.stringify(r.headers).includes(TOKEN);
    assert.deepEqual(w.requests.slice(sent, atNext).filter(carries), [], "a request carried the last start's session before the next ready line");
    assert.deepEqual(w.requests.slice(atNext).filter((r) => carries(r) && r.path !== "/api/auth/logout"), [], "a request but its sign-out carried the last start's session");
    assert.ok(!w.did.includes(`socket:${TOKEN}`));
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), NEXT);
  });

  it("a ready line from the child the app is stopping takes nothing: nothing is held, written or sent until the next start's", async () => {
    const w = world();
    const sent = w.requests.length;
    let stale = null;
    const checks = [];
    const nothingTaken = (step) => {
      w.holds(step);
      assert.equal(w.localSignIn.held, null, `${step}: the stopped child's session is held`);
      assert.deepEqual(loopbackCookies(w), [], `${step}: a session cookie is in the jar`);
      assert.deepEqual(carried(w, 0), [], `${step}: ${w.did.join(", ")}`);
      assert.equal(w.requests.length, sent, `${step}: something was sent`);
      checks.push(step);
    };
    w.box.stopGateway = async () => {
      w.box.gatewayProcess = null;
      // A ready line still in the pipe, or printed while the child stops: Node delivers it now.
      stale = w.box.takeReadyLine({ pid: 700 }, ready(51234, TOKEN), true);
      await stale;
      nothingTaken("after the stopped child's ready line");
    };
    w.box.startGateway = async () => {
      nothingTaken("while the next start starts");
      w.box.gatewayProcess = { pid: 701 };
      w.gateways.digest = "digest-of-start-2";
      w.gateways.record = { pid: 701, port: 52345 };
      await w.box.takeReadyLine({ pid: 701 }, ready(52345, NEXT), false);
    };
    assert.equal(await w.box.startLocalHere(), true);
    await settle();
    w.holds("after the next ready line");
    assert.ok(stale, "no ready line came from the stopped child: this case is blind");
    assert.deepEqual(checks, ["after the stopped child's ready line", "while the next start starts"]);
    assert.equal(w.cookieJar.cookies.get("pc_token_52345"), NEXT);
    const carries = (r) => JSON.stringify(r.headers).includes(TOKEN);
    assert.deepEqual(w.requests.slice(sent).filter(carries), [], "a request carried the stopped child's session");
    // It is kept, and signed out at the next proven gateway, as the quit's sign-outs do.
    assert.ok(w.box.keptSessions.some((k) => k.token === TOKEN), "the stopped child's session was dropped, not kept to sign out");
    const before = w.requests.length;
    await w.box.signOutKept();
    w.holds("after signOutKept");
    assert.deepEqual(w.requests.slice(before).filter(carries).map((r) => `${r.port} ${r.path}`), ["52345 /api/auth/logout"]);
  });

  it("a ready line from the child the app is stopping, with a session held: the credentials stay withheld and nothing carries the late session", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    const sent = w.requests.length;
    const LATE = "late-ready-token";
    w.box.stopGateway = async () => {
      w.box.gatewayProcess = null;
      await w.box.takeReadyLine({ pid: 700 }, ready(51234, LATE), true);
      w.holds("after the stopped child's ready line");
      assert.equal(w.box.gatewayGone, true, "the late line set the gateway back");
      assert.equal(w.box.credentialsWithheld(), true);
      assert.ok(![...w.cookieJar.cookies.values()].includes(LATE), "the late session went in the jar");
    };
    w.box.startGateway = async () => {
      assert.equal(w.box.credentialsWithheld(), true, "the credentials went again before the next start");
      throw new Error("the next start did not start");
    };
    w.box.ownGatewayLives = () => false;
    w.box.notStartedSentence = () => "did not start";
    w.box.findHomeGateway = async () => ({ ok: false, reason: "none" });
    assert.equal(await w.box.startLocalHere(), false);
    await settle();
    w.holds("after the failed start");
    assert.equal(w.box.credentialsWithheld(), true);
    assert.ok(!w.requests.slice(sent).some((r) => JSON.stringify(r.headers).includes(LATE)), "a request carried the late session");
    assert.ok(!w.did.includes(`socket:${LATE}`));
  });

  it("a ready line Node delivers after the child's exit takes nothing", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    await w.box.gatewayLost({ code: 1 });
    await settle();
    w.holds("after the exit");
    // The exit handler nulled gatewayProcess; the child's last output comes after it.
    w.box.gatewayProcess = null;
    const from = w.did.length;
    const sent = w.requests.length;
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    await settle();
    w.holds("after the line");
    assert.equal(w.box.gatewayWatch.kind, null, "the watch was armed by a line after the exit");
    assert.equal(w.box.localGatewayUrl, "http://127.0.0.1:51234", "the line moved the app to its address");
    assert.equal(w.box.gatewayGone, true);
    assert.deepEqual(loopbackCookies(w), []);
    assert.deepEqual(carried(w, from), [], w.did.slice(from).join(", "));
    assert.equal(w.requests.length, sent);
  });

  it("at the app's own gateway, a doorbell the bound stopped leaves its record read: a restart in place that unpublishes it is a loss within one read", async () => {
    const w = world();
    await ownStart(w);
    w.holds("after ownStart");
    w.box.startPresenceRefresh();
    w.holds("after the ring");
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after the first failure");
    w.gateways.proof = FOUND(700, 51234, "digest-of-start-1");
    await w.tick();
    await settle();
    w.holds("after the first lift");
    w.box.doorbellConnection("lost");
    await settle();
    w.holds("after the bound's trip");
    await w.tick();
    await settle();
    w.holds("after the second lift");
    assert.equal(w.cookieJar.cookies.get("pc_token_51234"), TOKEN, "the own gateway's same start was not lifted: this case is blind");
    assert.equal(w.box.credentialsWithheld(), false);
    assert.equal(w.later.length, 1, "the record is not read while the bound has the doorbell stopped");
    // Settings › Restart: the record is unpublished and the listener closes; the pid stays.
    w.gateways.record = null;
    const from = w.did.length;
    const sent = w.requests.length;
    w.runLater();
    await settle();
    w.holds("after the record's read");
    assert.equal(w.box.credentialsWithheld(), true, "the unpublished record was not a loss");
    assert.deepEqual(loopbackCookies(w), []);
    for (let look = 0; look < 3; look++) {
      await w.tick();
      await settle();
      w.holds(`look ${look}`);
    }
    assert.deepEqual(carried(w, from), [], w.did.slice(from).join(", "));
    assert.equal(w.requests.length, sent, "something was sent to the port the restart left");
    // The new image's ready line, on its new port.
    w.gateways.digest = "digest-of-start-2";
    w.gateways.record = { pid: 700, port: 52345 };
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, NEXT), true);
    await settle();
    w.holds("after the new ready line");
    assert.equal(w.cookieJar.cookies.get("pc_token_52345"), NEXT);
    assert.ok(w.requests.slice(sent).every((r) => r.port === 52345), "a request went to the port the restart left");
  });

  it("a door restart in place brings a parked window back only once the address it left is retired", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    const tab = windowAt(2, "file:///app/loading.html");
    w.windows.push(tab);
    const closed = [];
    w.box.BaseWindow = { getAllWindows: () => [{ webContents: tab, isDestroyed: () => false, close: () => closed.push(tab.id) }] };
    w.box.parked.set(2, "http://127.0.0.1:51234/download/x");
    w.gateways.digest = "digest-of-start-2";
    w.gateways.proof = FOUND(700, 52345, "digest-of-start-2", true);
    w.gateways.signIn = { ok: true, ready: ready(52345, NEXT, { window_door: true }), pair: "pair-of-the-second-start" };
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, "second-ready-token", { window_door: true }), true);
    await settle();
    w.holds("after the restart");
    assert.deepEqual(tab.loads, [], "the parked window loaded the address the restart left");
    assert.deepEqual(closed, [2], "a parked route that doesn't load on the cookie alone was carried to the new start");
  });

  it("an exit while a resume registers: the door is not asked again, and the row stays stopped", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    let asks = 0;
    let reached = 0;
    const reach = w.box.homeGateway.reach;
    w.box.homeGateway.reach = async (...args) => ((reached += 1), reach(...args));
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      w.box.loseGateway();
      return { ok: false, sentence: "the gateway went away" };
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after settle");
    assert.equal(asks, 1, "the first ask was not cut off: this case is blind");
    const register = w.box.registerWithGateway;
    w.box.registerWithGateway = async () => {
      await w.box.gatewayLost({ code: 1 });
      return register();
    };
    const before = reached;
    await w.tick();
    await settle();
    w.holds("after the resume");
    assert.equal(reached, before + 1, "the resume reached the gateway again after its exit");
    assert.equal(asks, 1, "the door was asked after the exit");
    rowIs(w, "stopped");
  });

  it("rings nothing while the credentials are withheld: a socket dropped between a first start's two rings rings no more", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    const signOut = w.box.signOutLocalSession;
    let dropped = 0;
    w.box.signOutLocalSession = async (...args) => {
      // The doorbell's first socket drops while the ready session is signed out.
      if (!dropped++) w.box.doorbellConnection("lost");
      return signOut(...args);
    };
    // A doorbell that opens its socket the way the real one does: a socket refused is a failure.
    w.box.doorbell = {
      connect: (url) => {
        w.did.push("doorbell");
        try {
          w.box.socketGateway(url, url);
        } catch {
          w.box.doorbellConnection("lost");
        }
      },
      stop: () => w.did.push("doorbell-stop"),
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    assert.equal(dropped, 1, "the ready session was not signed out: this case is blind");
    const rings = w.did.filter((d) => d === "doorbell").length;
    const row = w.box.localState.state;
    // whenReady rings once the start is up.
    w.box.startPresenceRefresh();
    w.holds("after whenReady's ring");
    assert.equal(w.did.filter((d) => d === "doorbell").length, rings, "the doorbell rang while the credentials were withheld");
    assert.equal(w.box.doorbellBound.failures, 1);
    assert.equal(w.box.localState.state, row);
  });

  it("rings nothing while the credentials are withheld: a socket dropped between a door restart's two rings rings no more", async () => {
    const w = world();
    w.gateways.proof = DOOR;
    w.gateways.signIn = OPENED();
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    const signOut = w.box.signOutLocalSession;
    let dropped = 0;
    w.box.signOutLocalSession = async (...args) => {
      if (!dropped++) w.box.doorbellConnection("lost");
      return signOut(...args);
    };
    w.gateways.digest = "digest-of-start-2";
    w.gateways.proof = FOUND(700, 52345, "digest-of-start-2", true);
    w.gateways.signIn = { ok: true, ready: ready(52345, NEXT, { window_door: true }), pair: "pair-of-the-second-start" };
    const rings = w.did.filter((d) => d === "doorbell").length;
    const row = w.box.localState.state;
    // A doorbell that opens its socket the way the real one does: a socket refused is a failure.
    w.box.doorbell = {
      connect: (url) => {
        w.did.push("doorbell");
        try {
          w.box.socketGateway(url, url);
        } catch {
          w.box.doorbellConnection("lost");
        }
      },
      stop: () => w.did.push("doorbell-stop"),
    };
    await w.box.takeReadyLine({ pid: 700 }, ready(52345, "second-ready-token", { window_door: true }), true);
    await settle();
    w.holds("after the restart");
    assert.equal(dropped, 1, "the replaced session was not signed out: this case is blind");
    assert.equal(w.did.filter((d) => d === "doorbell").length, rings + 1, "the restart rang again while the credentials were withheld");
    assert.equal(w.box.doorbellBound.failures, 1);
    assert.equal(w.box.localState.state, row);
  });

  it("a found gateway that moved: a mint its ask got across a withhold is signed out once the moved start is in use", async () => {
    const w = world();
    await foundStart(w, { door: true, token: WINDOW, pair: PAIR });
    w.gateways.record = { pid: 301, port: 5001 };
    w.box.loseGateway();
    w.holds("after loseGateway");
    await settle();
    w.holds("after the loss");
    w.gateways.proof = FOUND(301, 5001, "moved-start", true);
    let asks = 0;
    w.box.homeGateway.signIn = async () => {
      asks += 1;
      if (asks === 1) {
        w.box.withholdForGood();
        return { ok: true, ready: ready(5001, "minted-across-a-withhold", { window_door: true }), pair: "pair-minted-across" };
      }
      return { ok: true, ready: ready(5001, NEXT, { window_door: true }), pair: "pair-of-the-moved-start" };
    };
    await w.tick();
    await settle();
    w.holds("after the first look");
    assert.ok(!w.did.includes("signout:minted-across-a-withhold"), "the mint was signed out at an unproven port");
    await w.tick();
    await settle();
    w.holds("after the second look");
    assert.equal(asks, 2);
    assert.equal(w.cookieJar.cookies.get("pc_token_5001"), NEXT);
    assert.ok(w.did.includes("signout:minted-across-a-withhold"), "the mint kept across the withhold stayed live");
  });

  /**
   * The app's own door gateway whose first ask a loss cut off, with /download/x parked at 51234: the
   * same start proven back resumes and asks the door again. *door* answers that ask (51234) and the
   * restart's (52345).
   */
  async function resumingDoorAsk(w, door) {
    w.gateways.proof = DOOR;
    const asked = [];
    w.box.homeGateway.signIn = async (gateway, ...rest) => {
      asked.push(gateway.port);
      if (asked.length === 1) {
        w.box.loseGateway();
        return { ok: false, sentence: "the gateway went away" };
      }
      return door(gateway, ...rest);
    };
    await ownStart(w, 51234, TOKEN, { window_door: true });
    w.holds("after ownStart");
    await settle();
    w.holds("after the cut-off ask");
    const tab = windowAt(2, "file:///app/loading.html");
    w.windows.push(tab);
    const closed = [];
    w.box.BaseWindow = { getAllWindows: () => [{ webContents: tab, isDestroyed: () => false, close: () => closed.push(tab.id) }] };
    w.box.parked.set(2, "http://127.0.0.1:51234/download/x");
    return { asked, tab, closed };
  }
  const restartLine = () => ready(52345, "second-ready-token", { window_door: true });
  const restartDoor = () => ({ ok: true, ready: ready(52345, NEXT, { window_door: true }), pair: "pair-of-the-second-start" });
  const macrotask = () => new Promise((r) => setImmediate(r));
  /** The restart's own ready line: the start it names, its proof, and the line taken. */
  const restartNow = (w) => {
    w.gateways.digest = "digest-of-start-2";
    w.gateways.record = { pid: 700, port: 52345 };
    w.gateways.proof = FOUND(700, 52345, "digest-of-start-2", true);
    return w.box.takeReadyLine({ pid: 700 }, restartLine(), true);
  };

  it("a resume's door ask that a restart's ready line overtakes during its cookie write rings nothing and reloads nothing", async () => {
    const w = world();
    const { asked, tab, closed } = await resumingDoorAsk(w, async (gateway) => {
      if (gateway.port !== 52345) return OPENED();
      // The restart's own ask is slower than the write it overtook.
      await macrotask();
      await macrotask();
      return restartDoor();
    });
    const set = w.cookieJar.set;
    let restarted = null;
    let from = -1;
    w.cookieJar.set = (details) => {
      const written = set(details);
      if (details.value !== WINDOW || restarted) return written;
      restarted = Promise.resolve().then(() => {
        from = w.did.length;
        return restartNow(w);
      });
      return written.then(macrotask);
    };
    await w.tick();
    await settle(20);
    await restarted;
    await settle(20);
    w.holds("after the restart");
    assert.ok(from >= 0, "the restart's line came at no write: this case is blind");
    assert.deepEqual(tab.loads.filter((l) => l.includes(":51234")), [], "a window loaded the address the restart left");
    assert.deepEqual(closed, [2]);
    // The restart rings once its own door session is in, and again once it has moved: the overtaken
    // ask rings nothing before it.
    const after = w.did.slice(from);
    const at = after.indexOf("set:pc_token_52345");
    assert.ok(at >= 0, "the restart's door session was not written: this case is blind");
    assert.deepEqual(carried({ did: after.slice(0, at) }, 0), [], `the overtaken ask carried something: ${after.join(", ")}`);
    assert.ok(after.includes("signout:ready-session-token"), "the ready session the overtaken ask replaced stayed signed in");
    assert.deepEqual(asked.filter((port) => port === 52345), [52345]);
  });

  it("a resume's door ask that a restart's ready line overtakes during its sign-outs reloads nothing, nor does the resume", async () => {
    const w = world();
    const { tab, closed } = await resumingDoorAsk(w, async (gateway) => (gateway.port === 52345 ? restartDoor() : OPENED()));
    const signOut = w.box.signOutLocalSession;
    let calls = 0;
    let restarted = null;
    w.box.signOutLocalSession = async (...args) => {
      calls += 1;
      if (calls === 1) {
        // The ask's ready session is being signed out when the restart's line arrives.
        restarted = Promise.resolve().then(() => restartNow(w));
        await macrotask();
      } else {
        // The restart's own sign-outs outlast the ask's, so it retires the old address last.
        await macrotask();
        await macrotask();
        await macrotask();
      }
      return signOut(...args);
    };
    await w.tick();
    await settle(30);
    await restarted;
    await settle(30);
    w.holds("after the restart");
    assert.ok(restarted, "the restart's line came at no sign-out: this case is blind");
    assert.deepEqual(tab.loads.filter((l) => l.includes(":51234")), [], "a window loaded the address the restart left");
    assert.deepEqual(closed, [2]);
  });

  it("a resume whose registration a restart's ready line overtakes asks nothing more, and reloads nothing", async () => {
    const w = world();
    const { asked, tab, closed } = await resumingDoorAsk(w, async (gateway) => (gateway.port === 52345 ? restartDoor() : OPENED()));
    const register = w.box.registerWithGateway;
    let restarted = null;
    w.box.registerWithGateway = async () => {
      if (!restarted) restarted = restartNow(w);
      return register();
    };
    await w.tick();
    await settle(30);
    await restarted;
    await settle(30);
    w.holds("after the restart");
    assert.ok(restarted, "the restart's line came at no registration: this case is blind");
    assert.deepEqual(asked.filter((port) => port === 52345), [52345], "the resume asked the new start's door beside its own ready line");
    assert.deepEqual(tab.loads.filter((l) => l.includes(":51234")), [], "a window loaded the address the restart left");
    assert.deepEqual(closed, [2]);
  });

  it("a Start pressed while a resume registers: the door is not asked again", async () => {
    const w = world();
    let reached = 0;
    const { asked } = await resumingDoorAsk(w, async () => OPENED());
    const reach = w.box.homeGateway.reach;
    w.box.homeGateway.reach = async (...args) => ((reached += 1), reach(...args));
    // The last start's stop takes its time: the next start is not up while the resume goes on.
    w.box.stopGateway = () => {
      w.box.gatewayProcess = null;
      return new Promise(() => {});
    };
    const register = w.box.registerWithGateway;
    let pressed = false;
    w.box.registerWithGateway = async () => {
      if (!pressed) {
        pressed = true;
        w.box.startLocalHere();
      }
      return register();
    };
    const before = reached;
    await w.tick();
    await settle(20);
    w.holds("after the press");
    assert.ok(pressed, "the press came at no registration: this case is blind");
    assert.equal(w.box.gatewayGone, true);
    assert.equal(reached, before + 1, "the resume reached the gateway the press is stopping");
    assert.equal(asked.length, 1, "the door was asked after the press");
    rowIs(w, "own", "the resume spent the stopping start's asks");
  });
});

describe("the credentials stay out of what main logs and loads", () => {
  it("names no session or pair in a log line, a loaded address, a script run in a page or a request's path, across every sequence above", () => {
    const secrets = [...new Set([TOKEN, NEXT, WINDOW, PAIR, "window-session-again", "pair-again", ...SECRETS])];
    assert.ok(WORLDS.length > 20, "the sequences made no worlds: this scan is blind");
    // A pair only a fake door handed out, and a session only a ready line carried: the lists are built.
    assert.ok(SECRETS.has("pair-of-the-lost-ask") && SECRETS.has("second-ready-token"), "the fakes' sessions and pairs went unnoted: this scan is blind");
    assert.ok(WORLDS.some((w) => w.said.length > 0), "nothing main logged was kept: this scan is blind");
    const scan = (where, text) => {
      for (const secret of secrets) assert.ok(!String(text).includes(secret), `${where} names a session or pair: ${text}`);
    };
    for (const w of WORLDS) {
      for (const line of w.said) scan("a log line", line);
      for (const sent of w.requests) scan("a request's path", sent.path);
    }
    for (const wc of ALL_WINDOWS) {
      for (const load of wc.loads) scan("a load", load);
      for (const script of wc.scripts) scan("a script run in a page", script);
    }
  });
});
