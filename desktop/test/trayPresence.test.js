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
  trayImageFile,
  TRAY_STILLS,
  STILL_FAMILY,
  ROW_MAX,
  TOOLTIP_MAX,
  trayStill,
  stateWords,
  trayStillFile,
  dockStillFile,
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
  it("counts everything waiting on you — the documented GET /api/waiting shape", () => {
    // An approval, a workflow's gate and a loop's question: the tray counts every kind the list
    // holds, where it once counted `GET /api/approvals` alone.
    const list = { waiting: [{ kind: "approval" }, { kind: "gate" }, { kind: "loop_question" }], unreadable: [] };
    const p = summarizePresence(list, { running: [], finished: [] });
    assert.equal(p.waiting, 3);
    assert.equal(p.connected, true);
  });

  it("a failed poll (null) is `not connected` with zero counts, not a crash", () => {
    const p = summarizePresence(null, null);
    assert.deepStrictEqual(p, EMPTY_PRESENCE);
  });

  it("drops a running row with no work reference — an unclickable menu row is worse than none", () => {
    const p = summarizePresence([], { running: [{ name: "no work", phase: "thinking" }], finished: [] });
    assert.deepStrictEqual(p.running, []);
  });

  it("tolerates another envelope rather than reporting zero", () => {
    assert.equal(summarizePresence([1, 2], {}).waiting, 2);
    assert.equal(summarizePresence({ items: [1, 2, 3] }, {}).waiting, 3);
    assert.equal(summarizePresence({ pending: 7 }, {}).waiting, 7);
  });
});

describe("composeTrayTitle — the single writer for the menu-bar title", () => {
  it("capture beats the waiting badge", () => {
    // The indicator and the badge both want `tray.setTitle`. The privacy
    // signal wins; without one composer the later caller would silently erase the
    // other's text.
    assert.equal(composeTrayTitle({ capturing: true, waiting: 4 }), "● Listening");
  });

  it("shows the count when not capturing, and nothing at zero", () => {
    assert.equal(composeTrayTitle({ capturing: false, waiting: 4 }), "4");
    assert.equal(composeTrayTitle({ capturing: false, waiting: 0 }), "");
    assert.equal(composeTrayTitle(), "");
  });

  it("tooltip states connectedness, and capture still wins", () => {
    assert.match(composeTrayTooltip({ connected: false }), /not connected/);
    assert.match(composeTrayTooltip({ connected: true, waiting: 1, running: [{}] }), /1 waiting on you, 1 running/);
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
      toggleBuddy: (v) => calls.push(["toggleBuddy", v]),
      hideBuddy: (span) => calls.push(["hideBuddy", span]),
      showBuddyNow: () => calls.push(["showBuddyNow"]),
      quit: () => calls.push(["quit"]),
    };
  };

  it("renders every row the menu promises, and inspects a non-zero number of rows", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({
      presence: { ...EMPTY_PRESENCE, waiting: 3, running: [{ work: "loop:l1", name: "Loop one", phase: "Thinking", line: "", since: "", route: "#/loops/l1", blocked: false, waiting: 0 }], connected: true, listed: true },
      loginItem: { supported: true, enabled: false },
      actions: a,
    });

    // VACUITY FLOOR: deleting the feature must not make this suite pass by matching
    // nothing. A menu shorter than this cannot carry the menu's five commitments.
    assert.ok(template.length >= 10, `expected a full menu, got ${template.length} rows`);
    assert.ok(clickableRows(template).length >= 5, "at least five rows must actually do something");

    const text = labels(template).join(" | ");
    assert.match(text, /3 waiting on you/);
    assert.match(text, /1 running/);
    assert.match(text, /Quick Capture Note/);
    assert.match(text, /Open PersonalClaw/);
    assert.match(text, /Open at Login/);
    assert.match(text, /Quit PersonalClaw/);
  });

  it("the waiting row deep-links to the surface that lists every kind waiting on you", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({ presence: { waiting: 2, running: [], connected: true }, actions: a });
    // The first line says the state in words; the row under it is the one that opens the list.
    const row = template.slice(1).find((r) => /2 waiting on you/.test(r.label || ""));
    row.click();
    assert.deepStrictEqual(a.calls, [["deepLink", DEEP_LINKS.waiting]]);
    assert.equal(DEEP_LINKS.waiting, "#/mission-control", "Mission Control lists everything waiting on you");
  });

  it("stays click-through at zero instead of becoming a dead end", () => {
    const a = actions();
    const template = buildTrayMenuTemplate({ presence: { waiting: 0, running: [], connected: true }, actions: a });
    const row = template.find((r) => /Nothing waiting on you/.test(r.label || ""));
    assert.equal(typeof row.click, "function");
    row.click();
    assert.deepStrictEqual(a.calls, [["deepLink", "#/mission-control"]]);
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

  it("the buddy row is a checkbox, off until the owner turns it on, and reports the switch (#3831)", () => {
    const a = actions();
    const off = buildTrayMenuTemplate({ actions: a }).find((r) => r.label === "Show Buddy");
    assert.equal(off.type, "checkbox");
    assert.equal(off.checked, false, "off by default");
    off.click({ checked: true });
    const on = buildTrayMenuTemplate({ buddy: { enabled: true }, actions: a }).find((r) => r.label === "Show Buddy");
    assert.equal(on.checked, true);
    on.click({ checked: false });
    assert.deepStrictEqual(a.calls, [["toggleBuddy", true], ["toggleBuddy", false]]);
  });

  it("a buddy that is on can be hidden for an hour or until tomorrow, and a hidden one shown now (#3832)", () => {
    const a = actions();
    const labels = (buddy) => buildTrayMenuTemplate({ buddy, actions: a }).map((r) => r.label).filter(Boolean);
    assert.equal(labels({ enabled: false }).some((l) => /Hide Buddy|Show Buddy Now/.test(l)), false, "nothing to hide while it is off");
    const shown = buildTrayMenuTemplate({ buddy: { enabled: true }, actions: a });
    assert.deepEqual(labels({ enabled: true }).filter((l) => /Hide Buddy|Show Buddy Now/.test(l)), ["Hide Buddy for an Hour", "Hide Buddy Until Tomorrow"]);
    shown.find((r) => r.label === "Hide Buddy for an Hour").click();
    shown.find((r) => r.label === "Hide Buddy Until Tomorrow").click();
    const hidden = buildTrayMenuTemplate({ buddy: { enabled: true, hidden: true }, actions: a });
    assert.deepEqual(labels({ enabled: true, hidden: true }).filter((l) => /Hide Buddy|Show Buddy Now/.test(l)), ["Show Buddy Now"]);
    hidden.find((r) => r.label === "Show Buddy Now").click();
    assert.deepStrictEqual(a.calls, [["hideBuddy", "hour"], ["hideBuddy", "tomorrow"], ["showBuddyNow"]]);
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
    assert.equal(header.label, "PersonalClaw — Not connected to the gateway");
    assert.equal(header.enabled, false);
  });

  it("has no ambient-tiles seam: no option, no rows, and nothing in desktop/ names one (#3896)", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const plain = buildTrayMenuTemplate({});
    const offered = buildTrayMenuTemplate({ tiles: [{ label: "Next meeting: 3pm" }] });
    assert.deepEqual(labels(offered), labels(plain), "a tiles option is ignored, not rendered");
    const dir = path.join(__dirname, "..");
    for (const file of fs.readdirSync(dir).filter((f) => f.endsWith(".js"))) {
      assert.doesNotMatch(fs.readFileSync(path.join(dir, file), "utf8"), /\btiles\b|setTiles/, file);
    }
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
      trayImagePath: "/tray.png",
      actions: { open: () => {} },
    });
    assert.equal(presence.start(), true);
    assert.equal(presence.available, true);
    assert.equal(calls.menus.length, 1);
    // Vacuity floor: an empty template would satisfy "a menu was set".
    assert.ok(calls.menus[0].template.length >= 10);
  });

  it("loads the menu-bar image it is given, as drawn, and never resizes it", () => {
    // The menu-bar files are rendered at 18 points (with an @2x beside them), so the tray
    // takes them as they are. Resizing the 1024 app icon at run time is what the tray did
    // before they existed.
    const { Tray, calls } = fakeTray();
    const loaded = [];
    let resized = 0;
    const image = {
      isEmpty: () => false,
      resize: () => {
        resized += 1;
        return { resized: true };
      },
    };
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: {
        createFromPath: (p) => {
          loaded.push(p);
          return image;
        },
      },
      trayImagePath: "/app/trayTemplate.png",
    });
    assert.equal(presence.start(), true);
    assert.deepStrictEqual(loaded, ["/app/trayTemplate.png"]);
    assert.equal(resized, 0, "the menu-bar image must not be resized");
    assert.equal(calls.icon, image, "the tray is built from the loaded image itself");
  });

  it("picks the template image on macOS and the gradient image elsewhere", () => {
    // `Template` in the name is what makes macOS treat the image as a template image.
    assert.equal(trayImageFile("darwin"), "trayTemplate.png");
    assert.equal(trayImageFile("linux"), "tray.png");
  });

  it("main.js hands the tray the platform's file, never the app icon", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    const site = /makeTrayPresence\(\{[\s\S]*?\n\}\);/.exec(main);
    assert.ok(site, "the tray wiring site must be findable");
    assert.match(site[0], /trayImagePath:\s*path\.join\(__dirname,\s*trayImageFile\(process\.platform\)\)/);
    // The app icon is the dock's to go back to (#3896), never the menu bar's image.
    assert.deepEqual(site[0].split("\n").filter((l) => /icon\.png/.test(l)).map((l) => l.trim()), ['appIconPath: path.join(__dirname, "icon.png"),']);
  });

  it("with no menu-bar image path there is no tray", () => {
    const { Tray } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
  });

  it("a missing/unreadable icon degrades to no tray — it does not throw", () => {
    const { Tray } = fakeTray();
    const logs = [];
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: MenuOk,
      nativeImageMod: { createFromPath: () => ({ isEmpty: () => true }) },
      trayImagePath: "/missing.png",
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
      trayImagePath: "/tray.png",
    });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
  });

  it("a platform with no Tray implementation degrades to no tray", () => {
    const presence = makeTrayPresence({ TrayCtor: null, MenuCtor: null, nativeImageMod: nativeImageOk, trayImagePath: "/i.png" });
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
      trayImagePath: "/i.png",
      log: (m) => logs.push(m),
    });
    assert.equal(presence.start(), false);
    assert.equal(presence.available, false);
    assert.ok(logs.some((m) => /unavailable/.test(m)));
  });

  it("setPresence re-renders with the new counts, and the title follows", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, trayImagePath: "/i.png" });
    presence.start();
    presence.setPresence({ waiting: 2, running: [{ work: "loop:l1", name: "L", phase: "Thinking", line: "", since: "", route: "", blocked: false, waiting: 0 }], connected: true, listed: true });
    assert.equal(calls.titles.at(-1), "2");
    assert.match(calls.tooltips.at(-1), /2 waiting on you, 1 running/);
    assert.ok(calls.menus.length >= 2, "a count change must rebuild the menu");
  });

  it("setCapturing routes the indicator through the one title writer", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, trayImagePath: "/i.png" });
    presence.start();
    presence.setPresence({ waiting: 5, connected: true });
    assert.equal(calls.titles.at(-1), "5");
    presence.setCapturing(true);
    assert.equal(calls.titles.at(-1), "● Listening");
    presence.setCapturing(false);
    assert.equal(calls.titles.at(-1), "5", "the badge comes back when capture ends");
  });

  it("methods on a tray-less presence are safe no-ops", () => {
    const presence = makeTrayPresence({ TrayCtor: null });
    presence.start();
    presence.setPresence({ waiting: 3 });
    presence.setCapturing(true);
    presence.setLoginItemState({ supported: true, enabled: true });
    presence.setRestarting(true);
    presence.appearanceChanged();
    presence.setBuddyState({ enabled: true });
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
      trayImagePath: "/i.png",
      log: (m) => logs.push(m),
    });
    assert.equal(presence.start(), true);
    assert.ok(logs.some((m) => /render skipped/.test(m)));
  });

  it("destroy() tears the tray down once and flips available", () => {
    const { Tray, calls } = fakeTray();
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: MenuOk, nativeImageMod: nativeImageOk, trayImagePath: "/i.png" });
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
      trayImagePath: "/i.png",
      actions: { open: () => (opened += 1) },
    });
    presence.start();
    calls.handlers.get("click")();
    assert.equal(opened, 1);
  });
});

// ── The quick-capture row must reach a capability, not a bare navigation ───────────────────
//
// The tray first shipped the row and its deep link; nothing read the flag, so the menu item opened the
// inbox and wrote nothing. Measured at the time: `capture=1` appeared in exactly one place in
// the product (`main.js`) and zero places in `web/src`.
//
// Two gaps this closes, both of which let that ship:
//
//   1. The row had NO behavioural test. `buildTrayMenuTemplate` was asserted to CONTAIN the
//      label ("Quick Capture Note"), and the only thing that ever invoked its handler was the
//      blanket "survives being built with no actions at all" case — which calls every row
//      against noops and therefore cannot tell a wired row from a dead one.
//   2. The deep link's URL was untested entirely, in either repo half. So the tray and the SPA
//      could drift on the flag NAME with nothing to catch it — one renames `capture`, the
//      other keeps reading it, and the row silently reverts to a navigation.
describe("quick capture reaches the note capability", () => {
  const spy = () => {
    const calls = [];
    return {
      calls,
      open: () => calls.push(["open"]),
      deepLink: (href) => calls.push(["deepLink", href]),
      quickCapture: () => calls.push(["quickCapture"]),
      quit: () => calls.push(["quit"]),
    };
  };

  it("the Quick Capture row invokes quickCapture, and nothing else does", () => {
    const a = spy();
    const template = buildTrayMenuTemplate({ presence: EMPTY_PRESENCE, actions: a });
    const row = template.find((r) => /Quick Capture Note/.test(r.label || ""));
    assert.ok(row, "the row must exist to be wired (vacuity floor)");
    assert.equal(typeof row.click, "function", "a label with no handler IS the inert control");
    row.click();
    assert.deepStrictEqual(a.calls, [["quickCapture"]]);
  });

  it("no OTHER row fires quickCapture", () => {
    // Otherwise the assertion above could pass on a menu that captures a note when the user
    // meant to open the dashboard.
    const template = buildTrayMenuTemplate({ presence: EMPTY_PRESENCE, actions: spy() });
    const rows = template.filter((r) => /Quick Capture Note/.test(r.label || ""));
    assert.equal(rows.length, 1, "exactly one row owns the capture intent");
  });
});

describe("main.js quick-capture deep link agrees with the SPA that reads it", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const root = path.join(__dirname, "..", "..");
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const inboxPage = fs.readFileSync(
    path.join(root, "web", "src", "pages", "inbox", "InboxPage.tsx"),
    "utf8",
  );
  /** Comments stripped: main.js DISCUSSES the flag in prose, and this rail is about code. */
  const mainCode = main.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

  it("reads a main.js that actually wires the tray (vacuity floor)", () => {
    assert.ok(mainCode.length > 5000, "main.js looks truncated — the rails below prove nothing");
    assert.match(mainCode, /makeTrayPresence\(\{/, "the tray wiring site must be findable");
  });

  it("the shell builds the inbox deep link with ?capture=1", () => {
    assert.match(mainCode, /quickCapture:\s*\(\)\s*=>\s*deepLink\(`\$\{DEEP_LINKS\.inbox\}\?capture=1`\)/);
    assert.equal(DEEP_LINKS.inbox, "#/inbox", "the SPA route the flag rides on");
  });

  it("and the SPA reads that exact flag, so the row is not a navigation", () => {
    // The two halves live in different repo directories and ship together; this is the only
    // place that can see both. `useQueryFlag` treats '1' as on, which is what `?capture=1`
    // sends — asserted here rather than assumed, because a flag read as a truthy STRING would
    // also accept `?capture=0`.
    assert.match(inboxPage, /useQueryFlag\(query,\s*setQuery,\s*'capture'\)/);
    const hook = fs.readFileSync(
      path.join(root, "web", "src", "app", "useQueryState.ts"),
      "utf8",
    );
    assert.match(hook, /query\[key\] === '1'/);
  });

  it("the shell still mints no endpoint of its own", () => {
    // The capability is core's. A shell that started POSTing would be a consumer defining
    // its owner's contract — the thing the tray correctly refused to do.
    assert.ok(
      !/\/api\/inbox/.test(mainCode),
      "the desktop shell must reach the inbox through the SPA, not by calling the API",
    );
  });
});

// ── The buddy's state on the tray and the dock (#3896) ─────────────────────────────────────────

describe("the tray's still orb", () => {
  const live = (over = {}) => ({ ...EMPTY_PRESENCE, connected: true, listed: true, ...over });
  const row = (over = {}) => ({ work: "chat:1", name: "A chat", phase: "Thinking", phaseKey: "thinking", line: "", since: "", route: "#/chat/1", blocked: false, waiting: 0, ...over });

  it("is one of six, each a variant inside its family", () => {
    assert.deepEqual([...TRAY_STILLS], ["idle", "working", "needs-you", "error", "listening", "offline"]);
    assert.deepEqual({ ...STILL_FAMILY }, { idle: "quiet", working: "active", listening: "active", "needs-you": "waiting", error: "blocked", offline: "blocked" });
  });

  it("follows the state: each of the six from its own presence, and listening wins over the rest", () => {
    assert.equal(trayStill(live()), "idle");
    assert.equal(trayStill(live({ running: [row()] })), "working");
    assert.equal(trayStill(live({ waiting: 2, running: [row()] })), "needs-you");
    assert.equal(trayStill(live({ finished: [row({ phase: "Stopped", phaseKey: "error" })] })), "error");
    assert.equal(trayStill(live({ running: [row({ blocked: true })] })), "error", "blocked with nothing waiting on you");
    assert.equal(trayStill(live({ running: [row({ blocked: true, waiting: 1 })], waiting: 1 })), "needs-you");
    assert.equal(trayStill(live({ interrupted: [{ row: row(), at: 0 }] })), "error", "work a restart cut off");
    assert.equal(trayStill(EMPTY_PRESENCE), "offline");
    for (const p of [live(), live({ waiting: 3 }), EMPTY_PRESENCE, live({ finished: [row({ phaseKey: "error" })] })]) {
      assert.equal(trayStill(p, { capturing: true }), "listening");
    }
  });

  it("takes an announced restart from the doorbell's connection, the one place the shell hears it", () => {
    const main = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "main.js"), "utf8");
    const site = /onConnection: \(state\) => \{[\s\S]*?\n  \},/.exec(main);
    assert.ok(site, "the doorbell's onConnection must be findable");
    assert.match(site[0], /trayPresence\.setRestarting\(state === "restarting"\);/);
    assert.equal((main.match(/setRestarting\(/g) || []).length, 1, "nothing else says restarting");
  });

  it("shows an announced restart as working, said as restarting, never offline", () => {
    assert.equal(trayStill(EMPTY_PRESENCE, { restarting: true }), "working");
    assert.equal(stateWords(EMPTY_PRESENCE, { restarting: true }), "PersonalClaw is restarting");
  });

  it("names the exact state in words for every state, and a count only supports the words", () => {
    const cases = [
      [live(), {}, "Nothing running"],
      [live({ running: [row(), row({ work: "chat:2" })] }), {}, "Working: 2 running"],
      [live({ waiting: 3 }), {}, "Needs you: 3 waiting on you"],
      [live({ finished: [row({ phaseKey: "error" })] }), {}, "Something stopped"],
      [live(), { capturing: true }, "Listening"],
      [EMPTY_PRESENCE, {}, "Not connected to the gateway"],
    ];
    const seen = new Set();
    for (const [p, flags, words] of cases) {
      seen.add(trayStill(p, flags));
      assert.equal(stateWords(p, flags), words);
      const header = buildTrayMenuTemplate({ presence: p, flags })[0];
      assert.equal(header.label, `PersonalClaw — ${words}`, "the menu's first line");
      assert.ok(header.label.length <= ROW_MAX);
      if (!flags.capturing) {
        const tip = composeTrayTooltip({ ...p, buddyOn: true });
        assert.equal(tip, `PersonalClaw — ${words}`, "the tooltip");
        assert.doesNotMatch(tip, /—\s*\d+\s*$/, "never a bare count");
      }
    }
    assert.equal(seen.size, 6, "every still has its words");
  });

  it("keeps the tooltip within 80 characters and the first line within 56", () => {
    const p = live({ running: [row()], stale: true, finished: [row({ phaseKey: "error" })] });
    assert.ok(composeTrayTooltip({ ...p, buddyOn: true }).length <= TOOLTIP_MAX);
    assert.ok(buildTrayMenuTemplate({ presence: p })[0].label.length <= ROW_MAX);
  });

  it("puts no count beside the still while Buddy status is on, and keeps capture's title", () => {
    assert.equal(composeTrayTitle({ waiting: 4, buddyOn: true }), "");
    assert.equal(composeTrayTitle({ waiting: 4, buddyOn: true, capturing: true }), "● Listening");
    assert.equal(composeTrayTitle({ waiting: 4 }), "4", "Buddy status off: the menu bar as before");
  });

  it("is a template image on macOS, and the colour one for the appearance elsewhere", () => {
    assert.equal(trayStillFile("needs-you", { platform: "darwin" }), "buddy/tray-needs-youTemplate.png");
    assert.equal(trayStillFile("needs-you", { platform: "linux", dark: true }), "buddy/tray-needs-you-dark.png");
    assert.equal(trayStillFile("idle", { platform: "linux", dark: false }), "buddy/tray-idle-light.png");
    assert.equal(trayStillFile(null, { platform: "darwin" }), trayImageFile("darwin"), "no still: the claw");
    for (const still of TRAY_STILLS) {
      for (const [platform, dark] of [["darwin", false], ["linux", false], ["linux", true]]) {
        const file = require("node:path").join(__dirname, "..", trayStillFile(still, { platform, dark }));
        assert.ok(require("node:fs").existsSync(file), `${file} is rendered`);
      }
    }
  });
});

describe("makeTrayPresence with the buddy's stills", () => {
  const goodImage = (file) => ({ file, isEmpty: () => false });
  function rig({ platform = "darwin", dark = false } = {}) {
    const calls = { images: [], dockIcons: [] };
    class Tray {
      setTitle() {}
      setToolTip() {}
      setContextMenu() {}
      on() {}
      destroy() {}
      setImage(image) { calls.images.push(require("node:path").basename(image.file)); }
    }
    const presence = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: { buildFromTemplate: (t) => t },
      nativeImageMod: { createFromPath: goodImage },
      trayImagePath: "/app/trayTemplate.png",
      platform,
      isDark: () => dark,
      dock: platform === "darwin" ? { setIcon: (image) => calls.dockIcons.push(require("node:path").basename(image.file)) } : null,
      appIconPath: "/app/icon.png",
    });
    presence.start();
    return { presence, calls };
  }
  const live = (over = {}) => ({ ...EMPTY_PRESENCE, connected: true, listed: true, ...over });

  it("keeps the claw and the app icon while Buddy status is off", () => {
    const { presence, calls } = rig();
    presence.setPresence(live({ waiting: 2 }));
    assert.deepEqual(calls.images, []);
    assert.deepEqual(calls.dockIcons, []);
  });

  it("swaps the image on a change of state, once per change, and listening wins", () => {
    const { presence, calls } = rig();
    presence.setBuddyState({ enabled: true });
    presence.setPresence(live());
    presence.setPresence(live());
    presence.setPresence(live({ waiting: 1 }));
    presence.setCapturing(true);
    presence.setCapturing(false);
    presence.setPresence(EMPTY_PRESENCE);
    presence.setRestarting(true);
    assert.deepEqual(calls.images, [
      "tray-offlineTemplate.png",
      "tray-idleTemplate.png",
      "tray-needs-youTemplate.png",
      "tray-listeningTemplate.png",
      "tray-needs-youTemplate.png",
      "tray-offlineTemplate.png",
      "tray-workingTemplate.png",
    ]);
    presence.setBuddyState({ enabled: false });
    assert.equal(calls.images.at(-1), "trayTemplate.png", "off again: the claw");
  });

  it("follows the appearance with the colour stills on Linux", () => {
    let dark = false;
    const calls = { images: [] };
    class Tray { setTitle() {} setToolTip() {} setContextMenu() {} on() {} destroy() {} setImage(i) { calls.images.push(require("node:path").basename(i.file)); } }
    const presence = makeTrayPresence({ TrayCtor: Tray, MenuCtor: { buildFromTemplate: (t) => t }, nativeImageMod: { createFromPath: goodImage }, trayImagePath: "/app/tray.png", platform: "linux", isDark: () => dark });
    presence.start();
    presence.setBuddyState({ enabled: true });
    presence.setPresence(live());
    dark = true;
    presence.appearanceChanged();
    assert.deepEqual(calls.images.slice(-2), ["tray-idle-light.png", "tray-idle-dark.png"]);
  });

  it("shows the dock's still only while something needs the owner, and the app icon after", () => {
    const { presence, calls } = rig({ dark: true });
    presence.setBuddyState({ enabled: true });
    presence.setPresence(live({ running: [{ work: "w", name: "W", phase: "Thinking", phaseKey: "thinking", line: "", since: "", route: "", blocked: false, waiting: 0 }] }));
    assert.deepEqual(calls.dockIcons, [], "working is not something that needs the owner");
    presence.setPresence(live({ waiting: 1 }));
    presence.setPresence(live({ waiting: 2 }));
    presence.setPresence(live());
    assert.deepEqual(calls.dockIcons, ["dock-needs-you-dark.png", "icon.png"]);
    assert.equal(dockStillFile(live({ waiting: 1 }), { platform: "linux", buddyOn: true }), null, "no dock on Linux");
    assert.equal(dockStillFile(live({ waiting: 1 }), { platform: "darwin", buddyOn: false }), null, "Buddy status off");
  });
});
