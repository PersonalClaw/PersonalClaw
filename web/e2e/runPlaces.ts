import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmdirSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { homedir, tmpdir } from 'node:os'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

/** The port a default install's gateway listens on. A browser run never takes it, for either
 *  server: the suite drives real turns and signs a browser in, so it writes whatever home the
 *  gateway it reaches serves. */
export const INSTALL_PORT = 10000

/** Where one browser run lives: its gateway's home and its two ports. */
export interface RunPlaces {
  /** The gateway's home, made empty for this run. '' for a run that starts no servers. */
  home: string
  /** The preview server's port. */
  port: number
  /** The harness gateway's port. */
  gatewayPort: number
  /** Whether THIS call made the home, so this process removes it when the run ends. */
  madeHome: boolean
  /** The cookie jar `auth.setup.ts` writes and every spec reads: inside the run's home, so it
   *  holds only this run's sign-in and goes with the home. `STORAGE_STATE` names another. */
  storageState: string
  /** Where this run's specs write their output: a folder of its own under `test-results/`, which
   *  CI uploads. Playwright clears it when the run starts, so it clears only this run's. */
  outputDir: string
  /** Whether THIS call made the output folder, so this process removes it at exit if it is empty. */
  madeOutputDir: boolean
}

type Env = Record<string, string | undefined>

/** A port the OS says is free now: bind port 0 on loopback and read back what it gave. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => (port ? resolve(port) : reject(new Error('the OS handed back no port'))))
    })
  })
}

const set = (value: string | undefined): value is string => value !== undefined && value !== ''

/**
 * The harness gateway's home, as a spec that writes it with the CLI must take it.
 *
 * Refused, before anything starts on it:
 * - `PW_E2E_HOME` unset or empty: a CLI handed no home falls back to the default one and writes
 *   that instead;
 * - `PW_E2E_HOME` at, or under, the default home (`~/.personalclaw`). Both paths are compared
 *   after realpath of the deepest folder of each that exists, so a symlink into the default home,
 *   a `..` spelling and a folder not made yet are all caught. Only path names are read, never
 *   what the folders hold.
 */
export function harnessHome(env: Env): string {
  const home = env.PW_E2E_HOME
  if (!set(home)) {
    throw new Error('PW_E2E_HOME is unset: this run has no harness home, and a CLI handed none would write the default home')
  }
  const real = (path: string): string => {
    let head = resolve(path)
    const tail: string[] = []
    while (!existsSync(head) && dirname(head) !== head) {
      tail.unshift(basename(head))
      head = dirname(head)
    }
    try { head = realpathSync(head) } catch { /* the resolved path stands */ }
    return join(head, ...tail)
  }
  const defaultHome = real(join(homedir(), '.personalclaw'))
  const rel = relative(defaultHome, real(home))
  if (rel === '' || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel))) {
    throw new Error(`PW_E2E_HOME (${home}) is the default PersonalClaw home or inside it; a browser run never writes that home`)
  }
  return home
}

function named(env: Env, key: string): number | null {
  if (!set(env[key])) return null
  const port = Number(env[key])
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`${key}=${env[key]} is not a port`)
  if (port === INSTALL_PORT) throw new Error(`${key}=${port} is the install's own port; a browser run never takes it`)
  return port
}

/**
 * The home and ports of this run, chosen once.
 *
 * The runner loads the config first and chooses: a home made with mkdtemp under the temp dir, and
 * a free port for each server unless `PW_PORT` or `PW_GATEWAY_PORT` names one. It writes all three
 * to `env` (`PW_E2E_HOME`, `PW_PORT`, `PW_GATEWAY_PORT`), so each worker that loads the config
 * again, and each server and spec it starts, reads the same three and makes nothing. A run that
 * starts no servers (`PW_NO_SERVER`) gets no home unless `PW_E2E_HOME` names one.
 *
 * It also gives the run a cookie jar in its home (`STORAGE_STATE`, which `auth.setup.ts` writes)
 * and an output folder of its own under `test-results/` (`PW_OUTPUT_DIR`), handed on the same way,
 * so two runs never sign each other's specs out or clear each other's output. A run with no home
 * keeps the jar at `e2e/.auth/state.json`, as before: it drives a gateway pair of its own.
 */
export async function choosePlaces(
  env: Env,
  opts: { tmp?: string; pickPort?: () => Promise<number>; testResults?: string } = {},
): Promise<RunPlaces> {
  const pick = opts.pickPort ?? freePort
  let madeHome = false
  // A named home goes through the same guard a spec uses, so the gateway never boots on the
  // default home either.
  let home = set(env.PW_E2E_HOME) ? harnessHome(env) : ''
  if (!home && !set(env.PW_NO_SERVER)) {
    home = mkdtempSync(join(opts.tmp ?? tmpdir(), 'personalclaw-e2e-'))
    madeHome = true
  }

  const taken = new Set<number>([INSTALL_PORT])
  const choose = async (key: string): Promise<number> => {
    const given = named(env, key)
    if (given !== null) {
      taken.add(given)
      return given
    }
    for (let tries = 0; tries < 20; tries++) {
      const port = await pick()
      if (!taken.has(port)) {
        taken.add(port)
        return port
      }
    }
    throw new Error(`no free port for ${key} after 20 tries`)
  }
  const port = await choose('PW_PORT')
  const gatewayPort = await choose('PW_GATEWAY_PORT')

  const storageState = set(env.STORAGE_STATE)
    ? env.STORAGE_STATE
    : home ? join(home, '.auth', 'state.json') : 'e2e/.auth/state.json'
  let outputDir = set(env.PW_OUTPUT_DIR) ? env.PW_OUTPUT_DIR : ''
  let madeOutputDir = false
  if (!outputDir) {
    const results = opts.testResults ?? join(process.cwd(), 'test-results')
    mkdirSync(results, { recursive: true })
    outputDir = mkdtempSync(join(results, 'run-'))
    madeOutputDir = true
  }

  if (home) env.PW_E2E_HOME = home
  env.PW_PORT = String(port)
  env.PW_GATEWAY_PORT = String(gatewayPort)
  env.STORAGE_STATE = storageState
  env.PW_OUTPUT_DIR = outputDir
  return { home, port, gatewayPort, madeHome, storageState, outputDir, madeOutputDir }
}

/** Remove what this process made for the run: the home (and the cookie jar in it), and the
 *  output folder if the run wrote nothing there. Output a spec wrote stays, for CI to upload. A
 *  worker, and a run whose home or output folder the caller named, removes nothing it was given. */
export function cleanUpRun(places: RunPlaces): void {
  if (places.madeHome && places.home) rmSync(places.home, { recursive: true, force: true })
  if (places.madeOutputDir && places.outputDir) {
    try { rmdirSync(places.outputDir) } catch { /* not empty: the run's output stays */ }
  }
}

/** `cleanUpRun` when this process ends. */
export function cleanUpRunAtExit(places: RunPlaces): void {
  if (!places.madeHome && !places.madeOutputDir) return
  process.once('exit', () => cleanUpRun(places))
}
