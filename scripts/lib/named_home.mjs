// The gateway a dev tool drives, found from the scratch home it was NAMED, never from a default
// URL. Port 10000 is the install's own, so a capture or a probe aimed at "whatever answers" there
// acts on the install's data.
//
// The rule is written once, in Python (harness/named_home.py): it asks the product's own home
// resolver whether the name is the default home, reads the record a gateway writes into its home
// once it listens, and signs in through that home's local secret. This module only runs it and
// reads its one JSON line, so a JavaScript tool cannot drift from a Python one.
//
//   const gateway = scratchGatewayOrExit(homeArg())   // { url, token, home, pid }
//   await signIn(context, gateway)                     // a Playwright browser context
//
// The home is the one passed (a tool's --home), else $PERSONALCLAW_HOME. The Python that runs
// the rule is $PERSONALCLAW_PY when set, else this checkout's .venv.

import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

/** A dev tool refusing to run. Its message is the sentence to print. */
export class Refused extends Error {}

function python() {
  if (process.env.PERSONALCLAW_PY) return process.env.PERSONALCLAW_PY
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'python')
  if (existsSync(venv)) return venv
  throw new Refused(
    `No Python with this checkout's dev dependencies at ${venv}: make one ` +
    '(python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"), or set PERSONALCLAW_PY.',
  )
}

/** A home as the person meant it: a relative name is relative to where they ran the tool, not
 *  to the checkout the rule runs in. `~` is left for the rule to expand. */
function absolute(home) {
  return home.startsWith('~') ? home : path.resolve(home)
}

/** `--home DIR` or `--home=DIR` from a tool's arguments, if it was given. */
export function homeArg(argv = process.argv.slice(2)) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--home') return argv[i + 1]
    if (argv[i].startsWith('--home=')) return argv[i].slice('--home='.length)
  }
  return undefined
}

/** The running gateway of the named scratch home: `{ url, token, home, pid }`. Throws `Refused`
 *  with the sentence harness/named_home.py gave when there is none this tool may drive. */
export function scratchGateway(home) {
  const named = home || process.env.PERSONALCLAW_HOME
  const args = ['-m', 'harness.named_home']
  if (named) args.push('--home', absolute(named))
  const run = spawnSync(python(), args, { cwd: REPO_ROOT, encoding: 'utf8' })
  if (run.error) throw run.error
  if (run.status !== 0) {
    throw new Refused((run.stderr || run.stdout || `exit status ${run.status}`).trim())
  }
  return JSON.parse(run.stdout.trim().split('\n').pop())
}

/** `scratchGateway`, or end the tool with its sentence before anything has started. */
export function scratchGatewayOrExit(home) {
  try {
    return scratchGateway(home)
  } catch (err) {
    console.error(err instanceof Refused ? err.message : String(err))
    process.exit(1)
  }
}

/** Sign a Playwright browser context in, the way the gateway's sign-in link does: `/?token=`
 *  sets the session cookie. Through the context's own request client, which shares its cookies,
 *  so no page is opened (a recording context would record it). */
export async function signIn(context, gateway) {
  const answer = await context.request.get(`${gateway.url}/?token=${encodeURIComponent(gateway.token)}`)
  if (!answer.ok()) {
    throw new Refused(`the gateway of ${gateway.home} did not sign this browser in: HTTP ${answer.status()}`)
  }
}
