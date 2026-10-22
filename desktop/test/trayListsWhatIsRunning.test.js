// The menu-bar item lists what is running now (#3889).
//
// It read `GET /api/loops` and listed the loops whose status was `running`, by name, and nothing
// else: a chat's turn, a helper, a workflow run, an automation's fire and a webhook's turn never
// showed, and a loop's row said nothing of what it was doing. It reads `GET /api/running` now, the
// one list every reader of live work shares, and each row says the work's name, its exact phase in
// the dashboard's words, what it is doing, when it last changed, and opens the work where it runs.
// A blocked row and a row that waits on you say so. The menu says when nothing runs, when the list
// cannot be read, when it is showing the last rows it knew while the gateway is away, what just
// finished or failed, and what a gateway restart cut off.

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  DEEP_LINKS,
  EMPTY_PRESENCE,
  INTERRUPTED_KEPT,
  INTERRUPTED_MS,
  MAX_LISTED_ROWS,
  PHASE_WORDS,
  bootRead,
  buildTrayMenuTemplate,
  carryPresence,
  composeTrayTooltip,
  summarizePresence,
} = require("../trayPresence");
const { hashAssignmentScript } = require("../deepLinks");

const UPDATED = "2026-10-21T10:04:00Z";

function row(over = {}) {
  return {
    work: "chat:c1", kind: "chat", session: "c1", phase: "searching", line: "Searching your knowledge",
    since: UPDATED, name: "Plan the garden", agent: "gardener", started_at: "2026-10-21T10:00:00Z",
    route: "#/chat/c1", blocked: false, waiting: [], ...over,
  };
}
const list = (running = [], finished = []) => ({ running, finished });
const clock = (iso) => new Date(Date.parse(iso)).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

function actions() {
  const calls = [];
  return { calls, deepLink: (href) => calls.push(href) };
}
const menu = (presence, a = actions()) => buildTrayMenuTemplate({ presence: { ...EMPTY_PRESENCE, ...presence }, actions: a });
const running = (template) => template.slice(1).find((r) => /running|could not be read/.test(r.label || ""));
const finished = (template) => template.find((r) => /^Just finished/.test(r.label || ""));

describe("each row says what the work is doing, and opens it", () => {
  it("its name, its exact phase, its line and its last update, and its route on a click", () => {
    const a = actions();
    const template = menu(summarizePresence([], list([row()])), a);
    const parent = running(template);
    assert.equal(parent.label, "1 running");
    const [item] = parent.submenu;
    assert.equal(item.label, `Plan the garden — Searching: Searching your knowledge · ${clock(UPDATED)}`);
    item.click();
    assert.deepStrictEqual(a.calls, ["#/chat/c1"]);
  });

  it("every kind of work is listed, not only loops", () => {
    const rows = [
      row(),
      row({ work: "subagent:h1", kind: "subagent", name: "grep the tree", phase: "tool", line: "Running shell" }),
      row({ work: "workflow:r1", kind: "workflow", session: "", name: "nightly", phase: "thinking", line: "Running", route: "#/workflows/runs/r1" }),
      row({ work: "trigger:t1", kind: "trigger", session: "", name: "Friday digest", phase: "thinking", line: "Running", route: "#/triggers?open=t1" }),
      row({ work: "webhook:deploy", kind: "webhook", session: "", name: "Deploy hook", phase: "thinking", line: "Running", route: "" }),
    ];
    const parent = running(menu(summarizePresence([], list(rows))));
    assert.equal(parent.label, "5 running");
    assert.deepStrictEqual(parent.submenu.slice(0, 5).map((i) => i.label.split(" — ")[0]),
      ["Plan the garden", "grep the tree", "nightly", "Friday digest", "Deploy hook"]);
  });

  it("work no page opens takes you to Mission Control, where every piece of live work is", () => {
    const a = actions();
    const parent = running(menu(summarizePresence([], list([row({ route: "" })])), a));
    parent.submenu[0].click();
    assert.deepStrictEqual(a.calls, [DEEP_LINKS.missionControl]);
  });

  it("🔴 a queued helper reads blocked, with its reason, and the count says how many are", () => {
    const rows = [row(), row({ work: "subagent:h4", kind: "subagent", name: "grep the tree", phase: "queued", line: "Waiting to start", blocked: true })];
    const parent = running(menu(summarizePresence([], list(rows))));
    assert.equal(parent.label, "2 running, 1 blocked");
    assert.equal(parent.submenu[1].label, `grep the tree — Blocked, Waiting to start · ${clock(UPDATED)}`);
  });

  it("a row that waits on you says so", () => {
    const parent = running(menu(summarizePresence([], list([row({ phase: "approval", line: "Needs your OK: shell", waiting: ["ask-1"] })]))));
    assert.match(parent.submenu[0].label, /— Needs your OK: Needs your OK: shell · .* · 1 waiting on you$/);
  });

  it("each route the list gives is a hash the window takes", () => {
    for (const route of ["#/chat/dashboard%3Ac1", "#/loops/0a1b2c3d", "#/code/0a1b2c3d", "#/workflows/runs/r1?node=root.draft", "#/triggers?open=t1"]) {
      assert.doesNotThrow(() => hashAssignmentScript(route), route);
    }
  });

  it("the words for each phase are the dashboard's own", () => {
    const table = fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "lib", "workPhase.ts"), "utf8");
    // Every phase the web lists, each with its row's label wherever the row puts it.
    const listed = table.match(/export const WORK_PHASES = \[([\s\S]*?)\] as const/);
    assert.ok(listed, "the web's phase list must be read (vacuity floor)");
    const phases = [...listed[1].matchAll(/'(\w+)'/g)].map((m) => m[1]);
    assert.ok(phases.length >= 15, "every phase must be read (vacuity floor)");
    const labels = Object.fromEntries(phases.map((phase) => {
      const look = table.match(new RegExp(`\\n\\s+${phase}: \\{[^}]*?\\blabel: '([^']*)'`));
      assert.ok(look, `the web's row for ${phase} must be read`);
      return [phase, look[1]];
    }));
    assert.deepStrictEqual(PHASE_WORDS, labels);
  });
});

describe("every state of the list", () => {
  it("empty: it says nothing runs", () => {
    const parent = running(menu(summarizePresence([], list())));
    assert.equal(parent.label, "Nothing running");
    assert.equal(parent.enabled, false);
  });

  it("large: the count is all of it, the first rows show, and the rest open in Mission Control", () => {
    const a = actions();
    const many = Array.from({ length: 30 }, (_, i) => row({ work: `subagent:h${i}`, kind: "subagent", name: `Helper ${i}` }));
    const parent = running(menu(summarizePresence([], list(many)), a));
    assert.equal(parent.label, "30 running");
    const more = parent.submenu.at(-1);
    assert.equal(more.label, `${30 - MAX_LISTED_ROWS} more in Mission Control…`);
    assert.equal(parent.submenu.filter((i) => / — /.test(i.label || "")).length, MAX_LISTED_ROWS);
    more.click();
    assert.deepStrictEqual(a.calls, [DEEP_LINKS.missionControl]);
  });

  it("a list that cannot be read is said so, never as nothing running", () => {
    const parent = running(menu(summarizePresence([], null)));
    assert.equal(parent.label, "What is running could not be read");
  });

  it("reconnecting: while the gateway is away it shows the last rows it knew, said as such", () => {
    const before = summarizePresence([], list([row()]));
    const away = carryPresence(before, summarizePresence(null, null));
    assert.equal(away.connected, false);
    const parent = running(menu(away));
    assert.equal(parent.label, "Last known: 1 running");
    assert.match(parent.submenu[0].label, /^Plan the garden — Searching/);
  });

  it("failed and just finished: each ending says how it ended and when, and opens the work", () => {
    const a = actions();
    const ended = "2026-10-21T10:06:00Z";
    const p = summarizePresence([], list([], [
      row({ work: "workflow:r9", kind: "workflow", session: "", name: "nightly", phase: "done", line: "Done in 12 seconds", since: ended, route: "#/workflows/runs/r9" }),
      row({ work: "subagent:h2", kind: "subagent", name: "fetch the page", phase: "error", line: "Stopped: the site refused", since: ended }),
    ]));
    const template = menu(p, a);
    assert.equal(running(template).label, "Nothing running");
    const tail = finished(template);
    assert.equal(tail.label, "Just finished: 2");
    assert.equal(tail.submenu[0].label, `nightly — Done: Done in 12 seconds · ${clock(ended)}`);
    assert.equal(tail.submenu[1].label, `fetch the page — Stopped: Stopped: the site refused · ${clock(ended)}`);
    tail.submenu[0].click();
    assert.deepStrictEqual(a.calls, ["#/workflows/runs/r9"]);
  });

  it("interrupted: work a restart cut off is said once, opens the work, and goes when it is live again", () => {
    let p = carryPresence(EMPTY_PRESENCE, summarizePresence([], list([row()])), { boot: 1, now: 0 });
    // The gateway goes away, and comes back as a new start: the turn is gone, and no ending of it was kept.
    p = carryPresence(p, summarizePresence(null, null), { boot: 1, now: 1000 });
    p = carryPresence(p, summarizePresence([], list()), { boot: 2, now: 2000 });
    const a = actions();
    const tail = finished(menu(p, a));
    assert.equal(tail.label, "Just finished: 1");
    assert.equal(tail.submenu[0].label, "Plan the garden — Interrupted: The gateway restarted while it ran.");
    tail.submenu[0].click();
    assert.deepStrictEqual(a.calls, ["#/chat/c1"]);
    // A second restart says it not twice.
    p = carryPresence(p, summarizePresence([], list()), { boot: 3, now: 3000 });
    assert.equal(finished(menu(p)).submenu.length, 1);
    // Its chat runs again: it is not cut off any more.
    p = carryPresence(p, summarizePresence([], list([row()])), { boot: 3, now: 4000 });
    assert.equal(finished(menu(p)), undefined);
  });

  it("interrupted: a restart between two polls, the gateway never seen away, is told", () => {
    let p = carryPresence(EMPTY_PRESENCE, summarizePresence([], list([row()])), { boot: 1, now: 0 });
    p = carryPresence(p, summarizePresence([], list()), { boot: 2, now: 5000 });
    assert.equal(finished(menu(p)).label, "Just finished: 1");
  });

  it("interrupted: a poll that could not read the list on the new start leaves the rows before it to compare", () => {
    let p = carryPresence(EMPTY_PRESENCE, summarizePresence([], list([row()])), { boot: 1, now: 0 });
    p = carryPresence(p, summarizePresence([], null), { boot: 2, now: 1000 });
    assert.equal(finished(menu(p)), undefined);
    // Nor a poll whose start could not be told (`main.js` passes none when one came mid-read).
    p = carryPresence(p, summarizePresence([], list()), { boot: null, now: 1500 });
    assert.equal(finished(menu(p)), undefined);
    p = carryPresence(p, summarizePresence([], list()), { boot: 2, now: 2000 });
    assert.equal(finished(menu(p)).submenu[0].label, "Plan the garden — Interrupted: The gateway restarted while it ran.");
  });

  it("interrupted: it goes after as long as the gateway keeps an ending, and keeps no more than it does", () => {
    let p = carryPresence(EMPTY_PRESENCE, summarizePresence([], list([row()])), { boot: 1, now: 0 });
    p = carryPresence(p, summarizePresence([], list()), { boot: 2, now: 1000 });
    assert.ok(finished(menu(p)));
    p = carryPresence(p, summarizePresence([], list()), { boot: 2, now: 1000 + INTERRUPTED_MS + 1 });
    assert.equal(finished(menu(p)), undefined);

    const many = Array.from({ length: INTERRUPTED_KEPT + 5 }, (_, i) => row({ work: `subagent:h${i}`, kind: "subagent", name: `Helper ${i}` }));
    let q = carryPresence(EMPTY_PRESENCE, summarizePresence([], list(many)), { boot: 1, now: 0 });
    q = carryPresence(q, summarizePresence([], list()), { boot: 2, now: 1000 });
    assert.equal(q.interrupted.length, INTERRUPTED_KEPT);
    assert.equal(finished(menu(q)).submenu.length, MAX_LISTED_ROWS, "the submenu is capped as the running one is");
  });

  it("🪤 VACUITY: back on the same start, nothing is cut off", () => {
    let p = carryPresence(EMPTY_PRESENCE, summarizePresence([], list([row()])), { boot: 1, now: 0 });
    p = carryPresence(p, summarizePresence(null, null), { boot: 1, now: 1000 });
    p = carryPresence(p, summarizePresence([], list()), { boot: 1, now: 2000 });
    assert.equal(finished(menu(p)), undefined);
  });

  it("reconnecting with only endings kept says it is the last known, not that nothing runs", () => {
    const before = summarizePresence([], list([], [row({ phase: "done", line: "Done in 3 seconds" })]));
    const parent = running(menu(carryPresence(before, summarizePresence(null, null))));
    assert.equal(parent.label, "Last known: nothing running");
  });

  it("a stamp with no zone names no instant, and says no time", () => {
    const parent = running(menu(summarizePresence([], list([row({ since: "2026-10-21T10:04:00" })]))));
    assert.equal(parent.submenu[0].label, "Plan the garden — Searching: Searching your knowledge");
  });

  it("a name cannot turn the rest of its row around", () => {
    const parent = running(menu(summarizePresence([], list([row({ name: "evil\u202eoot" })]))));
    assert.match(parent.submenu[0].label, /^eviloot — /);
  });
});

describe("the tooltip and the poll", () => {
  it("the tooltip counts what runs, not only loops, and a list it could not read is no zero", () => {
    assert.match(composeTrayTooltip(summarizePresence({ waiting: [1, 2] }, list([row(), row({ work: "chat:c2" }), row({ work: "chat:c3" })]))), /2 waiting on you, 3 running/);
    assert.match(composeTrayTooltip(summarizePresence([], null)), /what is running could not be read/);
    const kept = carryPresence(summarizePresence([], list([row()])), summarizePresence([], null));
    assert.match(composeTrayTooltip(kept), /last known 1 running/);
  });

  it("main.js reads the running list on each ring, and tells a restart by the starts it took", () => {
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    assert.match(main, /getGateway\("\/api\/running"\)/);
    assert.doesNotMatch(main, /getGateway\("\/api\/loops"\)/);
    // The count is everything waiting on you (#3888), never the approvals alone.
    assert.match(main, /getGateway\("\/api\/waiting"\)/);
    assert.doesNotMatch(main, /getGateway\("\/api\/approvals"\)/);
    assert.match(main, /function adoptLocalGateway\(ready\) \{\s+localGatewayUrl = ready\.url;\s+gatewayStarts \+= 1;/);
    // The start is counted before the reads go out (the lists and Buddy status's policy) and
    // compared after they come back.
    assert.match(main, /const before = gatewayStarts;\s+const \[waiting, running, policy\] = await Promise\.all\(\[[\s\S]*?\]\);\s+const boot = bootRead\(before, gatewayStarts\);/);
    assert.match(main, /carryPresence\(trayPresence\.presence, summarizePresence\(waiting, running\), \{ boot \}\)/);
  });

  it("a poll tells which start it read only when no other came while its reads were out", () => {
    assert.equal(bootRead(2, 2), 2);
    assert.equal(bootRead(2, 3), null, "a restart mid-read: the reads went to the start before it");
    assert.equal(bootRead(0, 0), null, "no start taken yet");
  });
});
