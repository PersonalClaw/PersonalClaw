import { afterEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, type Server } from 'node:net'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'
import { INSTALL_PORT, choosePlaces, cleanUpRun } from '../../e2e/runPlaces'

// ── Each browser run has a home and ports of its own ───────────────────────────────────────────
//
// The harness used to wipe and recreate ONE fixed folder under the temp dir at the start of every
// run, and bind two fixed ports, reusing any server already on them. Two runs on one machine then
// shared a home: one run's boot wiped the other's live gateway home. Now the runner that loads the
// config first makes a home with mkdtemp and picks free ports, and writes them to the environment,
// so the workers that load the config again (and the servers it starts) all see the same three.

const WEB = process.cwd()

let scratch = ''
const open: Server[] = []
afterEach(async () => {
  await Promise.all(open.splice(0).map((s) => new Promise((resolve) => s.close(resolve))))
  if (scratch) rmSync(scratch, { recursive: true, force: true })
  scratch = ''
})

/** A scratch temp dir of the test's own, for the runs' homes to be made in. */
function tmp(): string {
  scratch = mkdtempSync(join(tmpdir(), 'pw-places-'))
  return scratch
}
const homesIn = (dir: string) => readdirSync(dir).filter((f) => f.startsWith('personalclaw-e2e-'))

/** Keep `port` bound, as a live server of another run would, so nothing can be handed it again. */
function hold(port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = createServer()
    s.once('error', reject)
    s.listen(port, '127.0.0.1', () => { open.push(s); resolve() })
  })
}

describe('choosePlaces', () => {
  it('gives two runs two homes and two pairs of ports', async () => {
    const dir = tmp()
    const a = await choosePlaces({ TMPDIR: dir }, { tmp: dir, testResults: join(dir, 'test-results') })
    // The first run's servers are up while the second chooses.
    await hold(a.port)
    await hold(a.gatewayPort)
    const b = await choosePlaces({ TMPDIR: dir }, { tmp: dir, testResults: join(dir, 'test-results') })

    expect(a.home).not.toBe(b.home)
    expect(new Set([a.port, a.gatewayPort, b.port, b.gatewayPort]).size).toBe(4)
    for (const p of [a, b]) {
      expect(p.home.startsWith(dir + sep), p.home).toBe(true)
      expect(existsSync(p.home), 'the home is made, empty, ready for the gateway').toBe(true)
      expect(p.madeHome).toBe(true)
    }
    expect(homesIn(dir)).toHaveLength(2)
  })

  it('a second load in the same run (a worker) reads the same home and ports, and makes nothing', async () => {
    const dir = tmp()
    const env: Record<string, string | undefined> = {}
    const first = await choosePlaces(env, { tmp: dir, testResults: join(dir, 'test-results') })
    expect(env.PW_E2E_HOME).toBe(first.home)
    expect(env.PW_PORT).toBe(String(first.port))
    expect(env.PW_GATEWAY_PORT).toBe(String(first.gatewayPort))

    const again = await choosePlaces({ ...env }, { tmp: dir, testResults: join(dir, 'test-results') })
    expect(again).toEqual({ ...first, madeHome: false, madeOutputDir: false })
    expect(homesIn(dir), 'one run, one home').toHaveLength(1)
  })

  it('takes PW_PORT and PW_GATEWAY_PORT when they are set', async () => {
    const dir = tmp()
    const p = await choosePlaces({ PW_PORT: '4400', PW_GATEWAY_PORT: '10500' }, { tmp: dir, testResults: join(dir, 'test-results') })
    expect([p.port, p.gatewayPort]).toEqual([4400, 10500])
  })

  it("never hands out the install's port, or one port twice", async () => {
    const dir = tmp()
    const offers = [INSTALL_PORT, 50001, 50001, INSTALL_PORT, 50002]
    const p = await choosePlaces({}, { tmp: dir, testResults: join(dir, 'test-results'), pickPort: async () => offers.shift()! })
    expect([p.port, p.gatewayPort]).toEqual([50001, 50002])
  })

  it("refuses PW_GATEWAY_PORT set to the install's port", async () => {
    const dir = tmp()
    await expect(choosePlaces({ PW_GATEWAY_PORT: String(INSTALL_PORT) }, { tmp: dir, testResults: join(dir, 'test-results') })).rejects.toThrow(/install/)
  })

  it('gives two runs two cookie jars and two output folders, and a worker the same ones', async () => {
    const dir = tmp()
    const results = join(dir, 'test-results')
    const envA: Record<string, string | undefined> = {}
    const envB: Record<string, string | undefined> = {}
    const a = await choosePlaces(envA, { tmp: dir, testResults: results })
    const b = await choosePlaces(envB, { tmp: dir, testResults: results })

    expect(a.storageState).not.toBe(b.storageState)
    expect(a.outputDir).not.toBe(b.outputDir)
    for (const p of [a, b]) {
      expect(p.storageState.startsWith(p.home + sep), 'the jar lives in the run home, and goes with it').toBe(true)
      expect(p.outputDir.startsWith(results + sep), 'the output folder is under test-results/, where CI uploads').toBe(true)
    }
    // auth.setup.ts writes the jar it reads from STORAGE_STATE, so the runner hands it on.
    expect(envA.STORAGE_STATE).toBe(a.storageState)
    const worker = await choosePlaces({ ...envA }, { tmp: dir, testResults: results })
    expect([worker.storageState, worker.outputDir]).toEqual([a.storageState, a.outputDir])
  })

  it('removes at exit the output folder it made if the run wrote nothing there, and keeps one with output', async () => {
    const dir = tmp()
    const results = join(dir, 'test-results')
    const empty = await choosePlaces({}, { tmp: dir, testResults: results })
    const used = await choosePlaces({}, { tmp: dir, testResults: results })
    writeFileSync(join(used.outputDir, 'geometry.txt'), 'kept for CI')
    const named = await choosePlaces({ PW_OUTPUT_DIR: join(dir, 'named') }, { tmp: dir, testResults: results })
    expect(named.madeOutputDir).toBe(false)
    for (const p of [empty, used, named]) cleanUpRun(p)
    expect(existsSync(empty.outputDir), 'an empty run folder is litter').toBe(false)
    expect(existsSync(join(used.outputDir, 'geometry.txt')), 'output CI uploads stays').toBe(true)
    expect(existsSync(empty.home) || existsSync(used.home), 'the homes go too').toBe(false)
  })

  it('keeps a STORAGE_STATE the caller names', async () => {
    const dir = tmp()
    const p = await choosePlaces({ STORAGE_STATE: join(dir, 'mine.json') }, { tmp: dir, testResults: join(dir, 'test-results') })
    expect(p.storageState).toBe(join(dir, 'mine.json'))
  })

  it('makes no home for a run that starts no servers, unless one is named', async () => {
    const dir = tmp()
    const none = await choosePlaces({ PW_NO_SERVER: '1' }, { tmp: dir, testResults: join(dir, 'test-results') })
    expect([none.home, none.madeHome]).toEqual(['', false])
    expect(homesIn(dir)).toEqual([])
  })
})

/** `playwright test --list` over the real config, as CI runs it: CI set, its reporters, and the
 *  JSON report's path from PLAYWRIGHT_JSON_OUTPUT_NAME. */
function listAsCi(dir: string, report: string) {
  return spawnSync('npx', ['playwright', 'test', '--list', '-c', 'playwright.config.ts'], {
    cwd: WEB,
    encoding: 'utf8',
    env: {
      ...process.env,
      CI: '1',
      TMPDIR: dir,
      PLAYWRIGHT_JSON_OUTPUT_NAME: report,
      PLAYWRIGHT_BLOB_OUTPUT_DIR: join(dir, 'blob'),
      PLAYWRIGHT_HTML_OUTPUT_DIR: join(dir, 'html'),
      PW_E2E_HOME: '', PW_PORT: '', PW_GATEWAY_PORT: '', PW_NO_SERVER: '', PW_REUSE_SERVER: '',
      STORAGE_STATE: '', PW_OUTPUT_DIR: '',
      PW_STATEFUL_SEED: '7',
    },
    timeout: 120_000,
  })
}

describe('the real config', () => {
  it("under CI, each run gets one home and free ports, the JSON report lands where CI reads it, and the home is gone after", () => {
    const dir = tmp()
    const runs = [1, 2].map((n) => {
      const report = join(dir, `report-${n}.json`)
      const r = listAsCi(dir, report)
      expect(r.status, `${r.stdout}\n${r.stderr}`).toBe(0)
      expect(existsSync(report), 'the JSON report is written at PLAYWRIGHT_JSON_OUTPUT_NAME').toBe(true)
      const config = JSON.parse(readFileSync(report, 'utf8')).config
      const outputDirs = new Set((config.projects as { outputDir: string }[]).map((p) => p.outputDir))
      expect(outputDirs.size, 'every project of one run writes one output folder').toBe(1)
      const seen: Record<string, string> = { ...(config.metadata as Record<string, string>), outputDir: String([...outputDirs][0]) }
      return seen
    })
    for (const m of runs) {
      expect(m.e2eHome.startsWith(dir + sep), m.e2eHome).toBe(true)
      expect(Number(m.previewPort)).toBeGreaterThan(0)
      expect(Number(m.gatewayPort)).not.toBe(INSTALL_PORT)
      expect(m.storageState.startsWith(m.e2eHome + sep), m.storageState).toBe(true)
      expect(m.outputDir.startsWith(join(WEB, 'test-results') + sep), m.outputDir).toBe(true)
    }
    expect(runs[0].e2eHome).not.toBe(runs[1].e2eHome)
    expect(runs[0].storageState).not.toBe(runs[1].storageState)
    expect(runs[0].outputDir).not.toBe(runs[1].outputDir)
    // A listing writes no output, so each run removes the empty run folder it made when it ends.
    for (const m of runs) expect(existsSync(m.outputDir), `${m.outputDir} was left behind`).toBe(false)
    expect(homesIn(dir), "each run removes the home it made when it ends").toEqual([])
  }, 240_000)

  it('names no fixed home, wipes no folder, and reuses a server only when asked', () => {
    const config = readFileSync(join(WEB, 'playwright.config.ts'), 'utf8')
    expect(config).toContain('choosePlaces(')
    expect(config).not.toMatch(/personalclaw-e2e-home/)
    expect(config).not.toMatch(/rm -rf/)
    expect(config).not.toMatch(/PW_(GATEWAY_)?PORT \|\| \d/)
    expect(config).toMatch(/reuseExistingServer: process\.env\.PW_REUSE_SERVER === '1'/)
  })
})
