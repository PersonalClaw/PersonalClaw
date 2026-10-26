const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn } = require("node:child_process");

const { shutdownGateway } = require("../gatewayShutdown");
const { ASK_MS, KILL_WAIT_MS, QUIT_DEADLINE_MS, SMOKE_WINDOW_MS, quitWithin } = require("../quitSequence");

const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");

/**
 * A gateway that answers nothing, as the x64 release rehearsal's did at quit: its port takes every
 * request and replies to none, and its process ignores SIGTERM, so a SIGKILL alone ends it. It leads
 * its own group, as the shell's gateway does (`detached: true`).
 */
async function aGatewayThatNeverAnswers() {
  const held = [];
  const server = http.createServer((req) => held.push(req));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const child = spawn(
    process.execPath,
    ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000); process.stdout.write('up\\n')"],
    { detached: true, stdio: ["ignore", "pipe", "ignore"] }
  );
  await new Promise((resolve) => child.stdout.once("data", resolve)); // its SIGTERM handler is set
  return {
    port: server.address().port,
    child,
    alive: () => child.exitCode === null && child.signalCode === null,
    close() {
      for (const req of held) req.socket.destroy();
      server.close();
      try {
        process.kill(-child.pid, "SIGKILL");
      } catch {
        // Already gone.
      }
    },
  };
}

/** One ask as the shell sends it (`main.js`'s `postGateway`): a POST given *ms* to answer. */
function ask(port, pathname) {
  return (ms) =>
    new Promise((resolve) => {
      const req = http.request({ hostname: "127.0.0.1", port, path: pathname, method: "POST", timeout: ms }, (res) => {
        res.resume();
        res.on("end", () => resolve(res.statusCode));
      });
      req.on("error", () => resolve(null));
      req.on("timeout", () => {
        req.destroy();
        resolve(null);
      });
      req.end("{}");
    });
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

describe("the shell's quit, against a gateway that answers nothing", { concurrency: true }, () => {
  it("ends the gateway within its deadline, inside the smoke's window with margin", { timeout: 20_000 }, async () => {
    const gateway = await aGatewayThatNeverAnswers();
    try {
      const began = Date.now();
      const result = await quitWithin({
        asks: [ask(gateway.port, "/api/desktop/unregister"), ask(gateway.port, "/api/auth/logout")],
        stop: (budget) => shutdownGateway({ child: gateway.child, killGroup: true, ...budget }),
      });
      const took = Date.now() - began;
      assert.equal(result.outcome, "killed", `premise: only a SIGKILL ends this gateway (${JSON.stringify(result)})`);
      assert.equal(gateway.alive(), false, "the quit left the gateway running");
      assert.ok(took <= QUIT_DEADLINE_MS + 300, `the quit took ${took} ms, past its deadline of ${QUIT_DEADLINE_MS} ms`);
      assert.ok(
        took <= SMOKE_WINDOW_MS - 1_000,
        `the quit took ${took} ms: the smoke SIGKILLs the shell ${SMOKE_WINDOW_MS} ms after its SIGTERM, ` +
          "and that never reaches the gateway's own group"
      );
    } finally {
      gateway.close();
    }
  });

  it("is what the quit it replaced was not: that one still had the gateway running at 10 s", { timeout: 30_000 }, async () => {
    // Planted: the old shape. The unregister, then the sign-out, each given 5 s, then 8 s for the
    // gateway to stop and 2 s for its SIGKILL. When the smoke's SIGKILL comes, the gateway runs on.
    const gateway = await aGatewayThatNeverAnswers();
    const signalled = [];
    let old = Promise.resolve();
    try {
      old = (async () => {
        await ask(gateway.port, "/api/desktop/unregister")(5_000);
        await ask(gateway.port, "/api/auth/logout")(5_000);
        return shutdownGateway({
          child: gateway.child,
          killGroup: true,
          graceMs: 8_000,
          killGraceMs: 2_000,
          killPid: (pid, sig) => {
            signalled.push(sig);
            process.kill(pid, sig);
          },
        });
      })();
      await wait(SMOKE_WINDOW_MS);
      assert.equal(gateway.alive(), true, "premise: the old quit had stopped the gateway by the smoke's SIGKILL");
      assert.ok(!signalled.includes("SIGKILL"), `premise: the old quit had SIGKILLed the gateway by 10 s: ${signalled}`);
    } finally {
      gateway.close();
      await old.catch(() => {});
    }
  });
});

describe("the quit's budget", () => {
  it("gives the gateway what is left of the deadline after the asks", { timeout: 10_000 }, async () => {
    const given = [];
    const stop = async (budget) => given.push(budget);
    await quitWithin({ asks: [async () => 200, async () => 200], stop });
    const answered = given.pop();
    assert.equal(answered.killGraceMs, KILL_WAIT_MS);
    assert.ok(answered.graceMs >= QUIT_DEADLINE_MS - KILL_WAIT_MS - 200, `asks answered at once: ${answered.graceMs} ms`);

    // Asks that never settle, nor honour the time they are given, are let go at ASK_MS, together.
    const began = Date.now();
    await quitWithin({ asks: [() => new Promise(() => {}), () => new Promise(() => {})], stop });
    const took = Date.now() - began;
    assert.ok(took >= ASK_MS - 50 && took < ASK_MS + 500, `two silent asks held the quit ${took} ms`);
    const silent = given.pop();
    assert.ok(silent.graceMs <= QUIT_DEADLINE_MS - KILL_WAIT_MS - ASK_MS + 50, `silent asks: ${silent.graceMs} ms`);
  });

  it("is what main.js quits by, and the shell's exit takes the gateway's group", () => {
    const quit = MAIN.slice(MAIN.indexOf('app.on("before-quit"'));
    const handler = quit.slice(0, quit.indexOf("\n});\n"));
    assert.match(handler, /quitWithin\(\{/, "before-quit no longer quits within the deadline");
    assert.match(handler, /stop: \(budget\) => stopGateway\(budget\)/, "the stop is not given the budget");
    assert.doesNotMatch(handler, /\.then\(\(\) => stopGateway\(\)\)/, "the stop waits on the asks again");
    assert.match(MAIN, /timeout: timeoutMs,/, "postGateway no longer takes the time it is given");
    const exit = MAIN.slice(MAIN.indexOf('process.on("exit"'));
    assert.match(exit.slice(0, exit.indexOf("\n});\n")), /process\.kill\(-liveGatewayGroup, "SIGKILL"\)/);
  });
});
