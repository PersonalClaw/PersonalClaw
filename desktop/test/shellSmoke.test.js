const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { PassThrough } = require("node:stream");
const asar = require("@electron/asar");

const { CODE_LOOP, SEED_CODE_LOOP, stubScript, writeStubBackend } = require("./smoke/writeStubBackend");
const { COCKPIT_LINK, DEVTOOLS_SWITCH, MODE_LINK, LOOPBACK_PAGE, literal, MAIN_ERROR, SETTINGS_LINK, carriedOut, cliOptions, devtoolsPipe, landingMisses, linkLaunchArgs, pageErrors } = require("./smoke/shellSmoke");
const { parseDeepLink } = require("../deepLinks");
const { CONTROLS, PLANTED_MISSING, asarSources, debugSwitchProblems, missingEntries, packArchive, plantedCopy, shellSources } = require("./smoke/packedApp");

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
    assert.match(text, /python3?' -c '/, "the code loop is seeded by the checkout's Python");
    assert.ok(fs.statSync(path.join(dir, "gateway-home", "workspace", CODE_LOOP.diff)).size > 0, "the file the cockpit link's diff names is in the loop's workspace, so its diff draws");
  });

  it("seeds the paused code loop the cockpit link opens, before the gateway starts, and never in the default home", () => {
    const given = { home: "/h", workspace: "/w", scripted: "/s", command: "/v/bin/personalclaw", src: "/src", pidFile: "/p" };
    const lines = stubScript({ ...given, python: "/v/bin/python" }).split("\n");
    const seed = lines.findIndex((l) => l.startsWith("'/v/bin/python' -c "));
    assert.ok(seed > 0 && seed < lines.findIndex((l) => l.startsWith("exec ")), "the seed runs before the gateway");
    assert.ok(!stubScript(given).includes(" -c "), "no seed without a Python to run it");
    assert.match(CODE_LOOP.id, /^[a-f0-9]{8}$/, "an id the loop store takes");
    assert.match(SEED_CODE_LOOP, /if not os\.environ\.get\('PERSONALCLAW_HOME'\) or uses_default_home\(\):\n {4}sys\.exit\(/, "refused before anything is imported that writes");
    assert.ok(SEED_CODE_LOOP.indexOf("sys.exit(") < SEED_CODE_LOOP.indexOf("store.create("), "the refusal comes first");
    assert.match(SEED_CODE_LOOP, new RegExp(`if store\\.get\\("${CODE_LOOP.id}"\\) is None:`), "seeded once, however often the gateway starts");
    assert.match(SEED_CODE_LOOP, /kind='code'.*status='paused'/, "a code loop nothing runs");
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

  it("puts a value in an expression the page evaluates as a string, and nothing that could end the script", () => {
    for (const value of ["Smoke cockpit", '[aria-label="Message input"]', "</script><img src=x>", "a\u2028b\u2029c", "back\\slash"]) {
      const text = literal(value);
      assert.equal(new Function(`return ${text};`)(), value, "it reads back as the value");
      assert.doesNotMatch(text, /[<>\u2028\u2029]/, "no character a script or a line could end on");
    }
  });

  it("opens the seeded cockpit by a link a user could be sent: its changes pane on one file, and a task", () => {
    const parsed = parseDeepLink(COCKPIT_LINK);
    assert.equal(parsed.ok, true, parsed.reason);
    assert.equal(parsed.hash, `#/code/${CODE_LOOP.id}?pane=changes&diff=${encodeURIComponent(CODE_LOOP.diff)}&task=${CODE_LOOP.task}`);
  });

  it("holds a cockpit link to landing where it points: the cockpit, its changes pane on the link's file, and its task", () => {
    const at = `#/code/${CODE_LOOP.id}`;
    assert.deepEqual(landingMisses(parseDeepLink(COCKPIT_LINK).hash), []);
    assert.deepEqual(landingMisses(`${at}?task=${CODE_LOOP.task}&diff=src%2Fapp.py&pane=changes`), [], "in any order");
    assert.deepEqual(landingMisses(`${at}?pane=changes`), [`diff=${CODE_LOOP.diff}`, `task=${CODE_LOOP.task}`]);
    assert.deepEqual(landingMisses(`${at}?pane=changes&diff=README.md&task=${CODE_LOOP.task}`), [`diff=${CODE_LOOP.diff}`], "another file");
    assert.deepEqual(landingMisses(`${at}?diff=src%2Fapp.py&task=${CODE_LOOP.task}`), ["pane=changes"], "the files tab");
    assert.deepEqual(landingMisses(`#/code/0ther000?pane=changes&diff=src%2Fapp.py&task=${CODE_LOOP.task}`), [at], "another cockpit");
    assert.deepEqual(landingMisses(at), ["pane=changes", `diff=${CODE_LOOP.diff}`, `task=${CODE_LOOP.task}`], "no query at all");
  });

  it("opens a new thread by a link that names its task mode, and only shows it", () => {
    const parsed = parseDeepLink(MODE_LINK);
    assert.equal(parsed.ok, true, parsed.reason);
    assert.equal(parsed.hash, "#/chat/new?mode=plan");
  });

  it("hands a link over as a second launch does: the app's own arguments and the link, and no DevTools pipe", () => {
    assert.deepEqual(linkLaunchArgs(null, "/tmp/u", "personalclaw://settings"), ["--user-data-dir=/tmp/u", "personalclaw://settings"]);
    assert.deepEqual(linkLaunchArgs("/src/desktop", "/tmp/u", "personalclaw://settings"), ["/src/desktop", "--user-data-dir=/tmp/u", "personalclaw://settings"]);
    assert.ok(!linkLaunchArgs(null, "/tmp/u", "x").includes(DEVTOOLS_SWITCH), "the second launch closes nothing the first one's pipe needs");
  });
});

describe("the command line", () => {
  it("runs the whole smoke unless told the app is on its first run, as a release's own is", () => {
    const desktopSmoke = cliOptions(["app/personalclaw", "--gateway-pid-file", "gateway.pid", "--timeout-ms", "9000"]);
    assert.equal(desktopSmoke.executable, path.resolve("app/personalclaw"));
    assert.equal(desktopSmoke.gatewayPidFile, path.resolve("gateway.pid"));
    assert.equal(desktopSmoke.timeoutMs, 9000);
    assert.equal(desktopSmoke.firstRun, false, "desktop-smoke's run checks the chat and the links");
    const release = cliOptions(["/Volumes/x/PersonalClaw.app/Contents/MacOS/PersonalClaw", "--first-run"]);
    assert.equal(release.firstRun, true);
    assert.equal(release.gatewayPidFile, null, "the shipped backend leaves no pid file: the app stops it");
    assert.equal(cliOptions([]).executable, null);
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
    await packArchive(path.join(dir, "tree"), path.join(dir, "app.asar"));
    assert.deepEqual(debugSwitchProblems(asarSources(path.join(dir, "app.asar"))), ["app.asar/main.js carries a remote-debugging switch"]);
  });
});

describe("an archive is read only once it is whole", () => {
  // asar.createPackage resolves once the archive's end is asked for, not once its bytes are on
  // disk, so a copy taken then can be short of the last file: CI's run 40056079320 planted a link
  // control in a deepLinks.js cut short and failed on "the plant's anchor is not once".
  const source = "x".repeat(64 * 1024) + "\nfunction registerDeepLinks() {}\n";

  /** createPackage as it behaves under load: it resolves with the header and part of the data
   * written, and the rest lands *lateMs* later, or never. */
  const resolvingEarly = (lateMs) => async (src, dest) => {
    const whole = `${dest}.whole`;
    await asar.createPackage(src, whole);
    const bytes = fs.readFileSync(whole);
    const cut = 8 + asar.getRawHeader(whole).headerSize + 1024;
    fs.writeFileSync(dest, bytes.subarray(0, cut));
    if (lateMs !== null) setTimeout(() => fs.appendFileSync(dest, bytes.subarray(cut)), lateMs);
  };

  function tree() {
    const dir = tmp();
    fs.mkdirSync(path.join(dir, "tree"));
    fs.writeFileSync(path.join(dir, "tree", "deepLinks.js"), source);
    return dir;
  }

  it("waits for the bytes the archive's own header promises before it hands the archive on", async () => {
    const dir = tree();
    const dest = path.join(dir, "app.asar");
    await packArchive(path.join(dir, "tree"), dest, { create: resolvingEarly(200) });
    assert.equal(asar.extractFile(dest, "deepLinks.js").toString("utf8"), source);
  });

  it("names the archive and how short it is when its bytes never land", async () => {
    const dir = tree();
    const dest = path.join(dir, "app.asar");
    await assert.rejects(packArchive(path.join(dir, "tree"), dest, { create: resolvingEarly(null), timeoutMs: 300 }), /app\.asar is \d+ of the \d+ bytes its header promises/);
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
    await packArchive(tree, path.join(app, "resources", "app.asar"));
    fs.writeFileSync(path.join(app, "chrome-sandbox"), "helper");
    return app;
  }

  it("holds the asar to build.files: a file by its name, a folder by anything in it", async () => {
    const app = await unpacked({ "main.js": "", "buddy/tray-idle.png": "", "loading.html": "" });
    const appAsar = path.join(app, "resources", "app.asar");
    assert.deepEqual(missingEntries(appAsar, ["main.js", "buddy", "loading.html"]), []);
    assert.deepEqual(missingEntries(appAsar, ["main.js", "preload.js", "buddy", "icons"]), ["preload.js", "icons"]);
  });

  it("plants the replaying control after the preload's own code, which it leaves as it was", async () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
    const app = await unpacked({ "preload.js": source });
    const copy = await plantedCopy(app, CONTROLS[5].plant);
    const planted = asar.extractFile(path.join(copy, "resources", "app.asar"), "preload.js").toString("utf8");
    assert.ok(planted.startsWith(source) && planted.length > source.length, "the preload's own code, then the plant");
  });

  it("plants in a copy, never the original, and the copy's sandbox helper is the original's", async () => {
    const app = await unpacked({ "main.js": "require('./doorbell')", [PLANTED_MISSING]: "", "preload.js": "" });
    const copy = await plantedCopy(app, CONTROLS[0].plant);
    assert.deepEqual(missingEntries(path.join(copy, "resources", "app.asar"), [PLANTED_MISSING, "main.js"]), [PLANTED_MISSING]);
    assert.deepEqual(missingEntries(path.join(app, "resources", "app.asar"), [PLANTED_MISSING]), [], "the original keeps it");
    assert.equal(fs.readlinkSync(path.join(copy, "chrome-sandbox")), path.join(app, "chrome-sandbox"));
  });

  it("plants the four link controls in deepLinks.js: one drops every link, one sends the draft it fills, one resumes the cockpit it opens, one opens it short of its diff", async () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "deepLinks.js"), "utf8");
    const app = await unpacked({ "deepLinks.js": source });
    for (const control of [...CONTROLS.slice(2, 5), CONTROLS[6]]) {
      const copy = await plantedCopy(app, control.plant);
      const planted = asar.extractFile(path.join(copy, "resources", "app.asar"), "deepLinks.js").toString("utf8");
      assert.notEqual(planted, source, `${control.name}: the plant changed nothing`);
    }
  });

  it("plants a shell that hands a cockpit link on without its diff and task, and every other link as it was", async () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "deepLinks.js"), "utf8");
    const app = await unpacked({ "deepLinks.js": source });
    const copy = await plantedCopy(app, CONTROLS[6].plant);
    const file = path.join(tmp(), "deepLinks.js");
    fs.writeFileSync(file, asar.extractFile(path.join(copy, "resources", "app.asar"), "deepLinks.js"));
    const planted = require(file);
    const moved = (link) => {
      const page = { location: { hash: "" } };
      new Function("window", planted.hashAssignmentScript(parseDeepLink(link).hash))(page);
      return page.location.hash;
    };
    assert.equal(moved(COCKPIT_LINK), `#/code/${CODE_LOOP.id}?pane=changes`);
    assert.deepEqual(landingMisses(moved(COCKPIT_LINK)), [`diff=${CODE_LOOP.diff}`, `task=${CODE_LOOP.task}`]);
    for (const link of [SETTINGS_LINK, MODE_LINK, "personalclaw://threads/new?prompt=smoke-draft-1"]) assert.equal(moved(link), parseDeepLink(link).hash, link);
  });

  it("a control is caught only for its own reason", () => {
    const [missing, preload, dropped, sends, resumes, replays, short] = CONTROLS;
    assert.equal(CONTROLS.length, 7);
    const shortOfIt = `${COCKPIT_LINK} opened ${CODE_LOOP.name}'s cockpit but not ${CODE_LOOP.diff}'s diff for ${CODE_LOOP.task}: the address lost diff=${CODE_LOOP.diff}, task=${CODE_LOOP.task} (the page is on #/code/${CODE_LOOP.id}?pane=changes)`;
    assert.equal(short.reads({ ok: false, reason: shortOfIt, mainOutput: "" }), true);
    assert.equal(short.reads({ ok: false, reason: `${COCKPIT_LINK} did not open ${CODE_LOOP.name}'s cockpit: the page is on #/chat`, mainOutput: "" }), false);
    assert.equal(short.reads({ ok: false, reason: `the cockpit link acted on the loop: POST http://localhost:1/api/loops/${CODE_LOOP.id}/resume carried it in its address`, mainOutput: "" }), false);
    assert.equal(resumes.reads({ ok: false, reason: shortOfIt, mainOutput: "" }), false);
    assert.equal(replays.reads({ ok: false, reason: shortOfIt, mainOutput: "" }), false);
    assert.equal(replays.reads({ ok: false, reason: "#/chat/new?mode=plan reopened in a new one replayed a write: POST http://localhost:1/api/chat/sessions", mainOutput: "" }), true);
    assert.equal(replays.reads({ ok: false, reason: `the cockpit link acted on the loop: POST http://localhost:1/api/loops/${CODE_LOOP.id}/resume carried it in its address`, mainOutput: "" }), false);
    assert.equal(resumes.reads({ ok: false, reason: "#/chat/new?mode=plan reopened in a new one replayed a write: POST http://localhost:1/api/chat/sessions", mainOutput: "" }), false);
    assert.equal(resumes.reads({ ok: false, reason: `the cockpit link acted on the loop: POST http://localhost:1/api/loops/${CODE_LOOP.id}/resume carried it in its address`, mainOutput: "" }), true);
    assert.equal(resumes.reads({ ok: false, reason: "the draft left the page: a WebSocket frame carried it", mainOutput: "" }), false);
    assert.equal(sends.reads({ ok: false, reason: "the cockpit link acted on the loop: a WebSocket frame carried it", mainOutput: "" }), false);
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
