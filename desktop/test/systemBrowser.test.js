"use strict";

/**
 * A page that is not the gateway's opens in the system browser, never in the app's windows.
 *
 * Connecting a remote tool server that asks for a sign-in opened a blank window first, to point
 * at the authorization server once the gateway answered. The window handler refuses a blank
 * window, rightly: the app's windows hold only the gateway's pages. So in the desktop app the
 * sign-in opened nothing at all. The page now hands the sign-in page to the shell through the
 * bridge (`systemBrowser.open`), and the shell opens it in the system browser and says whether it
 * did, with the rule every other door to the system browser follows.
 *
 * Everything here runs against fakes of Electron's `shell` and `ipcMain`: no browser opens.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { IPC_CHANNELS } = require("../capabilities");
const {
  MAX_URL,
  makeSystemBrowser,
  navigationGuard,
  registerSystemBrowserIpc,
  windowOpenHandler,
} = require("../systemBrowser");
const { loadPreloadSandboxed, stripComments } = require("./helpers/sandboxedPreload");

const ROOT = path.resolve(__dirname, "..");
const GATEWAY = "http://127.0.0.1:41234";
const AUTHORIZE =
  "https://auth.example.com/authorize?response_type=code&client_id=dcr-1&code_challenge=c&state=s";

/** Electron's `shell`, recording what it was asked to open. `fail` makes it reject like a system
 * with no browser to open the page with. */
function fakeShell({ fail = null } = {}) {
  const opened = [];
  return {
    opened,
    openExternal: async (url) => {
      opened.push(url);
      if (fail) throw new Error(fail);
    },
  };
}

/** `ipcMain`, holding the handlers registered on it so a test can answer a page's `invoke`. */
function fakeIpcMain() {
  const handlers = new Map();
  return {
    handlers,
    handle: (channel, fn) => handlers.set(channel, fn),
  };
}

/** A `systemBrowser` that records the pages handed to it, for driving the two doors alone. */
function recordingBrowser() {
  const handed = [];
  return { handed, open: async (url) => { handed.push(url); return { ok: true }; } };
}

/** The real preload, run the way a sandboxed renderer runs it, its `invoke` answered by the
 * handlers the real main-process half registered. */
function bridgedPage(shell, log = () => {}) {
  const ipcMain = fakeIpcMain();
  registerSystemBrowserIpc(ipcMain, makeSystemBrowser({ shell, log }), IPC_CHANNELS);
  const { exposed } = loadPreloadSandboxed(path.join(ROOT, "preload.js"), {
    invoke: (channel, ...args) => {
      const handler = ipcMain.handlers.get(channel);
      if (!handler) return Promise.reject(new Error(`No handler registered for '${channel}'`));
      return Promise.resolve(handler({}, ...args));
    },
  });
  return exposed.pclawDesktop;
}

describe("a remote tool server's sign-in page goes to the system browser", () => {
  it("the page hands it to the shell through the bridge, and hears that it opened", async () => {
    const shell = fakeShell();
    const bridge = bridgedPage(shell);
    assert.equal(
      typeof bridge.systemBrowser?.open,
      "function",
      "the bridge offers the page no way to open a page in the system browser",
    );
    assert.deepStrictEqual(await bridge.systemBrowser.open(AUTHORIZE), { ok: true });
    assert.deepStrictEqual(shell.opened, [AUTHORIZE]);
  });

  it("a browser that did not open is an answer with its reason, never a throw", async () => {
    const shell = fakeShell({ fail: "no application is set to open web pages" });
    const logged = [];
    const bridge = bridgedPage(shell, (msg) => logged.push(msg));
    assert.deepStrictEqual(await bridge.systemBrowser.open(AUTHORIZE), {
      ok: false,
      reason: "no application is set to open web pages",
    });
    assert.equal(logged.length, 1);
    // The sign-in page's address carries its single-use `state`: it is never written to the log.
    assert.equal(logged[0].includes("auth.example.com"), false, logged[0]);
  });

  it("a reason that quotes the address comes back, and is logged, without it", async () => {
    const logged = [];
    const browser = makeSystemBrowser({ shell: fakeShell({ fail: `could not open ${AUTHORIZE}` }), log: (m) => logged.push(m) });
    assert.deepStrictEqual(await browser.open(AUTHORIZE), { ok: false, reason: "could not open the page" });
    assert.deepStrictEqual(logged, ["could not open a page in the system browser: could not open the page"]);
  });

  it("a long reason comes back clipped, and an empty one still says something", async () => {
    const long = await makeSystemBrowser({ shell: fakeShell({ fail: "x".repeat(5000) }) }).open(AUTHORIZE);
    assert.equal(long.ok, false);
    assert.ok(long.reason.length <= 200, `reason is ${long.reason.length} characters`);
    const silent = await makeSystemBrowser({ shell: fakeShell({ fail: " " }) }).open(AUTHORIZE);
    assert.deepStrictEqual(silent, { ok: false, reason: "the system did not say why" });
  });
});

describe("only a web page is handed to the OS", () => {
  it("refuses an address that is not http or https, and the OS is never asked", async () => {
    const shell = fakeShell();
    const browser = makeSystemBrowser({ shell });
    const notWebPages = [
      "about:blank",
      "file:///tmp/notes.txt",
      "mailto:owner@example.com",
      "x-fixture-scheme://settings",
      "data:text/plain,hello",
      "",
      "not an address",
      `https://example.com/${"a".repeat(MAX_URL)}`,
      undefined,
      null,
      42,
      { toString: () => AUTHORIZE },
    ];
    for (const raw of notWebPages) {
      const answer = await browser.open(raw);
      assert.equal(answer.ok, false, `opened ${String(raw).slice(0, 40)}`);
      assert.match(answer.reason, /only a web page \(http or https\)/);
    }
    assert.deepStrictEqual(shell.opened, []);
  });

  it("opens what it checked: the parsed address, for http and https alike", async () => {
    const shell = fakeShell();
    const browser = makeSystemBrowser({ shell });
    assert.deepStrictEqual(await browser.open("HTTPS://Auth.Example.com/authorize?x=1"), { ok: true });
    assert.deepStrictEqual(await browser.open("http://127.0.0.1:8123/authorize"), { ok: true });
    assert.deepStrictEqual(shell.opened, [
      "https://auth.example.com/authorize?x=1",
      "http://127.0.0.1:8123/authorize",
    ]);
  });
});

describe("the app's windows hold only the gateway's pages", () => {
  const handlerFor = (origin = GATEWAY) => {
    const browser = recordingBrowser();
    return { browser, handle: windowOpenHandler({ allowedOrigin: () => origin, systemBrowser: browser }) };
  };

  it("a window on the gateway's own origin opens in the app", () => {
    const { browser, handle } = handlerFor();
    assert.deepStrictEqual(handle({ url: `${GATEWAY}/#/tools` }), { action: "allow" });
    // A widget's "Open in new tab" is a blob of the gateway's own origin.
    assert.deepStrictEqual(handle({ url: `blob:${GATEWAY}/7d0a4c1e` }), { action: "allow" });
    assert.deepStrictEqual(browser.handed, []);
  });

  it("a blank window is refused, and it opens nowhere", async () => {
    const shell = fakeShell();
    const handle = windowOpenHandler({ allowedOrigin: () => GATEWAY, systemBrowser: makeSystemBrowser({ shell }) });
    assert.deepStrictEqual(handle({ url: "about:blank" }), { action: "deny" });
    assert.deepStrictEqual(handle({ url: "" }), { action: "deny" });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepStrictEqual(shell.opened, []);
  });

  it("an external sign-in page is refused in the app and goes to the system browser", () => {
    const { browser, handle } = handlerFor();
    assert.deepStrictEqual(handle({ url: AUTHORIZE }), { action: "deny" });
    assert.deepStrictEqual(browser.handed, [AUTHORIZE]);
  });

  it("another origin on this computer is not the gateway's either", () => {
    const { browser, handle } = handlerFor();
    assert.deepStrictEqual(handle({ url: "http://127.0.0.1:41235/" }), { action: "deny" });
    assert.deepStrictEqual(handle({ url: "http://localhost:41234/" }), { action: "deny" });
    assert.deepStrictEqual(browser.handed, ["http://127.0.0.1:41235/", "http://localhost:41234/"]);
  });

  it("before a gateway is loaded no window opens in the app, and the origin is read each time", () => {
    let origin = "";
    const browser = recordingBrowser();
    const handle = windowOpenHandler({ allowedOrigin: () => origin, systemBrowser: browser });
    assert.deepStrictEqual(handle({ url: `${GATEWAY}/` }), { action: "deny" });
    // Switched to a paired gateway: a window on ITS origin opens, the loopback one's no longer does.
    origin = "https://claw.example.net";
    assert.deepStrictEqual(handle({ url: "https://claw.example.net/#/chat" }), { action: "allow" });
    assert.deepStrictEqual(handle({ url: `${GATEWAY}/` }), { action: "deny" });
  });
});

describe("the view cannot leave the gateway's origin", () => {
  const guardFor = () => {
    const browser = recordingBrowser();
    const logged = [];
    const guard = navigationGuard({
      allowedOrigin: () => GATEWAY,
      systemBrowser: browser,
      log: (msg) => logged.push(msg),
    });
    const navigate = (url) => {
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      guard(event, url);
      return event.prevented;
    };
    return { browser, logged, navigate };
  };

  it("a same-origin navigation and the loading page stay in the app", () => {
    const { browser, navigate } = guardFor();
    assert.equal(navigate(`${GATEWAY}/#/tools`), false);
    assert.equal(navigate("file:///Applications/PersonalClaw.app/Contents/Resources/loading.html"), false);
    assert.equal(navigate("about:blank"), false);
    assert.deepStrictEqual(browser.handed, []);
  });

  it("a navigation to another origin is cancelled and handed to the system browser", () => {
    const { browser, logged, navigate } = guardFor();
    assert.equal(navigate(AUTHORIZE), true);
    assert.deepStrictEqual(browser.handed, [AUTHORIZE]);
    assert.deepStrictEqual(logged, [`blocked in-app navigation to https://auth.example.com (allowed: ${GATEWAY})`]);
  });

  it("a navigation that is not an address is cancelled and handed nowhere", () => {
    const { browser, navigate } = guardFor();
    assert.equal(navigate("not an address"), true);
    assert.deepStrictEqual(browser.handed, []);
  });
});

describe("main.js wires these doors and asks the OS nowhere else", () => {
  const MAIN = stripComments(fs.readFileSync(path.join(ROOT, "main.js"), "utf8"));
  const MODULE = stripComments(fs.readFileSync(path.join(ROOT, "systemBrowser.js"), "utf8"));

  it("the view's window handler, its navigation guard and the bridge's channel are this module's", () => {
    assert.match(MAIN, /setWindowOpenHandler\(windowOpenHandler\(\{ allowedOrigin, systemBrowser \}\)\)/);
    assert.match(MAIN, /const guardNavigation = navigationGuard\(\{ allowedOrigin, systemBrowser,/);
    assert.match(MAIN, /registerSystemBrowserIpc\(ipcMain, systemBrowser, IPC_CHANNELS\)/);
  });

  it("calls `shell.openExternal` only through `makeSystemBrowser`, so no door can skip the rule", () => {
    assert.equal(/openExternal/.test(MAIN), false, "main.js opens a page in the system browser itself");
    assert.equal([...MODULE.matchAll(/openExternal\(/g)].length, 1);
  });
});
