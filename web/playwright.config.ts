import { defineConfig, devices } from '@playwright/test'

// ── Playwright visual-regression + a11y harness ────────────────────────────
// The S2/S3 SAFETY RAIL for the design-system consistency plan: every
// consistency fix must show ZERO unintended visual diff against a captured
// baseline. Mirrors the personalclaw.dev pattern — @playwright/test +
// toHaveScreenshot with platform-qualified baselines (the -<platform> suffix
// keeps CI/mac/linux baselines separate so font/AA rendering diffs don't
// cause false failures).
//
// Baselines live in e2e/__screenshots__/ (committed). Regenerate a touched
// surface's baseline INTENTIONALLY with `npm run e2e:update` and record the
// change in the plan's Execution log for owner review — never silently keep
// or revert a real visual change.
//
// The servers: this harness drives the built SPA served by vite preview, which
// proxies /api to a gateway the harness STARTS ITSELF — isolated, onboarded and
// token-authenticated. Without a gateway the SPA cannot resolve identity, so it
// renders the ONBOARDING screen for every route: no NavRail, no shell, no ⌘K
// listener. axe then reports a clean tree for 96 surfaces it never actually
// visited, and the one test that noticed (`command palette [opened]`) failed on
// its mounted-ness floor while naming the palette instead of the missing backend.

const PORT = Number(process.env.PW_PORT || 4318)
const BASE_URL = process.env.PW_BASE_URL || `http://localhost:${PORT}`

// The harness's OWN gateway — never the real one on 10000. Fixed (not auto) so
// the preview proxy target is known before the gateway prints its READY line; if
// something else squats it, the gateway fails to bind and auth.setup's
// shell-mounted assertion fails loudly rather than scanning an empty app.
const GATEWAY_PORT = Number(process.env.PW_GATEWAY_PORT || 10437)

// Where auth.setup.ts writes the authenticated cookie jar. Gitignored (it holds
// a live owner token for the throwaway gateway).
const STORAGE_STATE = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

// ── The scripted provider contract — ONE place to reconcile ─────────────────
// Read off the committed modules, not guessed: `src/personalclaw/llm/scripted.py`
// owns SCRIPT_ENV_VAR / HOME_ENV_VAR and the version-1 script schema;
// `src/personalclaw/llm/registry.py` owns SCRIPTED_PROVIDER_TYPE and the env
// opt-in that REGISTERS the type. If a name drifts, fix THIS object — nothing
// else in the harness spells any of them out.
//
// `ScriptedProvider` is deterministic, makes ZERO network calls and needs NO
// credential. TWO locks, and both must hold or it refuses loudly:
//   · its script env var must name a readable script JSON file; and
//   · PERSONALCLAW_HOME must be set and must NOT resolve to the real
//     ~/.personalclaw — the provider refuses in a real home outright, re-checking
//     at both __init__ and start(). The gateway boot below already satisfies this
//     with its $TMPDIR home. Do not drop it.
//
// ONE env var, not two. `registry.py` gates REGISTRATION of the `scripted` type and
// `scripted.py` gates CONSTRUCTION of the provider, and both now read
// PERSONALCLAW_SCRIPTED_MODEL_SCRIPT — so the pair can never be half-enabled. The two
// modules briefly disagreed (the registry spelled it PERSONALCLAW_SCRIPTED_LLM while
// claiming it was "the SAME variable ScriptedProvider itself requires"), which is why
// `tests/test_scripted_provider_binding.py` now asserts the two constants are equal:
// nothing else could catch it, because `ignore_missing_imports` keeps mypy silent about
// a sibling module that does not exist yet.
//
// Script schema (version 1, validated STRICTLY — unknown keys at any level are an
// error, so a typo in the fixture fails the boot instead of scripting nothing):
//   { "version": 1, "on_exhausted": "repeat_last",
//     "turns": [ { "text": "…", "stop_reason": "end_turn", "usage": {…} } ] }
// The Nth prompt maps to the Nth turn. The fixture keeps `repeat_last` — the
// default, written out rather than left implicit — and NOT the stricter `error`,
// because one user turn drives more than one model call. Measured, not assumed: a
// green run logs `WARNING personalclaw.suggestions: Failed to parse suggestions
// response: SCRIPTED-E2E-OK…`, i.e. the follow-up-chips pass consumed a scripted
// reply of its own. Under `error` that second call would red this spec for a reason
// that is not its clause. `expect_prompt` is omitted for the same reason: those
// extra calls carry their own prompts, and their order against the user's turn is
// not deterministic.
export const SCRIPTED = {
  /** The provider type `llm/registry.py` registers (SCRIPTED_PROVIDER_TYPE). */
  type: 'scripted',
  /** The ONE opt-in: `llm/scripted.py` SCRIPT_ENV_VAR == `llm/registry.py`
   *  SCRIPTED_PROVIDER_ENV. Its value IS the script path, so enabling the fixture and
   *  saying what it will reply are one act — there is no "on" state with a default. */
  scriptEnvVar: 'PERSONALCLAW_SCRIPTED_MODEL_SCRIPT',
  /** The script fixture, relative to `web/` (playwright's cwd for webServer). */
  scriptPath: 'e2e/fixtures/scripted-chat.json',
  /** The reply the fixture scripts. chat.spec.ts asserts the fixture still
   *  contains this exact string, so config↔fixture drift reds loudly. */
  reply: 'SCRIPTED-E2E-OK: this reply came from the offline scripted provider.',
} as const

// An ISOLATED, ONBOARDED gateway, with AUTH LEFT ON.
//  - PERSONALCLAW_HOME under the OS temp dir, wiped per run. Never ~/.personalclaw.
//  - PERSONALCLAW_WORKSPACE too: PERSONALCLAW_HOME does NOT confine workspace_dir,
//    which otherwise falls back to the real ~/workplace/personalclaw-workspace.
//  - `dashboard.user_name` pre-seeded into config.json, because `onboarded` is
//    DERIVED from a non-empty SERVER-side name (web/src/app/identity.tsx). Seeding
//    the file skips the onboarding hijack without a PUT — so no CSRF/origin dance,
//    and nothing here weakens a security control. "Keyur" matches the committed
//    visual baselines' greeting.
//  - NOT PERSONALCLAW_AUTH_MODE=none: that swaps csrf_middleware for
//    _dev_user_middleware, so any a11y/CSRF-adjacent conclusion drawn under it
//    would not describe a real user. The token flow below is the real one.
//  - A MODEL, and still no credential and still no network: the two scripted-provider
//    env vars below. config.json stays EXACTLY as it was — a user name and nothing
//    else — on purpose: `registry.py`'s `register_scripted_provider_type()`
//    synthesizes its own entry ("Scripted"/scripted-1, credential=None) and registers
//    it BEFORE the config-derived ones so it wins the implicit
//    first-entry-declaring-the-capability fallback. So there is no `providers[]` entry
//    and no `active_models.json` pin to write here, and adding either would put a
//    second, competing entry of the same type in the registry. Nothing is weakened:
//    the home is still under $TMPDIR, still wiped per run, auth is still on, and
//    readiness is still the READY line.
const GATEWAY_COMMAND = [
  'D="${TMPDIR:-/tmp}"; D="${D%/}/personalclaw-e2e-home"',
  'rm -rf "$D"; mkdir -p "$D/workspace"',
  // Fail the BOOT, loudly, if the script fixture is missing. The provider does
  // raise ScriptedScriptError on an unreadable path, but that surfaces at
  // CONSTRUCTION — mid-turn, inside a chat request — so without this guard the run
  // would come up green-looking and fail deep in the spec while naming the wrong
  // thing. The absolute path also means the gateway resolves it after any chdir.
  `S="$PWD/${SCRIPTED.scriptPath}"`,
  `[ -f "$S" ] || { echo "PERSONALCLAW_E2E_FATAL: scripted-provider script not found at $S (cwd $PWD)" >&2; exit 1; }`,
  `printf '%s' '{"dashboard":{"user_name":"Keyur"}}' > "$D/config.json"`,
  'PC="../.venv/bin/personalclaw"; [ -x "$PC" ] || PC=personalclaw',
  // Pin the gateway to THIS tree's source. Without it a worktree run boots whatever
  // `personalclaw` is on PATH — the editable install from the MAIN checkout — so the
  // gate silently tests code that is not the code under test. Measured: the first
  // integrated run failed with `no model provider resolves for use case 'background'`
  // and a traceback whose paths were all in the main checkout, because the scripted
  // provider does not exist there. In CI this is redundant (uv installs this repo) and
  // harmless; in a worktree it is the difference between a real result and a decoy.
  'export PYTHONPATH="$(cd .. && pwd)/src${PYTHONPATH:+:$PYTHONPATH}"',
  `PERSONALCLAW_HOME="$D" PERSONALCLAW_WORKSPACE="$D/workspace" ${SCRIPTED.scriptEnvVar}="$S" exec "$PC" gateway --port ${GATEWAY_PORT} --no-open --json-ready`,
].join('\n')

export default defineConfig({
  testDir: './e2e',
  // Baselines are platform-qualified (see snapshotPathTemplate) so a mac dev
  // and linux CI keep separate goldens.
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  expect: {
    // A small pixel tolerance absorbs sub-pixel AA noise while still catching
    // real chrome changes. Tune down as baselines stabilize.
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' },
  },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    // Deterministic viewport for stable screenshots.
    viewport: { width: 1280, height: 900 },
    // Auth: the `setup` project below mints this cookie jar against the gateway
    // the harness starts. Point PW_BASE_URL/STORAGE_STATE at your own pair (plus
    // PW_NO_SERVER=1) to drive an already-running gateway instead.
    storageState: STORAGE_STATE,
  },
  projects: [
    // Mints the authenticated session ONCE, then every spec reuses the jar. The
    // setup project must start from an EMPTY jar — an inline state object, not
    // `undefined`: a project `use` of `undefined` does not override the top-level
    // path, so setup died trying to read the file it exists to create.
    { name: 'setup', testMatch: /auth\.setup\.ts$/, use: { storageState: { cookies: [], origins: [] } } },
    { name: 'chromium', use: { ...devices['Desktop Chrome'] }, dependencies: ['setup'] },
  ],
  // Two servers: the isolated gateway, then the built SPA that proxies to it.
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : [
        {
          command: GATEWAY_COMMAND,
          // Readiness is the READY line, NOT a port probe: the named capture
          // group lands in process.env.PW_TOKEN (Playwright's documented
          // behaviour), which is exactly the input e2e/auth.setup.ts already
          // expects. Deliberately no `url`/`port` — either would let the run
          // proceed on a bound-but-unauthenticated gateway with no token.
          wait: { stdout: /PERSONALCLAW_READY:.*"token":\s*"(?<pw_token>[^"]+)"/ },
          timeout: 180_000,
        },
        {
          command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
          url: BASE_URL,
          timeout: 180_000,
          reuseExistingServer: !process.env.CI,
          // vite.config.ts reads PERSONALCLAW_PORT for its /api proxy target.
          env: { PERSONALCLAW_PORT: String(GATEWAY_PORT) },
        },
      ],
})
