const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { DEEP_LINKS, MAX_LISTED_ROWS, ROW_MAX, WAITING_KIND_LABEL, buildTrayMenuTemplate, summarizePresence, waitingRowOf } = require("../trayPresence");

// ── The tray menu lists what waits on you, then live work (#3896) ─────────────────────────────
//
// The room budget's tray menu (`docs/design/buddy/surfaces.md`): everything waiting on the owner,
// in the list's order, then live work, each one line. Each waiting item is its own row, in the
// app's own words for its kind and its text, at most `ROW_MAX` characters, opening its route; past
// the menu's cap, the rest is one row that opens Mission Control, where every one of them is.

const GATE = { id: "gate:r1", kind: "gate", brief: { ask: { prompt: "Deploy the release now?" }, workflow: "release" }, route: "#/workflows/runs/r1" };
const QUESTION = { id: "loop_question:l1", kind: "loop_question", brief: { question: "Which branch should the sweep use?", name: "Sweep" }, route: "#/loops/l1" };
const HELPER = { work: "helper:h1", kind: "helper", session: "s1", phase: "tool", line: "Reading the docs", since: null, name: "Research helper", route: "#/chat/s1", blocked: false, waiting: [] };

function menu(waiting, running = []) {
  const calls = [];
  const presence = summarizePresence({ waiting, unreadable: [] }, { running, finished: [] });
  const template = buildTrayMenuTemplate({ presence, actions: { deepLink: (href) => calls.push(href) } });
  return { template, calls, presence };
}

const labelsOf = (template) => template.map((r) => r.label || "");

describe("each waiting item is its own row", () => {
  it("a gate, a question and a running helper are all listed: what waits first, in the list's order, then live work", () => {
    const { template } = menu([GATE, QUESTION], [HELPER]);
    const text = labelsOf(template);
    const gate = text.indexOf("Workflow: Deploy the release now?");
    const question = text.indexOf("Loop: Which branch should the sweep use?");
    const running = text.findIndex((l) => /^1 running/.test(l));
    assert.ok(gate > 0, `the gate has a row: ${text.join(" | ")}`);
    assert.ok(question > gate, "the question comes after the gate, as the list orders them");
    assert.ok(running > question, "live work comes after what waits on you");
    const helper = template[running].submenu.map((r) => r.label);
    assert.match(helper[0], /^Research helper — /, "the running helper is listed");
  });

  it("each row opens its own route", () => {
    const { template, calls } = menu([GATE, QUESTION]);
    template.find((r) => r.label === "Workflow: Deploy the release now?").click();
    template.find((r) => r.label === "Loop: Which branch should the sweep use?").click();
    assert.deepStrictEqual(calls, ["#/workflows/runs/r1", "#/loops/l1"]);
  });

  it("an item with no route opens Mission Control, where every item is", () => {
    const { template, calls } = menu([{ ...GATE, route: "" }]);
    template.find((r) => (r.label || "").startsWith("Workflow:")).click();
    assert.deepStrictEqual(calls, [DEEP_LINKS.waiting]);
  });

  it(`keeps each row within ${ROW_MAX} characters`, () => {
    const long = { ...QUESTION, brief: { question: "Which of the three release branches should the nightly sweep merge into first, and why?" } };
    const { template } = menu([long]);
    const row = template.find((r) => (r.label || "").startsWith("Loop:"));
    assert.ok(row.label.length <= ROW_MAX, `${row.label.length}: ${row.label}`);
    assert.ok(row.label.endsWith("…"), "a cut row says it was cut");
  });

  it("past the cap, the rest is one row that opens Mission Control", () => {
    const many = Array.from({ length: MAX_LISTED_ROWS + 3 }, (_, i) => ({ ...GATE, id: `gate:r${i}`, brief: { ask: { prompt: `Step ${i}?` } }, route: `#/workflows/runs/r${i}` }));
    const { template, calls } = menu(many);
    const text = labelsOf(template);
    assert.equal(text.filter((l) => l.startsWith("Workflow: Step")).length, MAX_LISTED_ROWS);
    const rest = template.find((r) => r.label === "3 more waiting on you…");
    assert.ok(rest, `the overflow row: ${text.join(" | ")}`);
    rest.click();
    assert.deepStrictEqual(calls, [DEEP_LINKS.waiting]);
  });

  it("an item whose source said nothing is named by its kind", () => {
    const { template } = menu([{ id: "approval:a1", kind: "approval", brief: {}, route: "#/chat/s1" }]);
    assert.ok(labelsOf(template).includes("Approval: A tool call"));
  });

  it("an item of a kind the shell does not know is still listed, as waiting on you", () => {
    const { template } = menu([{ id: "x:1", kind: "something_new", brief: {}, route: "#/mission-control" }]);
    assert.ok(labelsOf(template).includes("Waiting on you"));
  });

  it("summarizePresence keeps the items in the list's order, beside the count", () => {
    const { presence } = menu([GATE, QUESTION]);
    assert.equal(presence.waiting, 2);
    assert.deepStrictEqual(presence.waitingRows.map((r) => r.label), ["Workflow: Deploy the release now?", "Loop: Which branch should the sweep use?"]);
  });
});

describe("in the app's own words", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "lib", "waitingList.ts"), "utf8");

  it("each kind is called what a card calls it", () => {
    const table = source.match(/export const WAITING_KIND_LABEL: Record<WaitingKind, string> = \{([\s\S]*?)\n\}/);
    assert.ok(table, "the web's kind labels must be read (vacuity floor)");
    const web = Object.fromEntries([...table[1].matchAll(/\n\s+(\w+): '([^']*)'/g)].map((m) => [m[1], m[2]]));
    assert.ok(Object.keys(web).length >= 8, "every kind must be read (vacuity floor)");
    assert.deepStrictEqual(WAITING_KIND_LABEL, web);
  });

  it("an item's text is its source's own words, and a source that said nothing is named the same way", () => {
    const fn = source.slice(source.indexOf("export function waitingText"), source.indexOf("export function waitingSource"));
    const fallbacks = [...fn.matchAll(/\|\| '([^']+)'/g)].map((m) => m[1]);
    assert.ok(fallbacks.length >= 7, "every kind's fallback must be read (vacuity floor)");
    const shell = Object.keys(WAITING_KIND_LABEL).map((kind) => waitingRowOf({ kind, brief: {} }).text);
    for (const words of fallbacks) assert.ok(shell.includes(words), `the shell names a silent item as the app does: "${words}"`);
  });
});
