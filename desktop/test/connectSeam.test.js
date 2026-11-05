/**
 * The `main.js` wiring claims that would go stale in silence.
 *
 * Everything below is a property of `desktop/main.js` as a FILE, because none of it can be observed
 * from a unit test that never launches Electron — and each one is a claim the report makes:
 *
 *   - the capability bridge is attached only under a `shouldAttachBridge` decision;
 *   - `.local_secret`, the shell token and the shell's sign-in are only ever sent by
 *     `postGateway` and `getGateway`, which assert loopback first;
 *   - the paired-gateway URL is never handed to a credential-bearing call;
 *   - the spawn-local readiness path is untouched;
 *   - there is no second retry timer racing the SPA's own reconnect.
 *
 * A source scan is a weak instrument and this file says so rather than pretending otherwise: each
 * test is paired with a floor that proves the pattern it greps for is actually present in the file,
 * so a rename cannot turn a scan into a no-op that passes forever.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const PRELOAD_MAIN = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
/** The child env moved into its own pure module so a test could EXECUTE it (issue 2673). */
const GATEWAY_ENV = fs.readFileSync(path.join(__dirname, "..", "gatewayEnv.js"), "utf8");
const { AUTH_SWITCHES } = require("../gatewayEnv");

describe("the scans below are not vacuous", () => {
  it("is reading a main.js that still contains the things it looks for", () => {
    assert.ok(MAIN.length > 20_000, "main.js is suspiciously small — is this the right file?");
    for (const anchor of [
      "localGatewayUrl",
      "activeUrl",
      "shouldAttachBridge",
      "assertLoopbackTarget",
      "X-Local-Secret",
      "X-Shell-Token",
      "scheduleReachProbe",
      "waitForBackend",
    ]) {
      assert.ok(MAIN.includes(anchor), `main.js no longer mentions ${anchor}; this rail is now blind`);
    }
  });
});

describe("the capability bridge is attached only by decision", () => {
  it("passes `preload.js` exactly once, and only behind an `attachBridge` conditional", () => {
    const sites = [...MAIN.matchAll(/preload:\s*path\.join\(__dirname,\s*"preload\.js"\)/g)];
    assert.strictEqual(sites.length, 1, `expected one preload site, found ${sites.length}`);
    // The one site must be inside the ternary/spread that consults `attachBridge`.
    const line = MAIN.slice(0, sites[0].index).split("\n").length;
    const context = MAIN.split("\n").slice(line - 3, line + 1).join("\n");
    assert.match(context, /attachBridge/, "the preload is attached unconditionally");
  });

  it("takes the bridge decision from `shouldAttachBridge` and not from a local guess", () => {
    // Two call sites: the main window's navigation and a new tab. Both must ask the same function.
    assert.ok([...MAIN.matchAll(/shouldAttachBridge\(/g)].length >= 2);
    // And nothing may hand `setupWindowContents` a hardcoded `true` for a non-local target.
    assert.strictEqual(/attachBridge:\s*true/.test(MAIN), false, "a call site hardcodes attachBridge: true");
  });

  it("gives the bridge only to the gateway proven to serve this home: every decision names `localGatewayUrl`", () => {
    // `localGatewayUrl` has two writers: the spawned gateway's ready line, and an attach that
    // proved the gateway holds this home's secret (`homeGateway.reach`). A decision made on the
    // address alone is not one about this home: being on this computer proves nothing.
    const calls = [...MAIN.matchAll(/shouldAttachBridge\(([^()]*)\)/g)];
    assert.ok(calls.length >= 2, "the bridge decisions are gone — this rail is now blind");
    for (const m of calls) {
      assert.match(m[1], /^[^,]+,\s*localGatewayUrl$/, `shouldAttachBridge(${m[1]}) decides without the app's own gateway`);
    }
  });

  it("does not expose the dialog's IPC through the capability preload, or vice versa", () => {
    // Two preloads, two surfaces. A switcher that could reach the microphone bridge, or a dashboard
    // page that could reach the switcher's `confirm`, would collapse them back into one.
    assert.strictEqual(PRELOAD_MAIN.includes("pclawConnect"), false);
    assert.strictEqual(PRELOAD_MAIN.includes("connectDialog"), false);
    const dialogPreload = fs.readFileSync(path.join(__dirname, "..", "connectPreload.js"), "utf8");
    assert.strictEqual(dialogPreload.includes("pclawDesktop"), false);
    assert.strictEqual(dialogPreload.includes("capabilities"), false);
  });
});

describe("machine-local credentials never leave loopback", () => {
  it("sends `X-Local-Secret` and `X-Shell-Token` only through `postGateway`", () => {
    for (const header of ["X-Local-Secret", "X-Shell-Token"]) {
      for (const m of MAIN.matchAll(new RegExp(`"${header}"`, "g"))) {
        // Walk back to the nearest call opener and require it to be postGateway.
        const before = MAIN.slice(Math.max(0, m.index - 600), m.index);
        assert.match(before, /postGateway\(/, `${header} is sent by something other than postGateway`);
      }
    }
  });

  it("makes `postGateway` and `getGateway` read `localGatewayUrl`, never `activeUrl`", () => {
    for (const fn of ["postGateway", "getGateway"]) {
      const start = MAIN.indexOf(`function ${fn}(`);
      assert.ok(start > 0, `${fn} not found`);
      // The function body up to the next top-level `\n}` — enough to cover the URL resolution.
      const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
      assert.match(body, /localGatewayUrl/, `${fn} does not target the spawned gateway`);
      assert.strictEqual(/activeUrl/.test(body), false, `${fn} can target a user-supplied endpoint`);
    }
  });

  it("asserts loopback inside `postGateway`, before a body is written", () => {
    const start = MAIN.indexOf("function postGateway(");
    const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
    const assertAt = body.indexOf("assertLoopbackTarget");
    const writeAt = body.indexOf("req.write(");
    assert.ok(assertAt > 0, "postGateway does not assert its target");
    assert.ok(writeAt > assertAt, "postGateway writes a body before it checks where it is going");
  });

  it("asserts loopback inside `getGateway`, before its signed-in request is made", () => {
    const start = MAIN.indexOf("function getGateway(");
    const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
    const assertAt = body.indexOf("assertLoopbackTarget");
    const requestAt = body.indexOf("http.request(");
    assert.ok(assertAt > 0, "getGateway does not assert its target");
    assert.ok(requestAt > assertAt, "getGateway sends the shell's sign-in before it checks where");
  });

  it("sends the shell's sign-in only through `postGateway`, `getGateway` and the doorbell's `socketGateway`", () => {
    // The session the ready line hands out is the owner's. Read from one place (`localSignIn`),
    // and put on a request by the three helpers that target the spawned gateway and nothing else.
    const sites = [...MAIN.matchAll(/localSignIn\.authorization\(\)/g)];
    assert.strictEqual(sites.length, 3, `expected three sites, found ${sites.length}`);
    for (const m of sites) {
      const before = MAIN.slice(0, m.index);
      const opener = Math.max(before.lastIndexOf("function postGateway("), before.lastIndexOf("function getGateway("), before.lastIndexOf("function socketGateway("));
      const closer = MAIN.indexOf("\n}\n", opener);
      assert.ok(opener > 0 && m.index < closer, "the sign-in is sent outside the three loopback helpers");
      assert.match(MAIN.slice(opener, closer), /assertLoopbackTarget\(localGatewayUrl/, "a helper that sends the sign-in re-asserts loopback");
    }
    // A sign-out is its own session ending itself, still through postGateway.
    const signOut = MAIN.slice(MAIN.indexOf("async function signOutLocalSession("));
    const body = signOut.slice(0, signOut.indexOf("\n}\n"));
    assert.match(body, /signOutRequest\(/);
    assert.match(body, /postGateway\(/, "a sign-out is sent by something other than postGateway");
  });

  it("puts no token in any URL the shell loads or requests", () => {
    // The gateway's `/?token=` link would leave the session in the window's history, and the cookie
    // the gateway sets for it would outlive the app in its cookie file.
    assert.strictEqual(/[?&](app_)?token=/.test(MAIN), false, "main.js composes a URL with a token in it");
    assert.match(MAIN, /wc\.loadURL\(localGatewayUrl\)/, "the window no longer loads the bare origin");
  });
});

describe("the spawn-local path is unchanged", () => {
  it("still waits on the spawned gateway's own readiness probe", () => {
    // `waitForBackend` is the ORIGINAL readiness wait, with `checkBackend`'s `< 500` tolerance.
    // Connect-mode added `waitForEndpoint` beside it rather than generalising it, precisely so this
    // stays true. It asks `/api/healthz`, which needs no sign-in: the gateway asks every other
    // request for one, so a credential-free probe of `/api/status` was a refusal in its audit log
    // at every launch and every new tab.
    const start = MAIN.indexOf("function waitForBackend(");
    const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
    assert.match(body, /\$\{localGatewayUrl\}\/api\/healthz/);
    assert.match(body, /checkBackend\(healthUrl\)/);
    assert.strictEqual(/probeEndpoint/.test(body), false, "the spawn-local wait now goes through the connect-mode probe");
    assert.match(MAIN, /function waitForEndpoint\(/, "the paired-gateway wait is missing");
  });

  it("still spawns with the same argv", () => {
    assert.match(MAIN, /\["gateway", "--port", "auto", "--json-ready", "--no-open"\]/);
  });

  it("builds the child env in one place, the spawn, and names no auth switch in main.js", () => {
    // The env literal moved to `gatewayEnv.js` (issue 2673) so a node test could execute it
    // instead of grepping it, and that test holds it to passing on no authentication switch. A
    // switch named here instead would be invisible to it.
    for (const name of AUTH_SWITCHES) {
      assert.strictEqual(MAIN.includes(name), false, `main.js names ${name} — the child env belongs to gatewayEnv.js`);
    }
    const calls = [...MAIN.matchAll(/buildGatewayEnv\(/g)];
    assert.strictEqual(calls.length, 1, `expected one buildGatewayEnv call, found ${calls.length}`);
    const spawnAt = MAIN.indexOf("gatewayProcess = spawn(");
    assert.ok(spawnAt > 0, "the spawn call is gone — this rail is now blind");
    assert.ok(calls[0].index > spawnAt, "the child env is built somewhere other than the spawn");
    // The module that OWNS the child env cannot even name a user-supplied endpoint, so no edit
    // there can describe a gateway the shell did not spawn.
    assert.strictEqual(
      /activeUrl|connect/i.test(GATEWAY_ENV), false,
      "gatewayEnv.js mentions connect-mode — it must only ever describe the spawned child",
    );
  });

  it("reads every ready line, so a restart in place is followed rather than lost", () => {
    // The gateway's Restart re-executes the same process on a NEW port with a new sign-in, and
    // prints a second ready line. Reading only the first left every window on a dead address.
    const start = MAIN.indexOf('gatewayProcess.stdout.on("data"');
    assert.ok(start > 0, "the stdout reader is gone — this rail is now blind");
    const body = MAIN.slice(start, MAIN.indexOf("\n    });\n", start));
    assert.match(body, /parseReadyLine\(line\)/);
    assert.match(body, /if \(settled\) \{\s*followRestartedGateway\(ready\)/, "a later ready line is dropped");
    assert.match(body, /adoptLocalGateway\(ready\)/, "the first ready line signs nothing in");
  });
});

describe("the app starts its own gateway first, uses this home's running one only when asked, and never signals one it did not start", () => {
  const fnBody = (name) => {
    const start = MAIN.indexOf(`function ${name}(`);
    assert.ok(start > 0, `${name} is gone — this rail is now blind`);
    return MAIN.slice(start, MAIN.indexOf("\n}\n", start));
  };

  it("starts its own gateway first, and looks for this home's running one only once that start fails", () => {
    const up = fnBody("bringUpLocalGateway");
    const startAt = up.indexOf("await startGateway()");
    const catchAt = up.indexOf("} catch (err) {");
    const findAt = up.indexOf("await findHomeGateway(");
    assert.ok(startAt > 0 && catchAt > startAt && findAt > catchAt, "the app looks for another gateway before its own start has failed");
    assert.doesNotMatch(up.slice(0, catchAt), /reach|findHomeGateway/, "the own start waits on a look for another gateway");
    assert.match(up, /const served = exit\.code === HOME_SERVED_EXIT;[\s\S]*?await findHomeGateway\(\{ waitMs: served \? 20_000 : 0 \}\)/, "a start lost to another gateway gives up before that one listens");
    assert.match(up, /if \(found\.reason !== "none"\) \{[\s\S]*?setLocalState\("served", found\.sentence\);[\s\S]*?return false;/, "a gateway the app cannot sign in to is not said");
  });

  it("offers a running gateway it found without signing in to it, and uses it unasked only on the owner's standing yes", () => {
    const up = fnBody("bringUpLocalGateway");
    // The look after a failed start proves and sends no credential; the sign-in is the press's.
    assert.doesNotMatch(up, /reachHomeGateway|homeGateway\.signIn|adoptHomeGateway/, "the start signs in to a gateway nobody chose");
    assert.match(up, /if \(found\.ok\) \{\s*\/\/[^\n]*\n\s*if \(autoUseRunning\(\)\) return \(await useRunningGateway\(\)\)\.ok;\s*startFailure = \{ \.\.\.exit, found: true \};\s*setLocalState\("found", foundSentence\(found\.gateway\)\);\s*return false;/);
    assert.match(fnBody("autoUseRunning"), /shellStore\.getItem\(AUTO_USE_KEY\) === "1"/, "the standing yes is on unless the owner ticked it");
    // Signing in to a gateway the app found happens in exactly one place, and is reached only from
    // a press, the standing yes, and the adopted watch's follow of a gateway already in use.
    const signIns = [...MAIN.matchAll(/homeGateway\.signIn\(/g)];
    assert.equal(signIns.length, 1);
    assert.ok(signIns[0].index > MAIN.indexOf("async function reachHomeGateway(") && signIns[0].index < MAIN.indexOf("const AUTO_USE_KEY"), "a sign-in outside reachHomeGateway");
    const callers = [...MAIN.matchAll(/await reachHomeGateway\(/g)].map((m) => MAIN.slice(MAIN.lastIndexOf("\nfunction ", m.index) + 1 || MAIN.lastIndexOf("\nasync function ", m.index) + 1, m.index));
    assert.equal(callers.length, 2, "reachHomeGateway gained a caller");
    assert.match(fnBody("useRunningGateway"), /const found = await reachHomeGateway\(\);/);
    assert.match(MAIN.slice(MAIN.indexOf("const adoptedWatch = makeAdoptedWatch({"), MAIN.indexOf("gone: () => adoptedGone(),")), /follow: async \(\) => \{\s*const moved = await reachHomeGateway\(\);/);
  });

  it("uses a running gateway only while its own is neither up nor starting, on a press and the standing yes alike, and keeps the box's answer", () => {
    // The refusal is useRunningGateway's first step, before any sign-in, so the press (through
    // useRunningHere) and the standing yes both meet it.
    assert.match(fnBody("useRunningGateway"), /^function useRunningGateway\(\) \{\n\s*if \(ownGatewayLives\(\)\) return \{ ok: false, code: "own"/, "its own gateway can be adopted as one it did not start");
    assert.doesNotMatch(MAIN, /if \(gatewayProcess\) return \{ ok: false, code: "own"/, "a second, narrower refusal beside useRunningGateway's");
    const use = MAIN.slice(MAIN.indexOf("    use: async ({ remember }) => {"), MAIN.indexOf("    autoUse: async ({ on }) => {"));
    assert.match(use, /const used = await useRunningHere\(\);\s*if \(!used\.ok\) return used;\s*setAutoUseRunning\(remember\);/, "the box is kept for a gateway that could not be used");
    assert.match(fnBody("setAutoUseRunning"), /if \(on\) shellStore\.setItem\(AUTO_USE_KEY, "1"\);\s*else shellStore\.removeItem\(AUTO_USE_KEY\);/);
  });

  it("opens the chooser when nothing serves the windows, and no window when its own gateway starts", () => {
    const shown = fnBody("showNotConnected");
    assert.match(shown, /connectDialog\?\.openChooser\(\);/);
    assert.doesNotMatch(shown, /openConnectDialog\(\)/, "the full Gateways window opens instead of the chooser");
    assert.doesNotMatch(fnBody("bringUpLocalGateway"), /openChooser|openConnectDialog|showNotConnected/, "a start opens a window before anyone knows it failed");
    // The chooser's Add a Gateway… is the Gateways window, in place of the chooser.
    const gateways = MAIN.slice(MAIN.indexOf("    gateways: async () => {"), MAIN.indexOf("  };\n  return handlers;"));
    assert.match(gateways, /connectDialog\.closeChooser\(\);\s*openConnectDialog\(\);/);
  });

  it("holds an adopted gateway's session the way it holds a ready line's", () => {
    const adopt = fnBody("adoptHomeGateway");
    assert.match(adopt, /await followRestartedGateway\(found\.ready\)/);
    assert.match(adopt, /await adoptLocalGateway\(found\.ready\)/);
  });

  it("stops, signals and kills only its own child", () => {
    // The one stop takes `gatewayProcess`, the app's own child, and the exit handler's group kill
    // takes `liveGatewayGroup`, which only `startGateway` sets. Neither ever holds an adopted pid.
    assert.match(fnBody("stopGateway"), /const child = gatewayProcess;/);
    const kills = [...MAIN.matchAll(/process\.kill\(([^)]*)\)/g)].map((m) => m[1]);
    assert.deepStrictEqual(kills, ["-liveGatewayGroup, \"SIGKILL\""]);
    for (const m of MAIN.matchAll(/liveGatewayGroup = ([^;]+);/g)) {
      assert.ok(["child.pid ?? null", "null"].includes(m[1]), `liveGatewayGroup is set from ${m[1]}`);
    }
    for (const sink of ["process.kill(", "shutdownGateway(", "stopGateway("]) {
      for (let at = MAIN.indexOf(sink); at !== -1; at = MAIN.indexOf(sink, at + sink.length)) {
        const end = MAIN.indexOf("\n", at);
        const line = MAIN.slice(MAIN.lastIndexOf("\n", at) + 1, end === -1 ? MAIN.length : end);
        assert.doesNotMatch(line, /adopted/, `an adopted gateway reaches ${sink}: ${line.trim()}`);
      }
    }
  });

  it("follows an adopted gateway through the doorbell, and registers again only once it is proven", () => {
    // `adoptedWatch.js` decides; main.js routes the doorbell's news to it and does what it says.
    const at = MAIN.indexOf("const adoptedWatch = makeAdoptedWatch({");
    assert.ok(at > 0, "the adopted watch is gone — this rail is now blind");
    const made = MAIN.slice(at, MAIN.indexOf("\n});\n", at));
    assert.match(made, /reach: \(home\) => homeGateway\.reach\(home\)/);
    assert.match(made, /proven: \(\{ pid, port \}\) => \{\n\s+adoptedGateway = \{ pid, port \};\n\s+shellToken = null;\n\s+registerWithGateway\(\)/, "a restart's new registry is not registered with, or not with the proof's pid");
    assert.match(made, /gone: \(\) => adoptedGone\(\)/);
    assert.match(MAIN, /if \(adoptedGateway\) \{\n\s+if \(state === "up"\) adoptedWatch\.up\(\);\n\s+else adoptedWatch\.lost\(\);/);
    assert.doesNotMatch(MAIN, /function watchAdoptedGateway\(/, "a second follower of the doorbell's news");
    assert.match(fnBody("adoptHomeGateway"), /adoptedWatch\.adopt\(adoptedGateway\);[\s\S]*followRestartedGateway/, "a gateway is followed before the watch knows it");
  });

  it("sends nothing that carries the home's credentials while they are withheld", () => {
    // The shell's session, the local secret and the capability token all leave through these three
    // doors; each asks first. Withheld from the doorbell's loss of an adopted gateway until a proof on
    // its port, and from the stop of the gateway the app used until a new one is in use.
    assert.match(fnBody("postGateway"), /if \(!localGatewayUrl \|\| credentialsWithheld\(\)\) return resolve\(null\);/);
    assert.match(fnBody("getGateway"), /if \(!localGatewayUrl \|\| credentialsWithheld\(\)\) return resolve\(null\);/);
    assert.match(fnBody("socketGateway"), /if \(credentialsWithheld\(\)\) throw/);
    assert.match(fnBody("credentialsWithheld"), /return gatewayGone \|\| adoptedWatch\.withheld;/);
    assert.match(fnBody("gatewayLost"), /gatewayGone = true;/);
    assert.match(fnBody("adoptedGone"), /gatewayGone = true;/);
    assert.match(fnBody("adoptLocalGateway"), /gatewayGone = false;/);
    const sends = [...MAIN.matchAll(/X-Local-Secret|X-Shell-Token|localSignIn\.authorization\(\)/g)].length;
    assert.equal(sends, 6, "a new door carries the home's credentials: give it the same ask");
  });
});

describe("a start that timed out is the app's own gateway, never another one (main.js's functions, run under stand-ins)", () => {
  const vm = require("node:vm");
  /** The source of main.js's top-level function *name*, as written (`async` included). */
  const fnSource = (name) => {
    const at = MAIN.search(new RegExp(`^(async )?function ${name}\\(`, "m"));
    assert.ok(at >= 0, `${name} is gone — this rail is now blind`);
    return MAIN.slice(at, MAIN.indexOf("\n}\n", at) + 2);
  };
  /** bringUpLocalGateway, ownGatewayLives and useRunningGateway as main.js has them, in a context that
   *  stands in for every name they reach for. *did* records the look, the sign-in, the adoption and
   *  every row state, in order. */
  const run = ({ child = null, start, found = null, autoUse = false }) => {
    const did = [];
    const box = {
      console: { log() {}, warn() {}, error() {} },
      HOME_SERVED_EXIT: 3,
      gatewayProcess: child,
      startFailure: null,
      adoptedGateway: null,
      localState: { state: "", sentence: "" },
      adoptedWatch: { drop() {} },
      sendStatus() {},
      setLocalState: (state, sentence = "") => {
        box.localState = { state, sentence };
        did.push(state);
      },
      startGateway: start,
      notStartedSentence: () => "PersonalClaw's gateway didn't start.",
      foundSentence: () => "This home's gateway is already running, started outside this app.",
      autoUseRunning: () => autoUse,
      findHomeGateway: async () => (did.push("look"), found),
      reachHomeGateway: async () => (did.push("sign-in"), { ok: true, gateway: found && found.gateway, ready: {} }),
      adoptHomeGateway: async () => {
        did.push("adopt");
      },
    };
    vm.createContext(box);
    vm.runInContext(["ownGatewayLives", "useRunningGateway", "bringUpLocalGateway"].map(fnSource).join("\n"), box);
    return { box, did };
  };
  const living = () => ({ pid: 4242, exitCode: null, signalCode: null });
  const timedOut = () => Promise.reject(new Error("Gateway start timed out"));
  const refused = () => Promise.reject(Object.assign(new Error("Gateway exited with code 3"), { exit: { code: 3 } }));
  // What `homeGateway.reach` proves of the app's own late child once it listens: it serves the home.
  const serving = { ok: true, gateway: { pid: 4242, port: 50715 } };

  it("a start that times out, whose child then serves the home, is neither offered as found nor used automatically", async () => {
    for (const autoUse of [false, true]) {
      const { box, did } = run({ child: living(), start: timedOut, found: serving, autoUse });
      assert.equal(await box.bringUpLocalGateway(), false);
      assert.deepEqual(did, ["starting", "stopped"], `with the box ${autoUse ? "ticked" : "clear"}: ${did.join(", ")}`);
      // Made inside the context, so compared as JSON: its prototype is that realm's.
      assert.equal(JSON.stringify(box.startFailure), JSON.stringify({ startError: "Gateway start timed out" }));
      assert.equal(box.localState.sentence, "PersonalClaw's gateway didn't start.");
    }
  });

  it("once its child is gone, the gateway serving the home is looked for, offered, and used on the standing yes", async () => {
    for (const child of [null, { pid: 4242, exitCode: 3, signalCode: null }, { pid: 4242, exitCode: null, signalCode: "SIGTERM" }]) {
      const offered = run({ child, start: refused, found: serving });
      assert.equal(await offered.box.bringUpLocalGateway(), false);
      assert.deepEqual(offered.did, ["starting", "look", "found"], offered.did.join(", "));
      const used = run({ child, start: refused, found: serving, autoUse: true });
      assert.equal(await used.box.bringUpLocalGateway(), true);
      assert.deepEqual(used.did, ["starting", "look", "starting", "sign-in", "adopt"], used.did.join(", "));
    }
  });

  it("refuses Use while the app's own gateway is up or starting, before any sign-in", async () => {
    const { box, did } = run({ child: living(), start: timedOut, found: serving });
    const answer = await box.useRunningGateway();
    assert.equal(answer.ok, false);
    assert.equal(answer.code, "own");
    assert.match(answer.message, /own gateway is running/);
    assert.deepEqual(did, []);
  });

  it("counts a child as living only while it spawned and has not exited", () => {
    const { box } = run({ start: timedOut });
    const lives = (child) => {
      box.gatewayProcess = child;
      return box.ownGatewayLives();
    };
    assert.equal(lives(living()), true);
    assert.equal(lives(null), false);
    assert.equal(lives({ pid: undefined, exitCode: null, signalCode: null }), false, "a spawn that failed has no pid");
    assert.equal(lives({ pid: 4242, exitCode: 0, signalCode: null }), false);
    assert.equal(lives({ pid: 4242, exitCode: null, signalCode: "SIGKILL" }), false);
  });
});

describe("there is no second retry loop racing the SPA", () => {
  it("re-checks reachability with a self-scheduling timeout, not an interval", () => {
    // The companion guide: "Do not add a wrapper-side retry loop." The shell's legitimate job is
    // noticing the machine is unreachable. A `setInterval` would keep firing regardless of outcome,
    // which is what turns that job into the loop the guide forbids.
    const start = MAIN.indexOf("function scheduleReachProbe(");
    assert.ok(start > 0, "scheduleReachProbe not found");
    const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
    assert.match(body, /setTimeout\(/);
    assert.strictEqual(/setInterval\(/.test(body), false, "the reachability probe is on an interval");
    assert.match(body, /nextReconnectStep\(/, "the probe decides its own next step instead of asking the policy");
  });

  it("reloads only when a probe came back reachable", () => {
    const start = MAIN.indexOf("function scheduleReachProbe(");
    const body = MAIN.slice(start, MAIN.indexOf("\n}\n", start));
    const stay = body.indexOf('step.action === "stay"');
    const load = body.indexOf("loadURL(target)");
    assert.ok(stay > 0 && load > stay, "the reload is not gated on a reachable probe");
  });

  it("has no interval in the file: the menu bar's counts ride the doorbell, which polls only while down", () => {
    // The presence poll was the one interval here. The doorbell (`doorbell.js`) reads on each ring
    // while its socket is up and on its own timeouts while it is down, so a quit leaves no interval.
    assert.strictEqual([...MAIN.matchAll(/setInterval\(/g)].length, 0);
    assert.match(MAIN, /makeDoorbell\(/);
  });
});

describe("every `desktop/main.js:N` citation in the repo still resolves", () => {
  // 🪤 THIS RAIL EXISTS BECAUSE THE DRIFT HAPPENED TWICE IN ONE SESSION. Two earlier changes both
  // recorded that they had to fix stale `desktop/main.js:<line>` anchors BY HAND because no test
  // checks line numbers. This change then staled them a third time — first by renaming `backendUrl`,
  // and again by adding code above the cited lines after having just corrected them. A citation
  // that silently stops resolving is the exact failure the contract docs exist to prevent, so it
  // gets a rail instead of a habit.
  const REPO = path.join(__dirname, "..", "..");
  const CITERS = [
    "web/src/lib/endpoints.ts",
    "web/src/lib/endpoints.test.ts",
    "docs/guides/companion-apps.md",
  ];
  /** The lines those files are allowed to cite, identified by content rather than by number. */
  const ANCHORS = [
    "wc.loadURL(localGatewayUrl)",
    "localGatewayUrl = ready.url;",
  ];

  const lines = MAIN.split("\n");
  const anchorLines = ANCHORS.map((needle) => {
    const at = lines.findIndex((l) => l.includes(needle));
    assert.ok(at >= 0, `main.js no longer contains the anchor ${JSON.stringify(needle)}`);
    return at + 1;
  });

  it("finds the anchors it is going to check against", () => {
    assert.strictEqual(anchorLines.length, ANCHORS.length);
    assert.strictEqual(new Set(anchorLines).size, anchorLines.length, "two anchors resolved to one line");
  });

  for (const rel of CITERS) {
    it(`${rel} cites only lines that exist and hold an anchor`, () => {
      const text = fs.readFileSync(path.join(REPO, rel), "utf8");
      const cited = [...text.matchAll(/desktop\/main\.js:(\d+)/g)].map((m) => Number(m[1]));
      assert.ok(cited.length > 0, `${rel} cites no main.js line — did the reference move?`);
      for (const n of cited) {
        assert.ok(n >= 1 && n <= lines.length, `${rel} cites main.js:${n}, which is past the end of the file`);
        assert.ok(
          anchorLines.includes(n),
          `${rel} cites main.js:${n}, which now holds ${JSON.stringify((lines[n - 1] || "").trim().slice(0, 90))}. ` +
            `The anchors are at ${anchorLines.join(", ")}.`
        );
      }
    });
  }
});

describe("the host-moved guard has a live caller", () => {
  it("resolves a current fingerprint before EVERY decision to connect", () => {
    // `confirmationHolds` only compares fingerprints when it is given one, so a call site that
    // omits `currentFingerprint` skips the time-of-check/time-of-use guard in total silence. That
    // is what both call sites did in the first draft of this change, and a source scan is the only
    // instrument that catches it without launching Electron.
    for (const site of ["describeStartup({", "switchTo(shellStore,"]) {
      const at = MAIN.indexOf(site);
      assert.ok(at > 0, `${site} not found in main.js`);
      const window = MAIN.slice(Math.max(0, at - 700), at + 300);
      assert.match(window, /currentFingerprintFor\(/, `${site} decides without resolving the host first`);
      assert.match(MAIN.slice(at, at + 300), /currentFingerprint/, `${site} does not pass the fingerprint it resolved`);
    }
  });

  it("awaits the resolution rather than passing a promise", () => {
    // `currentFingerprintFor` is async; a forgotten `await` yields a Promise, which compares unequal
    // to the recorded string and would demand re-confirmation on every launch.
    assert.strictEqual([...MAIN.matchAll(/currentFingerprintFor\(/g)].length, 2);
    for (const m of MAIN.matchAll(/currentFingerprintFor\(/g)) {
      const before = MAIN.slice(Math.max(0, m.index - 40), m.index);
      assert.match(before, /await\s+$|await\s*$/, "a currentFingerprintFor call is not awaited");
    }
  });
});

describe("the view cannot leave the origin the user confirmed", () => {
  it("guards both `will-navigate` and `will-redirect`", () => {
    // `will-navigate` covers a link or a `location` assignment; `will-redirect` covers a server 3xx,
    // which is the shape the SSRF guidance singles out.
    assert.match(MAIN, /on\("will-navigate", guardNavigation\)/);
    assert.match(MAIN, /on\("will-redirect", guardNavigation\)/);
  });

  it("compares against the ACTIVE origin, so a paired gateway's own links still work", () => {
    const start = MAIN.indexOf("const allowedOrigin = ()");
    assert.ok(start > 0, "allowedOrigin not found");
    const body = MAIN.slice(start, start + 400);
    assert.match(body, /activeUrl \|\| localGatewayUrl/);
  });
});
