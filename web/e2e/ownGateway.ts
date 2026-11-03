import { expect, type Page, type TestInfo } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { freePort, gatewayEnvPrefix, userHomeFor } from './runPlaces'

// ── A gateway of a spec's own, serving a copy of the build ────────────────────────────────────
//
// For a spec that restarts its gateway, which the run's gateway must never be: every other spec
// is on that one. Its own home, port and HOME beside the home, started through the harness's
// environment prefix (`gatewayEnvPrefix`) as the run's gateway is, from the interpreter that one
// uses. It serves a COPY of `dist/` (`PERSONALCLAW_SPA_DIST`), which the spec may change, so a run
// killed part way leaves the real build as it was. The copy, the home and its HOME are removed
// when it stops; a failed test keeps the gateway's log in its output folder.

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export const DIST = join(WEB, 'dist')

export interface Gateway {
  origin: string
  token: string
  /** The copy of `dist/` this gateway serves. */
  dist: string
  stop: () => Promise<void>
}

/** The build a dist folder serves, as the gateway reckons it (`frontend.spa_build_id`). */
export function buildOf(dist: string): string {
  return createHash('sha256').update(readFileSync(join(dist, 'index.html'))).digest('hex').slice(0, 16)
}

/** Start a gateway on a home, port and dist copy of its own, and wait for its READY line. */
export async function startGateway(testInfo: TestInfo): Promise<Gateway> {
  const home = mkdtempSync(join(tmpdir(), 'personalclaw-e2e-'))
  const userHome = userHomeFor(home)
  const dist = join(dirname(home), `served-build-for-${basename(home)}`)
  mkdirSync(userHome, { recursive: true })
  cpSync(DIST, dist, { recursive: true })
  const port = await freePort()
  const command = [
    'D="$PW_E2E_HOME"',
    'mkdir -p "$D/workspace"',
    'cp e2e/fixtures/home/config.json "$D/config.json"',
    'cp e2e/fixtures/home/triggers.json "$D/triggers.json"',
    // The interpreter as the run's gateway finds it (playwright.config.ts's GATEWAY_COMMAND).
    'if [ -n "${PERSONALCLAW_PY:-}" ]; then PC="$(dirname "$PERSONALCLAW_PY")/personalclaw"; '
      + 'else PC="../.venv/bin/personalclaw"; [ -x "$PC" ] || PC=personalclaw; fi',
    'export PYTHONPATH="$(cd .. && pwd)/src${PYTHONPATH:+:$PYTHONPATH}"',
    `${gatewayEnvPrefix()} PERSONALCLAW_HOME="$D" PERSONALCLAW_WORKSPACE="$D/workspace" PERSONALCLAW_SPA_DIST="$PW_SPA_DIST" exec "$PC" gateway --port ${port} --no-open --json-ready`,
  ].join('\n')
  const child: ChildProcess = spawn('/bin/sh', ['-c', command], {
    cwd: WEB,
    env: { ...process.env, PW_E2E_HOME: home, PW_SPA_DIST: dist },
    // Its own process group, so the stop below ends what it started and nothing else.
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let exited = false
  child.once('exit', () => { exited = true })

  const stop = async () => {
    if (!exited && child.pid) {
      try { process.kill(-child.pid, 'SIGTERM') } catch { /* gone already */ }
      const ended = await Promise.race([
        new Promise<boolean>((done) => child.once('exit', () => done(true))),
        new Promise<boolean>((done) => setTimeout(() => done(false), 15_000)),
      ])
      if (!ended) try { process.kill(-child.pid, 'SIGKILL') } catch { /* gone already */ }
    }
    if (testInfo.status !== testInfo.expectedStatus) keepGatewayLog(home, testInfo)
    for (const dir of [home, userHome, dist]) rmSync(dir, { recursive: true, force: true })
  }

  try {
    const token = await new Promise<string>((ready, failed) => {
      let out = ''
      let err = ''
      const timer = setTimeout(() => failed(new Error(`the gateway was not ready in 180 s:\n${err.slice(-2000)}`)), 180_000)
      child.stdout!.on('data', (chunk: Buffer) => {
        out += chunk.toString()
        const line = out.split('\n').find((l) => l.startsWith('PERSONALCLAW_READY:'))
        if (!line) return
        clearTimeout(timer)
        ready(String(JSON.parse(line.slice('PERSONALCLAW_READY:'.length)).token))
      })
      child.stderr!.on('data', (chunk: Buffer) => { err += chunk.toString() })
      child.once('exit', (code) => {
        clearTimeout(timer)
        failed(new Error(`the gateway exited (${code}) before it was ready:\n${err.slice(-2000)}`))
      })
    })
    return { origin: `http://127.0.0.1:${port}`, token, dist, stop }
  } catch (error) {
    await stop()
    throw error
  }
}

function keepGatewayLog(home: string, testInfo: TestInfo): void {
  try {
    for (const name of readdirSync(home).filter((n) => /^gateway\.log/.test(n))) {
      copyFileSync(join(home, name), testInfo.outputPath(name))
    }
  } catch { /* the rest of the clean-up still runs */ }
}

/** Restart the gateway a page is on, the way Settings does: a re-exec in place on the same port,
 *  which keeps its environment. */
export async function restartFromPage(page: Page): Promise<void> {
  const answer = await page.evaluate(() =>
    fetch('/api/system/restart', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Key': 'dashboard:ui' } })
      .then((r) => r.status))
  expect(answer, 'the gateway refused the restart').toBe(200)
}
