"use strict";

/**
 * The packed shell's smoke (#3942): launch the app electron-builder unpacked, as a user starts it,
 * and watch it reach its own gateway and draw the chat.
 *
 *   node desktop/test/smoke/shellSmoke.js <executable> [--gateway-pid-file <file>] [--app <dir>] [--first-run] [--timeout-ms N]
 *
 * The shell spawns the backend in its place (`writeStubBackend.js` writes it), waits for its ready
 * line, signs its window in and loads the gateway. The smoke drives the window over the DevTools
 * protocol on a pipe (`--remote-debugging-pipe`: commands on the app's fd 3, answers on its fd 4),
 * so nothing listens on a port while it runs, and nothing here depends on an automation library's
 * Electron support. The switch is the harness's alone: the app's own code and its builder config
 * never carry one (`desktop/test/shellSmoke.test.js` holds that). It passes when:
 *
 *   - the window loaded an origin that answers as a PersonalClaw gateway (`/api/healthz`);
 *   - the preload put the desktop bridge on the page (`window.pclawDesktop`);
 *   - the chat shell mounted: `#/chat` draws its composer ("Message input");
 *   - `personalclaw://` links, handed over by a second launch as another app hands them (#3827),
 *     only move the page: `personalclaw://settings` opens `#/settings`, a link to no route moves
 *     nothing, and `personalclaw://threads/new?prompt=<draft>` opens a new thread with the draft in
 *     its composer, unsent. The draft is a word of the run's own, and it never leaves the page: no
 *     request's body or non-GET address and no WebSocket frame carries it. Reloading the address
 *     the link left, and the same link again, bring back the same draft and still send nothing;
 *   - and nothing threw: no JavaScript error in the main process, no uncaught one in the page.
 *
 * It prints one line per step, and `SHELL_SMOKE {"ok":…}` last; it exits 0 only when ok. Where
 * Linux has no display it starts Xvfb itself (`-displayfd`, so it picks a free display), rather
 * than through `xvfb-run`, so nothing between the smoke and the app can close the pipe's fds.
 * `--app` starts Electron's own binary on a source folder instead of a packed app, for trying the
 * smoke where the packed app is not this platform's.
 *
 * `--first-run` is for the app a release ships, with its own bundled backend in place of the stub:
 * that gateway starts on its first run, where `#/chat` draws no composer until a provider is set
 * up, so the smoke stops after the desktop bridge (the chat and link steps stay desktop-smoke's),
 * and still fails on anything that threw.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const DEFAULT_TIMEOUT_MS = 150_000;
/** What Electron prints when main.js throws, a missing module included. */
const MAIN_ERROR = /A JavaScript error occurred in the main process|Uncaught Exception|Error: Cannot find module/;
const COMPOSER = '[aria-label="Message input"]';
/** A page served by a gateway on this machine: the shell's own loads `http://localhost:<port>/`. */
const LOOPBACK_PAGE = /^http:\/\/(localhost|127\.0\.0\.1|\[::1\]):\d+\//;
/** The one DevTools switch the smoke gives the app: a pipe, never a port. */
const DEVTOOLS_SWITCH = "--remote-debugging-pipe";
/** A link to a route, and one to no route the app has. */
const SETTINGS_LINK = "personalclaw://settings";
const NO_ROUTE_LINK = "personalclaw://nowhere-the-app-has";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** Whether the app needs a virtual display to start: Linux with none set. */
const needsDisplay = (env = process.env) => process.platform === "linux" && !env.DISPLAY && !env.WAYLAND_DISPLAY;

/** Start Xvfb on a display of its own choosing; resolves `{display, stop}`. */
function startXvfb() {
  return new Promise((resolve, reject) => {
    const x = spawn("Xvfb", ["-displayfd", "3", "-screen", "0", "1280x800x24", "-nolisten", "tcp"], { stdio: ["ignore", "ignore", "pipe", "pipe"] });
    let said = "";
    let err = "";
    x.stdio[2].on("data", (d) => (err += d));
    x.on("error", (e) => reject(new Error(`Xvfb did not start: ${e.message}`)));
    x.on("exit", (code) => reject(new Error(`Xvfb exited (code ${code}) before it named a display: ${err.trim().slice(-300)}`)));
    x.stdio[3].on("data", (d) => {
      said += d;
      const n = said.match(/^(\d+)\n/);
      if (n) resolve({ display: `:${n[1]}`, stop: () => x.kill("SIGTERM") });
    });
  });
}

/**
 * The DevTools protocol over the app's pipe: each message is JSON ending in a NUL byte. `send`
 * takes a session, from `Target.attachToTarget` with `flatten`, for a command to one page.
 */
function devtoolsPipe(toApp, fromApp) {
  let id = 0;
  let buffered = "";
  const pending = new Map();
  const events = [];
  fromApp.setEncoding("utf8");
  fromApp.on("data", (chunk) => {
    buffered += chunk;
    for (let end = buffered.indexOf("\0"); end >= 0; end = buffered.indexOf("\0")) {
      const msg = JSON.parse(buffered.slice(0, end));
      buffered = buffered.slice(end + 1);
      if (msg.id && pending.has(msg.id)) {
        pending.get(msg.id)(msg);
        pending.delete(msg.id);
      } else if (msg.method) events.push(msg);
    }
  });
  const send = (method, params = {}, sessionId = undefined) =>
    new Promise((resolve, reject) => {
      const n = ++id;
      pending.set(n, (msg) => (msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)));
      toApp.write(`${JSON.stringify({ id: n, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
    });
  /** A command that may get no answer, as when the app has not started its DevTools yet. */
  const ask = (method, params, ms) => Promise.race([send(method, params), wait(ms).then(() => null)]);
  return { send, ask, events };
}

/** The page errors among *events* for *sessionId*: its uncaught exceptions. */
function pageErrors(events, sessionId = undefined) {
  const out = [];
  for (const e of events) {
    if (e.method !== "Runtime.exceptionThrown" || (sessionId && e.sessionId !== sessionId)) continue;
    const d = e.params.exceptionDetails;
    out.push(`uncaught: ${d.exception?.description || d.text}`);
  }
  return out;
}

/**
 * What carried *marker* out of *sessionId*'s page: a request's body (as sent, or as *bodies* holds
 * it by request id, for a body the event left out), a non-GET request's address, or a WebSocket
 * frame the page sent. A GET's address is not the page sending the text, nor is a frame received.
 */
function carriedOut(events, marker, sessionId, bodies = new Map()) {
  const out = [];
  for (const e of events) {
    if (sessionId && e.sessionId !== sessionId) continue;
    if (e.method === "Network.requestWillBeSent") {
      const r = e.params.request;
      const body = r.postData ?? bodies.get(e.params.requestId) ?? "";
      if (body.includes(marker)) out.push(`${r.method} ${r.url} carried it in its body`);
      else if (!["GET", "HEAD", "OPTIONS"].includes(r.method) && r.url.includes(marker)) out.push(`${r.method} ${r.url} carried it in its address`);
    } else if (e.method === "Network.webSocketFrameSent" && String(e.params.response?.payloadData).includes(marker)) {
      out.push("a WebSocket frame carried it");
    }
  }
  return out;
}

/** A second launch's arguments: the app's own, with the link, and no DevTools pipe of its own. */
const linkLaunchArgs = (appDir, userDataDir, link) => [...(appDir ? [appDir] : []), `--user-data-dir=${userDataDir}`, link];

/** Hand *link* to the running app as another app does: a second launch, which forwards it and quits. */
function deliverLink(executable, args, env, timeoutMs = 30_000) {
  return new Promise((resolve) => {
    const second = spawn(executable, args, { env, stdio: "ignore" });
    const timer = setTimeout(() => {
      second.kill("SIGKILL");
      resolve(false);
    }, timeoutMs);
    second.on("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
    second.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

/** Stop the gateway the stub backend started, by the pid it left: SIGTERM, then SIGKILL. */
async function stopGateway(pidFile) {
  let pid = 0;
  try {
    pid = Number(fs.readFileSync(pidFile, "utf8").trim());
  } catch {
    return;
  }
  const alive = () => {
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  };
  if (!(pid > 1) || !alive()) return;
  process.kill(pid, "SIGTERM");
  for (let i = 0; i < 40 && alive(); i++) await wait(250);
  if (alive()) process.kill(pid, "SIGKILL");
}

/**
 * Run the smoke against the app at *executable*. Resolves `{ok, steps, reason, mainOutput}`; never
 * throws for a failure the app made, so a planted control can read the reason.
 */
async function smoke(executable, { app: appDir = null, gatewayPidFile = null, firstRun = false, timeoutMs = DEFAULT_TIMEOUT_MS, env = process.env, log = () => {} } = {}) {
  if (gatewayPidFile) {
    await stopGateway(gatewayPidFile);
    fs.rmSync(gatewayPidFile, { force: true });
  }
  const steps = [];
  const step = (s) => {
    steps.push(s);
    log(s);
  };
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-shell-smoke-"));
  const xvfb = needsDisplay(env) ? await startXvfb() : null;
  const userDataDir = path.join(scratch, "user-data");
  const args = [...(appDir ? [appDir] : []), DEVTOOLS_SWITCH, `--user-data-dir=${userDataDir}`];
  const childEnv = { ...env, PERSONALCLAW_HOME: path.join(scratch, "shell-home"), ELECTRON_ENABLE_LOGGING: "1", ...(xvfb ? { DISPLAY: xvfb.display } : {}) };
  delete childEnv.ELECTRON_RUN_AS_NODE;
  step(`launch: ${executable} ${args.join(" ")}${xvfb ? ` on Xvfb ${xvfb.display}` : ""}`);
  const app = spawn(executable, args, { env: childEnv, detached: true, stdio: ["ignore", "pipe", "pipe", "pipe", "pipe"] });
  let output = "";
  app.stdout.on("data", (d) => (output += d));
  app.stderr.on("data", (d) => (output += d));
  let exited = null;
  app.on("exit", (code, signal) => (exited = { code, signal }));
  app.stdio[3].on("error", () => {});
  const cdp = devtoolsPipe(app.stdio[3], app.stdio[4]);

  const end = Date.now() + timeoutMs;
  const fail = (reason) => ({ ok: false, steps, reason, mainOutput: output.slice(-4000) });
  try {
    // 1. The window the shell opened, once it shows a page served over HTTP: the gateway's.
    let target = null;
    while (!target) {
      if (MAIN_ERROR.test(output)) return fail(`the main process threw: ${output.match(/.*(Error|Exception).*/)?.[0] || "see its output"}`);
      if (exited) return fail(`the app exited before its window loaded (code ${exited.code}, signal ${exited.signal})`);
      if (Date.now() > end) return fail("no window loaded the gateway in time");
      const listed = await cdp.ask("Target.getTargets", {}, 1000);
      target = listed?.targetInfos?.find((t) => t.type === "page" && LOOPBACK_PAGE.test(t.url)) || null;
      if (!target) await wait(500);
    }
    const origin = new URL(target.url).origin;
    step(`window loaded ${target.url}`);
    const health = await fetch(`${origin}/api/healthz`).then((r) => (r.ok ? r.json() : null), () => null);
    if (!health) return fail(`${origin} does not answer as a PersonalClaw gateway (/api/healthz)`);
    step(`the window's origin is the shell's gateway: ${origin} answers /api/healthz`);

    // 2. The page: the desktop bridge, then the chat shell.
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
    await cdp.send("Runtime.enable", {}, sessionId);
    const evaluate = async (expression) => {
      const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
      if (r.exceptionDetails) throw new Error(`evaluate: ${r.exceptionDetails.text}`);
      return r.result.value;
    };
    await cdp.send("Page.enable", {}, sessionId);
    // Every request and WebSocket frame from here on, so a link's draft is seen if it leaves. The
    // page opened its sockets before this, and a socket's frames are reported only when it opens
    // after it, so the page loads once more.
    await cdp.send("Network.enable", {}, sessionId);
    const reloaded = cdp.events.length;
    await cdp.send("Page.reload", {}, sessionId);
    while (!cdp.events.slice(reloaded).some((e) => e.method === "Page.loadEventFired" && e.sessionId === sessionId)) {
      if (Date.now() > end) return fail("the page did not load again after its reload");
      await wait(250);
    }
    const settle = async () => {
      await wait(500);
      const errors = pageErrors(cdp.events, sessionId);
      if (errors.length) return fail(`the page threw: ${errors.join("; ")}`);
      if (MAIN_ERROR.test(output)) return fail("the main process threw");
      step("no error in the main process or the page");
      return { ok: true, steps, reason: null, mainOutput: output.slice(-4000) };
    };
    const bridge = await evaluate("typeof window.pclawDesktop");
    if (bridge !== "object") return fail(`the preload put no desktop bridge on the page (window.pclawDesktop is ${bridge})`);
    step("the preload's desktop bridge is on the page");
    if (firstRun) {
      step("a first run stops here: its chat draws no composer until a provider is set up");
      return await settle();
    }
    await evaluate("location.hash = '#/chat'; true");
    let mounted = false;
    while (!mounted) {
      if (Date.now() > end) return fail(`the chat shell did not mount: no ${COMPOSER} on #/chat`);
      mounted = await evaluate(`Boolean(document.querySelector(${JSON.stringify(COMPOSER)}))`).catch(() => false);
      if (!mounted) await wait(250);
    }
    step(`the chat shell mounted: ${COMPOSER} on #/chat`);

    // 3. Links from other apps move the page and act on nothing (#3827).
    const hash = () => evaluate("location.hash");
    const composerText = () => evaluate(`document.querySelector(${JSON.stringify(COMPOSER)})?.textContent ?? ""`).catch(() => "");
    const until = async (what, test, ms = 20_000) => {
      const by = Math.min(end, Date.now() + ms);
      for (;;) {
        const got = await test().catch(() => false);
        if (got) return true;
        if (Date.now() > by) return false;
        await wait(250);
      }
    };
    const hand = async (link) => {
      if (!(await deliverLink(executable, linkLaunchArgs(appDir, userDataDir, link), childEnv))) throw new Error(`the second launch with ${link} did not exit`);
    };
    const bodies = new Map();
    const leftThePage = async (marker) => {
      for (const e of cdp.events) {
        const r = e.method === "Network.requestWillBeSent" && e.sessionId === sessionId ? e.params : null;
        if (r && r.request.hasPostData && r.request.postData === undefined && !bodies.has(r.requestId)) {
          const got = await cdp.send("Network.getRequestPostData", { requestId: r.requestId }, sessionId).catch(() => null);
          bodies.set(r.requestId, got?.postData ?? "");
        }
      }
      return carriedOut(cdp.events, marker, sessionId, bodies);
    };

    await hand(SETTINGS_LINK);
    if (!(await until("settings", async () => (await hash()) === "#/settings"))) return fail(`${SETTINGS_LINK} did not open #/settings: the page stayed on ${await hash()}`);
    step(`${SETTINGS_LINK} opened #/settings`);
    await hand(NO_ROUTE_LINK);
    await wait(2000);
    if ((await hash()) !== "#/settings") return fail(`${NO_ROUTE_LINK} moved the page to ${await hash()}, though it names no route`);
    step(`${NO_ROUTE_LINK} moved nothing`);

    const draft = `smoke-draft-${crypto.randomBytes(4).toString("hex")}`;
    const promptLink = `personalclaw://threads/new?prompt=${draft}`;
    const shown = async () => (await hash()).startsWith("#/chat/new") && (await composerText()).includes(draft);
    await hand(promptLink);
    // What left the page is read before where the page is: a link that sends its draft moves the
    // page on to the thread it started, and the send is the failure to name.
    const opened = await until("the draft", async () => (await shown()) || (await leftThePage(draft)).length > 0);
    let gone = await leftThePage(draft);
    if (gone.length) return fail(`the draft left the page: ${gone.join("; ")}`);
    if (!opened) return fail(`${promptLink} did not open a new thread with the draft in its composer: the page is on ${await hash()}`);
    const address = await hash();
    await wait(2000);
    gone = await leftThePage(draft);
    if (gone.length) return fail(`the draft left the page: ${gone.join("; ")}`);
    step(`${promptLink} opened ${address} with the draft in the composer, and sent nothing`);

    const again = cdp.events.length;
    await cdp.send("Page.reload", {}, sessionId);
    const loaded = async () => cdp.events.slice(again).some((e) => e.method === "Page.loadEventFired" && e.sessionId === sessionId);
    if (!(await until("the reload", async () => (await loaded()) && (await hash()) === address && (await composerText()).includes(draft)))) {
      return fail(`reloading ${address} did not bring back the draft: the page is on ${await hash()}`);
    }
    await hand(promptLink);
    await wait(2000);
    gone = await leftThePage(draft);
    if (gone.length) return fail(`the draft left the page: ${gone.join("; ")}`);
    const text = await composerText();
    if ((await hash()) !== address || text.split(draft).length !== 2) return fail(`the same link again left ${await hash()} with ${JSON.stringify(text)}, not the one draft on ${address}`);
    step("reloading the address and the same link again brought back the same draft, and sent nothing");

    // 4. Nothing threw on the way.
    return await settle();
  } finally {
    if (!exited) {
      for (const [sig, tries] of [["SIGTERM", 40], ["SIGKILL", 20]]) {
        if (exited) break;
        try {
          process.kill(-app.pid, sig);
        } catch {
          // Already gone.
        }
        for (let i = 0; i < tries && !exited; i++) await wait(250);
      }
    }
    if (gatewayPidFile) await stopGateway(gatewayPidFile);
    if (xvfb) xvfb.stop();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

/** The command line's options: `<executable>` first, then the flags the usage line names. */
function cliOptions(argv) {
  const [executable, ...rest] = argv;
  const opt = (name) => (rest.indexOf(name) >= 0 ? rest[rest.indexOf(name) + 1] : null);
  return {
    executable: executable ? path.resolve(executable) : null,
    app: opt("--app") ? path.resolve(opt("--app")) : null,
    gatewayPidFile: opt("--gateway-pid-file") ? path.resolve(opt("--gateway-pid-file")) : null,
    firstRun: rest.includes("--first-run"),
    timeoutMs: opt("--timeout-ms") ? Number(opt("--timeout-ms")) : DEFAULT_TIMEOUT_MS,
  };
}

module.exports = { COMPOSER, DEVTOOLS_SWITCH, LOOPBACK_PAGE, MAIN_ERROR, NO_ROUTE_LINK, SETTINGS_LINK, carriedOut, cliOptions, devtoolsPipe, linkLaunchArgs, needsDisplay, pageErrors, smoke, stopGateway };

if (require.main === module) {
  const { executable, ...options } = cliOptions(process.argv.slice(2));
  if (!executable) {
    console.error("usage: node desktop/test/smoke/shellSmoke.js <executable> [--gateway-pid-file <file>] [--app <dir>] [--first-run] [--timeout-ms N]");
    process.exit(2);
  }
  smoke(executable, { ...options, log: (s) => console.log(`shell-smoke: ${s}`) }).then((r) => {
    if (!r.ok) console.log(`shell-smoke: FAILED: ${r.reason}\n--- the app's output, last part ---\n${r.mainOutput}`);
    console.log(`SHELL_SMOKE ${JSON.stringify({ ok: r.ok, reason: r.reason })}`);
    process.exit(r.ok ? 0 : 1);
  });
}
