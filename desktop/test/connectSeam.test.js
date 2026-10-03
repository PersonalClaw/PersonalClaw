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

  it("sends the shell's sign-in only through `postGateway` and `getGateway`", () => {
    // The session the ready line hands out is the owner's. Read from one place (`localSignIn`),
    // and put on a request by the two helpers that target the spawned gateway and nothing else.
    const sites = [...MAIN.matchAll(/localSignIn\.authorization\(\)/g)];
    assert.strictEqual(sites.length, 2, `expected two sites, found ${sites.length}`);
    for (const m of sites) {
      const before = MAIN.slice(0, m.index);
      const opener = Math.max(before.lastIndexOf("function postGateway("), before.lastIndexOf("function getGateway("));
      const closer = MAIN.indexOf("\n}\n", opener);
      assert.ok(opener > 0 && m.index < closer, "the sign-in is sent outside the two loopback helpers");
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

  it("has exactly one interval in the file, and it is the pre-existing presence poll", () => {
    const intervals = [...MAIN.matchAll(/setInterval\(/g)];
    assert.strictEqual(intervals.length, 1);
    const before = MAIN.slice(Math.max(0, intervals[0].index - 200), intervals[0].index);
    assert.match(before, /presenceTimer/);
  });
});

describe("every `desktop/main.js:N` citation in the repo still resolves", () => {
  // 🪤 THIS RAIL EXISTS BECAUSE THE DRIFT HAPPENED TWICE IN ONE SESSION. `CA-6` and `CA-7` both
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
