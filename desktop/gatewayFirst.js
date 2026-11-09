"use strict";

/**
 * A gateway the app found, brought to the app's release or newer: one update moves both, and the
 * app needs its gateway at its own version or newer.
 *
 * `plan` says what bringing the gateway at version *G* to release *T* takes, from the gateway's own
 * update read (`GET /api/update/check`): nothing (`none`), its update (`update`, to the release the
 * gateway resolves, which can be newer than *T*), or a hold, with why. Only at a press does it ask
 * the gateway to look again (`POST /api/update/check`); otherwise it only reads.
 *
 * `run` drives that update: it plans again on the same proven start, reads the gateway's progress
 * once, asks the gateway to update (`POST /api/update`, with no body: the gateway resolves its own
 * target), and waits for a new start proven at *T* or newer. It proves starts itself, so a start
 * that refuses a sign-in can't stall it. Every request goes right after a proof of the start it was
 * planned on, and none goes once the gateway says it is restarting, a read of its progress or a
 * proof fails, or a proof names another start. It says whether the gateway gave a sign that it
 * restarts (`restarted`), so the caller ends its session only at a start proven after that.
 *
 * The transport (`ask`), the proof (`prove`), whether a pid lives, the clock and the log are
 * injected, so `test/gatewayFirst.test.js` drives every answer against fakes. The gateway's own
 * words (an error, a detail) reach a sentence on one cleaned line of at most 200 characters, and
 * never the log: the log says only why it held. A version reaches a sentence only with a version's
 * shape (`shownVersion`); the compares read it as reported.
 */

const { compareVersions } = require("./appUpdate");
const { VERSION_SHAPE } = require("./homeGateway");

/** The requests an update of a found gateway sends, and nothing else: method, path and query. */
const ALLOWED_REQUESTS = Object.freeze([
  "GET /api/update/check",
  "POST /api/update/check",
  "POST /api/system/restart?probe=1",
  "POST /api/update",
  "GET /api/status",
  "POST /api/auth/logout",
]);

/** How often the wait looks, and its bounds. */
const POLL_MS = 3000;
const POST_TIMEOUT_MS = 30_000;
const NO_STEP_MS = 30_000;
const RESTART_MS = 3 * 60_000;
const TOTAL_MS = 25 * 60_000;
/** The gateway's progress steps that say its update is under way. */
const LIVE_STEPS = Object.freeze(["pulling", "installing", "building", "warning", "restarting"]);
/** The steps an update ends on: one left from an earlier update is not this one's. */
const TERMINAL_STEPS = Object.freeze(["done", "error", "failed", "cancelled"]);
const DETAIL_MAX = 200;
/** What a sentence says for a version without a version's shape. */
const UNREADABLE_VERSION = "a version this app can't read";

/** The gateway's own words, as a sentence may carry them: one line, no control characters, capped. */
function cleaned(text) {
  const line = String(text == null ? "" : text)
    .replace(/[\u0000-\u001f\u007f-\u009f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return line.length > DETAIL_MAX ? `${line.slice(0, DETAIL_MAX - 1)}…` : line;
}

/** *version* as a sentence or the confirm may say it: trimmed, when it has a version's shape
 *  (`homeGateway.VERSION_SHAPE`), else the words for a version this app can't read. */
function shownVersion(version) {
  const text = typeof version === "string" ? version.trim() : "";
  return VERSION_SHAPE.test(text) ? text : UNREADABLE_VERSION;
}

/** What a gateway's error answer says: its envelope's message, else its status. */
function errorOf(body, status) {
  const error = body && body.error;
  if (error && typeof error === "object") return cleaned(error.message || error.code || `it answered ${status}`);
  return error ? cleaned(error) : `it answered ${status}`;
}

/** Whether an answer says the session it carried isn't signed in there. */
const signedOut = (status) => status === 401 || status === 403;

const atOrAbove = (version, target) => {
  const order = compareVersions(version, target);
  return order === null ? null : order >= 0;
};

/**
 * The one sentence a hold is said with on the This computer row: *d* carries the target (`to`),
 * the version it stays on (`version`), the port and pid, the gateway's own words (`detail`), its
 * pin (`pin`) and what the gateway saw (`latest`). Each version is said as `shownVersion` has it.
 */
function holdSentence(why, d = {}) {
  const again = "then press Update the Gateway again.";
  const to = d.to ? shownVersion(d.to) : "the app's version";
  switch (why) {
    case "container":
      return `This gateway runs in a container, which updates from its host: pull the ${to} image and recreate it (Settings › Updates in a browser tab has the commands), ${again}`;
    case "other_app":
      return `This gateway is built into a PersonalClaw app, so only that app updates it. Stop it (pid ${d.pid}, port ${d.port}), then press Try Again.`;
    case "developer_channel":
      return `This gateway is a source checkout on the Developer channel, which follows its branch, not releases. Bring it to ${to} or newer yourself, or set Release line to Beta or Stable, ${again}`;
    case "pinned":
      return `This gateway is pinned to ${shownVersion(d.pin)}, not ${to}. Change or clear the pin in Release line, ${again}`;
    case "not_seen":
      return `This gateway doesn't see release ${to} yet (its newest is ${d.latest ? shownVersion(d.latest) : "unknown"}). Check its Release line, ${again}`;
    case "gateway_offline":
      return `This gateway couldn't reach GitHub to look for ${to} just now. Press Update the Gateway again in a few minutes.`;
    case "refused":
      return `This gateway didn't start its update: ${d.detail}. Press Update the Gateway again once that's sorted.`;
    case "installer":
      return `This gateway can't install updates here: ${d.detail}`;
    case "signed_out":
      return "This app couldn't sign in to this gateway to update it. Press Update the Gateway again.";
    case "not_ended":
      return "This gateway didn't answer this app as expected, so it wasn't asked to update. Press Update the Gateway again, or update the gateway from Settings › Updates in a browser tab, then press Try Again.";
    case "nothing_to_move":
      return `This gateway found nothing to move to ${to}${d.detail ? ` (${d.detail})` : ""}. Check its Release line, ${again}`;
    case "failed":
      return `This gateway's update to ${to} didn't finish${d.detail ? `: ${d.detail}` : ""}. See Settings › Updates in a browser tab, ${again}`;
    case "cancelled":
      return `This gateway's update to ${to} was cancelled${d.detail ? `: ${d.detail}` : ""}.`;
    case "came_back_older":
      return `This gateway restarted on ${shownVersion(d.version)}, still older than ${to}. See Settings › Updates in a browser tab for what it did, ${again}`;
    case "timeout_restart":
      return `This gateway stopped for its update 3 minutes ago and isn't back yet. Press Update the Gateway again once it is.`;
    case "timeout_total":
      return `This gateway's update to ${to} is still running after 25 minutes. Press Try Again once Settings › Updates in a browser tab shows ${to}.`;
    case "unknown_version":
      return `This gateway's version (${shownVersion(d.version)}) can't be compared with ${to}. Update the gateway yourself, then press Try Again.`;
    case "plan_changed":
      return "What Update the Gateway does has changed. Press it again to go on.";
    case "gone":
      return `The gateway on port ${d.port} stopped during its update to ${to}. Start it again, or start a gateway here.`;
    case "error":
      return "Update the Gateway stopped on an error. Press Update the Gateway again.";
    default:
      return `This gateway's update didn't finish. Press Update the Gateway again.`;
  }
}

const held = (why, d) => ({ step: "held", why, sentence: holdSentence(why, d), latest: d.latest || "", kind: d.kind || "" });

/**
 * What bringing the gateway at *G* to *T* takes, from its update read: `{step: "none"}`,
 * `{step: "update", latest, kind}` or `{step: "held", why, sentence}`. *press* lets it ask the
 * gateway to look again once, when what it last saw is below *T*. A read answered 401 or 403 is a
 * session the gateway doesn't take: `signed_out`. *kind* is the install kind the read named.
 */
async function plan({ G, T, ask, press = false, pid = 0, port = 0 }) {
  const base = { to: T, version: G, pid, port };
  const order = compareVersions(G, T);
  if (order === null) return held("unknown_version", base);
  if (order >= 0) return { step: "none", why: "", sentence: "", latest: "", kind: "" };
  const read = async (method) => {
    const answer = await ask(method, "/api/update/check");
    const status = answer ? answer.status : 0;
    const body = answer && status === 200 && answer.body && typeof answer.body === "object" ? answer.body : null;
    return { status, body };
  };
  const decide = (info, kind) => {
    if (!info) return held("gateway_offline", base);
    if (kind === "container") return held("container", base);
    if (kind === "desktop") return held("other_app", base);
    if (kind === "git" && info.channel === "nightly") return held("developer_channel", base);
    const pin = typeof info.pin === "string" ? info.pin.trim() : "";
    if (pin && compareVersions(pin, T) !== 0) return held("pinned", { ...base, pin });
    const latest = typeof info.latest === "string" ? info.latest : "";
    if (latest && info.update_available === true && atOrAbove(latest, T)) return { step: "update", why: "", sentence: "", latest, kind };
    return null;
  };
  const first = await read("GET");
  if (signedOut(first.status)) return held("signed_out", base);
  const kind = first.body ? String(first.body.kind || "") : "";
  const firstSaid = decide(first.body, kind);
  if (firstSaid) return firstSaid;
  if (!press) return held("not_seen", { ...base, latest: "", kind });
  const second = await read("POST");
  if (signedOut(second.status)) return held("signed_out", base);
  const looked = second.body;
  if (!looked || looked.checked_now === false || !looked.latest) return held("gateway_offline", base);
  return decide(looked, kind) || held("not_seen", { ...base, latest: String(looked.latest), kind });
}

/**
 * Bring the gateway proven at *start* (`{pid, port, secretDigest}`, on version *G*) to *T*, as
 * *planned* (`plan`'s `update`). Resolves `{ok: true, start, version, restarted: true}` once a new
 * start is proven at *T* or newer, or `{ok: false, why, sentence, restarted}`, or `{ok: false, why:
 * "stopping"}` when the app quits. *restarted* says whether the gateway gave a sign it restarts: a
 * 200 `restarting`, a 409 while it restarts, a `restarting` step, a read of its progress that
 * failed, a proof it missed, or a new start. *onRestart()* is told at the first sign, as it happens,
 * and RESTART_MS runs from then: a new start must be proven within it, whatever the old start still
 * answers. *prove()* resolves this home's gateway as `homeGateway.reach` proves it.
 */
async function run({ G, T, start, planned, ask, prove, pidIsAlive, stopping = () => false, now = Date.now, sleep, log = () => {}, onRestart = () => {} }) {
  const base = { to: planned.latest || T, version: G, pid: start.pid, port: start.port };
  const sameStart = (g) => g && g.pid === start.pid && g.secretDigest === start.secretDigest;
  let restarting = false;
  let restartSince = 0;
  /** The gateway gave a sign it restarts: once, the first time, told and timed. */
  const sawRestart = () => {
    if (restarting) return;
    restarting = true;
    restartSince = now();
    onRestart();
  };
  const hold = (why, d = {}) => {
    log(`the gateway's update held: ${why}`);
    return { ok: false, ...held(why, { ...base, ...d }), restarted: restarting };
  };
  // Planned again right before the POST, on the start the plan was made on.
  const before = await prove();
  if (!before.ok || !sameStart(before.gateway)) return hold("plan_changed");
  const again = await plan({ G, T, ask, pid: start.pid, port: start.port });
  if (again.step === "held" && again.why === "signed_out") return hold("signed_out");
  if (again.step !== "update" || again.latest !== planned.latest) return hold("plan_changed");
  if (!(await stillThere())) return hold("plan_changed");
  // The progress as it stands before the POST: a step an earlier update ended on isn't this one's.
  const first = await statusRead();
  if (signedOut(first.status)) return hold("signed_out");
  let stale = first.progress && TERMINAL_STEPS.includes(first.progress.step) ? first.progress : null;
  const posted = await ask("POST", "/api/update", { timeoutMs: POST_TIMEOUT_MS });
  // A timeout or a dropped connection may be an update that started: wait.
  if (posted) {
    const { status, body = {} } = posted;
    if (status === 200 && body.status === "restarting") {
      sawRestart();
      return hold("nothing_to_move", { detail: cleaned(body.detail || "") });
    }
    if (status === 200 && body.status === "instructions") return hold(body.kind === "desktop" ? "other_app" : "container");
    if (status === 409 && body.error && typeof body.error === "object" && body.error.code === "update_installer_missing") return hold("installer", { detail: errorOf(body, status) });
    if (status === 409) {
      const read = (await stillThere()) ? await statusRead() : null;
      if (read && signedOut(read.status)) return hold("signed_out");
      // A proof that missed or named another start, or a progress read that failed: it may restart.
      if (!read || !read.ok) sawRestart();
      const progress = read && read.progress;
      if (!(progress && LIVE_STEPS.includes(progress.step))) return hold("refused", { detail: errorOf(body, status) });
      if (progress.step === "restarting") sawRestart();
    } else if (signedOut(status)) {
      return hold("signed_out");
    } else if (status === 400 || status >= 500 || (status !== 200 && status !== 0)) {
      return hold("refused", { detail: errorOf(body, status) });
    }
  }
  const began = now();
  let sawStep = false;
  let quietSince = now();
  /** Nothing moved: a read-only plan first says why, when it now holds for a reason of its own. */
  const nothingToMove = async (detail) => {
    const said = await plan({ G, T, ask, pid: start.pid, port: start.port });
    if (said.step === "held") {
      log(`the gateway's update held: ${said.why}`);
      return { ok: false, ...said, restarted: restarting };
    }
    return hold("nothing_to_move", { detail });
  };
  for (;;) {
    if (stopping()) return { ok: false, why: "stopping", sentence: "", restarted: restarting };
    if (now() - began > TOTAL_MS) return hold("timeout_total");
    await sleep(POLL_MS);
    if (stopping()) return { ok: false, why: "stopping", sentence: "", restarted: restarting };
    const proof = await prove();
    if (proof.ok && !sameStart(proof.gateway)) {
      sawRestart();
      const version = proof.gateway.version || "";
      const order = atOrAbove(version, T);
      if (order === null) return hold("unknown_version", { version });
      if (!order) return hold("came_back_older", { version });
      return { ok: true, start: { pid: proof.gateway.pid, port: proof.gateway.port, secretDigest: proof.gateway.secretDigest }, version, gateway: proof.gateway, restarted: true };
    }
    if (proof.ok) {
      // Nothing credentialed goes once the gateway said it restarts: only proofs, until a new start
      // within RESTART_MS of that sign.
      if (restarting) {
        if (now() - restartSince > RESTART_MS) return hold("timeout_restart");
        continue;
      }
      const read = await statusRead();
      if (signedOut(read.status)) return hold("signed_out");
      if (!read.ok) {
        sawRestart();
        continue;
      }
      const progress = read.progress;
      const step = progress ? progress.step : "";
      if (stale && progress && progress.step === stale.step && progress.detail === stale.detail) {
        if (now() - quietSince > NO_STEP_MS) return nothingToMove("");
        continue;
      }
      stale = null;
      if (step === "restarting") {
        sawRestart();
        continue;
      }
      if (step === "error" || step === "failed") return hold("failed", { detail: cleaned(progress.detail) });
      if (step === "cancelled") return hold("cancelled", { detail: cleaned(progress.detail) });
      if (LIVE_STEPS.includes(step)) {
        sawStep = true;
        quietSince = now();
        continue;
      }
      // A 200 whose progress is done, or gone: after a live step at once, before one after 30 s.
      if (step === "done" || (sawStep && !progress) || (!progress && now() - quietSince > NO_STEP_MS)) {
        return nothingToMove(step === "done" ? cleaned(progress.detail) : "");
      }
      continue;
    }
    if (!pidIsAlive(start.pid)) return hold("gone");
    sawRestart();
    if (now() - restartSince > RESTART_MS) return hold("timeout_restart");
  }

  /** Whether the start the update was planned on is the one proven there now. */
  async function stillThere() {
    const proof = await prove();
    return proof.ok && sameStart(proof.gateway);
  }

  /** The gateway's progress, read right after a proof of the same start: `{ok, status, progress}`,
   *  *progress* `{step, detail}` or null, and *ok* false when the read wasn't answered 200. */
  async function statusRead() {
    const answer = await ask("GET", "/api/status");
    const status = answer ? answer.status : 0;
    if (!answer || status !== 200 || !answer.body || typeof answer.body !== "object") return { ok: false, status, progress: null };
    const p = answer.body.update_progress;
    return { ok: true, status, progress: p && typeof p === "object" ? { step: String(p.step || ""), detail: String(p.detail || "") } : null };
  }
}

module.exports = {
  ALLOWED_REQUESTS,
  DETAIL_MAX,
  LIVE_STEPS,
  NO_STEP_MS,
  POLL_MS,
  POST_TIMEOUT_MS,
  RESTART_MS,
  TERMINAL_STEPS,
  TOTAL_MS,
  UNREADABLE_VERSION,
  cleaned,
  holdSentence,
  plan,
  run,
  shownVersion,
};
