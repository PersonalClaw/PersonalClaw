/**
 * A gateway the app found, brought to the app's release or newer (`gatewayFirst.js`): its plan
 * from the gateway's own update read, and the run that asks the gateway to update and waits for a
 * new start proven at the release, against a fake gateway, proof and clock.
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { NO_STEP_MS, POLL_MS, RESTART_MS, TOTAL_MS, UNREADABLE_VERSION, cleaned, holdSentence, plan, run, shownVersion } = require("../gatewayFirst");

const START = { pid: 4242, port: 8765, secretDigest: "start-1" };
const NEXT = { pid: 4242, port: 8765, secretDigest: "start-2" };
const check = (over = {}) => ({ status: 200, body: { kind: "pip", current: "0.3.7", latest: "0.3.8", update_available: true, channel: "stable", pin: "", ...over } });

/** A gateway that answers *answers*["METHOD /path"] (a value, or a function of the call's index),
 *  and keeps every request it was sent. */
function gateway(answers = {}) {
  const sent = [];
  const ask = async (method, route, options = {}) => {
    const key = `${method} ${route}`;
    sent.push({ key, options });
    const answer = answers[key];
    const n = sent.filter((s) => s.key === key).length - 1;
    return typeof answer === "function" ? answer(n) : answer === undefined ? { status: 404, body: {} } : answer;
  };
  return { ask, sent, keys: () => sent.map((s) => s.key) };
}

/** A clock that moves only when the run sleeps. */
function clock() {
  let t = 1_000_000;
  return { now: () => t, sleep: async (ms) => { t += ms; }, advance: (ms) => { t += ms; } };
}

/** Proofs in turn: the same start until *after* looks, then *then* (a proven start, or a miss). */
function proofs({ after = 2, then = { ok: true, gateway: { ...NEXT, version: "0.3.8" } } } = {}) {
  let n = 0;
  return async () => (n++ < after ? { ok: true, gateway: { ...START, version: "0.3.7" } } : typeof then === "function" ? then(n) : then);
}

const drive = (over = {}) => {
  const c = clock();
  const logs = [];
  const restarts = [];
  const g = over.gateway || gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status: 200, body: { update_progress: { step: "installing", detail: "" } } } });
  const done = run({
    G: "0.3.7",
    T: "0.3.8",
    start: START,
    planned: { step: "update", latest: "0.3.8" },
    ask: g.ask,
    prove: over.prove || proofs(),
    pidIsAlive: over.pidIsAlive || (() => true),
    stopping: over.stopping || (() => false),
    now: c.now,
    sleep: c.sleep,
    log: (line) => logs.push(line),
    onRestart: () => restarts.push(c.now()),
  });
  return { done, g, c, logs, restarts };
};

describe("plan: what bringing the gateway to the app's release takes", () => {
  it("asks the gateway nothing when it is at the release or newer, and holds a version it can't compare", async () => {
    for (const G of ["0.3.8", "0.3.9", "0.3.8.post1"]) {
      const g = gateway();
      assert.deepEqual(await plan({ G, T: "0.3.8", ask: g.ask }), { step: "none", why: "", sentence: "", latest: "", kind: "" });
      assert.deepEqual(g.sent, [], G);
    }
    const g = gateway();
    const unknown = await plan({ G: "0.3.7+local", T: "0.3.8", ask: g.ask });
    assert.equal(unknown.why, "unknown_version");
    assert.deepEqual(g.sent, []);
  });

  it("holds a container, another app's built-in gateway, a checkout on the Developer channel and a pin not at the release, with nothing asked of the update", async () => {
    const cases = [
      [{ kind: "container" }, "container"],
      [{ kind: "desktop" }, "other_app"],
      [{ kind: "git", channel: "nightly" }, "developer_channel"],
      [{ pin: "0.3.7" }, "pinned"],
      [{ pin: "0.3.9" }, "pinned"],
    ];
    for (const [over, why] of cases) {
      const g = gateway({ "GET /api/update/check": check(over) });
      const said = await plan({ G: "0.3.7", T: "0.3.8", ask: g.ask, press: true });
      assert.equal(said.why, why, JSON.stringify(over));
      assert.deepEqual(g.keys(), ["GET /api/update/check"], JSON.stringify(over));
    }
    // A checkout on a release line, and a pin at the release in another spelling, are updated.
    const git = gateway({ "GET /api/update/check": check({ kind: "git", channel: "beta", pin: "v0.3.8" }) });
    assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: git.ask })).step, "update");
  });

  it("updates to the release the gateway resolves, which can be newer than the app's", async () => {
    const g = gateway({ "GET /api/update/check": check({ latest: "0.3.9" }) });
    assert.deepEqual(await plan({ G: "0.3.7", T: "0.3.8", ask: g.ask }), { step: "update", why: "", sentence: "", latest: "0.3.9", kind: "pip" });
    // The install kind is the read's own, for the confirm's checkout sentence: no second read.
    const git = gateway({ "GET /api/update/check": check({ kind: "git", channel: "stable" }) });
    assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: git.ask })).kind, "git");
    assert.deepEqual(git.keys(), ["GET /api/update/check"]);
  });

  it("holds signed_out when the gateway answers 401 or 403 to its update read or its look, never gateway_offline", async () => {
    for (const status of [401, 403]) {
      const read = gateway({ "GET /api/update/check": { status, body: {} } });
      assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: read.ask, press: true })).why, "signed_out", `GET ${status}`);
      assert.deepEqual(read.keys(), ["GET /api/update/check"], `GET ${status}: it looked after a refused read`);
      const stale = check({ latest: "0.3.7", update_available: false });
      const look = gateway({ "GET /api/update/check": stale, "POST /api/update/check": { status, body: {} } });
      assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: look.ask, press: true })).why, "signed_out", `POST ${status}`);
    }
    // A read that wasn't answered at all is still a gateway that couldn't look.
    const silent = gateway({ "GET /api/update/check": null });
    assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: silent.ask })).why, "gateway_offline");
  });

  it("says every version in a hold as it has a version's shape, and one without it in words, comparing it as reported", async () => {
    const odd = "0.3.9<b>";
    assert.equal(shownVersion(" 0.3.9 "), "0.3.9");
    assert.equal(shownVersion(odd), UNREADABLE_VERSION);
    const pinned = gateway({ "GET /api/update/check": check({ pin: odd }) });
    const said = await plan({ G: "0.3.7", T: "0.3.8", ask: pinned.ask, press: true });
    assert.equal(said.why, "pinned");
    assert.match(said.sentence, /pinned to a version this app can't read, not 0\.3\.8/);
    const stale = check({ latest: "0.3.7", update_available: false });
    const behind = gateway({ "GET /api/update/check": stale, "POST /api/update/check": { status: 200, body: { checked_now: true, latest: odd, update_available: false } } });
    assert.match((await plan({ G: "0.3.7", T: "0.3.8", ask: behind.ask, press: true })).sentence, /its newest is a version this app can't read/);
    for (const why of ["came_back_older", "unknown_version"]) {
      const sentence = holdSentence(why, { to: "0.3.8", version: odd });
      assert.ok(!sentence.includes("<b>"), `${why}: ${sentence}`);
      assert.ok(sentence.includes(UNREADABLE_VERSION), `${why}: ${sentence}`);
    }
    assert.ok(!holdSentence("nothing_to_move", { to: odd }).includes("<b>"));
  });

  it("asks the gateway to look again only at a press, once, and says what it saw", async () => {
    const stale = check({ latest: "0.3.7", update_available: false });
    const quiet = gateway({ "GET /api/update/check": stale });
    assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: quiet.ask })).why, "not_seen");
    assert.deepEqual(quiet.keys(), ["GET /api/update/check"], "a read-only plan asked the gateway to look");
    const looked = gateway({ "GET /api/update/check": stale, "POST /api/update/check": { status: 200, body: { ...check().body, checked_now: true } } });
    assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: looked.ask, press: true })).step, "update");
    assert.deepEqual(looked.keys(), ["GET /api/update/check", "POST /api/update/check"]);
    for (const body of [{ checked_now: false, latest: "0.3.8", update_available: true }, { checked_now: true, latest: "" }]) {
      const off = gateway({ "GET /api/update/check": stale, "POST /api/update/check": { status: 200, body } });
      assert.equal((await plan({ G: "0.3.7", T: "0.3.8", ask: off.ask, press: true })).why, "gateway_offline", JSON.stringify(body));
    }
    const behind = gateway({ "GET /api/update/check": stale, "POST /api/update/check": { status: 200, body: { checked_now: true, latest: "0.3.7", update_available: false } } });
    const said = await plan({ G: "0.3.7", T: "0.3.8", ask: behind.ask, press: true });
    assert.equal(said.why, "not_seen");
    assert.match(said.sentence, /doesn't see release 0\.3\.8 yet \(its newest is 0\.3\.7\)/);
    assert.deepEqual(behind.keys().filter((k) => k === "POST /api/update"), [], "a plan asked the gateway to update");
  });
});

describe("holdSentence: what the row says for each hold", () => {
  it("says the owner's other way out, Settings › Updates, in a browser tab, and the error and not-ended holds in their own words", () => {
    const d = { to: "0.3.8", version: "0.3.7", detail: "it said why" };
    for (const why of ["container", "failed", "came_back_older", "timeout_total"]) {
      assert.match(holdSentence(why, d), /Settings › Updates in a browser tab/, why);
    }
    assert.equal(holdSentence("not_ended", d), "This gateway didn't answer this app as expected, so it wasn't asked to update. Press Update the Gateway again, or update the gateway from Settings › Updates in a browser tab, then press Try Again.");
    assert.equal(holdSentence("error", d), "Update the Gateway stopped on an error. Press Update the Gateway again.");
  });
});

describe("run: the gateway's update, then a new start proven at the release", () => {
  it("asks once, and resolves on a new start proven at the release, with no sign-in", async () => {
    const { done, g } = drive();
    const out = await done;
    assert.equal(out.ok, true);
    assert.deepEqual(out.start, { pid: NEXT.pid, port: NEXT.port, secretDigest: NEXT.secretDigest });
    assert.equal(out.version, "0.3.8");
    assert.equal(g.keys().filter((k) => k === "POST /api/update").length, 1);
    assert.equal(g.sent.find((s) => s.key === "POST /api/update").options.timeoutMs, 30_000);
  });

  it("plans again on the same start right before it asks, and holds when the start or the plan moved", async () => {
    const moved = drive({ prove: async () => ({ ok: true, gateway: { ...NEXT, version: "0.3.7" } }) });
    assert.equal((await moved.done).why, "plan_changed");
    assert.deepEqual(moved.g.keys().filter((k) => k === "POST /api/update"), []);
    const newer = drive({ gateway: gateway({ "GET /api/update/check": check({ latest: "0.3.9" }) }) });
    assert.equal((await newer.done).why, "plan_changed");
    assert.deepEqual(newer.g.keys().filter((k) => k === "POST /api/update"), []);
  });

  it("maps every answer to its update as written", async () => {
    const live = { status: 200, body: { update_progress: { step: "installing", detail: "" } } };
    const cases = [
      [{ status: 200, body: { status: "updating" } }, true],
      [{ status: 200, body: { status: "restarting", detail: "already at the tag" } }, "nothing_to_move"],
      [{ status: 200, body: { status: "instructions", kind: "container" } }, "container"],
      [{ status: 200, body: { status: "instructions", kind: "desktop" } }, "other_app"],
      [{ status: 409, body: { error: "An update is already running." } }, true, live],
      [{ status: 409, body: { error: "The working tree has local changes." } }, "refused", { status: 200, body: {} }],
      [{ status: 409, body: { error: { code: "update_installer_missing", message: "uv isn't installed." } } }, "installer"],
      [{ status: 400, body: { error: "no checkout" } }, "refused"],
      [{ status: 401, body: {} }, "signed_out"],
      [{ status: 403, body: {} }, "signed_out"],
      [{ status: 502, body: {} }, "refused"],
      [null, true],
      [{ status: 0, body: {} }, true],
    ];
    for (const [answer, expect, status = live] of cases) {
      const { done } = drive({ gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": answer, "GET /api/status": status }), prove: proofs({ after: 3 }) });
      const out = await done;
      const what = JSON.stringify(answer);
      if (expect === true) assert.equal(out.ok, true, what);
      else assert.equal(out.why, expect, what);
    }
  });

  it("holds the gateway's own failure with its words on one cleaned line, never in the log", async () => {
    const long = `Upgrade timed out\n\u001b[31m${"x".repeat(400)}`;
    for (const step of ["error", "failed", "cancelled"]) {
      const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => ({ status: 200, body: { update_progress: n === 0 ? null : { step, detail: long } } }) });
      const { done, logs } = drive({ gateway: g, prove: proofs({ after: 50 }) });
      const out = await done;
      assert.equal(out.why, step === "error" ? "failed" : step, step);
      assert.ok(out.sentence.includes(cleaned(long)), step);
      assert.doesNotMatch(out.sentence, /[\u0000-\u001f]/, step);
      assert.ok(cleaned(long).length <= 200);
      assert.ok(logs.every((line) => !line.includes("Upgrade timed out")), `${step}: the gateway's words reached the log`);
    }
  });

  it("holds nothing_to_move on done, on progress gone after a step, and on no step for 30 seconds; ten minutes installing never times out", async () => {
    // The first read is the one before the POST.
    const steps = (list) => (n) => ({ status: 200, body: { update_progress: list[Math.min(n, list.length - 1)] } });
    const cases = [
      [[null, { step: "done", detail: "nothing to install" }], "nothing_to_move"],
      [[null, { step: "installing", detail: "" }, null], "nothing_to_move"],
      [[null], "nothing_to_move"],
    ];
    for (const [list, why] of cases) {
      const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": steps(list) });
      const { done } = drive({ gateway: g, prove: proofs({ after: 100 }) });
      assert.equal((await done).why, why, JSON.stringify(list));
    }
    // Ten minutes of `installing` on the same start, then the new start: resolved, not timed out.
    const looks = Math.ceil((10 * 60_000) / POLL_MS);
    const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status: 200, body: { update_progress: { step: "installing", detail: "" } } } });
    const { done } = drive({ gateway: g, prove: proofs({ after: looks }) });
    assert.equal((await done).ok, true);
    assert.ok(NO_STEP_MS === 30_000);
  });

  it("holds a start back on an older version, a restart that never proves a start, the 25 minutes, and a gateway that is gone; a quit stops it", async () => {
    const older = drive({ prove: proofs({ then: { ok: true, gateway: { ...NEXT, version: "0.3.7" } } }) });
    assert.equal((await older.done).why, "came_back_older");
    const unread = drive({ prove: proofs({ then: { ok: true, gateway: { ...NEXT, version: "0.3.8+local" } } }) });
    assert.equal((await unread.done).why, "unknown_version");
    const away = drive({ prove: proofs({ then: { ok: false, reason: "none" } }) });
    const out = await away.done;
    assert.equal(out.why, "timeout_restart");
    assert.ok(RESTART_MS === 3 * 60_000);
    const forever = drive({ prove: proofs({ after: 100_000 }) });
    assert.equal((await forever.done).why, "timeout_total");
    assert.ok(TOTAL_MS === 25 * 60_000);
    const gone = drive({ prove: proofs({ then: { ok: false, reason: "none" } }), pidIsAlive: () => false });
    assert.equal((await gone.done).why, "gone");
    let quitting = false;
    const quit = drive({ prove: proofs({ after: 5, then: (n) => ((quitting = true), { ok: false, reason: "none" }) }), stopping: () => quitting });
    assert.equal((await quit.done).why, "stopping");
  });

  it("sends nothing to the gateway once it says it is restarting, and nothing after a failed proof but proofs", async () => {
    const g = gateway({
      "GET /api/update/check": check(),
      "POST /api/update": { status: 200, body: { status: "updating" } },
      "GET /api/status": (n) => ({ status: 200, body: { update_progress: { step: n <= 1 ? "installing" : "restarting", detail: "" } } }),
    });
    const { done } = drive({ gateway: g, prove: proofs({ after: 8 }) });
    const out = await done;
    assert.equal(out.ok, true);
    assert.equal(out.restarted, true);
    // The one before the POST, one installing, and the one that said restarting: none after it.
    assert.equal(g.keys().filter((k) => k === "GET /api/status").length, 3, "a status read after the gateway said it restarts");
    const missed = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status: 200, body: { update_progress: { step: "installing", detail: "" } } } });
    let n = 0;
    const flaky = async () => {
      n += 1;
      if (n <= 3) return { ok: true, gateway: { ...START, version: "0.3.7" } };
      if (n <= 6) return { ok: false, reason: "silent" };
      if (n <= 8) return { ok: true, gateway: { ...START, version: "0.3.7" } };
      return { ok: true, gateway: { ...NEXT, version: "0.3.8" } };
    };
    const after = drive({ gateway: missed, prove: flaky });
    assert.equal((await after.done).ok, true);
    // The one before the POST and one before the proofs failed; once they did, the same start's
    // proofs carry none again.
    assert.equal(missed.keys().filter((k) => k === "GET /api/status").length, 2, missed.keys().join(", "));
  });

  it("says the gateway gave a sign it restarts: a 200 restarting, a 409 while it restarts, a restarting step or a missed proof", async () => {
    const restarting = { status: 200, body: { update_progress: { step: "restarting", detail: "" } } };
    const said = await drive({ gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "restarting", detail: "already at the tag" } }, "GET /api/status": { status: 200, body: {} } }) }).done;
    assert.equal(said.why, "nothing_to_move");
    assert.equal(said.restarted, true, "a 200 restarting");
    // A 409 while it restarts: the wait sends nothing more but proofs, and resolves on the new start.
    const busy = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 409, body: { error: "An update is already running." } }, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : restarting) });
    const out = await drive({ gateway: busy, prove: proofs({ after: 4 }) }).done;
    assert.equal(out.ok, true);
    assert.equal(out.restarted, true);
    assert.equal(busy.keys().filter((k) => k === "GET /api/status").length, 2, busy.keys().join(", "));
    // No sign of a restart: a hold before the POST, and a refusal of it.
    const refused = await drive({ gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 400, body: { error: "no checkout" } }, "GET /api/status": { status: 200, body: {} } }) }).done;
    assert.equal(refused.why, "refused");
    assert.equal(refused.restarted, false);
    const missed = await drive({ prove: proofs({ after: 3, then: { ok: false, reason: "none" } }) }).done;
    assert.equal(missed.why, "timeout_restart");
    assert.equal(missed.restarted, true, "a missed proof");
  });

  it("holds refused on a 409 whose proof misses or whose progress read fails, with the sign it restarts told once", async () => {
    const busy = { status: 409, body: { error: "An update is already running." } };
    const kinds = {
      // The two proofs before the POST hold; the one after the 409 misses.
      "a missed proof": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": busy, "GET /api/status": { status: 200, body: {} } }), prove: proofs({ after: 2, then: { ok: false, reason: "none" } }) },
      "a read answered 502": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": busy, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : { status: 502, body: {} }) }), prove: proofs({ after: 3 }) },
    };
    for (const [kind, over] of Object.entries(kinds)) {
      const { done, restarts } = drive(over);
      const out = await done;
      assert.equal(out.why, "refused", kind);
      assert.equal(out.restarted, true, `${kind}: the hold doesn't say the gateway may be restarting`);
      assert.equal(restarts.length, 1, `${kind}: told ${restarts.length} times`);
    }
  });

  it("tells the caller of the first sign it restarts, as it happens, once, whatever the sign", async () => {
    const restarting = { status: 200, body: { update_progress: { step: "restarting", detail: "" } } };
    const updating = { status: 200, body: { status: "updating" } };
    const kinds = {
      "a 200 restarting": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "restarting" } }, "GET /api/status": { status: 200, body: {} } }) },
      "a 409 while it restarts": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 409, body: { error: "An update is already running." } }, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : restarting) }), prove: proofs({ after: 6 }) },
      "a restarting step": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": updating, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : restarting) }), prove: proofs({ after: 6 }) },
      "a failed read": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": updating, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : { status: 502, body: {} }) }), prove: proofs({ after: 6 }) },
      "a missed proof": { prove: proofs({ after: 3, then: (n) => (n < 8 ? { ok: false, reason: "none" } : { ok: true, gateway: { ...NEXT, version: "0.3.8" } }) }) },
      "a new start": { prove: proofs({ after: 3 }) },
    };
    for (const [kind, over] of Object.entries(kinds)) {
      const { done, restarts } = drive(over);
      const out = await done;
      assert.equal(out.restarted, true, kind);
      assert.equal(restarts.length, 1, `${kind}: told ${restarts.length} times`);
    }
    // No sign, no word: a refusal of the POST.
    const refused = drive({ gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 400, body: { error: "no checkout" } }, "GET /api/status": { status: 200, body: {} } }) });
    await refused.done;
    assert.deepEqual(refused.restarts, []);
  });

  it("holds timeout_restart 3 minutes after the first sign it restarts, while the old start is still proven", async () => {
    const restarting = { status: 200, body: { update_progress: { step: "restarting", detail: "" } } };
    const updating = { status: 200, body: { status: "updating" } };
    const signs = {
      "a missed proof": { prove: (() => { let n = 0; return async () => (++n === 3 ? { ok: false, reason: "silent" } : { ok: true, gateway: { ...START, version: "0.3.7" } }); })() },
      "a restarting step": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": updating, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : restarting) }), prove: proofs({ after: 100_000 }) },
      "a 409 while it restarts": { gateway: gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 409, body: { error: "An update is already running." } }, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : restarting) }), prove: proofs({ after: 100_000 }) },
    };
    for (const [sign, over] of Object.entries(signs)) {
      const { done, restarts, c } = drive(over);
      const out = await done;
      assert.equal(out.why, "timeout_restart", `${sign}: ${out.why}`);
      // Held at the first look past 3 minutes from the sign, never at the 25 minutes.
      assert.ok(c.now() - restarts[0] > RESTART_MS && c.now() - restarts[0] <= RESTART_MS + POLL_MS, `${sign}: held ${c.now() - restarts[0]} ms after the sign`);
    }
  });

  it("holds signed_out on a 401 or 403 to the plan again before the POST, and to the progress read before it", async () => {
    for (const status of [401, 403]) {
      let checks = 0;
      const replan = gateway({ "GET /api/update/check": () => (++checks === 1 ? { status, body: {} } : check()), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status: 200, body: {} } });
      const out = await drive({ gateway: replan }).done;
      assert.equal(out.why, "signed_out", `re-plan ${status}`);
      assert.ok(!replan.keys().includes("POST /api/update"), `re-plan ${status}: it asked for the update`);
      const read = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status, body: {} } });
      assert.equal((await drive({ gateway: read }).done).why, "signed_out", `status ${status}`);
      assert.ok(!read.keys().includes("POST /api/update"), `status ${status}: it asked for the update`);
    }
  });

  it("holds signed_out on a 401 or 403 to a progress read, and takes a failed read as a missed proof, counted toward the restart's 3 minutes", async () => {
    for (const status of [401, 403]) {
      const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : { status, body: {} }) });
      assert.equal((await drive({ gateway: g, prove: proofs({ after: 50 }) }).done).why, "signed_out", String(status));
    }
    for (const answer of [null, { status: 502, body: {} }]) {
      const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => (n === 0 ? { status: 200, body: {} } : answer) });
      const out = await drive({ gateway: g, prove: proofs({ after: 1000 }) }).done;
      const what = JSON.stringify(answer);
      assert.equal(out.why, "timeout_restart", `${what}: a failed read wasn't a missed proof`);
      assert.equal(out.restarted, true, what);
      assert.equal(g.keys().filter((k) => k === "GET /api/status").length, 2, `${what}: a read after the failed one`);
    }
  });

  it("takes no step an earlier update ended on for this one's, until another step comes", async () => {
    const old = { step: "failed", detail: "the earlier one" };
    // The left-over failure, twice, then this update's steps and the new start.
    const list = [old, old, old, { step: "installing", detail: "" }];
    const g = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => ({ status: 200, body: { update_progress: list[Math.min(n, list.length - 1)] } }) });
    const out = await drive({ gateway: g, prove: proofs({ after: 6 }) }).done;
    assert.equal(out.ok, true, JSON.stringify(out));
    // A different terminal step after it is this update's own.
    const now = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => ({ status: 200, body: { update_progress: n === 0 ? old : { step: "failed", detail: "this one" } } }) });
    const failed = await drive({ gateway: now, prove: proofs({ after: 50 }) }).done;
    assert.equal(failed.why, "failed");
    assert.match(failed.sentence, /this one/);
    // A left-over step that never changes is nothing moving, after 30 seconds, not this update's failure.
    const stuck = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": { status: 200, body: { update_progress: old } } });
    assert.equal((await drive({ gateway: stuck, prove: proofs({ after: 1000 }) }).done).why, "nothing_to_move");
  });

  it("plans again, reading only, before it holds nothing_to_move, and holds the plan's own reason when it has one", async () => {
    let checks = 0;
    const g = gateway({
      // The read before the press's POST plans the update; the one after nothing moved names a pin.
      "GET /api/update/check": () => (++checks <= 1 ? check() : check({ pin: "0.3.7" })),
      "POST /api/update": { status: 200, body: { status: "updating" } },
      "GET /api/status": (n) => ({ status: 200, body: { update_progress: n === 0 ? null : { step: "done", detail: "nothing to install" } } }),
    });
    const out = await drive({ gateway: g, prove: proofs({ after: 50 }) }).done;
    assert.equal(out.why, "pinned");
    assert.ok(!g.keys().slice(g.keys().indexOf("POST /api/update")).includes("POST /api/update/check"), "the plan after the update asked the gateway to look");
    const plain = gateway({ "GET /api/update/check": check(), "POST /api/update": { status: 200, body: { status: "updating" } }, "GET /api/status": (n) => ({ status: 200, body: { update_progress: n === 0 ? null : { step: "done", detail: "" } } }) });
    await drive({ gateway: plain, prove: proofs({ after: 50 }) }).done;
    assert.equal(plain.keys().filter((k) => k === "GET /api/update/check").length, 2, "no read-only plan before the hold");
  });

  it("says a new start's version as it has a version's shape", async () => {
    const out = await drive({ prove: proofs({ then: { ok: true, gateway: { ...NEXT, version: "0.3.7<i>" } } }) }).done;
    assert.equal(out.why, "unknown_version");
    assert.ok(!out.sentence.includes("<i>") && out.sentence.includes(UNREADABLE_VERSION), out.sentence);
  });
});
