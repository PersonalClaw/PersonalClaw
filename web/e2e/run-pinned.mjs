// Runs one Playwright invocation against the browser of the pinned Playwright image
// (e2e/visual-image.json), the one environment the container-made baselines are made and compared
// in (#3943).
//
//     node e2e/run-pinned.mjs -- e2e/visual.spec.ts            compare against the baselines
//     node e2e/run-pinned.mjs --update -- e2e/visual.spec.ts   rewrite them, and the manifest
//
// The image is named only by its linux/amd64 digest, and its Playwright is the one the lock
// resolves for `@playwright/test`. Only the browser runs in it: the browser, the fonts and the
// renderer decide the pixels. The test runner, the harness and the gateway it boots stay on the
// host as in every browser leg, so what the gateway sees (its network, tmux, docker) is the
// runner's and not the image's. The image serves its browser with the locked playwright-core's
// `run-server`, bound to 127.0.0.1 on the host's network, and the run connects to it through
// Playwright's own PW_TEST_CONNECT_WS_ENDPOINT; the checkout and this Node are mounted read-only at
// their own paths, so nothing is installed and nothing in the checkout is written from inside.
// Only a linux/amd64 host is accepted for now, and the runs happen on CI (merge.yml's browser leg
// and the refresh workflow, visual-baselines.yml).
//
// Before the invocation it probes the image (e2e/visual-environment.mjs) in a container with no
// network. A compare run fails when that environment differs from the one the committed manifest
// (e2e/visual-environment.json) records, or when there is no manifest, so a pixel diff is never
// the first sign that the environment moved; it passes `--update-snapshots=none`, so a missing
// baseline fails rather than being written. An update run writes the manifest from the probe and
// passes `--update-snapshots=all`. None of the caller's variables enter the image.
// web/src/design/pinnedRun.test.ts plants each refusal through main() with fake processes.
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolvedPlaywrightCore } from './visual-environment.mjs'

/** The pin and the manifest, from web/. */
export const PIN_FILE = 'e2e/visual-image.json'
export const MANIFEST = 'e2e/visual-environment.json'
/** What a compare run holds this run's environment to. */
export const COMPARED = ['image', 'platform', 'playwright', 'browser', 'renderer', 'fonts']
export const PIN_KEYS = ['image', 'platform', 'playwright', 'tag', 'index']
export const PLATFORM = 'linux/amd64'
/** The image's repository: the pin names it with its digest, and nothing else names it. */
const REPOSITORY = 'mcr.microsoft.com/playwright'
const IMAGE = /^mcr\.microsoft\.com\/playwright@sha256:[0-9a-f]{64}$/
const DIGEST = /^sha256:[0-9a-f]{64}$/
/** The image's browser server, as it announces itself: loopback, nowhere else. */
export const LOOPBACK = /^ws:\/\/127\.0\.0\.1:\d+\/$/
const SERVER_START_MS = 60_000
const SERVER_STOP_MS = 15_000
const SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
const ELSEWHERE =
  'Elsewhere they run on CI: merge.yml compares them, and the Refresh visual baselines workflow ' +
  '(.github/workflows/visual-baselines.yml) regenerates them.'

export class Refusal extends Error {
  constructor(message, status = 2) {
    super(message)
    this.status = status
  }
}

const tail = (text) => String(text ?? '').trim().split('\n').slice(-5).join('\n').slice(-600)

/** The Playwright versions the lock resolves for `@playwright/test`: its own, its `playwright`'s,
 *  and that one's `playwright-core` (nested when the top level holds another). */
export function lockVersions(lock) {
  const packages = lock?.packages ?? {}
  return {
    test: packages['node_modules/@playwright/test']?.version,
    playwright: packages['node_modules/playwright']?.version,
    core:
      packages['node_modules/playwright/node_modules/playwright-core']?.version ??
      packages['node_modules/playwright-core']?.version,
  }
}

/** What is wrong with the pin, held to the lock. */
export function checkPin(pin, lock) {
  const problems = []
  if (!pin || typeof pin !== 'object') return [`${PIN_FILE} is not an object`]
  const keys = Object.keys(pin).sort()
  if (JSON.stringify(keys) !== JSON.stringify([...PIN_KEYS].sort())) {
    problems.push(`${PIN_FILE} holds ${keys.join(', ')}, not ${PIN_KEYS.join(', ')}`)
  }
  if (!IMAGE.test(String(pin.image))) {
    problems.push(`${PIN_FILE}'s image ${pin.image} is not ${REPOSITORY} named by its sha256 digest`)
  }
  if (pin.platform !== PLATFORM) problems.push(`${PIN_FILE}'s platform is ${pin.platform}, not ${PLATFORM}`)
  if (pin.tag !== `v${pin.playwright}-noble`) {
    problems.push(`${PIN_FILE}'s tag ${pin.tag} is not v${pin.playwright}-noble`)
  }
  if (!DIGEST.test(String(pin.index))) problems.push(`${PIN_FILE}'s index ${pin.index} is no digest`)
  const locked = lockVersions(lock)
  for (const [name, version] of Object.entries(locked)) {
    if (version !== pin.playwright) {
      problems.push(`the lock resolves ${name} ${version}, and ${PIN_FILE} pins Playwright ${pin.playwright}`)
    }
  }
  return problems
}

/** What differs between the environment the baselines were made in and this run's. */
export function compareEnvironment(manifest, current, pin) {
  const problems = []
  const missing = COMPARED.filter((field) => manifest?.[field] === undefined)
  if (missing.length > 0) problems.push(`${MANIFEST}: missing ${missing.join(', ')}`)
  if (manifest?.image !== undefined && manifest.image !== pin.image) {
    problems.push(`${MANIFEST} names image ${manifest.image}, not the pinned ${pin.image}`)
    return problems
  }
  for (const field of COMPARED) {
    if (manifest?.[field] === undefined) continue
    const made = JSON.stringify(manifest[field])
    const now = JSON.stringify(current?.[field])
    if (made !== now) problems.push(`${field}: the baselines were made with ${made}, this run has ${now}`)
  }
  return problems
}

/** The folders the container sees at their own paths, read-only: the checkout, whose
 *  node_modules hold the locked playwright-core, and this Node's prefix. */
export function hostMounts({ root, execPath, exists = fs.existsSync }) {
  if (!exists(path.join(root, 'node_modules', '@playwright', 'test'))) {
    throw new Refusal(`${path.join(root, 'node_modules')} has no @playwright/test: run \`npm ci\` first`)
  }
  return [root, path.dirname(path.dirname(execPath))]
}

/** `docker run` of one command in the pinned image: the probe with no network, and the browser
 *  server, which is named and keeps its stdin open (it exits when that closes), on the host's. */
export function dockerArgs({ root, mounts, pin, uid, gid, execPath, network, name, command }) {
  const env = { HOME: '/tmp', PATH: `${path.dirname(execPath)}:${SYSTEM_PATH}`, LANG: 'C.UTF-8', TZ: 'Etc/UTC', VISUAL_IMAGE: pin.image }
  return [
    'run',
    '--rm',
    ...(name ? ['--name', name, '--interactive'] : []),
    '--init',
    '--platform', pin.platform,
    '--network', network,
    '--ipc=host',
    '--user', `${uid}:${gid}`,
    ...Object.entries(env).flatMap(([key, value]) => ['-e', `${key}=${value}`]),
    ...mounts.flatMap((mount) => ['-v', `${mount}:${mount}:ro`]),
    '-w', path.join(root, 'web'),
    pin.image,
    ...command,
  ]
}

/** The `playwright` command `@playwright/test` installs: its package's `bin`, which its `exports`
 *  keep out of reach of a plain resolve. */
export function playwrightCli(web) {
  const manifest = createRequire(path.join(web, 'package.json')).resolve('@playwright/test/package.json')
  return path.join(path.dirname(manifest), JSON.parse(fs.readFileSync(manifest, 'utf8')).bin.playwright)
}

/** The command of the playwright-core that `@playwright/test` loads, which serves the browser. */
export function playwrightCoreCli(web) {
  const manifest = resolvedPlaywrightCore(path.join(web, 'package.json'))
  return path.join(path.dirname(manifest), JSON.parse(fs.readFileSync(manifest, 'utf8')).bin['playwright-core'])
}

function runProcess(command, args, { capture = false, env, cwd } = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit', env, cwd })
    let stdout = ''
    let stderr = ''
    child.stdout?.on('data', (chunk) => (stdout += chunk))
    child.stderr?.on('data', (chunk) => (stderr += chunk))
    child.on('error', (error) => resolve({ status: null, error, stdout, stderr }))
    child.on('close', (status, signal) => resolve({ status, signal, stdout, stderr }))
  })
}

/** Starts the image's browser server and resolves with the endpoint it announces, and a stop that
 *  closes its stdin and waits for it (killing it when it does not go). */
export function startServer(command, args, { log }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'inherit'] })
    let out = ''
    let listening = false
    const exited = new Promise((done) => child.on('close', done))
    const stop = async () => {
      child.stdin.end()
      const timer = setTimeout(() => child.kill('SIGKILL'), SERVER_STOP_MS)
      await exited
      clearTimeout(timer)
    }
    const fail = (message) => {
      clearTimeout(timer)
      child.kill('SIGKILL')
      reject(new Refusal(message))
    }
    const timer = setTimeout(() => fail(`the pinned browser server did not listen within ${SERVER_START_MS / 1000} s: ${tail(out)}`), SERVER_START_MS)
    child.on('error', (error) => fail(error.code === 'ENOENT' ? `The pinned browser needs docker, and none was found. ${ELSEWHERE}` : `docker could not be started: ${error.message}`))
    child.on('close', (status) => listening || fail(`the pinned browser server exited (${status}) before it listened: ${tail(out)}`))
    child.stdout.on('data', (chunk) => {
      if (listening) return log(String(chunk).trimEnd())
      out += chunk
      const found = /Listening on (\S+)\r?\n/.exec(out)
      if (!found) return
      listening = true
      clearTimeout(timer)
      resolve({ endpoint: found[1], stop })
    })
  })
}

function parseArgs(argv) {
  const split = argv.indexOf('--')
  const own = split < 0 ? argv : argv.slice(0, split)
  const passed = split < 0 ? [] : argv.slice(split + 1)
  for (const arg of own) {
    if (arg !== '--update') throw new Refusal(`unknown argument ${arg} (usage: node e2e/run-pinned.mjs [--update] -- <playwright test arguments>)`)
  }
  if (passed.length === 0) throw new Refusal('name what to run after `--`: the specs and any playwright test option')
  const snapshot = passed.find((arg) => arg.startsWith('--update-snapshots') || arg === '-u')
  if (snapshot) throw new Refusal(`${snapshot}: run-pinned decides that (--update for a refresh)`)
  return { update: own.includes('--update'), passed }
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

export async function main(argv = [], options = {}) {
  const web = options.web ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const root = path.dirname(web)
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const execPath = options.execPath ?? process.execPath
  const uid = options.uid ?? process.getuid?.() ?? 0
  const gid = options.gid ?? process.getgid?.() ?? 0
  const hostEnv = options.env ?? process.env
  const log = options.log ?? ((line) => console.log(line))
  const mountsOf = options.mounts ?? (() => hostMounts({ root, execPath }))
  const cli = options.cli ?? (() => playwrightCli(web))
  const coreCli = options.coreCli ?? (() => playwrightCoreCli(web))
  const run = options.run ?? runProcess
  const serve = options.serve ?? ((command, args) => startServer(command, args, { log }))

  try {
    const { update, passed } = parseArgs(argv)
    if (platform !== 'linux' || arch !== 'x64') {
      throw new Refusal(`The pinned browser runs only on a linux/amd64 host for now; this host is ${platform}/${arch}. ${ELSEWHERE}`)
    }
    const pin = readJson(path.join(web, PIN_FILE))
    const pinProblems = checkPin(pin, readJson(path.join(root, 'package-lock.json')))
    if (pinProblems.length > 0) throw new Refusal(pinProblems.join('\n'))
    const mounts = mountsOf()
    const container = (command, more) => dockerArgs({ root, mounts, pin, uid, gid, execPath, command, ...more })

    const probe = await run('docker', container([execPath, 'e2e/visual-environment.mjs'], { network: 'none' }), { capture: true })
    if (probe.error) {
      throw new Refusal(probe.error.code === 'ENOENT' ? `The pinned browser needs docker, and none was found. ${ELSEWHERE}` : `docker could not be started: ${probe.error.message}`)
    }
    if (probe.status !== 0) throw new Refusal(`the environment probe failed (exit ${probe.status}): ${tail(probe.stderr || probe.stdout)}`)
    let current
    try {
      current = JSON.parse(probe.stdout)
    } catch {
      throw new Refusal(`the environment probe printed no JSON: ${tail(probe.stdout)}`)
    }
    log(`pinned environment: ${JSON.stringify(current)}`)

    if (update) {
      fs.writeFileSync(path.join(web, MANIFEST), `${JSON.stringify(current, null, 2)}\n`)
      log(`wrote ${MANIFEST}`)
    } else {
      const manifest = readJson(path.join(web, MANIFEST))
      if (!manifest) {
        throw new Refusal(`There is no environment manifest at web/${MANIFEST}, so no baseline was made in the pinned image. Run the Refresh visual baselines workflow and commit its artifact.`, 1)
      }
      const problems = compareEnvironment(manifest, current, pin)
      if (problems.length > 0) {
        throw new Refusal(`This run's environment is not the one the baselines were made in; regenerate them with the Refresh visual baselines workflow:\n${problems.join('\n')}`, 1)
      }
    }

    const name = `pinned-browser-${process.pid}`
    const server = await serve('docker', container([execPath, coreCli(), 'run-server', '--host', '127.0.0.1', '--port', '0'], { network: 'host', name }))
    let suite
    try {
      if (!LOOPBACK.test(server.endpoint)) throw new Refusal(`the pinned browser server listens on ${server.endpoint}, not on 127.0.0.1`)
      log(`pinned browser: ${server.endpoint}`)
      const env = { ...hostEnv, PW_TEST_CONNECT_WS_ENDPOINT: server.endpoint }
      suite = await run(execPath, [cli(), 'test', ...passed, update ? '--update-snapshots=all' : '--update-snapshots=none'], { env, cwd: web })
    } finally {
      await server.stop()
    }
    if (suite.error) throw new Refusal(`playwright could not be started: ${suite.error.message}`)
    if (suite.status !== 0) {
      log(`::error title=pinned browser::playwright test ${passed.join(' ')} failed against the pinned browser (exit ${suite.status ?? suite.signal})`)
      return suite.status ?? 1
    }
    log(update ? 'Rewritten against the pinned browser: review the diff before committing it.' : 'Matched against the pinned browser.')
    return 0
  } catch (error) {
    if (!(error instanceof Refusal)) throw error
    log(`::error title=pinned browser::${error.message.split('\n')[0]}`)
    log(error.message)
    return error.status
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2))
}
