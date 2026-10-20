import { defineConfig, devices } from '@playwright/test'
import { readdirSync } from 'node:fs'
import { SPEC_ISOLATION, STATEFUL_SPECS, seededOrder, unclassified } from './e2e/isolation'
import { choosePlaces, cleanUpRunAtExit, gatewayEnvPrefix } from './e2e/runPlaces'

// ── Playwright visual-regression + a11y harness ────────────────────────────
// The SAFETY RAIL for design-system consistency work: every
// consistency fix must show ZERO unintended visual diff against a captured
// baseline. Mirrors the personalclaw.dev pattern — @playwright/test +
// toHaveScreenshot with platform-qualified baselines (the -<platform> suffix
// keeps CI/mac/linux baselines separate so font/AA rendering diffs don't
// cause false failures).
//
// Baselines live in e2e/__screenshots__/ (committed). Regenerate a touched
// surface's baseline INTENTIONALLY with `npm run e2e:update` and say in the
// change's description which baselines moved and why — never silently keep
// or revert a real visual change.
//
// 🪤 `e2e:update` regenerates from a VISUAL-ONLY run, and `npm run e2e` runs the
// visual rail as a second invocation, for one reason: the specs that drive real
// scripted chat turns write flywheel state into the single gateway below, which
// changes what `#/learning` renders. Capturing in a mixed run and verifying in a
// visual-only one (or the reverse) makes the same golden both correct and wrong.
// See `helpers.assertPristineFlywheel` and `e2e/README.md`.
//
// The servers: this harness drives the built SPA served by vite preview, which
// proxies /api to a gateway the harness STARTS ITSELF — isolated, onboarded and
// token-authenticated. Without a gateway the SPA cannot resolve identity, so it
// renders the ONBOARDING screen for every route: no NavRail, no shell, no ⌘K
// listener. axe then reports a clean tree for 96 surfaces it never actually
// visited, and the one test that noticed (`command palette [opened]`) failed on
// its mounted-ness floor while naming the palette instead of the missing backend.

// ── Where this run lives: a home and two ports of its own ───────────────────
// Two runs on one machine must not meet. The runner's first load of this config makes the
// gateway's home with mkdtemp under the temp dir and picks a free port for each server (bind
// port 0, read it back), unless PW_PORT / PW_GATEWAY_PORT names one; neither is ever the
// install's own port. It writes all three to the environment (PW_E2E_HOME, PW_PORT,
// PW_GATEWAY_PORT), so every worker that loads this config again, every server it starts and
// every spec reads the same three, and makes nothing. The runner that made the home removes it
// when the run ends (`e2e/runPlaces.ts`). The gateway port is chosen here, before the gateway
// starts, so the preview's proxy target is known before the READY line; if something takes the
// port in between, the gateway fails to bind and auth.setup's shell-mounted assertion fails
// loudly rather than scanning an empty app.
const PLACES = await choosePlaces(process.env)
cleanUpRunAtExit(PLACES)
const PORT = PLACES.port
const BASE_URL = process.env.PW_BASE_URL || `http://localhost:${PORT}`
const GATEWAY_PORT = PLACES.gatewayPort

// Where auth.setup.ts writes the authenticated cookie jar (it reads STORAGE_STATE, which
// `choosePlaces` sets): a 0700 folder beside this run's home, never in it (the Doctor audits the
// home, and a jar there is a path no snapshot claims). It holds a live owner token for this run's
// throwaway gateway only and goes with the home. Two runs never sign each other's specs out.
const STORAGE_STATE = PLACES.storageState
// Where this run's specs write their output: a folder of its own under test-results/, which CI
// uploads. Playwright clears the output folder when a run starts, so it clears only this run's.
const OUTPUT_DIR = PLACES.outputDir

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
//     with this run's own home under the temp dir. Do not drop it.
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
//  - PERSONALCLAW_HOME is this run's own home (PW_E2E_HOME, above): made empty for the run
//    under the OS temp dir, and removed when the run ends. Never ~/.personalclaw, and never a
//    folder another run could be using.
//  - PERSONALCLAW_WORKSPACE too, at the home's own `workspace` folder: the default is there
//    anyway, and naming it keeps the run inside the temp dir whatever that default becomes.
//  - `dashboard.user_name` pre-seeded into config.json, because `onboarded` is
//    DERIVED from a non-empty SERVER-side name (web/src/app/identity.tsx). Seeding
//    the file skips the onboarding hijack without a PUT — so no CSRF/origin dance,
//    and nothing here weakens a security control. "Keyur" matches the committed
//    visual baselines' greeting.
//  - NOT PERSONALCLAW_AUTH_MODE=none: that swaps csrf_middleware for
//    _dev_user_middleware, so any a11y/CSRF-adjacent conclusion drawn under it
//    would not describe a real user. The token flow below is the real one.
//  - A MODEL, and still no credential and still no network: the two scripted-provider
//    env vars below. config.json holds a user name and two switches (the seed below)
//    and NO model entry, on purpose: `registry.py`'s `register_scripted_provider_type()`
//    synthesizes its own entry ("Scripted"/scripted-1, credential=None) and registers
//    it BEFORE the config-derived ones so it wins the implicit
//    first-entry-declaring-the-capability fallback. So there is no `providers[]` entry
//    and no `active_models.json` pin to write here, and adding either would put a
//    second, competing entry of the same type in the registry. Nothing is weakened:
//    the home is still under the temp dir, still fresh for the run, auth is still on, and
//    readiness is still the READY line.
const GATEWAY_COMMAND = [
  // The run's own home, made by `choosePlaces` and handed over in the environment. Refused if it
  // is unset or not there, so the gateway can never fall back to another home.
  'D="$PW_E2E_HOME"',
  '[ -n "$D" ] && [ -d "$D" ] || { echo "PERSONALCLAW_E2E_FATAL: PW_E2E_HOME names no folder (got \'$D\')" >&2; exit 1; }',
  'mkdir -p "$D/workspace"',
  // Fail the BOOT, loudly, if the script fixture is missing. The provider does
  // raise ScriptedScriptError on an unreadable path, but that surfaces at
  // CONSTRUCTION — mid-turn, inside a chat request — so without this guard the run
  // would come up green-looking and fail deep in the spec while naming the wrong
  // thing. The absolute path also means the gateway resolves it after any chdir.
  `S="$PWD/${SCRIPTED.scriptPath}"`,
  `[ -f "$S" ] || { echo "PERSONALCLAW_E2E_FATAL: scripted-provider script not found at $S (cwd $PWD)" >&2; exit 1; }`,
  // The home starts from `e2e/fixtures/home/`: config.json (the user name, and the two settings
  // that keep the identity report and self-remediation off) and triggers.json (every system
  // trigger the boot creates, switched off). The gateway runs its automations, so without the
  // seed the minutely heartbeat queue fires during the run and the dashboard's schedule card
  // changes state with the run's age. `tests/rails/test_e2e_home_seed_matches_the_boot_tree.py`
  // holds the seed to what the boot creates.
  'cp e2e/fixtures/home/config.json "$D/config.json"',
  'cp e2e/fixtures/home/triggers.json "$D/triggers.json"',
  '[ -f "$D/config.json" ] && [ -f "$D/triggers.json" ] '
    + '|| { echo "PERSONALCLAW_E2E_FATAL: the home seed did not copy into $D (cwd $PWD)" >&2; exit 1; }',
  // Install the app-UI fixture, so the sweep reaches app-AUTHORED DOM and not just the
  // app-hosting shell. `#/app/<name>` needs a real installed app WITH a bundle: the asset route
  // reads `installed.json` and refuses an app that is absent or disabled, so the fixture ships
  // both files plus its ESM entry (`e2e/fixtures/app-ui/`). Copied rather than generated here —
  // an ESM module printf'd into a shell string is unreviewable and unlintable.
  'mkdir -p "$D/apps"',
  'cp -R e2e/fixtures/app-ui "$D/apps/e2e-ui-fixture"',
  // 🪤 FAIL THE BOOT if the copy did not land, for the same reason the scripted-provider guard
  // above exists — and here the silent mode is worse than a crash. With the app absent,
  // `#/app/e2e-ui-fixture` renders the SHELL's "isn't installed" EmptyState, which is already
  // covered by `app/not-a-real-app`: axe and both detectors would sweep it, pass, and report the
  // app-authored surface clean while never having loaded a line of app code.
  '[ -f "$D/apps/e2e-ui-fixture/installed.json" ] && [ -f "$D/apps/e2e-ui-fixture/ui/index.mjs" ] '
    + '|| { echo "PERSONALCLAW_E2E_FATAL: app-UI fixture did not install into $D/apps (cwd $PWD)" >&2; exit 1; }',
  'PC="../.venv/bin/personalclaw"; [ -x "$PC" ] || PC=personalclaw',
  // Pin the gateway to THIS tree's source. Without it a worktree run boots whatever
  // `personalclaw` is on PATH — the editable install from the MAIN checkout — so the
  // gate silently tests code that is not the code under test. The first
  // integrated run failed with `no model provider resolves for use case 'background'`
  // and a traceback whose paths were all in the main checkout, because the scripted
  // provider does not exist there. In CI this is redundant (uv installs this repo) and
  // harmless; in a worktree it is the difference between a real result and a decoy.
  'export PYTHONPATH="$(cd .. && pwd)/src${PYTHONPATH:+:$PYTHONPATH}"',
  // HOME is the run's own folder beside its home, and every variable that names a folder outside
  // the home is taken out (`gatewayEnvPrefix`, `GATEWAY_UNSET` in e2e/runPlaces.ts). Without it the
  // gateway found the machine's own folders through the home folder: the shared skills folder it
  // lists at every start, other tools' setups and the Hugging Face folder, which Settings named.
  // So is every credential-shaped variable (`GATEWAY_CREDENTIAL_*`), which Settings › Secrets
  // listed as inherited from whoever started the run.
  // `tests/rails/test_e2e_gateway_home_is_the_runs_tree.py` holds every such folder to the run, and
  // holds the secrets read to none of the credentials it plants.
  `${gatewayEnvPrefix()} PERSONALCLAW_HOME="$D" PERSONALCLAW_WORKSPACE="$D/workspace" ${SCRIPTED.scriptEnvVar}="$S" exec "$PC" gateway --port ${GATEWAY_PORT} --no-open --json-ready`,
].join('\n')

// ── One invocation, three ways to share the gateway ───────────────────────────────────────────
// `smoke` runs first and reaches no gateway (smoke.spec.ts). `chromium` runs every spec the
// isolation table calls shared, in parallel against the one gateway and home. The specs it calls
// stateful run after it, one at a time (`stateful`), then once more each in an order drawn from
// a seed (`stateful-again-1`, `stateful-again-2`), so a stateful spec that passes only after the
// other has run reds here. The seed is PW_STATEFUL_SEED when set, so a run's order can be
// replayed, and the run reports it (`metadata.statefulSeed`, and each second-pass test's
// `stateful-seed` annotation). It is written back to the environment so every worker this config
// is re-read in draws the same order.
//
// The three passes run one after another: `stateful`, then `stateful-again-1`, then
// `stateful-again-2`, each starting only once the one before it has ended. `e2e/assert-one-run.mjs`
// reads the report's timings and reds a pass that started early.
//
// 🪤 TWO SHAPES, ONE ORDER. Playwright allows each only where the other cannot be used.
//   · A run whose file filter names only the stateful specs (CI's `e2e-stateful` job) chains them
//     with dependencies: `stateful` on `setup`, `stateful-again-1` on `stateful`, and
//     `stateful-again-2` on `stateful-again-1`. A red pass skips the passes after it, and the
//     isolation reporter fails the run, naming each test that did not run.
//   · A full run cannot: the stateful set must follow `chromium` without depending on it passing,
//     or a red shared spec would hide every stateful one. That takes a teardown, and Playwright
//     refuses a dependency on a teardown project. So each pass is the teardown of a project one
//     level further out: `chromium` owns `stateful`; `setup`, which `chromium` depends on, owns
//     `stateful-again-1`; and `stateful-anchor`, an empty project `setup` depends on, owns
//     `stateful-again-2`. A teardown runs once its owner and everything that depends on it,
//     teardowns included, have ended.
// No teardown has one of its own: Playwright 1.64 never runs such a teardown and still exits 0,
// which is how the stateful-only run lost its whole second pass. And `stateful-again-2` is no
// longer the smoke's teardown, which ran as soon as a worker came free: on a full run it started
// before the first pass, while the shared specs were still running.
const SPECS = readdirSync('./e2e').filter((f) => f.endsWith('.spec.ts'))
const missing = unclassified(SPECS)
if (missing.length) {
  throw new Error(`e2e/isolation.ts gives no isolation mode to: ${missing.join(', ')}. Classify each before it can run.`)
}
process.env.PW_STATEFUL_SEED ||= String(Math.floor(Math.random() * 2 ** 31))
const STATEFUL_SEED = Number(process.env.PW_STATEFUL_SEED)
const SECOND_PASS = seededOrder(STATEFUL_SPECS, STATEFUL_SEED)
if (SECOND_PASS.length !== 2) {
  throw new Error(`the stateful second pass has a project per spec, two of them; e2e/isolation.ts now names ${SECOND_PASS.length}`)
}
const fileMatcher = (files: readonly string[]) => files.map((f) => new RegExp(`(^|/)${f.replace(/\./g, '\\.')}$`))

// Teardown projects ignore the command line's file filter, so `playwright test e2e/visual.spec.ts`
// (`npm run e2e:visual`) would run the stateful set too. A run whose file filter names no
// stateful spec leaves the chain out. Decided once, in the runner, and handed to the workers that
// re-read this config through the environment, like the seed, so all of them see one project list.
const VALUE_FLAGS = new Set(['-c', '--config', '-g', '--grep', '-G', '--grep-invert', '--project', '--shard', '-j',
  '--workers', '--reporter', '--output', '--retries', '--repeat-each', '--timeout', '--max-failures', '-x'])
function fileFilters(argv: readonly string[]): string[] {
  const at = argv.indexOf('test')
  if (at < 0) return []
  const out: string[] = []
  for (let i = at + 1; i < argv.length; i++) {
    const word = argv[i]
    if (VALUE_FLAGS.has(word)) i++
    else if (!word.startsWith('-')) out.push(word)
  }
  return out
}
if (process.env.PW_STATEFUL_CHAIN === undefined) {
  const filters = fileFilters(process.argv)
  const named = filters.some((f) => STATEFUL_SPECS.some((spec) => spec.includes(f.split('/').pop() ?? f) || f.endsWith(spec)))
  process.env.PW_STATEFUL_CHAIN = filters.length === 0 || named ? '1' : '0'
}
const STATEFUL_CHAIN = process.env.PW_STATEFUL_CHAIN === '1'

// A run whose file filter names ONLY stateful specs (`playwright test e2e/staleWrite.spec.ts`) leaves
// `chromium` with nothing to run, so nothing would pull in `setup`, and every stateful spec would
// meet the onboarding screen. Then the chain hangs off `setup` instead: `stateful` depends on it, and
// each second-pass project on the pass before it.
if (process.env.PW_STATEFUL_ONLY === undefined) {
  const filters = fileFilters(process.argv)
  const onlyStateful =
    filters.length > 0 &&
    filters.every((f) => STATEFUL_SPECS.some((spec) => f.endsWith(spec) || spec.includes(f.split('/').pop() ?? f)))
  process.env.PW_STATEFUL_ONLY = onlyStateful ? '1' : '0'
}
const STATEFUL_ONLY = process.env.PW_STATEFUL_ONLY === '1'

// The local pre-push browser check runs the smoke project alone, which needs no gateway, so
// the gateway is not booted for it.
const ONLY_SMOKE = !!process.env.PW_ONLY_SMOKE

// The preview serves the dist the build ALREADY made, and refuses one that does not match the
// checked-out sources: building here made every invocation rebuild the SPA (14 builds per pull
// request) and could serve whatever was in web/dist. The interpreter is the repo's venv when
// there is one, which is where the freshness check's imports resolve. Its verdict goes to stderr:
// Playwright shows a web server's stderr and drops its stdout, so a refusal names its reason.
const FRESHNESS_CHECK =
  'PY=../.venv/bin/python; [ -x "$PY" ] || PY=python3; "$PY" ../scripts/spa_dist_freshness.py check --require-stamp >&2'

export default defineConfig({
  testDir: './e2e',
  outputDir: OUTPUT_DIR,
  metadata: {
    statefulSeed: String(STATEFUL_SEED),
    statefulOrder: SECOND_PASS,
    // Where this run lived, so a report says which home and ports it used.
    e2eHome: PLACES.home,
    previewPort: String(PORT),
    gatewayPort: String(GATEWAY_PORT),
    storageState: STORAGE_STATE,
    isolation: Object.fromEntries(Object.entries(SPEC_ISOLATION).map(([spec, { mode }]) => [spec, mode])),
  },
  // Baselines are platform-qualified (see snapshotPathTemplate) so a mac dev
  // and linux CI keep separate goldens.
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // ── Why this is set at all, and why 3 ────────────────────────────────────
  // Unset, playwright runs HALF the logical cores. `ubuntu-latest` on a public
  // repo is 4-core, so CI was silently running 2 workers — and the browser job
  // (then `e2e-a11y`, now the `e2e-shard` legs) is the binding constraint on this repo's merge rate. Measured 2026-09-18 from
  // the Actions API across five CONCLUDED runs (35381670780, 35381697823,
  // 35381857440, 35382011453, 35382279089): the job is 23.6–28.7 min while the
  // next-longest job in the whole workflow is `test-shard` at ~9 min, so every
  // PR waits on this one job for roughly 20 minutes after everything else is
  // green.
  //
  // The cost is NOT setup — checkout+uv+npm+chromium total 54 SECONDS, the
  // caches are doing their job. It was test VOLUME in two steps (measured then):
  //   the walkthrough spec  14.5–17.1 min   (~60% of the job)
  //   a11y.spec.ts           5.8– 7.7 min
  // Both were pure read-only sweeps (navigate, tab, read computed styles, run
  // axe), so they were embarrassingly parallel and were being run two-at-a-time
  // for no reason other than an unset default. #3941 has since folded both into
  // `surfaces.spec.ts`, which visits each surface once per theme instead of once
  // per check; it is just as read-only, so the reasoning below still holds.
  //
  // 🪤 3, NOT 4 (= `'100%'`). The sweeps share ONE gateway and ONE vite preview
  // on the same 4-core box. Those two are near-idle while a read-only sweep
  // runs, but pinning all 4 cores to chromium leaves nothing for them or for
  // the test runner itself, and a CPU-starved page trips playwright's 30 s
  // per-test default — which would convert a throughput win into flaky reds.
  // Flaky-red CI is strictly worse for merge rate than slow-green CI, so this
  // takes the +50% that needs no headroom argument rather than the +100% that
  // does.
  //
  // This changes concurrency only. Every test still runs, once, asserting
  // exactly what it asserted before; nothing is skipped or retimed by it. (CI's
  // legs split the run on their command line, `--shard=N/2`.)
  // Local runs keep playwright's default so a dev machine is not pinned.
  workers: process.env.CI ? 3 : undefined,
  // A retried pass is reported as `flaky` in the JSON and blob reports, never as a plain pass.
  // One retry, in a new worker process, under the same test timeout: it only classifies. Under CI
  // a test that passes only on its retry fails the run (`failOnFlakyTests`), and `tools/ci
  // run-job` records it as a flake from the JSON report (tools/ci/jobs.py, classify_playwright).
  retries: process.env.CI ? 1 : 0,
  failOnFlakyTests: !!process.env.CI,
  // The isolation reporter is FIRST: it annotates every test with its isolation mode before the
  // JSON and blob reporters read them, and fails the run when a spec runs where its mode forbids.
  // The JSON report's path is PLAYWRIGHT_JSON_OUTPUT_NAME; the blob's is PLAYWRIGHT_BLOB_OUTPUT_DIR
  // (blob-report/ unset), which `e2e/merge-legs.mjs` hands `merge-reports` when the run is sharded.
  reporter: process.env.CI
    ? [['./e2e/isolationReporter.ts'], ['github'], ['json'], ['blob'], ['html', { open: 'never' }]]
    : [['./e2e/isolationReporter.ts'], ['list']],
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
    // A full run's outermost project: it holds no test, and owns the last pass (see above).
    ...(STATEFUL_CHAIN && !STATEFUL_ONLY
      ? [{ name: 'stateful-anchor', testMatch: /^$/, teardown: 'stateful-again-2' }]
      : []),
    // Mints the authenticated session ONCE, then every spec reuses the jar. The
    // setup project must start from an EMPTY jar — an inline state object, not
    // `undefined`: a project `use` of `undefined` does not override the top-level
    // path, so setup died trying to read the file it exists to create.
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts$/,
      use: { storageState: { cookies: [], origins: [] } },
      ...(STATEFUL_CHAIN && !STATEFUL_ONLY ? { dependencies: ['stateful-anchor'], teardown: 'stateful-again-1' } : {}),
    },
    // The render smoke, first: the built bundle mounts with no gateway behind it.
    {
      name: 'smoke',
      testMatch: /smoke\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], storageState: { cookies: [], origins: [] } },
    },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      // `setup` only. The smoke has no dependencies, so it runs in the first phase beside `setup`,
      // before any spec here; it is not one of these dependencies, because a dependency that fails
      // skips its dependents, and a red smoke would then hide every shared spec in the run.
      dependencies: ['setup'],
      // Every spec but the smoke and the stateful set. Not `visual.spec.ts`: a testIgnore would
      // hide it from its own `--project=chromium` invocation, so `--grep-invert @visual` leaves it
      // out of this one.
      testIgnore: fileMatcher(['smoke.spec.ts', ...STATEFUL_SPECS]),
      ...(STATEFUL_CHAIN && !STATEFUL_ONLY ? { teardown: 'stateful' } : {}),
    },
    ...(STATEFUL_CHAIN
      ? [
          {
            name: 'stateful',
            use: { ...devices['Desktop Chrome'] },
            testMatch: fileMatcher(STATEFUL_SPECS),
            workers: 1,
            fullyParallel: false,
            ...(STATEFUL_ONLY ? { dependencies: ['setup'] } : {}),
          },
          ...SECOND_PASS.map((spec, i) => ({
            name: `stateful-again-${i + 1}`,
            use: { ...devices['Desktop Chrome'] },
            testMatch: fileMatcher([spec]),
            workers: 1,
            fullyParallel: false,
            ...(STATEFUL_ONLY ? { dependencies: [i === 0 ? 'stateful' : `stateful-again-${i}`] } : {}),
          })),
        ]
      : []),
  ],
  // Two servers: the isolated gateway, then the built SPA that proxies to it.
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : [
        ...(ONLY_SMOKE ? [] : [{
          command: GATEWAY_COMMAND,
          // Readiness is the READY line, NOT a port probe: the named capture
          // group lands in process.env.PW_TOKEN (Playwright's documented
          // behaviour), which is exactly the input e2e/auth.setup.ts already
          // expects. Deliberately no `url`/`port` — either would let the run
          // proceed on a bound-but-unauthenticated gateway with no token.
          wait: { stdout: /PERSONALCLAW_READY:.*"token":\s*"(?<pw_token>[^"]+)"/ },
          timeout: 180_000,
          env: { PW_E2E_HOME: PLACES.home },
        }]),
        {
          // No build: the dist is built and stamped once (`make web-build`, or CI's `web` job),
          // and a stale or unstamped one fails here, naming the freshness check, before any test.
          command: `${FRESHNESS_CHECK} && npm run preview -- --port ${PORT} --strictPort`,
          url: BASE_URL,
          // 60s, from 900s. The 900s budget existed only for the build this entry used to run
          // (a full vite build, on a loaded host). What is left is the freshness digest over
          // web/src and starting a static server, which takes seconds; 60s keeps a loaded host
          // from reading as a broken suite without waiting a quarter hour on a hang.
          timeout: 60_000,
          // Never a server this run did not start, unless PW_REUSE_SERVER=1 asks for it (with a
          // PW_PORT naming that server). Reusing whatever answered on a fixed port once let one
          // run drive another run's preview, and its gateway, with its own specs.
          reuseExistingServer: process.env.PW_REUSE_SERVER === '1',
          // vite.config.ts reads PERSONALCLAW_PORT for its /api proxy target.
          env: { PERSONALCLAW_PORT: String(GATEWAY_PORT) },
        },
      ],
})
