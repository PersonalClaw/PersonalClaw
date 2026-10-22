"use strict";

/**
 * The packed shell's smoke (#3942): launch the app electron-builder unpacked, as a user starts it,
 * and watch it reach its own gateway and draw the chat.
 *
 *   node desktop/test/smoke/shellSmoke.js <executable> --gateway-pid-file <file> [--app <dir>] [--timeout-ms N]
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
 *   - and nothing threw: no JavaScript error in the main process, no uncaught one in the page.
 *
 * It prints one line per step, and `SHELL_SMOKE {"ok":…}` last; it exits 0 only when ok. Where
 * Linux has no display it starts Xvfb itself (`-displayfd`, so it picks a free display), rather
 * than through `xvfb-run`, so nothing between the smoke and the app can close the pipe's fds.
 * `--app` starts Electron's own binary on a source folder instead of a packed app, for trying the
 * smoke where the packed app is not this platform's.
 */

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
async function smoke(executable, { app: appDir = null, gatewayPidFile = null, timeoutMs = DEFAULT_TIMEOUT_MS, env = process.env, log = () => {} } = {}) {
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
  const args = [...(appDir ? [appDir] : []), DEVTOOLS_SWITCH, `--user-data-dir=${path.join(scratch, "user-data")}`];
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
    const bridge = await evaluate("typeof window.pclawDesktop");
    if (bridge !== "object") return fail(`the preload put no desktop bridge on the page (window.pclawDesktop is ${bridge})`);
    step("the preload's desktop bridge is on the page");
    await evaluate("location.hash = '#/chat'; true");
    let mounted = false;
    while (!mounted) {
      if (Date.now() > end) return fail(`the chat shell did not mount: no ${COMPOSER} on #/chat`);
      mounted = await evaluate(`Boolean(document.querySelector(${JSON.stringify(COMPOSER)}))`).catch(() => false);
      if (!mounted) await wait(250);
    }
    step(`the chat shell mounted: ${COMPOSER} on #/chat`);

    // 3. Nothing threw on the way.
    await wait(500);
    const errors = pageErrors(cdp.events, sessionId);
    if (errors.length) return fail(`the page threw: ${errors.join("; ")}`);
    if (MAIN_ERROR.test(output)) return fail("the main process threw");
    step("no error in the main process or the page");
    return { ok: true, steps, reason: null, mainOutput: output.slice(-4000) };
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

module.exports = { COMPOSER, DEVTOOLS_SWITCH, LOOPBACK_PAGE, MAIN_ERROR, devtoolsPipe, needsDisplay, pageErrors, smoke, stopGateway };

if (require.main === module) {
  const [executable, ...rest] = process.argv.slice(2);
  if (!executable) {
    console.error("usage: node desktop/test/smoke/shellSmoke.js <executable> --gateway-pid-file <file> [--app <dir>] [--timeout-ms N]");
    process.exit(2);
  }
  const opt = (name) => (rest.indexOf(name) >= 0 ? rest[rest.indexOf(name) + 1] : null);
  const timeoutMs = opt("--timeout-ms") ? Number(opt("--timeout-ms")) : DEFAULT_TIMEOUT_MS;
  const app = opt("--app") ? path.resolve(opt("--app")) : null;
  const gatewayPidFile = opt("--gateway-pid-file") ? path.resolve(opt("--gateway-pid-file")) : null;
  smoke(path.resolve(executable), { app, gatewayPidFile, timeoutMs, log: (s) => console.log(`shell-smoke: ${s}`) }).then((r) => {
    if (!r.ok) console.log(`shell-smoke: FAILED: ${r.reason}\n--- the app's output, last part ---\n${r.mainOutput}`);
    console.log(`SHELL_SMOKE ${JSON.stringify({ ok: r.ok, reason: r.reason })}`);
    process.exit(r.ok ? 0 : 1);
  });
}
