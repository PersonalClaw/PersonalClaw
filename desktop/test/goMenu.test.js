const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { COMMAND_EVENT, GO_COMMANDS, activeTabContents, commandScript, goMenuTemplate, makeGoCommandSender } = require("../goMenu");

const ROOT = path.resolve(__dirname, "..");
const KEYMAP = fs.readFileSync(path.join(ROOT, "..", "web", "src", "app", "keymap.ts"), "utf8");

describe("the Go menu", () => {
  it("binds ⌘N and ⌘1 to ⌘9 as accelerators, each sending its command id", () => {
    const sent = [];
    const menu = goMenuTemplate((id) => sent.push(id));
    const items = menu.submenu.filter((i) => i.accelerator);
    assert.deepEqual(
      items.map((i) => i.accelerator),
      ["CmdOrCtrl+N", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `CmdOrCtrl+${n}`)],
    );
    for (const i of items) i.click();
    assert.deepEqual(sent, [...GO_COMMANDS]);
  });

  it("binds the keys the page's keymap writes for those rows, so a key is written down once", () => {
    // The `new-chat` and `chat-<n>` rows of `web/src/app/keymap.ts`, as `mod+<key>`.
    assert.match(KEYMAP, /id: 'new-chat',[^\n]*chords: \['mod\+n'\]/);
    assert.match(KEYMAP, /id: `chat-\$\{n\}`,[^\n]*chords: \[`mod\+\$\{n\}`\]/);
    const chordOf = (id) => (id === "new-chat" ? "mod+n" : `mod+${id.slice("chat-".length)}`);
    const sent = [];
    const items = goMenuTemplate((id) => sent.push(id)).submenu.filter((i) => i.accelerator);
    for (const item of items) {
      item.click();
      const key = chordOf(sent.at(-1)).slice("mod+".length);
      assert.equal(item.accelerator, `CmdOrCtrl+${key.toUpperCase()}`, sent.at(-1));
    }
  });

  it("keeps ⌘T for tabs", () => {
    const menu = goMenuTemplate(() => {});
    assert.ok(!menu.submenu.some((i) => i.accelerator === "CmdOrCtrl+T"));
  });

  it("sends only the ids the page's keymap accepts from a menu, and never an approval", () => {
    assert.match(KEYMAP, new RegExp(`COMMAND_EVENT = '${COMMAND_EVENT}'`));
    assert.match(KEYMAP, /DESKTOP_COMMANDS[^=]*= \['new-chat', \.\.\.THREAD_NUMBERS/);
    assert.ok(!GO_COMMANDS.some((id) => /approve/.test(id)));
    assert.throws(() => commandScript("approve-focused"));
    assert.throws(() => commandScript("new-chat'); alert(1); ('"));
  });

  it("hands the page the id as a quoted string", () => {
    assert.equal(
      commandScript("chat-3"),
      `window.dispatchEvent(new CustomEvent("pclaw:command", { detail: "chat-3" }));`,
    );
  });

  it("is in the app menu, and sends to the tab in view", () => {
    const main = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
    assert.match(main, /goMenuTemplate\(sendGoCommand\)/);
    assert.match(main, /const sendGoCommand = makeGoCommandSender\(\{\s*contents: \(\) => activeTabContents\(BaseWindow\.getFocusedWindow\(\), mainWindow\)/);
  });
});

const GATEWAY = "http://127.0.0.1:9123";

/** A tab's web contents, recording every script run in it. */
function tab({ url = `${GATEWAY}/#/chat`, loading = false, destroyed = false } = {}) {
  const ran = [];
  return {
    ran,
    isDestroyed: () => destroyed,
    isLoading: () => loading,
    getURL: () => url,
    executeJavaScript: (script) => { ran.push(script); return Promise.resolve(); },
  };
}

/** A window holding *contents*, as `setupWindowContents` leaves one. */
const windowOf = (contents, destroyed = false) => ({ webContents: contents, isDestroyed: () => destroyed });

function senderFor(contents) {
  return makeGoCommandSender({ contents, gatewayUrl: () => GATEWAY });
}

describe("where a Go menu command goes", () => {
  it("to the tab in view, not to the first window, when two tabs are open", () => {
    const first = tab();
    const second = tab();
    senderFor(() => activeTabContents(windowOf(second), windowOf(first)))("chat-2");
    assert.equal(first.ran.length, 0);
    assert.deepEqual(second.ran, [commandScript("chat-2")]);
  });

  it("to the first window when no window has focus, and to none that is gone", () => {
    const first = tab();
    senderFor(() => activeTabContents(null, windowOf(first)))("new-chat");
    assert.deepEqual(first.ran, [commandScript("new-chat")]);
    const gone = tab();
    senderFor(() => activeTabContents(windowOf(gone, true), null))("new-chat");
    assert.equal(gone.ran.length, 0);
  });

  it("only to the dashboard of the gateway the window is on", () => {
    for (const url of [
      "file:///Applications/PersonalClaw.app/loading.html",
      `${GATEWAY}/api/apps/notes/ui/`,
      "http://127.0.0.1:9999/#/chat",
      "https://example.org/",
    ]) {
      const other = tab({ url });
      senderFor(() => other)("chat-1");
      assert.equal(other.ran.length, 0, url);
    }
  });

  it("not while the page is still loading, nor to contents that are gone", () => {
    for (const contents of [tab({ loading: true }), tab({ destroyed: true })]) {
      senderFor(() => contents)("chat-1");
      assert.equal(contents.ran.length, 0);
    }
    assert.doesNotThrow(() => senderFor(() => null)("chat-1"));
  });

  it("never an id the page's keymap does not take from a menu", () => {
    const page = tab();
    const send = senderFor(() => page);
    for (const id of ["approve-focused", "palette", "restore-last-prompt", "chat-10"]) {
      assert.doesNotThrow(() => send(id));
    }
    assert.equal(page.ran.length, 0);
  });
});
