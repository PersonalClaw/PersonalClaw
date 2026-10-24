import { afterEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// ── A stale dist fails the browser run before any test ────────────────────────────────────────
//
// The browser run builds the dashboard once (CI's `web` job, or `make web-build`) and stamps what
// it was built from. The preview server it runs against builds nothing: it runs
// `scripts/spa_dist_freshness.py check --require-stamp` first, so a dist built from other sources,
// or one with no stamp, is refused before the run's first test, naming the check
// (`web/playwright.config.ts`). These run the real config's preview server in a scratch repository
// of the same shape, whose dist is stamped and then has a source edited after the stamp, and a
// stand-in spec that leaves a mark if it ever runs.

const WEB = process.cwd()
const REPO = resolve(WEB, '..')
const PW_TEST = createRequire(join(WEB, 'package.json')).resolve('@playwright/test')

let scratch = ''
afterEach(() => {
  if (scratch) rmSync(scratch, { recursive: true, force: true })
  scratch = ''
})

/** A repository-shaped tree: the freshness script beside a `web/` with sources and a stamped dist.
 *  Its `src/` and `.venv` are the repository's, for the script's imports and interpreter. */
function stampedRepo(): string {
  scratch = mkdtempSync(join(tmpdir(), 'pw-stale-dist-'))
  const root = join(scratch, 'repo')
  mkdirSync(join(root, 'scripts'), { recursive: true })
  copyFileSync(join(REPO, 'scripts', 'spa_dist_freshness.py'), join(root, 'scripts', 'spa_dist_freshness.py'))
  symlinkSync(join(REPO, 'src'), join(root, 'src'))
  if (existsSync(join(REPO, '.venv'))) symlinkSync(join(REPO, '.venv'), join(root, '.venv'))
  const web = join(root, 'web')
  mkdirSync(join(web, 'src'), { recursive: true })
  writeFileSync(join(web, 'src', 'App.tsx'), 'export const App = () => null\n')
  writeFileSync(join(web, 'index.html'), '<div id="root"></div>\n')
  // A preview that serves the dist on the port the config names, as `vite preview` would: so once
  // the check passes the server starts and the spec runs, and a run that skipped the check would too.
  writeFileSync(join(web, 'package.json'), '{ "name": "web", "scripts": { "preview": "node serve.mjs" } }\n')
  writeFileSync(
    join(web, 'serve.mjs'),
    "import { createServer } from 'node:http'\n" +
      "import { readFileSync } from 'node:fs'\n" +
      "const port = Number(process.argv[process.argv.indexOf('--port') + 1])\n" +
      "createServer((req, res) => { res.writeHead(200, { 'content-type': 'text/html' }); res.end(readFileSync('dist/index.html')) })" +
      ".listen(port, 'localhost')\n",
  )
  writeFileSync(join(root, 'package-lock.json'), '{ "lockfileVersion": 3 }\n')
  mkdirSync(join(web, 'dist'))
  writeFileSync(join(web, 'dist', 'index.html'), '<script src="/assets/built.js"></script>\n')
  const stamped = spawnSync(python(root), [join(root, 'scripts', 'spa_dist_freshness.py'), 'stamp'], { encoding: 'utf8' })
  expect(stamped.status, `${stamped.stdout}${stamped.stderr}`).toBe(0)
  return root
}

/** The interpreter the config's freshness check picks: the repository's venv, else python3. */
function python(root: string): string {
  const venv = join(root, '.venv', 'bin', 'python')
  return existsSync(venv) ? venv : 'python3'
}

/** One run of the real config with only its preview server, started in `root`'s `web/`, over a
 *  stand-in spec that writes `ran` if it runs. */
function run(root: string) {
  const specs = join(scratch, 'specs')
  mkdirSync(specs, { recursive: true })
  writeFileSync(join(specs, 'package.json'), '{ "type": "commonjs" }\n')
  const ran = join(scratch, 'ran')
  writeFileSync(
    join(specs, 'a11y.spec.ts'),
    `import { test } from ${JSON.stringify(PW_TEST)}\n` +
      `import { writeFileSync } from 'node:fs'\n` +
      `test('a route', () => { writeFileSync(${JSON.stringify(ran)}, 'ran') })\n`,
  )
  const config = join(scratch, 'stale.config.ts')
  writeFileSync(join(scratch, 'package.json'), '{ "type": "module" }\n')
  writeFileSync(
    config,
    `import base from ${JSON.stringify(join(WEB, 'playwright.config.ts'))}\n` +
      // The preview server alone, as the config writes it, started where the scratch web/ is.
      `const servers = [base.webServer].flat().filter((s) => s && String(s.command).includes('npm run preview'))\n` +
      `if (servers.length !== 1) throw new Error('the config has no one preview server: ' + servers.length)\n` +
      `export default { ...base, testDir: ${JSON.stringify(specs)}, retries: 0, projects: [{ name: 'chromium' }],\n` +
      `  reporter: [['list']], webServer: [{ ...servers[0], cwd: ${JSON.stringify(join(root, 'web'))}, timeout: 30_000 }] }\n`,
  )
  const result = spawnSync('npx', ['playwright', 'test', '-c', config], {
    cwd: WEB,
    encoding: 'utf8',
    // No gateway (the preview server is the one under test), and every run place in the scratch.
    env: { ...process.env, PW_ONLY_SMOKE: '1', PW_STATEFUL_CHAIN: '0', PW_ALONE_HOME: '', PW_OUTPUT_DIR: join(scratch, 'out') },
    timeout: 120_000,
  })
  return { status: result.status, out: `${result.stdout}\n${result.stderr}`, ran: existsSync(ran) }
}

describe('a stale dist fails the browser run before any test', () => {
  it('a source edited after the stamp: the preview refuses it, naming the check, and no spec runs', () => {
    const root = stampedRepo()
    writeFileSync(join(root, 'web', 'src', 'App.tsx'), 'export const App = () => "edited after the build"\n')
    const { status, out, ran } = run(root)
    expect(status, out).not.toBe(0)
    expect(out).toContain('spa-check: STALE')
    expect(out).toMatch(/config\.webServer was not able to start/)
    expect(ran, 'a spec ran against the stale dist').toBe(false)
  }, 150_000)

  it('a dist with no stamp is refused the same way', () => {
    const root = stampedRepo()
    for (const name of readdirSync(join(root, 'web', 'dist'))) {
      if (name !== 'index.html') rmSync(join(root, 'web', 'dist', name))
    }
    const { status, out, ran } = run(root)
    expect(status, out).not.toBe(0)
    expect(out).toContain('spa-check: UNSTAMPED')
    expect(out).toContain('cannot be proven current')
    expect(ran, 'a spec ran against the unstamped dist').toBe(false)
  }, 150_000)

  it('the control: the same dist untouched passes the check, the preview starts and the spec runs', () => {
    const root = stampedRepo()
    const { status, out, ran } = run(root)
    expect(out).toContain('spa-check: FRESH')
    expect(status, out).toBe(0)
    expect(ran, 'the spec did not run against the fresh dist').toBe(true)
  }, 150_000)
})
