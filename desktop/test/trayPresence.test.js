const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  DEEP_LINKS,
  EMPTY_PRESENCE,
  summarizePresence,
  composeTrayTitle,
  composeTrayTooltip,
  buildTrayMenuTemplate,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
  makeTrayPresence,
} = require("../trayPresence");

/** Flatten a template (including submenus) to every row carrying a click handler. */
function clickableRows(template) {
  const out = [];
  for (const row of template) {
    if (typeof row.click === "function") out.push(row);
    if (Array.isArray(row.submenu)) out.push(...clickableRows(row.submenu));
  }
  return out;
}

function labels(template) {
  return template.filter((r) => r.label).map((r) => r.label);
}

describe("summarizePresence", () => {
  it("counts a bare approvals array — the documented GET /api/approvals shape", () => {
    const p = summarizePresence([{ id: "a" }, { id: "b" }], { loops: [] });
    assert.equal(p.approvals, 2);
    assert.equal(p.connected, true);
  });

  it("picks running loops out of {loops:[...]} and ignores every other status", () => {
    const payload = {
      loops: [
        { id: "l1", status: "running", name: "Ship DC-4" },
        { id: "l2", status: "paused", name: "Paused one" },
        { id: "l3", status: "complete", name: "Done" },
        { id: "l4", status: "RUNNING", task: "Case-insensitive" },
        { id: "l5", status: "needs_input", name: "Waiting" },
      ],
    };
    const p = summarizePresence([], payload);
    assert.deepStrictEqual(
      p.running.map((l) => l.id),
      ["l1", "l4"],
      "only `running` is a running loop — paused/blocked/needs_input are active, not running"
    );
    assert.equal(p.running[0].label, "Ship DC-4");
    assert.equal(p.running[1].label, "Case-insensitive", "a loop without a name falls back to its task");
  });

  it("a failed poll (null) is `not connected` with zero counts, not a crash", () => {
    const p = summarizePresence(null, null);
    assert.deepStrictEqual(p, { approvals: 0, running: [], connected: false });
  });

  it("drops a loop with no id — an unclickable menu row is worse than none", () => {
    const p = summarizePresence([], { loops: [{ status: "running", name: "no id" }] });
    assert.deepStrictEqual(p.running, []);
  });

  it("tolerates an enveloped approvals payload rather than reporting zero", () => {
    assert.equal(summarizePresence({ approvals: [1, 2, 3] }, {}).approvals, 3);
    assert.equal(summarizePresence({ pending: 7 }, {}).approvals, 7);
  });
});

describe("composeTrayTitle — the single writer for the menu-bar title", () => {
  it("capture beats the approvals badge", () => {
    // DC-3's indicator and DC-4's badge both want `tray.setTitle`. The privacy
    // signal wins; without one composer the later caller would silently erase the
    // other's text.
    assert.equal(composeTrayTitle({ capturing: true, approvals: 4 }), "● Listening");
  });

  it("shows the count when not capturing, and nothing at zero", () => {
    assert.equal(composeTrayTitle({ capturing: false, approvals: 4 }), "4");
    assert.equal(composeTrayTitle({ capturing: false, approvals: 0 }), "");
    assert.equal(composeTrayTitle(), "");
  });

  it("tooltip states connectedness, and capture still wins", () => {
    assert.match(composeTrayTooltip({ connected: false }), /not connected/);
    assert.match(composeTrayTooltip({ connected: true, approvals: 1, running: [{}] }), /1 approval waiting/);
    assert.match(composeTrayTooltip({ connected: true, capturing: true }), /listening/);
  });
});

describe("buildTrayMenuTemplate", () => {
  const actions = () => {
    const calls = [];
    return {
      calls,
      open: () => calls.push(["open"]),
      deepLink: (href) => calls.push(["deepLink", href]),
      quickCapture: () => calls.push(["quickCapture"]),
      toggleLoginItem: (v) => calls.push(["toggleLoginItem", v]),
      quit: () => calls.push(["quit"]),
    };
  };

  it("renders every clause the atom names, and inspects a non-zero number of rows", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({
      presence: { approvals: 3, running: [{ id: "l1", label: "Loop one" }], connected: true },
      loginItem: { supported: true, enabled: false },
      actions: a,
    });

    // VACUITY FLOOR: deleting the feature must not make this suite pass by matching
    // nothing. A menu shorter than this cannot carry the atom's five commitments.
    assert.ok(template.length >= 10, `expected a full menu, got ${template.length} rows`);
    assert.ok(clickableRows(template).length >= 5, "at least five rows must actually do something");

    const text = labels(template).join(" | ");
    assert.match(text, /3 approvals waiting/);
    assert.match(text, /1 loop running/);
    assert.match(text, /Quick Capture Note/);
    assert.match(text, /Open Dashboard/);
    assert.match(text, /Open at Login/);
    assert.match(text, /Quit PersonalClaw/);
  });

  it("the approvals row deep-links to the surface that renders approvals", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({ presence: { approvals: 2, running: [], connected: true }, actions: a });
    const row = template.find((r) => /approvals waiting/.test(r.label || ""));
    row.click();
    assert.deepStrictEqual(a.calls, [["deepLink", DEEP_LINKS.approvals]]);
    assert.equal(DEEP_LINKS.approvals, "#/chat", "ApprovalPrompt renders on the chat route");
  });

  it("stays click-through at zero approvals instead of becoming a dead end", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({ presence: { approvals: 0, running: [], connected: true }, actions: a });
    const row = template.find((r) => /No approvals waiting/.test(r.label || ""));
    assert.equal(typeof row.click, "function");
    row.click();
    assert.deepStrictEqual(a.calls, [["deepLink", "#/chat"]]);
  });

  it("each running loop deep-links to its own detail route", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({
      presence: { approvals: 0, running: [{ id: "l1", label: "One" }, { id: "l 2", label: "Two" }], connected: true },
      actions: a,
    });
    const parent = template.find((r) => Array.isArray(r.submenu));
    assert.equal(parent.submenu.length, 2);
    parent.submenu[0].click();
    parent.submenu[1].click();
    assert.deepStrictEqual(a.calls, [
      ["deepLink", "#/loops/l1"],
      ["deepLink", "#/loops/l%202"],
    ]);
  });

  it("caps the inline loop list so the menu cannot grow without bound", () => {
    const running = Array.from({ length: 20 }, (_, i) => ({ id: `l${i}`, label: `Loop ${i}` }));
    const template = buildTrayMenuTemplate({ presence: { approvals: 0, running, connected: true } });
    const parent = template.find((r) => Array.isArray(r.submenu));
    assert.equal(parent.label, "20 loops running", "the COUNT stays truthful even when the list is capped");
    assert.equal(parent.submenu.length, 8);
  });

  it("the login-item row is a checkbox that reflects and reports state", () => {
    const a = actions();
    const on = buildTrayMenuTemplate({ loginItem: { supported: true, enabled: true }, actions: a }).find(
      (r) => r.label === "Open at Login"
    );
    assert.equal(on.type, "checkbox");
    assert.equal(on.checked, true);
    assert.equal(on.enabled, true);
    on.click({ checked: false });
    assert.deepStrictEqual(a.calls, [["toggleLoginItem", false]]);
  });

  it("the login-item row is disabled where login items are unsupported", () => {
    const row = buildTrayMenuTemplate({ loginItem: { supported: false, enabled: false } }).find(
      (r) => r.label === "Open at Login"
    );
    assert.equal(row.enabled, false);
    assert.equal(row.checked, false);
  });

  it("says `not connected` when the gateway poll failed", () => {
    const header = buildTrayMenuTemplate({ presence: EMPTY_PRESENCE })[0];
    assert.match(header.label, /not connected/);
    assert.equal(header.enabled, false);
  });

  it("renders AMBIENT-SURFACES tiles when supplied and adds NOTHING when absent", () => {
    const without = buildTrayMenuTemplate({});
    const withTiles = buildTrayMenuTemplate({ tiles: [{ label: "Next meeting: 3pm" }] });
    assert.ok(!labels(without).includes("Next meeting: 3pm"));
    assert.ok(labels(withTiles).includes("Next meeting: 3pm"));
    // Non-blocking: an absent tile registry must not leave an empty section behind.
    assert.equal(withTiles.length, without.length + 2);
  });

  it("survives being built with no actions at all", () => {
    const template = buildTrayMenuTemplate();
    for (const row of clickableRows(template)) row.click();
    assert.ok(template.length >= 10);
  });
});

describe("degradation — no tray must never strand the user", () => {
  it("hides on close only while a menu-bar item can bring the window back", () => {
    assert.equal(shouldHideOnClose({ trayAvailable: true, isQuitting: false }), true);
    assert.equal(shouldHideOnClose({ trayAvailable: false, isQuitting: false }), false);
    assert.equal(shouldHideOnClose({ trayAvailable: true, isQuitting: true }), false);
  });

  it("quits on window-all-closed when there is no tray, even on macOS", () => {
    assert.equal(shouldQuitOnAllWindowsClosed({ platform: "darwin", trayAvailable: true }), false);
    assert.equal(
      shouldQuitOnAllWindowsClosed({ platform: "darwin", trayAvailable: false }),
      true,
      "a hidden window with no menu-bar item is the phantom state this prevents"
    );
    assert.equal(shouldQuitOnAllWindowsClosed({ platform: "win32", trayAvailable: true }), true);
  });
});

describe("makeTrayPresence", () => {
  const goodImage = { isEmpty: () => false, resize: () => ({ resized: true }) };
  const nativeImageOk = { createFromPath: () => goodImage };

  function fakeTray() {
    const calls = { titles: [], tooltips: [], menus: [], handlers: new Map(), destroyed: 0 };
    class Tray {
      constructor(icon) {
        calls.icon = icon;
      }
      setTitle(t) {
        calls.titles.push(t);
      }
      setToolTip(t) {
        calls.tooltips.push(t);
      }
      setContextMenu(m) {
        calls.menus.push(m);
      }
      on(evt, fn) {
        calls.handlers.set(evt, fn);
      }
      destroy() {
        calls.destroyed += 1;
      }
    }
    return { Tray, calls };
  }

  const MenuOk = { buildFromTemplate: (t) => ({ template: t }) };

  it("builds a tray, reports available, and renders a menu with rows in it", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: nativeImageOk,
      iconPath: "/icon.png",
      actions: { open: () => {} },
    });
    assert.equal(presence.start(), true);
    assert.equal(presence.available, true);
    assert.equal(calls.menus.length, 1);
    // Vacuity floor: an empty template would satisfy "a menu was set".
    assert.ok(calls.menus[0].template.length >= 10);
    assert.deepStrictEqual(calls.icon, { resized: true }, "the icon must be resized for the menu bar");
  });

  it("a missing/unreadable icon degrades to no tray — it does not throw", () => {
    const { Tray } = fakeTray();
    const logs = [];
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: { createFromPath: () => ({ isEmpty: () => true }) },
      iconPath: "/missing.png",
      log: (m) => logs.push(m),
    });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
    assert.ok(logs.some((m) => /icon/.test(m)));
    // And the fallback is reachable: with no tray, closing the window must close it.
    assert.equal(shouldHideOnClose({ trayAvailable: presence.available, isQuitting: false }), false);
  });

  it("a throwing nativeImage degrades to no tray", () => {
    const { Tray } = fakeTray();
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: {
        createFromPath: () => {
          throw new Error("bad png");
        },
      },
      iconPath: "/icon.png",
    });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
  });

  it("a platform with no Tray implementation degrades to no tray", () => {
    const presence = makeTrayPresence({ TrayCtor: null, MenuCtor: null, nativeImageMod: nativeImageOk, iconPath: "/i.png" });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
  });

  it("a Tray constructor that throws degrades to no tray", () => {
    class Boom {
      constructor() {
        throw new Error("no menu bar here");
      }
    }
    const logs = [];
    const presence = makeTrayPresence({
      TrayCtor: Boom,
      MenuCtor: MenuOk,
      nativeImageMod: nativeImageOk,
      iconPath: "/i.png",
      log: (m) => logs.push(m),
    });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
    assert.ok(logs.some((m) => /unavailable/.test(m)));
  });

  it("setPresence re-renders with the new counts, and the title follows", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, iconPath: "/i.png" });
    presence.start();
    presence.setPresence({ approvals: 2, running: [{ id: "l1", label: "L" }], connected: true });
    assert.equal(calls.titles.at(-1), "2");
    assert.match(calls.tooltips.at(-1), /2 approvals waiting, 1 loop running/);
    assert.ok(calls.menus.length >= 2, "a count change must rebuild the menu");
  });

  it("setCapturing routes DC-3's indicator through the one title writer", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, iconPath: "/i.png" });
    presence.start();
    presence.setPresence({ approvals: 5, connected: true });
    assert.equal(calls.titles.at(-1), "5");
    presence.setCapturing(true);
    assert.equal(calls.titles.at(-1), "● Listening");
    presence.setCapturing(false);
    assert.equal(calls.titles.at(-1), "5", "the badge comes back when capture ends");
  });

  it("methods on a tray-less presence are safe no-ops", () => {
    const presence = makeTrayPresence({ TrayCtor: null });
    presence.start();
    presence.setPresence({ approvals: 3 });
    presence.setCapturing(true);
    presence.setLoginItemState({ supported: true, enabled: true });
    presence.setTiles([{ label: "x" }]);
    presence.destroy();
    assert.equal(presence.available, false);
  });

  it("a tray destroyed mid-render is logged, not thrown", () => {
    class Dying {
      setContextMenu() {
        throw new Error("Object has been destroyed");
      }
      setTitle() {}
      setToolTip() {}
      on() {}
      destroy() {}
    }
    const logs = [];
    const presence = makeTrayPresence({
      TrayCtor: Dying,
      MenuCtor: MenuOk,
      nativeImageMod: nativeImageOk,
      iconPath: "/i.png",
      log: (m) => logs.push(m),
    });
    assert.equal(presence.start(), true);
    assert.ok(logs.some((m) => /render skipped/.test(m)));
  });

  it("destroy() tears the tray down once and flips available", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, iconPath: "/i.png" });
    presence.start();
    presence.destroy();
    presence.destroy();
    assert.equal(calls.destroyed, 1);
    assert.equal(presence.available, false);
  });

  it("clicking the tray icon opens the window", () => {
    const { Tray, calls } = fakeTray();
    let opened = 0;
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: nativeImageOk,
      iconPath: "/i.png",
      actions: { open: () => (opened += 1) },
    });
    presence.start();
    calls.handlers.get("click")();
    assert.equal(opened, 1);
  });
});
