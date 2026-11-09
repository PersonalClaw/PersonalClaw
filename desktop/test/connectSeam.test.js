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
    // A check that one is held (`.Authorization` read alone) sends nothing.
    const sites = [...MAIN.matchAll(/localSignIn\.authorization\(\)(?!\.Authorization)/g)];
    assert.strictEqual(sites.length, 3, `expected three sites, found ${sites.length}`);
    for (const m of sites) {
      const before = MAIN.slice(0, m.index);
      const opener = Math.max(before.lastIndexOf("function postGateway("), before.lastIndexOf("function getGateway("), before.lastIndexOf("function socketGateway("));
      const closer = MAIN.indexOf("\n}\n", opener);
      assert.ok(opener > 0 && m.index < closer, "the sign-in is sent outside the three loopback helpers");
      assert.match(MAIN.slice(opener, closer), /assertLoopbackTarget\(localGatewayUrl/, "a helper that sends the sign-in re-asserts loopback");
    }
    // The held session's one read before a found gateway's same-start lift goes to 127.0.0.1 alone.
    const held = [...MAIN.matchAll(/localSignIn\.heldAuthorization\(\)/g)];
    assert.equal(held.length, 1, "the held session is read for more than the one read before a lift");
    const read = MAIN.slice(MAIN.indexOf("function readHeldSessionAt("), MAIN.indexOf("\n}\n", MAIN.indexOf("function readHeldSessionAt(")));
    assert.ok(held[0].index > MAIN.indexOf("function readHeldSessionAt("), "the held session is read outside readHeldSessionAt");
    assert.match(read, /hostname: "127\.0\.0\.1"/);
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

describe("the app runs in the home its gateway resolves", () => {
  it("takes its home from appHome, never as the environment wrote it", () => {
    // The seam injects PERSONALCLAW_HOME as a constant, so no run under stand-ins can see this read.
    assert.match(MAIN, /^const PERSONALCLAW_HOME = appHome\(process\.env\)\.home;$/m, "main reads its home as written");
    assert.doesNotMatch(MAIN, /process\.env\.PERSONALCLAW_HOME\b/, "main reads PERSONALCLAW_HOME itself, past appHome");
    assert.match(MAIN, /const \{[^}]*\bappHome\b[^}]*\} = require\("\.\/gatewayEnv"\);/, "appHome is not gatewayEnv's");
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
    assert.match(body, /if \(settled\) \{\s*takeReadyLine\(child, ready, true\)/, "a later ready line is dropped");
    // Only from the child that is still the app's gateway: the handler asks before it hands a line on,
    // and takeReadyLine asks first.
    assert.match(body, /if \(!ready\) continue;\s*(\/\/[^\n]*\n\s*)*if \(readyLineIsStale\(child, ready\)\) continue;\s*if \(settled\)/, "the handler takes a line from a child that is no longer the gateway");
    assert.match(MAIN, /function takeReadyLine\(child, ready, settled\) \{\s*if \(readyLineIsStale\(child, ready\)\) return Promise\.resolve\(\);/, "takeReadyLine takes a line from a child that is no longer the gateway");
    assert.match(MAIN, /function readyLineIsStale\(child, ready\) \{\s*if \(gatewayProcess && gatewayProcess\.pid === child\.pid\) return false;/);
    assert.match(body, /takeReadyLine\(child, ready, false\)/, "the first ready line signs nothing in");
    const take = MAIN.slice(MAIN.indexOf("function takeReadyLine("), MAIN.indexOf("\n}\n", MAIN.indexOf("function takeReadyLine(")));
    assert.match(take, /gatewayWatch\.own\(start\);[\s\S]*followRestartedGateway\(ready, \{ start \}\) : adoptLocalGateway\(ready, \{ start \}\)/);
    assert.equal([...MAIN.matchAll(/gatewayWatch\.own\(start\)/g)].length, 2, "the own gateway's watch is armed outside the ready line and Sign In Again");
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
    // A gateway this app may not use is said on the row, and neither offered nor used.
    // A start proven there first ends what an Update the Gateway press kept for it.
    assert.match(up, /if \(found\.ok\) \{\s*await signOutUpdateSessions\(\);\s*\/\/[^\n]*\n\s*const verdict = mayUseFoundGateway\(found\.gateway\);\s*if \(!verdict\.ok\) \{\s*setBehind\(found\.gateway, verdict, \{ show: false \}\);\s*return false;\s*\}\s*\/\/[^\n]*\n\s*if \(autoUseRunning\(\)\) return \(await useRunningGateway\(\)\)\.ok;\s*startFailure = \{ \.\.\.exit, found: true \};\s*setLocalState\("found", foundSentence\(found\.gateway\)\);\s*return false;/);
    assert.match(fnBody("autoUseRunning"), /shellStore\.getItem\(AUTO_USE_KEY\) === "1"/, "the standing yes is on unless the owner ticked it");
    // Signing in happens in six places, each after a proof: reachHomeGateway (a press, the standing
    // yes), the watch's follow of a gateway in use that moved, its take-back of a gateway that
    // started again where it was, the owner's Sign In Again, the window's door of the app's own, and
    // Update the Gateway's own short sessions, which no window ever holds (`updateSession`).
    const signIns = [...MAIN.matchAll(/homeGateway\.signIn\(/g)];
    const inside = (fn, at) => at > MAIN.indexOf(`async function ${fn}(`) && at < MAIN.indexOf("\n}\n", MAIN.indexOf(`async function ${fn}(`));
    const places = ["reachHomeGateway", "followMovedGateway", "takeBackAdoptedGateway", "signInAgain", "askWindowDoor", "updateSession"];
    assert.equal(signIns.length, places.length);
    for (const m of signIns) assert.ok(places.some((fn) => inside(fn, m.index)), "a sign-in outside the six places");
    const callers = [...MAIN.matchAll(/await reachHomeGateway\(/g)];
    assert.equal(callers.length, 1, "reachHomeGateway gained a caller");
    assert.match(fnBody("useRunningGateway"), /const found = await reachHomeGateway\(\);/);
    assert.match(MAIN.slice(MAIN.indexOf("const gatewayWatch = makeGatewayWatch({"), MAIN.indexOf("gone: (was) => adoptedGone(was),")), /follow: \(\) => followMovedGateway\(\),/);
    // The follow asks a moved start's door within that start's asks, as the take-back does.
    assert.match(fnBody("followMovedGateway"), /if \(!spendDoorAsk\(start\)\) return false;\s*const gen = localSignIn\.gen;\s*const signed = await homeGateway\.signIn\(found\.gateway, PERSONALCLAW_HOME\);/);
  });

  it("sends a page's own pair on every fetch main runs in a page, as the page itself would", () => {
    const { PAGE_OWN_PAIR_HEADERS, PAIR_HEADER } = require("../windowTraffic");
    const vm = require("node:vm");
    const runs = [...MAIN.matchAll(/executeJavaScript\(\s*`([\s\S]*?)`/g)].map((m) => m[1]).filter((script) => /\bfetch\(/.test(script));
    assert.ok(runs.length >= 2, "the injected fetches are gone: this scan is blind");
    const pagePair = "C".repeat(43);
    for (const raw of runs) {
      assert.match(raw, /\$\{PAGE_OWN_PAIR_HEADERS\}/, raw.slice(0, 80));
      const sent = [];
      const page = {
        window: { localStorage: { getItem: (key) => (key === "pc_pair" ? pagePair : null) } },
        fetch: (url, init) => (sent.push({ url, headers: { ...(init && init.headers) } }), Promise.resolve({ ok: false, json: async () => null })),
      };
      vm.runInNewContext(raw.replace(/\$\{PAGE_OWN_PAIR_HEADERS\}/g, PAGE_OWN_PAIR_HEADERS), page);
      assert.equal(sent.length, 1, raw.slice(0, 80));
      assert.equal(sent[0].headers[PAIR_HEADER], pagePair, `${sent[0].url} went without the page's own pair`);
    }
    assert.match(fnBody("noteSessionEvent"), /\$\{PAGE_OWN_PAIR_HEADERS\}/);
    assert.match(fnBody("adoptInstanceName"), /\$\{PAGE_OWN_PAIR_HEADERS\}/);
  });

  it("uses a running gateway only while its own is neither up nor starting, on a press and the standing yes alike, and keeps the box's answer", () => {
    // The refusal is useRunningGateway's first step, before any sign-in, so the press (through
    // useRunningHere) and the standing yes both meet it.
    assert.match(fnBody("useRunningGateway"), /^function useRunningGateway\(\) \{\n\s*if \(ownGatewayLives\(\)\) \{\n(\s*\/\/[^\n]*\n)*\s*setLocalState\([^\n]*\);\n\s*return \{ ok: false, code: "own"/, "its own gateway can be adopted as one it did not start");
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
    assert.match(adopt, /await followRestartedGateway\(found\.ready, \{ pair: found\.pair, start \}\)/);
    assert.match(adopt, /await adoptLocalGateway\(found\.ready, \{ pair: found\.pair, start \}\)/);
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

  it("follows the gateway in use through the doorbell, and registers again only once it is proven", () => {
    // `gatewayWatch.js` decides; main.js routes the doorbell's news to it and does what it says.
    const at = MAIN.indexOf("const gatewayWatch = makeGatewayWatch({");
    assert.ok(at > 0, "the gateway watch is gone — this rail is now blind");
    const made = MAIN.slice(at, MAIN.indexOf("\n});\n", at));
    assert.match(made, /reach: \(home\) => homeGateway\.reach\(home\)/);
    assert.match(made, /proven: \(was, gateway\) => \(was\.kind === "own" \? localSignIn\.lift\(lostAt, \{ start: startOf\(gateway\) \}\) : takeBackAdoptedGateway\(was, gateway\)\)/);
    assert.match(made, /back: \(was\) => resumeGateway\(was\)/);
    assert.match(made, /gone: \(was\) => adoptedGone\(was\)/);
    // The registration and the doorbell come only once the windows' sign-in is back.
    // The doorbell first, right after the lift; then the kept sign-outs and the registration.
    assert.match(fnBody("resumeGateway"), /shellToken = null;[\s\S]*startPresenceRefresh\(\);\s*await signOutKept\(\);\s*await registerWithGateway\(\);/, "a restart's new registry is not registered with");
    // The app's start and a first gateway ring before they register, as every other sign-in does.
    assert.match(fnBody("setUpFirstGateway"), /startPresenceRefresh\(\);\s*await registerWithGateway\(\);/);
    assert.match(MAIN, /await bringUpLocalGateway\(\);[\s\S]{0,200}?\n\s*startPresenceRefresh\(\);[\s\S]{0,300}?\n\s*await registerWithGateway\(\);/);
    assert.match(MAIN, /if \(state === "up"\) \{[\s\S]{0,160}gatewayWatch\.up\(\);[\s\S]{0,80}\} else \{[\s\S]{0,80}loseGateway\(\);/);
    assert.doesNotMatch(MAIN, /function watchAdoptedGateway\(/, "a second follower of the doorbell's news");
    assert.match(fnBody("adoptHomeGateway"), /gatewayWatch\.adopt\([\s\S]*followRestartedGateway/, "a gateway is followed before the watch knows it");
  });

  it("sends nothing that carries the home's credentials while they are withheld", () => {
    // The shell's session, the local secret and the capability token all leave through these three
    // doors; each asks first. Withheld from the doorbell's loss of an adopted gateway until a proof on
    // its port, and from the stop of the gateway the app used until a new one is in use.
    // A sign-out (signedIn: false) waits only for a proven gateway; anything signed in waits for the
    // shell's own sign-in too, and for a credential to carry.
    assert.match(fnBody("postGateway"), /if \(!localGatewayUrl \|\| gatewayGone \|\| gatewayWatch\.withheld\) return resolve\(null\);\s*if \(signedIn && \(credentialsWithheld\(\) \|\| !localSignIn\.authorization\(\)\.Authorization\)\) return resolve\(null\);/);
    assert.match(fnBody("getGateway"), /if \(!localGatewayUrl \|\| credentialsWithheld\(\) \|\| !localSignIn\.authorization\(\)\.Authorization\) return resolve\(null\);/);
    assert.match(fnBody("socketGateway"), /if \(credentialsWithheld\(\)\) throw/);
    assert.match(fnBody("credentialsWithheld"), /return gatewayGone \|\| gatewayWatch\.withheld \|\| Boolean\(localSignIn && localSignIn\.signedOut\);/);
    assert.match(fnBody("gatewayLost"), /gatewayGone = true;/);
    assert.match(fnBody("adoptedGone"), /gatewayGone = true;/);
    assert.match(fnBody("adoptLocalGateway"), /gatewayGone = false;/);
    // A check that a credential is held (`.authorization().Authorization`) carries none.
    const sends = [...MAIN.matchAll(/X-Local-Secret|X-Shell-Token|localSignIn\.authorization\(\)(?!\.Authorization)|localSignIn\.heldAuthorization\(\)/g)].length;
    assert.equal(sends, 7, "a new door carries the home's credentials: give it the same ask");
  });

  it("opens the releases page for a home a newer PersonalClaw served at its one fixed address, with nothing the dialog sent", () => {
    assert.match(MAIN, /releases: \(\) => systemBrowser\.open\(RELEASES_PAGE_URL\),/);
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
      HOME_NEWER_EXIT: 4,
      gatewayProcess: child,
      startFailure: null,
      adoptedGateway: null,
      localState: { state: "", sentence: "" },
      gatewayWatch: { drop() {} },
      localGatewayUrl: null,
      // What a press of Update the Gateway kept: none here.
      signOutUpdateSessions: async () => {},
      sendStatus() {},
      setOlder: () => did.push("older"),
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
        return true;
      },
      // Whether a gateway it found may be used: a test's own verdict, else yes.
      mayUseFoundGateway: (gateway) => gateway.verdict || { ok: true },
      setBehind: (_gateway, verdict) => {
        box.localState = { state: verdict.state, sentence: verdict.sentence };
        did.push(verdict.state);
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

  it("neither offers nor uses a gateway it found that it may not use, on the standing yes either", async () => {
    const older = { ok: true, gateway: { pid: 4242, port: 50715, verdict: { ok: false, state: "behind", sentence: "older than this app" } } };
    for (const autoUse of [false, true]) {
      const { box, did } = run({ start: refused, found: older, autoUse });
      assert.equal(await box.bringUpLocalGateway(), false);
      assert.deepEqual(did, ["starting", "look", "behind"], `with the box ${autoUse ? "ticked" : "clear"}: ${did.join(", ")}`);
      assert.equal(box.localState.sentence, "older than this app");
    }
  });

  it("refuses Use while the app's own gateway is up or starting, before any sign-in", async () => {
    const { box, did } = run({ child: living(), start: timedOut, found: serving });
    const answer = await box.useRunningGateway();
    assert.equal(answer.ok, false);
    assert.equal(answer.code, "own");
    assert.match(answer.message, /own gateway is running/);
    // No look and no sign-in: the row says its own gateway, still starting.
    assert.deepEqual(did, ["starting"]);
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

  it("drops the last start's watch before a start, and registers the windows' traffic once, before the first window", () => {
    const up = MAIN.slice(MAIN.indexOf("async function bringUpLocalGateway("), MAIN.indexOf("\n}\n", MAIN.indexOf("async function bringUpLocalGateway(")));
    assert.match(up, /^async function bringUpLocalGateway\(\) \{\s*(\/\/[^\n]*\n\s*)*gatewayWatch\.drop\(\);/, "a start runs with the last start's watch armed");
    // Every webRequest listener is registered inside watchWindowTraffic, which whenReady calls once,
    // before the first window; the stale localhost cookies are gone by then too.
    const watch = MAIN.slice(MAIN.indexOf("function watchWindowTraffic("), MAIN.indexOf("\n}\n", MAIN.indexOf("function watchWindowTraffic(")));
    for (const m of MAIN.matchAll(/webRequest\.on[A-Z]\w*\(/g)) {
      assert.ok(m.index > MAIN.indexOf("function watchWindowTraffic(") && m.index < MAIN.indexOf("function watchWindowTraffic(") + watch.length, "a webRequest listener outside watchWindowTraffic");
    }
    // Across the file: watchWindowTraffic is defined once and called once, and registerWindowTraffic
    // is called once, inside it. No window, view or other session registers its own.
    assert.equal([...MAIN.matchAll(/watchWindowTraffic\(/g)].length, 2, "the windows' traffic is registered more than once");
    const registers = [...MAIN.matchAll(/registerWindowTraffic\(/g)];
    assert.equal(registers.length, 1, "the windows' traffic is registered outside watchWindowTraffic");
    const watchAt = MAIN.indexOf("function watchWindowTraffic(");
    assert.ok(registers[0].index > watchAt && registers[0].index < watchAt + watch.length, "the windows' traffic is registered outside watchWindowTraffic");
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then("));
    assert.equal([...ready.matchAll(/watchWindowTraffic\(/g)].length, 1);
    assert.ok(ready.indexOf("watchWindowTraffic(") < ready.indexOf("createWindow()"), "a window loads before its traffic is decided");
    const purge = ready.indexOf("await purgeStaleLocalCookies(session.defaultSession);");
    assert.ok(purge > ready.indexOf("syncPaired();") && purge < ready.indexOf("createWindow()"), "the first window loads before the stale cookies are gone");
  });

  it("has no interval in the file: the menu bar's counts ride the doorbell, which polls only while down", () => {
    // The presence poll was the one interval here. The doorbell (`doorbell.js`) reads on each ring
    // while its socket is up and on its own timeouts while it is down, so a quit leaves no interval.
    assert.strictEqual([...MAIN.matchAll(/setInterval\(/g)].length, 0);
    assert.match(MAIN, /makeDoorbell\(/);
    // The runtime record's read while the doorbell is up is a timeout its stop clears.
    const stop = MAIN.slice(MAIN.indexOf("function stopPresenceRefresh("), MAIN.indexOf("\n}\n", MAIN.indexOf("function stopPresenceRefresh(")));
    assert.match(stop, /stopRecordWatch\(\);\s*doorbell\.stop\(\);/);
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

describe("Update the Gateway sends only its own requests, over sessions of its own", () => {
  const body = (name) => {
    const start = MAIN.search(new RegExp(`^(async )?function ${name}\\(`, "m"));
    assert.ok(start >= 0, `${name} is gone — this rail is now blind`);
    return MAIN.slice(start, MAIN.indexOf("\n}\n", start));
  };
  const DESKTOP = path.join(__dirname, "..");
  const sources = fs.readdirSync(DESKTOP).filter((f) => f.endsWith(".js"));

  it("asks a gateway to update from one place, the engine, which only the press reaches", () => {
    const asking = sources.filter((f) => /"POST", "\/api\/update"[,)]/.test(fs.readFileSync(path.join(DESKTOP, f), "utf8")));
    assert.deepEqual(asking, ["gatewayFirst.js"]);
    assert.equal([...MAIN.matchAll(/runGatewayUpdate\(/g)].length, 1, "the engine gained a caller");
    assert.match(body("pressUpdateTheGateway"), /const out = await runGatewayUpdate\(\{/);
    assert.doesNotMatch(fs.readFileSync(path.join(DESKTOP, "appUpdate.js"), "utf8"), /gatewayFirst|\/api\/update"/, "the updater's own check reaches the gateway's update");
  });

  it("sends one of six requests, each right after a proof of its start, as Node http to 127.0.0.1 with no User-Agent and no cookie store", () => {
    const { ALLOWED_REQUESTS } = require("../gatewayFirst");
    assert.deepEqual([...ALLOWED_REQUESTS], [
      "GET /api/update/check",
      "POST /api/update/check",
      "POST /api/system/restart?probe=1",
      "POST /api/update",
      "GET /api/status",
      "POST /api/auth/logout",
    ]);
    const ask = body("askOver");
    const allowed = ask.indexOf("ALLOWED_REQUESTS.includes(`${method} ${route}`)");
    const proven = ask.indexOf("await homeGateway.reach(PERSONALCLAW_HOME)");
    const sent = ask.indexOf("sendToFoundGateway(");
    assert.ok(allowed > 0 && proven > allowed && sent > proven, "a request goes before it is allowed or proven");
    const send = body("sendToFoundGateway");
    assert.match(send, /http\.request\(\{ host: "127\.0\.0\.1", port: held\.port, path: route, method, agent: false/);
    assert.match(send, /Authorization: `Bearer \$\{held\.session\.token\}`/);
    // Headers only: no body is written, POST /api/update's included.
    assert.doesNotMatch(send, /User-Agent|Cookie|cookies\.set|net\.request|loadURL|token=|\.write\(/i);
    // An answer the press's session gets refused to is the press's hold, never this app's sign-in ending.
    for (const fn of ["updateFoundGatewayNow", "pressUpdateTheGateway", "askOver", "sendToFoundGateway", "holdUpdate"]) {
      assert.doesNotMatch(body(fn), /noteShellRefusal/, `${fn} ends this app's sign-in on the press's refusal`);
    }
  });

  it("mints its two sessions for minutes at the door, never its thirty days, and never adopts them", () => {
    assert.match(MAIN, /const PLAN_SESSION_TTL = "5m";/);
    assert.match(MAIN, /const UPDATE_SESSION_TTL = "30m";/);
    const minted = body("updateSession");
    assert.match(minted, /homeGateway\.signIn\(gateway, PERSONALCLAW_HOME, \{ ttl \}\)/);
    assert.doesNotMatch(minted, /localSignIn|adopt|cookies|webContents|send\(|console\.\w+\([^)]*token/);
    const press = body("pressUpdateTheGateway");
    // No sign-in before the version is compared, and the plan's session ends before the box opens.
    assert.ok(press.indexOf("mayUseFoundGateway(gateway)") < press.indexOf("updateSession(gateway, PLAN_SESSION_TTL, press)"));
    assert.ok(press.indexOf("await endUpdateSession(first);") < press.indexOf("await confirmGatewayUpdate("));
    assert.match(body("confirmGatewayUpdate"), /require\("electron"\)\.dialog\.showMessageBox\(\{ type: "question", buttons: \["Update the Gateway", "Cancel"\]/);
  });

  it("ends a press's session by its door's credential and counts it ended only when revoked, and asks at quit", () => {
    const out = body("signOutAtPort");
    assert.match(out, /const \{ path: route, headers \} = signOutRequest\(session, port, \{ windowDoor \}\);/);
    assert.match(out, /done\(revoked \|\| res\.statusCode === 401\);/);
    // Each transport settles on an answer's end, its close or its error, and on a timeout.
    for (const fn of ["signOutAtPort", "sendToFoundGateway"]) {
      const t = body(fn);
      for (const event of ["end", "close", "error"]) assert.match(t, new RegExp(`res\\.on\\("${event}", finish\\)`), `${fn} doesn't settle on ${event}`);
      assert.match(t, /req\.on\("timeout", \(\) => \{\s*req\.destroy\(\);\s*done\(/, `${fn} doesn't settle on a timeout`);
    }
    const quit = MAIN.slice(MAIN.indexOf('app.on("before-quit"'), MAIN.indexOf("stop: (budget) => stopGateway(budget)"));
    assert.match(quit, /await signOutUpdateSessions\(ms, \{ all: true \}\);/);
    // Its own ask, with its own share of the quit's budget, not the tail of the windows' sign-out.
    assert.match(quit, /async \(ms\) => \{\s*try \{\s*await signOutUpdateSessions\(ms, \{ all: true \}\);/);
    // A sign-out gets what the proof before it left of that budget.
    const end = body("endUpdateSession");
    assert.ok(end.indexOf("const left = deadline ? deadline - Date.now() : undefined;") > end.indexOf("await homeGateway.reach("), "the budget isn't read again after the proof");
    assert.match(end, /signOutAtPort\(held\.session, held\.port, \{ windowDoor: [^}]*, timeoutMs: left \}\)/);
  });

  it("tries a press's kept sessions around a site's proof, never between it and what that proof is for", () => {
    for (const fn of ["reachHomeGateway", "followMovedGateway"]) {
      const b = body(fn);
      assert.ok(b.indexOf("await signOutUpdateSessions();") >= 0 && b.indexOf("await signOutUpdateSessions();") < b.indexOf("await findHomeGateway("), `${fn} tries them after its own proof`);
    }
    const take = body("takeBackAdoptedGateway");
    assert.match(take, /\} finally \{\s*await signOutUpdateSessions\(2000\);\s*\}\s*$/, "the take-back tries them before its own step is done, or for longer than the press's own 2 s");
    assert.equal((take.match(/signOutUpdateSessions\(/g) || []).length, 1);
  });
});
