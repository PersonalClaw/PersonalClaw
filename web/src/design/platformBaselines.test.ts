import { afterEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// ── The matrix's baselines are per platform, and the -linux set is CI's own measurement ──────────
//
// One set for both platforms was a red on CI's Linux for what the machine decides (#3905). So each
// platform has its own (`<id>-<platform>`), feature PRs write the `-darwin` set locally, and the
// `-linux` set is written from a CI run's report by `e2e/platform-baselines.mjs`, from what each
// surface attached there. These hold the tree to that shape and plant each way a report can fail
// to be a whole set.

const WEB = process.cwd()
const E2E = join(WEB, 'e2e')

const dirs: string[] = []
afterEach(() => { for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true }) })
const scratch = () => {
  const d = mkdtempSync(join(tmpdir(), 'platform-baselines-'))
  dirs.push(d)
  return d
}

const b64 = (s: string) => Buffer.from(s).toString('base64')
const entry = (target: string) => ({ targets: { '320': [target] }, known: {} })
/** A JSON report whose tests attached `attachments`, one test per surface and theme. */
function report(attachments: Array<{ name: string; body: string }>) {
  return {
    suites: [{
      title: 'surfaces.spec.ts', file: 'surfaces.spec.ts', specs: [],
      suites: [{
        title: 'UI quality', specs: attachments.map((a) => ({
          title: a.name, tests: [{ results: [{ status: 'passed', attachments: [{ name: a.name, contentType: 'text/plain', body: b64(a.body) }] }] }],
        })),
      }],
    }],
  }
}
const WHOLE = [
  { name: 'platform-baseline-chat-light.json', body: JSON.stringify(entry('button "Send"')) },
  { name: 'platform-baseline-chat-dark.json', body: JSON.stringify(entry('button "Stop"')) },
  { name: 'platform-baseline-chat.aria.yml', body: '- main:\n  - heading "Chat"\n' },
]

function run(rep: unknown, platform = 'linux') {
  const dir = scratch()
  const path = join(dir, 'e2e-report.json')
  writeFileSync(path, JSON.stringify(rep))
  const into = join(dir, 'e2e')
  const out = spawnSync(process.execPath, [join(E2E, 'platform-baselines.mjs'), path, platform, '--into', into], { cwd: WEB, encoding: 'utf8' })
  return { ...out, into }
}

describe('the committed baselines are per platform', () => {
  it('every tree and every baseline file names its platform, and the Mac set covers each surface', () => {
    const trees = readdirSync(join(E2E, '__aria__'))
    const files = readdirSync(join(E2E, '__ui-baselines__'))
    expect(trees.length, 'no trees: this check would pass vacuously').toBeGreaterThan(50)
    expect(trees.filter((f) => !/-(darwin|linux)\.aria\.yml$/.test(f))).toEqual([])
    expect(files.filter((f) => !/-(darwin|linux)\.json$/.test(f))).toEqual([])
    const ids = (names: string[], suffix: RegExp) => new Set(names.map((f) => f.replace(suffix, '')))
    expect(ids(files.filter((f) => f.endsWith('-darwin.json')), /-darwin\.json$/))
      .toEqual(ids(trees.filter((f) => f.endsWith('-darwin.aria.yml')), /-darwin\.aria\.yml$/))
  })

  it('the matrix reads and writes this platform\'s files, and attaches what it measured under CI', () => {
    const spec = readFileSync(join(E2E, 'surfaces.spec.ts'), 'utf8')
    expect(spec).toMatch(/\$\{id\}-\$\{process\.platform\}\.aria\.yml/)
    expect(spec).toMatch(/\$\{id\}-\$\{process\.platform\}\.json/)
    expect(spec).toMatch(/platform-baseline-\$\{id\}-\$\{ctx\.theme\}\.json/)
    expect(spec).toMatch(/platform-baseline-\$\{id\}\.aria\.yml/)
  })
})

describe('platform-baselines.mjs writes a platform\'s set from a CI run', () => {
  it('writes each surface\'s tree and baseline file as the matrix writes them', () => {
    const { status, stderr, into } = run(report(WHOLE))
    expect(status, stderr).toBe(0)
    expect(readFileSync(join(into, '__aria__', 'chat-linux.aria.yml'), 'utf8')).toBe('- main:\n  - heading "Chat"\n')
    expect(readFileSync(join(into, '__ui-baselines__', 'chat-linux.json'), 'utf8'))
      .toBe(JSON.stringify({ dark: entry('button "Stop"'), light: entry('button "Send"') }, null, 2) + '\n')
  })

  it('keeps a surface\'s first measurement when a retry attached another', () => {
    const rep = report(WHOLE)
    rep.suites[0].suites[0].specs[0].tests[0].results.push(
      { status: 'passed', attachments: [{ name: 'platform-baseline-chat-light.json', contentType: 'text/plain', body: b64(JSON.stringify(entry('x'))) }] })
    const { status, into } = run(rep)
    expect(status).toBe(0)
    expect(JSON.parse(readFileSync(join(into, '__ui-baselines__', 'chat-linux.json'), 'utf8')).light).toEqual(entry('button "Send"'))
  })

  const PLANTS: Array<[string, Array<{ name: string; body: string }>, RegExp]> = [
    ['a surface attached one theme', WHOLE.filter((a) => !a.name.endsWith('-dark.json')), /chat attached no dark theme/],
    ['a surface attached no tree', WHOLE.filter((a) => !a.name.endsWith('.aria.yml')), /chat attached no accessibility tree/],
    ['the report holds no measurement', [], /holds no platform-baseline attachment/],
  ]
  for (const [name, attachments, message] of PLANTS) {
    it(`writes nothing and names it when ${name}`, () => {
      const { status, stderr, into } = run(report(attachments))
      expect(status).toBe(1)
      expect(stderr).toMatch(message)
      expect(existsSync(join(into, '__aria__')), 'a refused set leaves nothing behind').toBe(false)
    })
  }
})
