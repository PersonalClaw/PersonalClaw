import { chmodSync, existsSync, mkdirSync, mkdtempSync, realpathSync, rmdirSync, rmSync, statSync } from 'node:fs'
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
  /** The cookie jar `auth.setup.ts` writes and every spec reads, in `authDir`: this run's sign-in
   *  only, removed with the home. `STORAGE_STATE` names another. */
  storageState: string
  /** The jar's folder: BESIDE the run's home (`authDirFor`), never in it. In the home it was a
   *  path no snapshot claims, and the Doctor's state inventory reported the home degraded. */
  authDir: string
  /** Whether THIS call made `authDir`, so this process removes it with the home. */
  madeAuthDir: boolean
  /** The gateway's `HOME`: a folder of the run's own BESIDE its home (`userHomeFor`), so what the
   *  gateway finds through the home folder (other tools' setups, the shared skills folder, the
   *  Hugging Face folder) is this run's, never the machine's. Beside rather than in the home for
   *  the jar's reason: the Doctor reads a folder in the home that no store claims as degraded.
   *  '' for a run with no home. */
  userHome: string
  /** Whether THIS call made `userHome`, so this process removes it with the home. */
  madeUserHome: boolean
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

/** The folder that holds a run's cookie jar: a sibling of the run's home, named for it. Not under
 *  the `personalclaw-e2e-` prefix, so nothing that counts homes counts it. */
export function authDirFor(home: string): string {
  return join(dirname(home), `sign-in-for-${basename(home)}`)
}

/** The folder the gateway runs with as `HOME`: a sibling of the run's home, named for it, as the
 *  jar's folder is. */
export function userHomeFor(home: string): string {
  return join(dirname(home), `user-home-for-${basename(home)}`)
}

/** Every variable the gateway reads to find a folder outside its home. Each is taken out of the
 *  gateway's environment (`gatewayEnvPrefix`), so the gateway finds those folders under the run's
 *  `HOME` or not at all, never where the machine's shell points them.
 *  `tests/rails/test_e2e_gateway_home_is_the_runs_tree.py` holds this list to its own. */
export const GATEWAY_UNSET = [
  // other tools' setups and the Hugging Face folder (`outside_home`, `onboarding_import`)
  'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'HF_HOME', 'HF_HUB_CACHE', 'HF_TOKEN_PATH',
  // the XDG folders, and zsh's startup folder (`owner_only`, `sandbox`, `outside_home`)
  'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR',
  'ZDOTDIR',
  // tmux's socket folder for a home whose own socket path is too long (`tmux_substrate`)
  'TMUX_TMPDIR',
  // PersonalClaw's own overrides that name a folder or file outside the home
  'PERSONALCLAW_PROJECT_DIR', 'PERSONALCLAW_TRACE_DIR', 'PERSONALCLAW_EXTRA_BIN_PATHS',
  'PERSONALCLAW_FIRST_PARTY_APPS_DIR', 'PERSONALCLAW_ARTIFACT_BUILD_ROOT',
  'PERSONALCLAW_CEILING_FILE', 'PERSONALCLAW_COMPUTER_USE_ENABLE_FILE',
  'PERSONALCLAW_LOCAL_MODEL_APPS_DIR',
] as const

/** What makes a variable's name credential-shaped to the gateway's secrets read
 *  (`secrets_vault._host_secret_names`, through `workflows.secrets.matches_secret_hint`): any of
 *  these anywhere in the lowercased name. Every variable so named is taken out of the gateway's
 *  environment (`gatewayEnvPrefix`), so Settings › Secrets lists nothing inherited from whoever
 *  started the run, and reads the same on every machine.
 *  `tests/rails/test_e2e_gateway_home_is_the_runs_tree.py` holds these lists to the read's own. */
export const GATEWAY_CREDENTIAL_HINTS = [
  'api_key', 'apikey', 'token', 'secret', 'password', 'passwd', 'credential', 'private_key',
  'privatekey', 'auth', 'bearer', 'session_key', 'access_key', 'refresh', 'signing', 'webhook',
] as const

/** Hints that match a whole word of the lowercased name (split at anything not a letter or digit),
 *  with an optional index: `PAT` and `PAT2`, never `PATH`. */
export const GATEWAY_CREDENTIAL_WORD_HINTS = ['pat'] as const

/** Taken out by name too, whatever the hints become: the agent socket that signs as whoever
 *  started the run. */
export const GATEWAY_CREDENTIAL_NAMES = ['SSH_AUTH_SOCK'] as const

/** An awk program that prints, on one line, every name in its environment the secrets read calls a
 *  credential. It reads the names from `ENVIRON`, never by parsing `env`'s output, so a value that
 *  spans lines cannot pass for a name. A credential-shaped name the shell cannot unset (one with a
 *  character no shell name has) fails the run, named: some shells hand such a variable on to the
 *  gateway, and quietly keeping it would list it in Settings › Secrets again. */
function credentialNamesAwk(): string {
  return [
    'BEGIN {',
    `nh = split("${GATEWAY_CREDENTIAL_HINTS.join(' ')}", hint, " ");`,
    `nw = split("${GATEWAY_CREDENTIAL_WORD_HINTS.join(' ')}", word, " ");`,
    'for (name in ENVIRON) {',
    'low = tolower(name); hit = 0;',
    'for (i = 1; i <= nh; i++) if (index(low, hint[i])) hit = 1;',
    'np = split(low, part, /[^a-z0-9]+/);',
    'for (j = 1; j <= np; j++) for (i = 1; i <= nw; i++) if (part[j] ~ ("^" word[i] "[0-9]*$")) hit = 1;',
    'if (!hit) continue;',
    'if (name ~ /^[A-Za-z_][A-Za-z0-9_]*$/) { out = out " " name } else { odd = odd " [" name "]" }',
    '}',
    'if (odd != "") {',
    'print "PERSONALCLAW_E2E_FATAL: credential-shaped variables no shell can unset would reach the gateway, unset them before the run:" odd > "/dev/stderr";',
    'exit 1',
    '}',
    'print out',
    '}',
  ].join(' ')
}

/** The shell lines the gateway command runs before it starts the gateway, with `$D` already the
 *  run's home: they find the run's `HOME` folder beside it (refused, loudly, when it is not there),
 *  take every `GATEWAY_UNSET` variable and every credential-shaped one (`GATEWAY_CREDENTIAL_*`) out
 *  of the environment, and export `HOME`. */
export function gatewayEnvPrefix(): string {
  return [
    'U="$(dirname "$D")/user-home-for-$(basename "$D")";',
    '[ -d "$U" ] || { echo "PERSONALCLAW_E2E_FATAL: the run\'s HOME folder is not there ($U)" >&2; exit 1; };',
    `K="$(awk '${credentialNamesAwk()}')" || exit 1;`,
    `unset ${GATEWAY_UNSET.join(' ')} ${GATEWAY_CREDENTIAL_NAMES.join(' ')} $K K;`,
    'HOME="$U"; export HOME;',
  ].join(' ')
}

/** Make a run-owned folder; one there already (a named home that did not end cleanly) is taken. */
function makeFolder(dir: string): boolean {
  try {
    mkdirSync(dir, { mode: 0o700 })
    return true
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'EEXIST' && statSync(dir).isDirectory()) return false
    throw err
  }
}

/** Make the jar folder, 0700: it holds a live owner token for this run's gateway. A folder there
 *  already (a run on a named home that did not end cleanly) is taken and closed to 0700. */
function makeAuthDir(dir: string): boolean {
  try {
    mkdirSync(dir, { mode: 0o700 })
    chmodSync(dir, 0o700) // the umask may have narrowed the mode, never widened it; say it exactly
    return true
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'EEXIST' && statSync(dir).isDirectory()) {
      chmodSync(dir, 0o700)
      return false
    }
    throw err
  }
}

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
 * It also gives the run a cookie jar in a 0700 folder beside its home (`STORAGE_STATE`, which
 * `auth.setup.ts` writes; refused loudly when the folder cannot be made)
 * and an output folder of its own under `test-results/` (`PW_OUTPUT_DIR`), handed on the same way,
 * so two runs never sign each other's specs out or clear each other's output. A run with no home
 * keeps the jar at `e2e/.auth/state.json`, as before: it drives a gateway pair of its own.
 */
export async function choosePlaces(
  env: Env,
  opts: {
    tmp?: string; pickPort?: () => Promise<number>; testResults?: string
    /** Makes the jar folder; the default makes it 0700. A test replaces it to make it fail. */
    makeAuthDir?: (dir: string) => boolean
  } = {},
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

  // A STORAGE_STATE already set is the runner's (a worker reads it back) or the caller's: its
  // folder is someone else's to make and remove.
  let authDir = set(env.STORAGE_STATE) ? dirname(env.STORAGE_STATE) : home ? authDirFor(home) : 'e2e/.auth'
  let madeAuthDir = false
  if (!set(env.STORAGE_STATE) && home) {
    try {
      madeAuthDir = (opts.makeAuthDir ?? makeAuthDir)(authDir)
    } catch (err) {
      // Loudly, and before anything starts: with no jar folder the sign-in cannot be saved, and
      // every spec would run signed out against a gateway that shows them the onboarding screen.
      if (madeHome) rmSync(home, { recursive: true, force: true })
      throw new Error(`could not make the folder for this run's cookie jar (${authDir}): ${(err as Error).message}`)
    }
  }
  const storageState = set(env.STORAGE_STATE) ? env.STORAGE_STATE : join(authDir, 'state.json')
  authDir = dirname(storageState)
  // The gateway's HOME, beside the home. A worker finds the runner's there already and makes none.
  const userHome = home ? userHomeFor(home) : ''
  let madeUserHome = false
  if (userHome) {
    try {
      madeUserHome = makeFolder(userHome)
    } catch (err) {
      if (madeHome) rmSync(home, { recursive: true, force: true })
      if (madeAuthDir) rmSync(authDir, { recursive: true, force: true })
      throw new Error(`could not make the folder this run's gateway uses as HOME (${userHome}): ${(err as Error).message}`)
    }
  }
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
  return { home, port, gatewayPort, madeHome, storageState, authDir, madeAuthDir, userHome, madeUserHome, outputDir, madeOutputDir }
}

/** Remove what this process made for the run: the home, the cookie jar's folder beside it, and the
 *  output folder if the run wrote nothing there. Output a spec wrote stays, for CI to upload. A
 *  worker, and a run whose home or output folder the caller named, removes nothing it was given. */
export function cleanUpRun(places: RunPlaces): void {
  if (places.madeHome && places.home) rmSync(places.home, { recursive: true, force: true })
  if (places.madeAuthDir && places.authDir) rmSync(places.authDir, { recursive: true, force: true })
  if (places.madeUserHome && places.userHome) rmSync(places.userHome, { recursive: true, force: true })
  if (places.madeOutputDir && places.outputDir) {
    try { rmdirSync(places.outputDir) } catch { /* not empty: the run's output stays */ }
  }
}

/** `cleanUpRun` when this process ends. */
export function cleanUpRunAtExit(places: RunPlaces): void {
  if (!places.madeHome && !places.madeAuthDir && !places.madeUserHome && !places.madeOutputDir) return
  process.once('exit', () => cleanUpRun(places))
}
