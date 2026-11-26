"use strict";

/**
 * The buddy's Open, the menu bar's Open PersonalClaw and a notification each ask for the main
 * window from outside it. On macOS a window's `focus()` alone does not bring the app forward while
 * another app is frontmost, so the window showed without the focus moving to it (the owner's run,
 * 2026-11-25: the focus moved to the app only now and then). `showMainWindow` now asks the app
 * itself to come forward there. It runs below as main.js has it, under stand-ins.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");

function fnSource(name) {
  const at = MAIN.search(new RegExp(`^(async )?function ${name}\\(`, "m"));
  assert.ok(at >= 0, `${name} is gone — this rail is now blind`);
  return MAIN.slice(at, MAIN.indexOf("\n}\n", at) + 2);
}

function run(platform, { visible = false } = {}) {
  const did = [];
  const box = {
    process: { platform },
    app: { focus: (opts) => did.push(["app.focus", opts]) },
    mainWindow: {
      isDestroyed: () => false,
      isVisible: () => visible,
      show: () => did.push(["show"]),
      focus: () => did.push(["focus"]),
    },
  };
  vm.createContext(box);
  vm.runInContext(`${fnSource("showMainWindow")}\nshowMainWindow();`, box);
  // The calls as plain data: the options object was made in the context's own realm.
  return JSON.parse(JSON.stringify(did));
}

describe("the app comes forward when its window is asked for", () => {
  it("on macOS the app itself comes forward, before its window takes the focus", () => {
    assert.deepEqual(run("darwin"), [["show"], ["app.focus", { steal: true }], ["focus"]]);
    assert.deepEqual(run("darwin", { visible: true }), [["app.focus", { steal: true }], ["focus"]]);
  });

  it("elsewhere the window's own focus is the whole of it, as before", () => {
    for (const platform of ["linux", "win32"]) assert.deepEqual(run(platform), [["show"], ["focus"]]);
  });
});
