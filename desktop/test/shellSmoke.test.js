const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { PassThrough } = require("node:stream");
const asar = require("@electron/asar");

const { stubScript, writeStubBackend } = require("./smoke/writeStubBackend");
const { DEVTOOLS_SWITCH, LOOPBACK_PAGE, MAIN_ERROR, carriedOut, devtoolsPipe, linkLaunchArgs, pageErrors } = require("./smoke/shellSmoke");
const { CONTROLS, PLANTED_MISSING, asarSources, debugSwitchProblems, missingEntries, plantedCopy, shellSources } = require("./smoke/packedApp");

// ── The packed shell's smoke, its parts (#3942) ───────────────────────────────────────────────
//
// CI's `desktop-smoke` job packs the shell, starts it on a stub backend that runs the checkout's
// gateway, and watches it reach that gateway and draw the chat. These are its parts that need no
// packed app: what the stub runs, what reads as the shell's own gateway page or as a main-process
// error, how the asar is held to build.files, and the planted copies' plants.

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-smoke-test-"));

describe("the stub backend", () => {
  it("hands the shell's arguments to the checkout's gateway, in the smoke's own home, on the scripted provider", () => {
    const dir = tmp();
    const script = writeStubBackend(dir, { backendDir: path.join(dir, "backend") });
    const text = fs.readFileSync(script, "utf8");
    assert.equal(path.basename(script), "personalclaw-backend", "where findPersonalclawBin looks");
    assert.ok(fs.statSync(script).mode & 0o100, "executable");
    assert.match(text, new RegExp(`export PERSONALCLAW_HOME='${dir}/gateway-home'`));
    assert.match(text, /export PERSONALCLAW_SCRIPTED_MODEL_SCRIPT='.*web\/e2e\/fixtures\/scripted-chat\.json'/);
    assert.match(text, /exec '[^']*personalclaw' "\$@"\n$/);
    for (const seed of ["config.json", "triggers.json"]) assert.ok(fs.existsSync(path.join(dir, "gateway-home", seed)), `seeded with ${seed}`);
  });

  it("runs as the gateway would be run, and leaves the pid the smoke stops it by", () => {
    const dir = tmp();
    const fake = path.join(dir, "fake-personalclaw");
    fs.writeFileSync(fake, '#!/bin/sh\necho "args: $*"\necho "home: $PERSONALCLAW_HOME"\n', { mode: 0o755 });
    const pidFile = path.join(dir, "gateway.pid");
    const script = path.join(dir, "stub");
    fs.writeFileSync(script, stubScript({ home: path.join(dir, "it's home"), workspace: dir, scripted: dir, command: fake, src: dir, pidFile }), { mode: 0o755 });
    const run = spawnSync(script, ["gateway", "--port", "auto", "--json-ready", "--no-open"], { encoding: "utf8" });
    assert.equal(run.status, 0, run.stderr);
    assert.match(run.stdout, /args: gateway --port auto --json-ready --no-open/);
    assert.match(run.stdout, new RegExp(`home: ${dir}/it's home`), "a quote in a path is kept, not broken out of");
    assert.match(fs.readFileSync(pidFile, "utf8"), /^\d+\n$/);
  });
});

describe("what the smoke reads", () => {
  it("takes the shell's own gateway page, on any loopback name, and nothing else", () => {
    for (const url of ["http://localhost:61221/", "http://127.0.0.1:5173/#/chat", "http://[::1]:8080/"]) assert.match(url, LOOPBACK_PAGE, url);
    for (const url of ["file:///app/loading.html", "about:blank", "https://localhost:443/", "http://example.com:80/", "http://localhost/"]) {
      assert.doesNotMatch(url, LOOPBACK_PAGE, url);
    }
  });

  it("reads Electron's main-process error as one, a missing module included", () => {
    const missing = "A JavaScript error occurred in the main process\nUncaught Exception:\nError: Cannot find module './doorbell'\nRequire stack:";
    assert.match(missing, MAIN_ERROR);
    assert.doesNotMatch("Starting gateway: …/personalclaw-backend gateway --port auto\ndesktop: no local secret", MAIN_ERROR);
  });

  it("counts a page's uncaught exception, its own page's only, and not its console's other lines", () => {
    const events = [
      { method: "Runtime.consoleAPICalled", sessionId: "s1", params: { type: "warning" } },
      { method: "Runtime.exceptionThrown", sessionId: "s1", params: { exceptionDetails: { text: "Uncaught", exception: { description: "TypeError: x is undefined" } } } },
      { method: "Runtime.exceptionThrown", sessionId: "s2", params: { exceptionDetails: { text: "Uncaught" } } },
    ];
    assert.deepEqual(pageErrors(events, "s1"), ["uncaught: TypeError: x is undefined"]);
    assert.deepEqual(pageErrors([]), []);
  });
});

describe("a link the smoke hands the app (#3827)", () => {
  const MARK = "smoke-draft-0a1b2c3d";
  const sent = (request, extra = {}) => ({ method: "Network.requestWillBeSent", sessionId: "s", params: { requestId: "r1", request, ...extra } });

  it("finds the draft wherever it left the page: a request's body, its fetched body, a non-GET address, a WebSocket frame", () => {
    assert.deepEqual(carriedOut([sent({ method: "POST", url: "http://localhost:1/api/chat", postData: `{"text":"${MARK}"}` })], MARK, "s"), [
      "POST http://localhost:1/api/chat carried it in its body",
    ]);
    const bodies = new Map([["r1", `text=${MARK}`]]);
    assert.deepEqual(carriedOut([sent({ method: "POST", url: "http://localhost:1/x", hasPostData: true })], MARK, "s", bodies), [
      "POST http://localhost:1/x carried it in its body",
    ]);
    assert.deepEqual(carriedOut([sent({ method: "PUT", url: `http://localhost:1/api/drafts/${MARK}` })], MARK, "s"), [
      `PUT http://localhost:1/api/drafts/${MARK} carried it in its address`,
    ]);
    const frame = { method: "Network.webSocketFrameSent", sessionId: "s", params: { requestId: "w", response: { payloadData: `{"type":"send","text":"${MARK}"}` } } };
    assert.deepEqual(carriedOut([frame], MARK, "s"), ["a WebSocket frame carried it"]);
  });

  it("does not count what stayed: a GET's address, a frame that came in, another page's requests, another text", () => {
    const events = [
      sent({ method: "GET", url: `http://localhost:1/api/search?q=${MARK}` }),
      { method: "Network.webSocketFrameReceived", sessionId: "s", params: { response: { payloadData: MARK } } },
      { ...sent({ method: "POST", url: "http://localhost:1/api/chat", postData: MARK }), sessionId: "other" },
      sent({ method: "POST", url: "http://localhost:1/api/chat", postData: "something else" }),
    ];
    assert.deepEqual(carriedOut(events, MARK, "s"), []);
  });

  it("hands a link over as a second launch does: the app's own arguments and the link, and no DevTools pipe", () => {
    assert.deepEqual(linkLaunchArgs(null, "/tmp/u", "personalclaw://settings"), ["--user-data-dir=/tmp/u", "personalclaw://settings"]);
    assert.deepEqual(linkLaunchArgs("/src/desktop", "/tmp/u", "personalclaw://settings"), ["/src/desktop", "--user-data-dir=/tmp/u", "personalclaw://settings"]);
    assert.ok(!linkLaunchArgs(null, "/tmp/u", "x").includes(DEVTOOLS_SWITCH), "the second launch closes nothing the first one's pipe needs");
  });
});

describe("the DevTools pipe", () => {
  it("is the one switch the smoke gives the app: a pipe, never a port", () => {
    assert.equal(DEVTOOLS_SWITCH, "--remote-debugging-pipe");
    const src = fs.readFileSync(path.join(__dirname, "smoke", "shellSmoke.js"), "utf8");
    assert.doesNotMatch(src, /remote-debugging-(port|address)/, "the harness opens no debugging port");
  });

  it("frames each message with a NUL byte, matches answers to commands, and keeps events with their session", async () => {
    const toApp = new PassThrough();
    const fromApp = new PassThrough();
    const cdp = devtoolsPipe(toApp, fromApp);
    let raw = "";
    toApp.on("data", (d) => (raw += d));
    const answer = cdp.send("Runtime.evaluate", { expression: "1" }, "s1");
    await new Promise((r) => setImmediate(r));
    assert.ok(raw.endsWith("\0") && raw.indexOf("\0") === raw.length - 1, `one message, ended by one NUL: ${JSON.stringify(raw)}`);
    assert.deepEqual(JSON.parse(raw.slice(0, -1)), { id: 1, method: "Runtime.evaluate", params: { expression: "1" }, sessionId: "s1" });
    // An event and the answer, arriving split across chunks as a pipe may hand them over.
    const wire = `${JSON.stringify({ method: "Runtime.exceptionThrown", sessionId: "s1", params: { exceptionDetails: { text: "boom" } } })}\0${JSON.stringify({ id: 1, result: { result: { value: 1 } } })}\0`;
    fromApp.write(wire.slice(0, 20));
    fromApp.write(wire.slice(20));
    assert.deepEqual(await answer, { result: { value: 1 } });
    assert.deepEqual(pageErrors(cdp.events, "s1"), ["uncaught: boom"]);
  });

  it("an error answer rejects, naming the command", async () => {
    const toApp = new PassThrough();
    const fromApp = new PassThrough();
    const cdp = devtoolsPipe(toApp, fromApp);
    const answer = cdp.send("Target.attachToTarget", {});
    fromApp.write(`${JSON.stringify({ id: 1, error: { message: "No target with given id" } })}\0`);
    await assert.rejects(answer, /Target.attachToTarget: No target with given id/);
  });
});

describe("the app opens no debugging port or pipe of its own", () => {
  it("none of the shell's sources, nor its builder config, carries a remote-debugging switch", () => {
    const sources = shellSources();
    assert.ok(sources.some((s) => s.name === "desktop/main.js"), "main.js is read (vacuity floor)");
    assert.ok(sources.length >= 20, `every packed source is read: ${sources.length}`);
    assert.deepEqual(debugSwitchProblems(sources), []);
  });

  it("a switch set in main.js, the builder config or a packed file is caught", async () => {
    const planted = [
      { name: "desktop/main.js", text: 'app.commandLine.appendSwitch("remote-debugging-port", "9222");' },
      { name: "desktop/package.json's build config", text: JSON.stringify({ linux: { executableArgs: ["--remote-debugging-pipe"] } }) },
      { name: "desktop/main.js", text: "app.commandLine.appendSwitch('Remote_Debugging_Address', '0.0.0.0')" },
    ];
    assert.equal(debugSwitchProblems(planted).length, 3);
    assert.deepEqual(debugSwitchProblems([{ name: "desktop/main.js", text: "app.whenReady()" }]), []);
    const dir = tmp();
    fs.mkdirSync(path.join(dir, "tree"));
    fs.writeFileSync(path.join(dir, "tree", "main.js"), 'process.argv.push("--remote-debugging-port=0")');
    await asar.createPackage(path.join(dir, "tree"), path.join(dir, "app.asar"));
    assert.deepEqual(debugSwitchProblems(asarSources(path.join(dir, "app.asar"))), ["app.asar/main.js carries a remote-debugging switch"]);
  });
});

describe("the packed app", () => {
  /** A stand-in for electron-builder's linux-unpacked: an asar of *files* and a sandbox helper. */
  async function unpacked(files) {
    const dir = tmp();
    const tree = path.join(dir, "tree");
    for (const [name, text] of Object.entries(files)) {
      fs.mkdirSync(path.dirname(path.join(tree, name)), { recursive: true });
      fs.writeFileSync(path.join(tree, name), text);
    }
    const app = path.join(dir, "app");
    fs.mkdirSync(path.join(app, "resources"), { recursive: true });
    await asar.createPackage(tree, path.join(app, "resources", "app.asar"));
    fs.writeFileSync(path.join(app, "chrome-sandbox"), "helper");
    return app;
  }

  it("holds the asar to build.files: a file by its name, a folder by anything in it", async () => {
    const app = await unpacked({ "main.js": "", "buddy/tray-idle.png": "", "loading.html": "" });
    const appAsar = path.join(app, "resources", "app.asar");
    assert.deepEqual(missingEntries(appAsar, ["main.js", "buddy", "loading.html"]), []);
    assert.deepEqual(missingEntries(appAsar, ["main.js", "preload.js", "buddy", "icons"]), ["preload.js", "icons"]);
  });

  it("plants in a copy, never the original, and the copy's sandbox helper is the original's", async () => {
    const app = await unpacked({ "main.js": "require('./doorbell')", [PLANTED_MISSING]: "", "preload.js": "" });
    const copy = await plantedCopy(app, CONTROLS[0].plant);
    assert.deepEqual(missingEntries(path.join(copy, "resources", "app.asar"), [PLANTED_MISSING, "main.js"]), [PLANTED_MISSING]);
    assert.deepEqual(missingEntries(path.join(app, "resources", "app.asar"), [PLANTED_MISSING]), [], "the original keeps it");
    assert.equal(fs.readlinkSync(path.join(copy, "chrome-sandbox")), path.join(app, "chrome-sandbox"));
  });

  it("plants the two link controls in deepLinks.js: one drops every link, one sends the draft it fills", async () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "deepLinks.js"), "utf8");
    const app = await unpacked({ "deepLinks.js": source });
    for (const control of CONTROLS.slice(2)) {
      const copy = await plantedCopy(app, control.plant);
      const planted = asar.extractFile(path.join(copy, "resources", "app.asar"), "deepLinks.js").toString("utf8");
      assert.notEqual(planted, source, `${control.name}: the plant changed nothing`);
    }
  });

  it("a control is caught only for its own reason", () => {
    const [missing, preload, dropped, sends] = CONTROLS;
    assert.equal(dropped.reads({ ok: false, reason: "personalclaw://settings did not open #/settings: the page stayed on #/chat", mainOutput: "" }), true);
    assert.equal(dropped.reads({ ok: false, reason: "the draft left the page: a WebSocket frame carried it", mainOutput: "" }), false);
    assert.equal(sends.reads({ ok: false, reason: "the draft left the page: a WebSocket frame carried it", mainOutput: "" }), true);
    assert.equal(sends.reads({ ok: false, reason: "the chat shell did not mount", mainOutput: "" }), false);
    assert.equal(missing.reads({ ok: false, reason: "the main process threw", mainOutput: "Error: Cannot find module './doorbell'" }), true);
    assert.equal(missing.reads({ ok: false, reason: "no window loaded the gateway in time", mainOutput: "" }), false, "a timeout is not the missing module");
    assert.equal(preload.reads({ ok: false, reason: "the preload put no desktop bridge on the page (window.pclawDesktop is undefined)", mainOutput: "" }), true);
    assert.equal(preload.reads({ ok: false, reason: "the chat shell did not mount", mainOutput: "" }), false);
  });
});
