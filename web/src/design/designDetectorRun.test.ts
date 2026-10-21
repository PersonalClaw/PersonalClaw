import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { compareFindings, parseBaseline } from '../../scripts/designDetectorBaseline.mjs'
import {
  baselineText,
  configProblems,
  detectorCommand,
  platformPackage,
  readRun,
  type Resolver,
} from '../../scripts/designDetector.mjs'

// ── The design detector's run: the locked binary, what a run says, and the baseline it is held to ─
// scripts/designDetector.mjs runs the detector over web/src and hands what it finds to the
// comparator (designDetectorRatchet.test.ts). These cases plant the detector's own output, so how
// a run is read is proven without the binary; CI's `npm run design:detect` step runs the real one.

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const REPO = path.resolve(WEB, '..')

/** One finding as the detector prints it under --json: an absolute path, and more keys than read. */
const printed = (rel: string, antipattern: string, line = 1) => ({
  antipattern,
  name: antipattern,
  severity: 'advisory',
  file: path.join(WEB, rel),
  line,
  advisory: true,
})

const run = (findings: unknown, status = 2, stderr = '') => ({
  status,
  stdout: typeof findings === 'string' ? findings : JSON.stringify(findings),
  stderr,
})

/** A stand-in installed tree: each manifest by id, each resolved to a folder of its own. */
function installed(manifests: Record<string, { version?: string; optionalDependencies?: Record<string, string> }>): Resolver {
  const missing = (id: string) => Object.assign(new Error(`Cannot find module '${id}'`), { code: 'MODULE_NOT_FOUND' })
  const read = (id: string) => {
    if (!(id in manifests)) throw missing(id)
    return manifests[id]
  }
  return Object.assign(read, {
    resolve: (id: string) => {
      read(id)
      return path.join('/installed/node_modules', id)
    },
  })
}

const LINUX = { platform: 'linux', arch: 'x64', exists: () => true }
const DETECTOR = { version: '4.6.0', optionalDependencies: { '@impeccable/cli-linux-x64': '0.1.15' } }

describe('design detector run: the binary', () => {
  it('names the platform package the lockfile installs for each machine CI and the team use', () => {
    expect(platformPackage('darwin', 'arm64')).toBe('@impeccable/cli-darwin-arm64')
    expect(platformPackage('linux', 'x64')).toBe('@impeccable/cli-linux-x64')
    expect(platformPackage('win32', 'x64')).toBe('@impeccable/cli-windows-x64')
  })

  it("runs this tree's locked platform binary, never the npm shim, with inline ignores and telemetry off", () => {
    const command = detectorCommand()
    expect(command.problem).toBeUndefined()
    const bin = command.bin!.split(path.sep).join('/')
    expect(bin).toContain(`/node_modules/${platformPackage()}/bin/impeccable`)
    expect(bin).not.toContain('impeccable/cli/bin')
    expect(command).toMatchObject({
      args: ['detect', '--json', '--no-inline-ignores', 'src'],
      cwd: WEB,
      env: { IMPECCABLE_NO_TELEMETRY: '1', DO_NOT_TRACK: '1' },
    })
  })

  it('runs the platform package at the version the pinned detector names', () => {
    const require = installed({ 'impeccable/package.json': DETECTOR, '@impeccable/cli-linux-x64/package.json': { version: '0.1.15' } })
    expect(detectorCommand({ require, ...LINUX }).bin).toBe(path.join('/installed/node_modules/@impeccable/cli-linux-x64', 'bin', 'impeccable'))
  })

  it('refuses to run, saying why, when the locked binary is not the one installed', () => {
    const problem = (manifests: Parameters<typeof installed>[0], exists = () => true) =>
      detectorCommand({ require: installed(manifests), ...LINUX, exists }).problem
    expect(problem({})).toBe('impeccable is not installed: run `npm ci` at the repository root')
    expect(problem({ 'impeccable/package.json': { version: '4.6.0', optionalDependencies: {} } })).toBe(
      'impeccable 4.6.0 ships no binary for this machine (@impeccable/cli-linux-x64)',
    )
    expect(problem({ 'impeccable/package.json': DETECTOR })).toBe(
      '@impeccable/cli-linux-x64 is not installed: run `npm ci` at the repository root',
    )
    expect(problem({ 'impeccable/package.json': DETECTOR, '@impeccable/cli-linux-x64/package.json': { version: '0.1.16' } })).toBe(
      '@impeccable/cli-linux-x64 is 0.1.16, and impeccable 4.6.0 names 0.1.15',
    )
    expect(
      problem({ 'impeccable/package.json': DETECTOR, '@impeccable/cli-linux-x64/package.json': { version: '0.1.15' } }, () => false),
    ).toBe(`@impeccable/cli-linux-x64 has no binary at ${path.join('/installed/node_modules/@impeccable/cli-linux-x64', 'bin', 'impeccable')}`)
  })
})

describe('design detector run: reading what it printed', () => {
  it('names each finding relative to web/, by its rule, and leaves the line as it is', () => {
    const { findings, problems } = readRun(
      run([printed('src/pages/Home.tsx', 'side-tab', 30), printed('src/ui/Card.tsx', 'design-system-color', 4)]),
      WEB,
    )
    expect(problems).toEqual([])
    expect(findings).toEqual([
      { file: 'src/pages/Home.tsx', rule: 'side-tab', line: 30 },
      { file: 'src/ui/Card.tsx', rule: 'design-system-color', line: 4 },
    ])
  })

  it('leaves test files out: their literals are the values a test asserts, not styling', () => {
    const { findings, problems } = readRun(
      run([
        printed('src/ui/forms.test.tsx', 'design-system-font-size'),
        printed('src/pages/triggers/typeScaleRoles.test.ts', 'design-system-font-size'),
        printed('src/ui/forms.tsx', 'design-system-font-size'),
      ]),
      WEB,
    )
    expect(problems).toEqual([])
    expect(findings.map((f) => f.file)).toEqual(['src/ui/forms.tsx'])
  })

  it('reads a clean run (exit 0) and a run with primary findings (exit 2) the same way', () => {
    expect(readRun(run([], 0), WEB)).toEqual({ findings: [], problems: [] })
    expect(readRun(run([printed('src/a.tsx', 'side-tab')], 2), WEB).findings).toHaveLength(1)
  })

  it('fails a run that could not scan a target (exit 1), or that stopped any other way', () => {
    expect(readRun(run([], 1, 'src: no such file'), WEB).problems).toEqual([
      'the detector could not scan web/src (exit 1): src: no such file',
    ])
    expect(readRun({ status: null, stdout: '', stderr: '' }, WEB).problems).toEqual([
      'the detector stopped without an exit status',
    ])
    expect(readRun(run([], 127, 'impeccable: no binary'), WEB).problems).toEqual([
      'the detector exited 127: impeccable: no binary',
    ])
  })

  it('fails output that is not a list of findings, and a finding with no file or rule', () => {
    expect(readRun(run('Scanning…'), WEB).problems).toEqual([
      'the detector printed no JSON on stdout: Scanning…',
    ])
    expect(readRun(run({ findings: [] }), WEB).problems).toEqual([
      'the detector printed JSON that is not a list of findings',
    ])
    expect(readRun(run([{ file: path.join(WEB, 'src/a.tsx') }]), WEB).problems).toEqual([
      `finding 1 has no file and rule: {"file":${JSON.stringify(path.join(WEB, 'src/a.tsx'))}}`,
    ])
  })

  it('fails a finding outside web/src, so a scan of the wrong folder cannot pass', () => {
    const outside = { ...printed('src/a.tsx', 'side-tab'), file: path.join(REPO, 'docs/index.html') }
    expect(readRun(run([outside]), WEB).problems).toEqual([
      `a finding names ${path.join(REPO, 'docs/index.html')}, which is outside web/src`,
    ])
  })
})

describe('design detector run: the project config', () => {
  /** A web/ holding these files under .impeccable/, read the way the runner reads web/. */
  const folder = (files: Record<string, string>) => (rel: string) => files[rel.replace(/^\.impeccable\//, '')]

  it('accepts a web/ whose project config holds no detector settings', () => {
    expect(configProblems(folder({}))).toEqual([])
    expect(configProblems(folder({ 'config.json': '{"live":{}}' }))).toEqual([])
    expect(configProblems(), "this tree's web/.impeccable").toEqual([])
  })

  it('refuses detector settings in either config file: the baseline is the one list of accepted findings', () => {
    const read = folder({
      'config.json': '{"detector":{"ignoreRules":["side-tab"]}}',
      'config.local.json': '{"detector":{"ignoreFiles":["src/pages/**"]}}',
    })
    expect(configProblems(read)).toEqual([
      '.impeccable/config.json has detector settings, which would drop findings before the baseline sees them: ' +
        'list accepted findings in design-detector.baseline.jsonl instead',
      '.impeccable/config.local.json has detector settings, which would drop findings before the baseline sees them: ' +
        'list accepted findings in design-detector.baseline.jsonl instead',
    ])
  })

  it('refuses a config file it cannot read as JSON', () => {
    expect(configProblems(folder({ 'config.local.json': '{' }))).toEqual([
      '.impeccable/config.local.json is not JSON, so what it would drop cannot be told',
    ])
  })
})

describe('design detector run: writing the baseline', () => {
  it('writes one sorted line per (file, rule), which the comparator then passes', () => {
    const findings = [
      { file: 'src/pages/Tasks.tsx', rule: 'side-tab', line: 40 },
      { file: 'src/pages/Home.tsx', rule: 'side-tab', line: 30 },
      { file: 'src/pages/Home.tsx', rule: 'gradient-text', line: 88 },
      { file: 'src/pages/Home.tsx', rule: 'gradient-text', line: 12 },
    ]
    const text = baselineText(findings)
    expect(text).toBe(
      [
        '{"file":"src/pages/Home.tsx","rule":"gradient-text","count":2}',
        '{"file":"src/pages/Home.tsx","rule":"side-tab","count":1}',
        '{"file":"src/pages/Tasks.tsx","rule":"side-tab","count":1}',
        '',
      ].join('\n'),
    )
    expect(compareFindings(findings, text)).toEqual([])
  })
})

describe('design detector run: the tree', () => {
  it('commits a baseline that parses, names only files under src, and no test file', () => {
    // An empty baseline is allowed: it is what the tree has once every finding is fixed.
    const { counts, problems } = parseBaseline(fs.readFileSync(path.join(WEB, 'design-detector.baseline.jsonl'), 'utf8'))
    expect(problems).toEqual([])
    for (const { file } of counts.values()) {
      expect(file.startsWith('src/'), file).toBe(true)
      expect(/\.test\.[cm]?[jt]sx?$/.test(file), file).toBe(false)
    }
  })

  it('pins the detector to one version, and locks each platform binary at the version it names', () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(REPO, 'package.json'), 'utf8'))
    const pinned: string = manifest.devDependencies.impeccable
    expect(pinned, 'an exact version, no range').toMatch(/^\d+\.\d+\.\d+$/)
    const lock = JSON.parse(fs.readFileSync(path.join(REPO, 'package-lock.json'), 'utf8')).packages
    const entry = lock['node_modules/impeccable']
    expect(entry.version).toBe(pinned)
    expect(entry.integrity).toMatch(/^sha512-/)
    expect(entry.hasInstallScript, 'the detector runs no install script').toBeUndefined()
    const binaries = Object.entries(entry.optionalDependencies as Record<string, string>)
    expect(binaries.map(([name]) => name)).toContain(platformPackage('linux', 'x64'))
    for (const [name, version] of binaries) {
      const locked = lock[`node_modules/${name}`]
      expect(locked, `${name} is in the lockfile`).toBeDefined()
      expect(locked.version, name).toBe(version)
      expect(locked.integrity, name).toMatch(/^sha512-/)
    }
  })
})
