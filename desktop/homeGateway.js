/**
 * This app's home's running gateway, as a command run for the home reaches it, and nothing else.
 *
 * One gateway serves a home (`gateway_base.claim_home`). So while another gateway serves the
 * app's home (a terminal's, a service's), the app's own start of its bundled gateway can only be
 * refused, and the app is no use. It used to show that refusal and stop there. Now, before it
 * starts anything, it looks for that gateway and uses it, the way `personalclaw token` does
 * (`personalclaw/home_gateway.py`, whose rules this file follows and whose answers
 * `test/homeGateway.test.js` holds it to):
 *
 * - **Where.** The port the home's gateway recorded once it listened, in the runtime record inside
 *   the home (`gateway.runtime.json`), whose pid must still be alive. No port is assumed.
 * - **Whose.** Before anything that carries a credential is sent, the gateway at that port is asked
 *   which home it serves: `GET /api/healthz`, which answers without a sign-in, with a fresh nonce.
 *   Its `home_id` must be this home's, and its `proof` must be the one only a gateway holding this
 *   home's local secret can make over that nonce and that port. Anything else (another home's
 *   gateway, a program that is not one, an older gateway that proves nothing, a port that does not
 *   answer) is told apart in a sentence, and nothing more is sent to it.
 * - **How.** Every request goes to `127.0.0.1`, the address the identity was read at, never to a
 *   name that may resolve to another socket; through no proxy (`agent: false`); and never on to
 *   where a redirect points, which Node's `http` does not follow.
 *
 * Only `reach` makes a gateway `signIn` will sign in at, and `signIn` is the only place this file
 * sends the home's secret: `GET /api/token/local`, for a session that lasts what the ready line's
 * does. Its reply is shaped as a ready line, so the shell holds it exactly as it holds that one
 * (`localSignIn.js`): in memory, as the windows' session cookie, never on disk, in a URL or a log.
 *
 * Electron-free, so its decisions are driven against real sockets and real files.
 */

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

/** The one address the app reaches its home's gateway at. `localhost` can resolve to `::1`, where
 *  another program may listen. */
const LOOPBACK_HOST = "127.0.0.1";

/** The runtime record a gateway writes into its home once it listens (`gateway_base.publish`). */
const RUNTIME_FILE = "gateway.runtime.json";

/** The home's local secret, which its gateway writes each time it starts. */
const SECRET_FILE = ".local_secret";

/** The newest PersonalClaw that has served the home, as each start's claim keeps it
 *  (`gateway_base.VERSION_FILE`). */
const VERSION_FILE = "gateway.version.json";

/** What a version a stamp names may hold, the characters PEP 440 spells one with: anything else is
 *  no version to put in a sentence. */
const VERSION_SHAPE = /^v?[0-9][0-9A-Za-z.+!-]{0,63}$/;

/** What a gateway's proof is made over, besides the nonce and the port (`home_gateway.PROOF_CONTEXT`). */
const PROOF_CONTEXT = "personalclaw-gateway-proof";

/** How long one identity read waits, and how many are made: `home_gateway`'s measured numbers. A
 *  busy gateway can take over 2 s to answer, so a timeout is read again; a refusal is not. */
const ANSWER_TIMEOUT_MS = 10_000;
const ANSWER_ATTEMPTS = 3;

/** The most of an answer that is read: a gateway's identity answer is about 150 bytes. */
const ANSWER_LIMIT = 64 * 1024;

/** How long the session the app signs in for lasts: the ready line's own default (`auth.session_ttl`
 *  is 30 days unless set), so an app on a gateway it found lasts as long as one on its own. Quitting
 *  signs it out, as it signs the ready line's out. */
const SIGN_IN_TTL = "30d";
const SIGN_IN_TIMEOUT_MS = 5000;

/** What a read says of a port where nothing listens, and of one that took the connection and was silent. */
const NOTHING = "nothing";
const SILENT = "silent";

/** The gateways `reach` found. `signIn` signs in at these and at nothing else. */
const reached = new WeakSet();

/** `home_gateway.home_id` of a home already resolved: the first 16 hex characters of its sha256. */
function homeIdOf(resolved) {
  return crypto.createHash("sha256").update(String(resolved), "utf8").digest("hex").slice(0, 16);
}

/**
 * *home*'s id, as its gateway's `/api/healthz` says it: of the folder it is, links resolved first
 * (Node's `fs.realpathSync`, which walks the path as `Path.resolve` does), or `""` for a home that
 * does not exist, which nothing serves.
 */
function homeId(home) {
  try {
    return homeIdOf(fs.realpathSync(home));
  } catch {
    return "";
  }
}

/** A fresh nonce for one identity read: 32 hex characters. */
function newNonce() {
  return crypto.randomBytes(16).toString("hex");
}

/** Whether *value* is a nonce as `newNonce` makes one. */
function isNonce(value) {
  return typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
}

/** What a gateway holding *secret* answers a read carrying *nonce* on *port* with (`home_gateway.gateway_proof`). */
function gatewayProof(secret, nonce, port) {
  return crypto.createHmac("sha256", String(secret)).update(`${PROOF_CONTEXT}\n${nonce}\n${port}`).digest("hex");
}

function sameProof(said, expected) {
  if (typeof said !== "string" || said.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(said), Buffer.from(expected));
}

/** Whether *pid* is running: alive, or running as someone else (`gateway_base.pid_is_alive`). */
function pidIsAlive(pid, kill = process.kill.bind(process)) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err && err.code === "EPERM");
  }
}

/** The port and pid *home*'s runtime record names, or null for none or a damaged one. */
function readRecord(home) {
  let record;
  try {
    record = JSON.parse(fs.readFileSync(path.join(home, RUNTIME_FILE), "utf8"));
  } catch {
    return null;
  }
  if (!record || typeof record !== "object") return null;
  const port = Number(record.port);
  const pid = Number(record.pid ?? 0);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return { port, pid: Number.isInteger(pid) ? pid : 0 };
}

/** *home*'s local secret, or `""` when it has none or it cannot be read. */
function readSecret(home) {
  try {
    return fs.readFileSync(path.join(home, SECRET_FILE), "utf8").trim();
  } catch {
    return "";
  }
}

/** The newest PersonalClaw that has served *home*, as its stamp says, or `""` when it has none, it
 *  cannot be read, or what it names is no version. */
function newestThatServed(home) {
  try {
    const newest = JSON.parse(fs.readFileSync(path.join(home, VERSION_FILE), "utf8")).newest;
    return typeof newest === "string" && VERSION_SHAPE.test(newest) ? newest : "";
  } catch {
    return "";
  }
}

/**
 * One GET to `127.0.0.1:<port><route>`: `{status, body}` (the parsed body, `{}` for one that is not
 * an object), or `NOTHING` when nothing takes the connection, or `SILENT` when what took it said
 * nothing in time. No proxy, no redirect followed, at most `ANSWER_LIMIT` bytes read.
 */
function getOnce(port, route, { timeoutMs, headers = {} }) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const req = http.request(
      { host: LOOPBACK_HOST, port, path: route, method: "GET", agent: false, timeout: timeoutMs, headers: { Accept: "application/json", ...headers } },
      (res) => {
        const chunks = [];
        let size = 0;
        res.on("data", (chunk) => {
          if (size >= ANSWER_LIMIT) return;
          chunks.push(chunk);
          size += chunk.length;
          if (size >= ANSWER_LIMIT) res.destroy();
        });
        const finish = () => {
          let body = {};
          try {
            const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8").slice(0, ANSWER_LIMIT));
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) body = parsed;
          } catch {
            body = {};
          }
          done({ status: res.statusCode || 0, body });
        };
        res.on("end", finish);
        res.on("close", finish);
        res.on("error", finish);
      }
    );
    req.on("timeout", () => {
      req.destroy();
      done(SILENT);
    });
    req.on("error", (err) => {
      // Refused, or no route to it: nothing listens there. Anything after the connection was taken
      // (a reset, a reply that is not HTTP) is something that answered, and not as a gateway does.
      const before = ["ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH", "EADDRNOTAVAIL"].includes(err && err.code);
      done(before ? NOTHING : { status: 0, body: {} });
    });
    req.end();
  });
}

/** What answers `GET /api/healthz?nonce=<nonce>` on *port*, asked again only while it is silent. */
async function healthz(port, nonce, { timeoutMs = ANSWER_TIMEOUT_MS, attempts = ANSWER_ATTEMPTS } = {}) {
  for (let attempt = 0; attempt < Math.max(1, attempts); attempt += 1) {
    const said = await getOnce(port, `/api/healthz?nonce=${nonce}`, { timeoutMs });
    if (said !== SILENT) return said === NOTHING ? NOTHING : said.status === 200 ? said.body : {};
  }
  return SILENT;
}

const refused = (reason, sentence) => ({ ok: false, reason, sentence });

/**
 * This home's running gateway: `{ok: true, gateway: {port, pid, url, version}}` once the gateway at
 * the port its runtime record names has proved it serves this home, or `{ok: false, reason,
 * sentence}`. The reasons: `none` (no live record, or nothing listens there), `silent`, `other_home`,
 * `not_gateway`, `no_secret`, `unproved` and `wrong_proof`. Before any of them, the only request
 * sent is the identity read, which carries a nonce and no credential.
 */
async function reach(home, { timeoutMs = ANSWER_TIMEOUT_MS, attempts = ANSWER_ATTEMPTS, kill } = {}) {
  const record = readRecord(home);
  if (!record || !pidIsAlive(record.pid, kill)) return refused("none", `No gateway is running for this home (${home}).`);
  const { port } = record;
  const where = `Port ${port}, where this home's gateway said it listens,`;
  const nonce = newNonce();
  const said = await healthz(port, nonce, { timeoutMs, attempts });
  if (said === NOTHING) return refused("none", `No gateway is running for this home (${home}): nothing listens on port ${port}.`);
  if (said === SILENT) {
    const waited = Math.round((timeoutMs * Math.max(1, attempts)) / 1000);
    return refused(
      "silent",
      `${where} did not answer within ${waited} seconds, so this home's local secret was not sent there. If this home's gateway is starting, try again in a moment.`
    );
  }
  const id = homeId(home);
  if (!id || said.home_id !== id) {
    const theirs = said.status === "ok" && typeof said.home_id === "string";
    return refused(
      theirs ? "other_home" : "not_gateway",
      `${where} is answered by ${theirs ? "another PersonalClaw home's gateway" : "a program that is not a PersonalClaw gateway"}, not by this home's (${home}), so this home's local secret was not sent to it.`
    );
  }
  const secret = readSecret(home);
  if (!secret) {
    return refused(
      "no_secret",
      `This home's gateway answers on port ${port}, but this home's local secret (${path.join(home, SECRET_FILE)}) can't be read, so the app can't sign in to it.`
    );
  }
  if (typeof said.proof !== "string") {
    return refused(
      "unproved",
      `${where} is answered by a gateway that says it serves this home (${home}) but did not prove it holds this home's local secret, so the secret was not sent to it. If it is this home's gateway from an older PersonalClaw, restart it.`
    );
  }
  if (!sameProof(said.proof, gatewayProof(secret, nonce, port))) {
    return refused(
      "wrong_proof",
      `${where} is answered by a program that says it serves this home (${home}) with a proof this home's local secret did not make, so the secret was not sent to it. If it is this home's gateway and the home's secret was replaced since it started, restart it.`
    );
  }
  const gateway = Object.freeze({
    port,
    pid: Number.isInteger(said.pid) ? said.pid : record.pid,
    url: `http://${LOOPBACK_HOST}:${port}`,
    version: typeof said.version === "string" ? said.version : "",
  });
  reached.add(gateway);
  return { ok: true, gateway };
}

/** What a gateway's error answer says: its envelope's message, else its status (`home_gateway.error_text`). */
function errorText(body, status) {
  const error = body && body.error;
  if (error && typeof error === "object") return String(error.message || error.code || `HTTP ${status}`);
  return error ? String(error) : `HTTP ${status}`;
}

/**
 * Trade this home's local secret for a session at *gateway*, one `reach` found: `{ok: true, ready:
 * {port, token, url}}`, shaped as a ready line, or `{ok: false, sentence}`. Throws for a gateway
 * `reach` did not make: this is the one request that carries the secret.
 */
async function signIn(gateway, home, { ttl = SIGN_IN_TTL, timeoutMs = SIGN_IN_TIMEOUT_MS } = {}) {
  if (!gateway || typeof gateway !== "object" || !reached.has(gateway)) {
    throw new Error("not a gateway reach found: the app signs in only at one that proved it serves this home");
  }
  const secret = readSecret(home);
  if (!secret) return { ok: false, sentence: `This home's local secret can't be read, so the app can't sign in to its gateway on port ${gateway.port}.` };
  const answer = await getOnce(gateway.port, `/api/token/local?ttl=${encodeURIComponent(ttl)}`, {
    timeoutMs,
    headers: { "X-Local-Secret": secret },
  });
  if (answer === NOTHING || answer === SILENT) {
    return { ok: false, sentence: `This home's gateway on port ${gateway.port} could not be asked to sign this app in (${answer === SILENT ? "no answer" : "nothing listens there any more"}).` };
  }
  const token = answer.body && typeof answer.body.token === "string" ? answer.body.token : "";
  if (answer.status !== 200 || !token) {
    return { ok: false, sentence: `This home's gateway on port ${gateway.port} did not sign this app in: ${errorText(answer.body, answer.status)}` };
  }
  return { ok: true, ready: { port: gateway.port, token, url: gateway.url } };
}

module.exports = {
  ANSWER_ATTEMPTS,
  ANSWER_LIMIT,
  ANSWER_TIMEOUT_MS,
  LOOPBACK_HOST,
  PROOF_CONTEXT,
  RUNTIME_FILE,
  SECRET_FILE,
  SIGN_IN_TTL,
  VERSION_FILE,
  gatewayProof,
  homeId,
  homeIdOf,
  isNonce,
  newNonce,
  newestThatServed,
  pidIsAlive,
  reach,
  readRecord,
  signIn,
};
