/**
 * The app finds its home's running gateway the way a command does (`home_gateway.reach`), against
 * real loopback listeners and real files.
 *
 * Each fake gateway below answers on `127.0.0.1:0` and writes down every request it is sent, with
 * its headers, so a test can say what reached it and what never did: the home's local secret goes
 * to a gateway only after it proved it holds it, and to nothing else. The proof and the home's id
 * are made here by hand, from `home_gateway`'s definitions, rather than by the module under test,
 * and `VECTORS` are `home_gateway`'s own answers, which `tests/test_desktop_home_gateway_parity.py`
 * holds to the Python.
 */

const { describe, it, after } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const {
  ANSWER_ATTEMPTS,
  LOOPBACK_HOST,
  PROOF_CONTEXT,
  SIGN_IN_TTL,
  gatewayProof,
  homeId,
  homeIdOf,
  isNonce,
  newNonce,
  reach,
  signIn,
} = require("../homeGateway");

/** `home_gateway`'s answers for these inputs (`gateway_proof`, and `home_id`'s hash of a resolved path). */
const VECTORS = Object.freeze({
  proofs: [
    { secret: "s3cr3t-local", nonce: "0123456789abcdef0123456789abcdef", port: 54321, proof: "619c739f7f0d61668b41e9384a9b3140bd5df904ba49ef6d41ff006124319325" },
    { secret: "another secret", nonce: "ffffffffffffffffffffffffffffffff", port: 10000, proof: "94f771a349c49d6829a674a76f5346c808339fe062dc441a2b045f3797156977" },
  ],
  homeIds: [{ resolved: "/", id: "8a5edab282632443" }],
});

const tmpHome = () => fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-home-gateway-"));
const servers = [];
after(() => {
  for (const s of servers) {
    s.closeAllConnections?.();
    s.close();
  }
});

/** The id a gateway serving *home* answers with: `home_gateway.home_id`, made by hand. */
const idOf = (home) => crypto.createHash("sha256").update(fs.realpathSync(home)).digest("hex").slice(0, 16);
/** The proof a gateway holding *secret* answers a read carrying *nonce* on *port* with, made by hand. */
const proofOf = (secret, nonce, port) => crypto.createHmac("sha256", secret).update(`${PROOF_CONTEXT}\n${nonce}\n${port}`).digest("hex");

function writeRecord(home, record) {
  fs.writeFileSync(path.join(home, "gateway.runtime.json"), typeof record === "string" ? record : JSON.stringify(record));
}
function writeSecret(home, secret) {
  fs.writeFileSync(path.join(home, ".local_secret"), `${secret}\n`, { mode: 0o600 });
}

/** A pid that is not running: a child that has already exited. */
function deadPid() {
  const r = spawnSync(process.execPath, ["-e", "process.exit(0)"]);
  return r.pid;
}

/**
 * A fake gateway on 127.0.0.1. `health(req, url, port)` answers `/api/healthz` (`{status, headers,
 * body}`, or `"silent"` to take the connection and never answer). `/api/token/local` signs in only
 * the secret it is given. Every request is written down in `seen`.
 */
async function fakeGateway({ secret = "", health }) {
  const seen = [];
  let connections = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    seen.push({ method: req.method, path: url.pathname, query: url.search, headers: { ...req.headers } });
    if (url.pathname === "/api/healthz") {
      const answer = health(req, url, server.address().port);
      if (answer === "silent") return; // the socket is held open and nothing is said
      res.writeHead(answer.status || 200, { "Content-Type": "application/json", ...(answer.headers || {}) });
      return res.end(typeof answer.body === "string" ? answer.body : JSON.stringify(answer.body));
    }
    if (url.pathname === "/api/token/local") {
      if (secret && req.headers["x-local-secret"] === secret) {
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ token: "minted-token", expires_in: 2592000, expires_at: 1, open_within: 86400 }));
      }
      res.writeHead(403, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "invalid secret" }));
    }
    res.writeHead(404);
    res.end();
  });
  server.on("connection", () => (connections += 1));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  servers.push(server);
  return { server, port: server.address().port, seen, connections: () => connections };
}

/** The `/api/healthz` answer of a gateway serving *home*, holding *secret*, as `api_healthz` makes it. */
const proven = (home, secret, { pid = 4242, version = "0.3.6" } = {}) => (_req, url, port) => {
  const nonce = url.searchParams.get("nonce") || "";
  return { body: { status: "ok", version, pid, home_id: idOf(home), root_ok: true, ...(nonce ? { proof: proofOf(secret, nonce, port) } : {}) } };
};

/** Every header any request carried, lowercased, across everything *seen*. */
const headersSent = (seen) => new Set(seen.flatMap((r) => Object.keys(r.headers)));

describe("what a gateway answers with, the way home_gateway makes it", () => {
  it("proves a nonce on a port with HMAC-SHA256 under the secret, as gateway_proof does", () => {
    for (const v of VECTORS.proofs) assert.equal(gatewayProof(v.secret, v.nonce, v.port), v.proof);
  });

  it("fingerprints a resolved home as home_id does", () => {
    for (const v of VECTORS.homeIds) assert.equal(homeIdOf(v.resolved), v.id);
  });

  it("resolves the home first, so a home named through a link has the id of the folder it is", () => {
    const real = tmpHome();
    const link = path.join(tmpHome(), "linked-home");
    fs.symlinkSync(real, link);
    assert.equal(homeId(link), idOf(real));
    assert.equal(homeId(real), idOf(real));
  });

  it("has no id for a home that does not exist: nothing can serve it", () => {
    assert.equal(homeId(path.join(tmpHome(), "never-made")), "");
  });

  it("makes a fresh 32-character hex nonce each time", () => {
    const a = newNonce();
    const b = newNonce();
    assert.ok(isNonce(a) && isNonce(b), `${a} ${b}`);
    assert.notEqual(a, b);
    assert.equal(isNonce("0123"), false);
    assert.equal(isNonce("G".repeat(32)), false);
  });
});

describe("reach: this home's running gateway, or why not", () => {
  it("finds it at the port its runtime record names, once it has proved it holds the home's secret", async () => {
    const home = tmpHome();
    writeSecret(home, "home-secret");
    const gw = await fakeGateway({ secret: "home-secret", health: proven(home, "home-secret", { pid: 7777, version: "0.3.7" }) });
    writeRecord(home, { port: gw.port, pid: process.pid });

    const found = await reach(home);
    assert.equal(found.ok, true, found.sentence);
    assert.equal(found.gateway.port, gw.port);
    assert.equal(found.gateway.pid, 7777, "the pid the gateway itself answered with");
    assert.equal(found.gateway.version, "0.3.7");
    assert.equal(found.gateway.url, `http://${LOOPBACK_HOST}:${gw.port}`, "the address the identity was read at, never a name");
    // One identity read, carrying a fresh nonce and nothing else.
    assert.equal(gw.seen.length, 1);
    assert.equal(gw.seen[0].path, "/api/healthz");
    assert.match(gw.seen[0].query, /^\?nonce=[0-9a-f]{32}$/);
    assert.equal(gw.seen[0].headers.host, `${LOOPBACK_HOST}:${gw.port}`);
    for (const h of ["x-local-secret", "authorization", "cookie", "x-internal-secret"]) assert.equal(h in gw.seen[0].headers, false, `the identity read carried ${h}`);
  });

  it("finds nothing, and asks nothing, with no record, a damaged one, or one whose process is gone", async () => {
    const gw = await fakeGateway({ health: () => ({ body: {} }) });
    for (const record of [null, "{not json", JSON.stringify({ port: "x", pid: process.pid }), { port: gw.port, pid: 0 }, { port: gw.port, pid: deadPid() }]) {
      const home = tmpHome();
      writeSecret(home, "s");
      if (record !== null) writeRecord(home, record);
      const found = await reach(home);
      assert.equal(found.ok, false);
      assert.equal(found.reason, "none", JSON.stringify(record));
    }
    assert.equal(gw.seen.length, 0, "a record that names no live gateway sends nothing anywhere");
  });

  it("finds nothing when nothing listens at the port the record names", async () => {
    const gw = await fakeGateway({ health: () => ({ body: {} }) });
    const port = gw.port;
    gw.server.close();
    await new Promise((r) => setTimeout(r, 50));
    const home = tmpHome();
    writeSecret(home, "s");
    writeRecord(home, { port, pid: process.pid });
    const found = await reach(home);
    assert.equal(found.reason, "none");
  });

  for (const [what, health, reason] of [
    ["another home's gateway", (home) => (_r, url, port) => ({ body: { status: "ok", version: "0.3.6", pid: 1, home_id: "0000000000000000", proof: proofOf("home-secret", url.searchParams.get("nonce"), port) } }), "other_home"],
    ["a gateway of this home that proves nothing (an older one)", (home) => (_r, _u, _p) => ({ body: { status: "ok", version: "0.3.0", pid: 1, home_id: idOf(home) } }), "unproved"],
    ["a gateway of this home whose proof another secret made", (home) => proven(home, "a different secret"), "wrong_proof"],
    ["a program that is not a gateway", () => () => ({ body: "<html>hello</html>", headers: { "Content-Type": "text/html" } }), "not_gateway"],
    ["a refusal", () => () => ({ status: 403, body: { error: "no" } }), "not_gateway"],
  ]) {
    it(`sends the secret nowhere when the port is answered by ${what}`, async () => {
      const home = tmpHome();
      writeSecret(home, "home-secret");
      const gw = await fakeGateway({ secret: "home-secret", health: health(home) });
      writeRecord(home, { port: gw.port, pid: process.pid });
      const found = await reach(home);
      assert.equal(found.ok, false);
      assert.equal(found.reason, reason);
      assert.match(found.sentence, new RegExp(`port ${gw.port}`, "i"));
      assert.match(found.sentence, /secret was not sent/i);
      assert.equal(gw.seen.length, 1, "nothing was asked after the identity read");
      assert.equal(headersSent(gw.seen).has("x-local-secret"), false);
    });
  }

  it("never follows a redirect: the place it points hears nothing", async () => {
    const elsewhere = await fakeGateway({ health: () => ({ body: {} }) });
    const home = tmpHome();
    writeSecret(home, "home-secret");
    const gw = await fakeGateway({ health: () => ({ status: 302, headers: { Location: `http://127.0.0.1:${elsewhere.port}/api/healthz` }, body: "" }) });
    writeRecord(home, { port: gw.port, pid: process.pid });
    const found = await reach(home);
    assert.equal(found.reason, "not_gateway");
    assert.equal(elsewhere.seen.length, 0, "the redirect was followed");
  });

  it("does not attach without the home's secret: it could not sign in, and could not check a proof", async () => {
    const home = tmpHome();
    const gw = await fakeGateway({ health: proven(home, "whatever") });
    writeRecord(home, { port: gw.port, pid: process.pid });
    const found = await reach(home);
    assert.equal(found.reason, "no_secret");
    assert.equal(headersSent(gw.seen).has("x-local-secret"), false);
  });

  it("asks a port that takes the connection and says nothing again, then says it did not answer", async () => {
    const home = tmpHome();
    writeSecret(home, "s");
    const gw = await fakeGateway({ health: () => "silent" });
    writeRecord(home, { port: gw.port, pid: process.pid });
    const found = await reach(home, { timeoutMs: 150 });
    assert.equal(found.reason, "silent");
    assert.equal(gw.seen.length, ANSWER_ATTEMPTS, "a timeout is read again, up to the attempts");
    assert.match(found.sentence, /did not answer/);
  });
});

describe("signIn: trade the home's secret for a session, at the gateway reach found, and nowhere else", () => {
  it("asks /api/token/local on 127.0.0.1 at the proven port, with the secret in its header, for the ready line's lifetime", async () => {
    const home = tmpHome();
    writeSecret(home, "home-secret");
    const gw = await fakeGateway({ secret: "home-secret", health: proven(home, "home-secret") });
    writeRecord(home, { port: gw.port, pid: process.pid });
    const found = await reach(home);
    const signed = await signIn(found.gateway, home);
    assert.equal(signed.ok, true, signed.sentence);
    assert.deepEqual(signed.ready, { port: gw.port, token: "minted-token", url: `http://127.0.0.1:${gw.port}` }, "shaped as a ready line, for adoptLocalGateway");
    const ask = gw.seen[1];
    assert.equal(ask.path, "/api/token/local");
    assert.equal(ask.query, `?ttl=${SIGN_IN_TTL}`);
    assert.equal(SIGN_IN_TTL, "30d", "the ready line's own default lifetime (auth.session_ttl)");
    assert.equal(ask.headers["x-local-secret"], "home-secret");
    assert.equal(ask.headers.host, `127.0.0.1:${gw.port}`);
  });

  it("says why when the gateway does not sign it in", async () => {
    const home = tmpHome();
    writeSecret(home, "home-secret");
    const gw = await fakeGateway({ secret: "other", health: proven(home, "home-secret") });
    writeRecord(home, { port: gw.port, pid: process.pid });
    const found = await reach(home);
    const signed = await signIn(found.gateway, home);
    assert.equal(signed.ok, false);
    assert.match(signed.sentence, /did not sign this app in: invalid secret/);
  });

  it("signs in only at a gateway reach made, never at one described by hand", async () => {
    const home = tmpHome();
    writeSecret(home, "home-secret");
    const gw = await fakeGateway({ secret: "home-secret", health: proven(home, "home-secret") });
    for (const forged of [{ port: gw.port, url: `http://127.0.0.1:${gw.port}` }, null, undefined]) {
      await assert.rejects(() => signIn(forged, home), /not a gateway reach found/);
    }
    assert.equal(gw.seen.length, 0, "a hand-made gateway was sent something");
  });
});
