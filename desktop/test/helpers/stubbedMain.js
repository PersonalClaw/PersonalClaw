"use strict";

/**
 * Load `desktop/main.js` in Node under an injected Electron stand-in, so a test can see what the
 * file's top level wires up before the app is ready, with no Electron process and no window.
 *
 * Not a test. The stand-in records every `app.on`/`app.once` listener and the calls a start makes
 * (`requestSingleInstanceLock`, `setAsDefaultProtocolClient`, `quit`); every other Electron name
 * is an inert object that answers any property or call with another one. `app.whenReady()` never
 * settles, so nothing after ready runs: what the test sees is exactly what the file does at load.
 * The shell's console output during the load is captured rather than printed.
 */

const Module = require("node:module");
const path = require("node:path");

const MAIN = path.join(__dirname, "..", "..", "main.js");

function inert() {
  return new Proxy(function () {}, {
    get: (_t, key) => (key === "then" ? undefined : key === Symbol.toPrimitive ? () => "" : inert()),
    apply: () => inert(),
    construct: () => inert(),
  });
}

/**
 * @param {{lock?: boolean, isPackaged?: boolean, argv?: string[]}} options
 *   `lock` is what `requestSingleInstanceLock()` answers; `argv` the process's arguments at load.
 * @returns {{listeners: Array<[string, Function]>, calls: Array<any[]>, logs: string[], fire: (evt: string, ...args: any[]) => void}}
 */
function loadMainWithStubs({ lock = true, isPackaged = false, argv = [process.execPath, "."] } = {}) {
  const listeners = [];
  const calls = [];
  const logs = [];
  const own = {
    isPackaged,
    on: (evt, fn) => (listeners.push([evt, fn]), app),
    once: (evt, fn) => (listeners.push([evt, fn]), app),
    requestSingleInstanceLock: () => (calls.push(["requestSingleInstanceLock"]), lock),
    setAsDefaultProtocolClient: (...args) => (calls.push(["setAsDefaultProtocolClient", ...args]), true),
    quit: () => calls.push(["quit"]),
    whenReady: () => new Promise(() => {}),
  };
  const app = new Proxy(own, {
    get: (t, key) => (key in t ? t[key] : inert()),
    set: (t, key, value) => ((t[key] = value), true),
  });
  const electron = new Proxy({ app }, { get: (t, key) => (key in t ? t[key] : inert()) });

  const realLoad = Module._load;
  const realArgv = process.argv;
  const realConsole = { log: console.log, warn: console.warn, error: console.error };
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return electron;
    return realLoad.call(this, request, parent, isMain);
  };
  process.argv = argv;
  for (const level of Object.keys(realConsole)) console[level] = (...parts) => logs.push(parts.join(" "));
  delete require.cache[MAIN];
  try {
    require(MAIN);
  } finally {
    delete require.cache[MAIN];
    Object.assign(console, realConsole);
    process.argv = realArgv;
    Module._load = realLoad;
  }

  /** Call every listener the file added for `evt`, capturing what they log. */
  const fire = (evt, ...args) => {
    const before = { log: console.log, warn: console.warn, error: console.error };
    for (const level of Object.keys(before)) console[level] = (...parts) => logs.push(parts.join(" "));
    try {
      for (const [name, fn] of listeners) if (name === evt) fn(...args);
    } finally {
      Object.assign(console, before);
    }
  };
  return { listeners, calls, logs, fire };
}

module.exports = { loadMainWithStubs };
