// Runs the design detector over web/src and compares what it finds with the committed baseline
// (scripts/designDetectorBaseline.mjs), so a new finding fails and a fixed one lowers its line.
//
//     npm run design:detect                    compare; exit 1 on any difference
//     node scripts/designDetector.mjs --write  rewrite the baseline from this run (from web/)
//
// The detector is Impeccable, pinned exactly in the root package.json. Its npm entry point is a
// shim that runs the binary $IMPECCABLE_BIN names before anything else, then the platform
// package's, then one cached in the home folder, and downloads one when it finds none. This runs
// the binary the lockfile installed and nothing else: the @impeccable/cli-<os>-<arch> package, at
// the version the pinned package names.
// Telemetry is off (IMPECCABLE_NO_TELEMETRY and DO_NOT_TRACK); a run with both set opens no
// connection.
//
// It reads the tracked design system (DESIGN.md and .impeccable/design.json), so the type ramp,
// colours and radii it checks against are the repository's. Inline ignore comments are not
// honoured, and detector settings in .impeccable/config.json or config.local.json are refused:
// the baseline is the one list of accepted findings, so a local run and CI's find the same. The
// detector reads its project config from the folder it runs in, web/, and from nowhere else.
// Test files are left out, because their literals are the values a test asserts, not styling.

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { BASELINE_FILE, baselineLine, compareFindings } from './designDetectorBaseline.mjs'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SCANNED = 'src'
const CONFIGS = ['config.json', 'config.local.json']
const TEST_FILE = /\.test\.[cm]?[jt]sx?$/

/** The package that carries the detector's binary for this machine, as the shim names it. */
export function platformPackage(platform = process.platform, arch = process.arch) {
  const os = { darwin: 'darwin', linux: 'linux', win32: 'windows' }[platform] ?? platform
  return `@impeccable/cli-${os}-${arch}`
}

/** The text of a file under web/, or undefined when there is none. */
function readWeb(rel) {
  try {
    return fs.readFileSync(path.join(WEB, rel), 'utf8')
  } catch {
    return undefined
  }
}

/** How the detector runs: the locked binary, its arguments, and telemetry off. Or the sentence
 *  that says why it cannot. `require` and `exists` stand in for the installed tree in a test. */
export function detectorCommand({
  require = createRequire(import.meta.url),
  exists = fs.existsSync,
  platform = process.platform,
  arch = process.arch,
} = {}) {
  const name = platformPackage(platform, arch)
  let detector
  try {
    detector = require('impeccable/package.json')
  } catch {
    return { problem: 'impeccable is not installed: run `npm ci` at the repository root' }
  }
  const wanted = detector.optionalDependencies?.[name]
  if (!wanted) return { problem: `impeccable ${detector.version} ships no binary for this machine (${name})` }
  let manifest
  try {
    manifest = require.resolve(`${name}/package.json`)
  } catch {
    return { problem: `${name} is not installed: run \`npm ci\` at the repository root` }
  }
  const { version } = require(`${name}/package.json`)
  if (version !== wanted) return { problem: `${name} is ${version}, and impeccable ${detector.version} names ${wanted}` }
  const bin = path.join(path.dirname(manifest), 'bin', platform === 'win32' ? 'impeccable.exe' : 'impeccable')
  if (!exists(bin)) return { problem: `${name} has no binary at ${bin}` }
  return {
    bin,
    args: ['detect', '--json', '--no-inline-ignores', SCANNED],
    cwd: WEB,
    env: { IMPECCABLE_NO_TELEMETRY: '1', DO_NOT_TRACK: '1' },
  }
}

/** Every detector setting in web/.impeccable that would drop a finding before the baseline sees it.
 *  `read` gives a file's text under web/, or undefined. */
export function configProblems(read = readWeb) {
  const out = []
  for (const name of CONFIGS) {
    const rel = `.impeccable/${name}`
    const text = read(rel)
    if (text === undefined) continue
    let config
    try {
      config = JSON.parse(text)
    } catch {
      out.push(`${rel} is not JSON, so what it would drop cannot be told`)
      continue
    }
    if (config && typeof config === 'object' && 'detector' in config) {
      out.push(
        `${rel} has detector settings, which would drop findings before the baseline sees them: ` +
          `list accepted findings in ${BASELINE_FILE} instead`,
      )
    }
  }
  return out
}

/** The findings of one `detect --json` run, each named relative to web/, test files left out. */
export function readRun({ status, stdout, stderr }, webRoot = WEB) {
  const said = String(stderr ?? '').trim()
  if (status === null) return { findings: [], problems: ['the detector stopped without an exit status'] }
  if (status === 1) return { findings: [], problems: [`the detector could not scan web/${SCANNED} (exit 1): ${said}`] }
  if (status !== 0 && status !== 2) return { findings: [], problems: [`the detector exited ${status}: ${said}`] }
  let printed
  try {
    printed = JSON.parse(stdout)
  } catch {
    return { findings: [], problems: [`the detector printed no JSON on stdout: ${String(stdout).slice(0, 200)}`] }
  }
  if (!Array.isArray(printed)) return { findings: [], problems: ['the detector printed JSON that is not a list of findings'] }
  const root = path.join(webRoot, SCANNED) + path.sep
  const findings = []
  const problems = []
  printed.forEach((f, i) => {
    if (typeof f?.file !== 'string' || typeof f?.antipattern !== 'string') {
      problems.push(`finding ${i + 1} has no file and rule: ${JSON.stringify(f)}`)
      return
    }
    if (!f.file.startsWith(root)) {
      problems.push(`a finding names ${f.file}, which is outside web/${SCANNED}`)
      return
    }
    const file = path.relative(webRoot, f.file).split(path.sep).join('/')
    if (TEST_FILE.test(file)) return
    findings.push({ file, rule: f.antipattern, line: f.line })
  })
  return { findings, problems }
}

/** The baseline that lists exactly these findings. */
export function baselineText(findings) {
  const counts = new Map()
  for (const { file, rule } of findings) {
    const key = JSON.stringify([file, rule])
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return [...counts.keys()]
    .sort()
    .map((key) => {
      const [file, rule] = JSON.parse(key)
      return baselineLine({ file, rule, count: counts.get(key) })
    })
    .map((line) => `${line}\n`)
    .join('')
}

/** The variables the detector's binary may see: where to find programs and write temporary files,
 *  the locale, and the telemetry switches. Nothing else of the caller's environment reaches it, so
 *  no token (CI's, a developer's credentials, an app session's) is handed to a third-party binary
 *  that can open connections. */
const PASSED_THROUGH = ['PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'SystemRoot', 'USERPROFILE']

export function detectorEnv(command, base = process.env) {
  const env = {}
  for (const name of PASSED_THROUGH) if (typeof base[name] === 'string') env[name] = base[name]
  return { ...env, ...command.env }
}

function main(argv) {
  const write = argv.includes('--write')
  const refused = configProblems()
  const command = detectorCommand()
  if (command.problem) refused.push(command.problem)
  if (refused.length) {
    for (const line of refused) console.error(`design detector: ${line}`)
    return 1
  }
  const result = spawnSync(command.bin, command.args, {
    cwd: command.cwd,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: detectorEnv(command),
  })
  if (result.error) {
    console.error(`design detector: ${command.bin} did not run: ${result.error.message}`)
    return 1
  }
  const { findings, problems } = readRun(result)
  if (problems.length === 0 && write) {
    fs.writeFileSync(path.join(WEB, BASELINE_FILE), baselineText(findings))
    console.log(`design detector: wrote ${findings.length} findings to ${BASELINE_FILE}`)
    return 0
  }
  const baseline = readWeb(BASELINE_FILE)
  if (baseline === undefined) problems.push(`${BASELINE_FILE} is missing: write it with \`node scripts/designDetector.mjs --write\` from web/`)
  const out = [...problems, ...(problems.length ? [] : compareFindings(findings, baseline))]
  if (out.length) {
    for (const line of out) console.error(`design detector: ${line}`)
    return 1
  }
  console.log(`design detector: ${findings.length} findings over web/${SCANNED}, each in ${BASELINE_FILE}`)
  return 0
}

// Through the real path: Node resolves links in import.meta.url, and not in argv.
if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  process.exitCode = main(process.argv.slice(2))
}
