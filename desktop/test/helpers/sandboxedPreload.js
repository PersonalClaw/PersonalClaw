"use strict";

/**
 * What a SANDBOXED Electron preload can actually do — modelled, so a test can see it (#3348).
 *
 * The desktop suite was 359 source-text assertions over the shell, and #3346 proved what that
 * buys: three rails about the bridge stayed green for the whole time the shipped bridge was
 * DEAD. A regex cannot see a preload throw. This module exists so two rails can:
 *
 *   - `loadPreloadSandboxed()` EXECUTES a preload in Node under a `require` shaped like the
 *     sandboxed one, so "the preload throws at load" and "the preload requires something a
 *     sandboxed preload cannot resolve" both become ordinary red tests, on every platform,
 *     with no display and no Electron process;
 *   - `webPreferenceBlocks()` reads the real `webPreferences` literals out of a source file, so
 *     the flag/preload pairing is asserted against the block it lives in.
 *
 * `desktop/test/bridgeLoads.test.js` then confirms this MODEL against a real Electron binary,
 * because a model of a sandbox that has never met the sandbox is just another regex.
 */

const fs = require("node:fs");

/** Every specifier a sandboxed preload's polyfilled `require` resolves.
 *
 * Electron gives a sandboxed preload `electron` (renderer-side only) plus these three
 * builtins, and nothing else — no relative paths, no `fs`, no `path`. Anything outside this
 * set throws `module not found`, and because Electron reports that through `preload-error`
 * rather than as a page failure, the preload is skipped and the bridge is simply ABSENT.
 * That silence is the whole bug class this file is here to make loud. */
const SANDBOX_REQUIRABLE = new Set(["electron", "events", "timers", "url"]);

/** Comments out. 🪤 Load-bearing, and the trap is not hypothetical: both files this module
 * scans EXPLAIN the sandbox in prose that quotes `require("./capabilities")`, so the first cut
 * of `requiredSpecifiers` reported a blocked require in a preload that has none. Reading a
 * flag or a call out of a comment is the same class of false answer the rails exist to end. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

/** Every `require("…")` specifier in a source string, comments excluded. */
function requiredSpecifiers(src) {
  return [...stripComments(src).matchAll(/\brequire\(\s*["']([^"']+)["']\s*\)/g)].map((m) => m[1]);
}

/** The specifiers in `src` that a sandboxed preload could NOT resolve.
 *
 * Empty means the preload runs with the Chromium process sandbox ON. Non-empty means the
 * window attaching it has to spend `sandbox: false` to get a bridge at all — which is a real
 * trade, so the pairing is asserted both ways round. */
function unsandboxableRequires(src) {
  return requiredSpecifiers(src).filter((spec) => !SANDBOX_REQUIRABLE.has(spec));
}

/** The body of each `webPreferences: { … }` literal in a source file, comments STRIPPED.
 *
 * Brace-balanced rather than line-matched, because a flag has to be read against the block it
 * sits in: the shell opens windows with and without a preload and they want different flags.
 * Comments come out because a block's own explanation names the flags it sets — the first cut
 * of this helper kept them and passed a main.js whose only `sandbox: false` was the prose
 * describing it, which is the same class of false green the rail exists to end. */
function webPreferenceBlocks(src) {
  const blocks = [];
  const opener = /webPreferences:\s*\{/g;
  for (let m = opener.exec(src); m; m = opener.exec(src)) {
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    for (; i < src.length && depth > 0; i += 1) {
      if (src[i] === "{") depth += 1;
      else if (src[i] === "}") depth -= 1;
    }
    if (depth !== 0) continue;
    blocks.push(
      src
        .slice(start, i - 1)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/[^\n]*/g, ""),
    );
  }
  return blocks;
}

/** The preload file a `webPreferences` block attaches, or `null` for a block with none. */
function preloadOf(block) {
  const m = /preload:\s*path\.join\(__dirname,\s*["']([^"']+)["']\s*\)/.exec(block);
  return m ? m[1] : null;
}

/**
 * Execute a preload file the way a SANDBOXED renderer would, and report what it exposed.
 *
 * Returns `{ exposed, channels }`, and THROWS whatever the preload throws — which is the
 * point: a preload that dies at load (or reaches for a module the sandbox does not carry)
 * fails the test instead of silently shipping a renderer with no bridge.
 *
 * The `electron` stub is deliberately inert: `exposeInMainWorld` records, `ipcRenderer`
 * records the channel and resolves nothing. This observes the bridge's SHAPE, not its
 * behaviour — the behaviour is already covered against real stubs elsewhere.
 */
function loadPreloadSandboxed(file) {
  const src = fs.readFileSync(file, "utf8");
  const exposed = Object.create(null);
  const channels = [];

  const electron = {
    contextBridge: {
      exposeInMainWorld: (key, value) => {
        exposed[key] = value;
      },
    },
    ipcRenderer: {
      on: (channel) => {
        channels.push(channel);
      },
      removeListener: () => {},
      send: (channel) => {
        channels.push(channel);
      },
      invoke: (channel) => {
        channels.push(channel);
        return Promise.resolve(undefined);
      },
    },
  };

  const shimRequire = (spec) => {
    if (spec === "electron") return electron;
    if (SANDBOX_REQUIRABLE.has(spec)) return require(spec);
    // Electron's own wording, so a red here reads like the `preload-error` it stands in for.
    const err = new Error(`module not found: ${spec}`);
    err.code = "MODULE_NOT_FOUND";
    throw err;
  };

  const mod = { exports: {} };
  // eslint-disable-next-line no-new-func -- executing the preload IS the assertion.
  const run = new Function("require", "module", "exports", "__filename", "__dirname", src);
  run(shimRequire, mod, mod.exports, file, require("node:path").dirname(file));
  return { exposed, channels };
}

module.exports = {
  SANDBOX_REQUIRABLE,
  loadPreloadSandboxed,
  preloadOf,
  requiredSpecifiers,
  stripComments,
  unsandboxableRequires,
  webPreferenceBlocks,
};
