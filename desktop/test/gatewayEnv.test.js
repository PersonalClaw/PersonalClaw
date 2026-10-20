const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { AUTH_SWITCHES, INSTALL_KIND, buildGatewayEnv } = require("../gatewayEnv");

/**
 * The gateway spawn environment.
 *
 * The variable these tests exist for is `PERSONALCLAW_INSTALL_KIND`. It was missing for the whole
 * life of the shipped Linux desktop artifact (issue #2673): the gateway inside the AppImage/.deb
 * fell through `detect_install_kind()` to `"pip"`, and the Updates panel offered an in-app apply
 * that runs an installer against a frozen PyInstaller binary. Every assertion below is EXECUTED
 * against the real builder — the omission was invisible precisely because the env was an object
 * literal buried in `startGateway`, where only a regex over `main.js` could see it.
 */
describe("buildGatewayEnv", () => {
  const base = {
    PATH: "/usr/bin:/bin",
    HOME: "/Users/someone",
    PERSONALCLAW_PORT: "8765",
  };
  const built = () =>
    buildGatewayEnv({ env: base, loginPath: "/opt/homebrew/bin:/usr/bin", projectDir: "/app/resources" });

  it("declares the install kind, so the gateway never has to guess", () => {
    // 🔴 THE REGRESSION THIS FILE EXISTS FOR. Without this key a packaged install classifies as
    // `pip` (its project dir is inside the bundle and carries no `.git`) and the Updates panel
    // offers `install -U personalclaw` against the frozen backend binary.
    assert.strictEqual(built().PERSONALCLAW_INSTALL_KIND, "desktop");
    assert.strictEqual(INSTALL_KIND, "desktop");
  });

  it("overrides an INHERITED install kind rather than deferring to it", () => {
    // A stray marker in the user's shell profile must not make a desktop install describe
    // itself as something else — the explicit keys are spread after the inherited env.
    const env = buildGatewayEnv({
      env: { ...base, PERSONALCLAW_INSTALL_KIND: "container" },
      loginPath: "/usr/bin",
      projectDir: "/app/resources",
    });
    assert.strictEqual(env.PERSONALCLAW_INSTALL_KIND, "desktop");
  });

  it("drops an inherited PERSONALCLAW_PORT so `--port auto` is honored", () => {
    assert.ok(!("PERSONALCLAW_PORT" in built()), "PERSONALCLAW_PORT must not reach the child");
  });

  it("replaces PATH with the resolved login-shell PATH and keeps the rest of the env", () => {
    const env = built();
    assert.strictEqual(env.PATH, "/opt/homebrew/bin:/usr/bin");
    assert.strictEqual(env.HOME, "/Users/someone");
  });

  it("sets the project dir it was given", () => {
    assert.strictEqual(built().PERSONALCLAW_PROJECT_DIR, "/app/resources");
  });

  it("sets no authentication switch, so the gateway asks every request for a sign-in", () => {
    // The shell signs its windows in with the session the ready line carries (localSignIn.js).
    // Any key here that changed how the gateway admits a request would hand the app's gateway,
    // and every approval waiting on it, to any process on the computer.
    const added = Object.keys(built()).filter((key) => !(key in base));
    assert.deepStrictEqual(added.sort(), ["PERSONALCLAW_INSTALL_KIND", "PERSONALCLAW_PROJECT_DIR"]);
  });

  it("passes on no inherited switch that would weaken the sign-in or leave loopback", () => {
    // `npm start` from a terminal that exports one of these must still start a local-token
    // gateway on loopback: none-mode admits every caller as the owner, the local-network bypass
    // admits any loopback caller with no token, and a bind host puts the gateway on the network.
    const env = buildGatewayEnv({
      env: {
        ...base,
        PERSONALCLAW_AUTH_MODE: "none",
        PERSONALCLAW_BYPASS_LOCAL_NETWORKS: "1",
        PERSONALCLAW_BIND_HOST: "0.0.0.0",
      },
      loginPath: "/usr/bin",
      projectDir: "/app/resources",
    });
    for (const key of AUTH_SWITCHES) assert.ok(!(key in env), `${key} reached the gateway`);
    assert.deepStrictEqual(
      [...AUTH_SWITCHES].sort(),
      ["PERSONALCLAW_AUTH_MODE", "PERSONALCLAW_BIND_HOST", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"],
    );
    assert.strictEqual(env.HOME, "/Users/someone", "the rest of the environment still reaches it");
  });

  it("never mutates the environment it was handed", () => {
    // `process.env` is the real caller. Mutating it would change THIS process's environment as a
    // side effect of describing a child's.
    const snapshot = { ...base };
    built();
    assert.deepStrictEqual(base, snapshot);
  });

  it("has no Electron import, so it is testable without a display", () => {
    const src = require("node:fs").readFileSync(require.resolve("../gatewayEnv.js"), "utf8");
    assert.ok(!/require\("electron"\)/.test(src), "gatewayEnv.js must stay a pure module");
  });
});
