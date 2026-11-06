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
 * does. A gateway whose proven answer says it has the window's own door (`WINDOW_DOOR_FIELD`) is
 * asked at that door, `GET /api/token/local?for=window`, for a session of the windows' own with its
 * pair; one that doesn't say so is asked as it always was. The reply is shaped as a ready line, so
 * the shell holds it exactly as it holds that one (`localSignIn.js`): in memory, as the windows'
 * session cookie, never on disk, in a URL or a log.
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

/** How long a session the app signs in for at a gateway it found lasts: 30 days, the door's
 *  maximum, as the token door's life is today. At the app's own gateway the window's door is asked
 *  with no ttl (`signIn`'s `ttl: null`), and gives `auth.session_ttl`, what the ready line's session
 *  lasts. Quitting signs either out, as it signs the ready line's out. */
const SIGN_IN_TTL = "30d";
/** A pair's shape, as the gateway gives it and the dashboard keeps it (`web/src/lib/pair.ts`). */
const PAIR_SHAPE = /^[A-Za-z0-9_-]{43}$/;
const SIGN_IN_TIMEOUT_MS = 5000;

/** The name the gateway says it has the window's own door by, on its ready line and in a proven
 *  health answer (`sign_in_pair.WINDOW_DOOR`, `tests/test_the_window_door_flag_parity.py`). */
const WINDOW_DOOR_FIELD = "window_door";
/** The route that ends the session that authorized it (`handlers/auth.api_auth_logout`). */
const LOGOUT_PATH = "/api/auth/logout";

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
 * One request (a GET, or *method*) to `127.0.0.1:<port><route>`: `{status, body}` (the parsed body,
 * `{}` for one that is not an object), or `NOTHING` when nothing takes the connection, or `SILENT`
 * when what took it said nothing in time. No proxy, no redirect followed, at most `ANSWER_LIMIT`
 * bytes read. A POST carries no body.
 */
function getOnce(port, route, { timeoutMs, headers = {}, method = "GET" }) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const req = http.request(
      {
        host: LOOPBACK_HOST,
        port,
        path: route,
        method,
        agent: false,
        timeout: timeoutMs,
        headers: { Accept: "application/json", ...(method === "GET" ? {} : { "Content-Length": "0" }), ...headers },
      },
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
  // Read only now, beside a proof this home's secret made: the flag rides on nothing less.
  const gateway = Object.freeze({
    port,
    pid: Number.isInteger(said.pid) ? said.pid : record.pid,
    url: `http://${LOOPBACK_HOST}:${port}`,
    version: typeof said.version === "string" ? said.version : "",
    windowDoor: said[WINDOW_DOOR_FIELD] === true,
    secretDigest: digestOf(secret),
  });
  reached.add(gateway);
  return { ok: true, gateway };
}

/** The sha256 of a local secret: what tells one start of a gateway from the next on the same port. */
function digestOf(secret) {
  return crypto.createHash("sha256").update(String(secret)).digest("hex");
}

/** The digest of the secret this home's gateway wrote for its current start, or "" for none. Read at
 *  a ready line, it is what a later proof on the same port must have been checked with to be the
 *  same start (`gatewayWatch`). */
function startDigest(home) {
  const secret = readSecret(home);
  return secret ? digestOf(secret) : "";
}

/** What a gateway's error answer says: its envelope's message, else its status (`home_gateway.error_text`). */
function errorText(body, status) {
  const error = body && body.error;
  if (error && typeof error === "object") return String(error.message || error.code || `HTTP ${status}`);
  return error ? String(error) : `HTTP ${status}`;
}

/**
 * Trade this home's local secret for a session at *gateway*, one `reach` found: `{ok: true, ready:
 * {port, token, url, windowDoor}, pair}`, shaped as a ready line, or `{ok: false, status, sentence}`,
 * *status* being the refusal's HTTP status, or 0 where nothing answered or nothing was sent. Throws
 * for a gateway `reach` did not make: this is the one request that carries the secret. Only the
 * secret the proof was checked with is sent: one that changed since is not (`secretDigest`).
 *
 * The door is the one *gateway*'s proven answer names. At the window's own door, an answer without
 * a pair, or with one of another shape, is a failed sign-in: that session is signed out at once,
 * and nothing of it reaches a window (`{ok: false, pairless: true}`). A *ttl* of null asks for none,
 * so the gateway gives its own.
 */
async function signIn(gateway, home, { ttl = SIGN_IN_TTL, timeoutMs = SIGN_IN_TIMEOUT_MS } = {}) {
  if (!gateway || typeof gateway !== "object" || !reached.has(gateway)) {
    throw new Error("not a gateway reach found: the app signs in only at one that proved it serves this home");
  }
  const secret = readSecret(home);
  if (!secret) return { ok: false, status: 0, sentence: `This home's local secret can't be read, so the app can't sign in to its gateway on port ${gateway.port}.` };
  if (digestOf(secret) !== gateway.secretDigest) {
    return { ok: false, status: 0, sentence: `This home's local secret changed after its gateway on port ${gateway.port} proved it serves this home, so nothing was sent. Try again.` };
  }
  const query = [gateway.windowDoor ? "for=window" : "", ttl ? `ttl=${encodeURIComponent(ttl)}` : ""].filter(Boolean).join("&");
  const answer = await getOnce(gateway.port, `/api/token/local${query ? `?${query}` : ""}`, {
    timeoutMs,
    headers: { "X-Local-Secret": secret },
  });
  if (answer === NOTHING || answer === SILENT) {
    return { ok: false, status: 0, sentence: `This home's gateway on port ${gateway.port} could not be asked to sign this app in (${answer === SILENT ? "no answer" : "nothing listens there any more"}).` };
  }
  const token = answer.body && typeof answer.body.token === "string" ? answer.body.token : "";
  if (answer.status !== 200 || !token) {
    return { ok: false, status: answer.status, sentence: `This home's gateway on port ${gateway.port} did not sign this app in: ${errorText(answer.body, answer.status)}` };
  }
  const ready = { port: gateway.port, token, url: gateway.url, windowDoor: gateway.windowDoor };
  if (!gateway.windowDoor) return { ok: true, ready, pair: null };
  const pair = typeof answer.body.pair === "string" && PAIR_SHAPE.test(answer.body.pair) ? answer.body.pair : "";
  if (!pair) {
    await getOnce(gateway.port, LOGOUT_PATH, { timeoutMs, method: "POST", headers: { Authorization: `Bearer ${token}` } });
    return { ok: false, status: 200, pairless: true, sentence: `This home's gateway on port ${gateway.port} didn't finish signing this app's windows in, so that sign-in was ended.` };
  }
  return { ok: true, ready, pair };
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
  WINDOW_DOOR_FIELD,
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
  startDigest,
};
