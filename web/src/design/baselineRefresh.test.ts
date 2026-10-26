import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { crc32, deflateSync } from 'node:zlib'
import { UPDATE_SNAPSHOTS, playwrightCli } from '../../e2e/run-pinned.mjs'
import { stepVerdict } from '../../e2e/surfaceChecks'
import { main, parseStatus, summary } from '../../e2e/baseline-changes.mjs'
import { CAPTURED_PLATFORMS, expectedKeys, main as coverage } from '../../e2e/visual-coverage.mjs'

// ── A refresh concludes on what it wrote (#3943) ─────────────────────────────────────────────────
//
// The Refresh visual baselines workflow runs the UI matrix with `--update-snapshots`, and every
// step held to a baseline used to fail on the difference it was about to write: the first attempt
// failed, the file was written, the retry passed against it, and `failOnFlakyTests` turned the run
// red. So any refresh that changed a baseline was red by construction. Now a baselined step's
// difference is the change written (`stepVerdict`), and `e2e/baseline-changes.mjs` lists each
// changed file in the job summary. A step held to zero still fails a refresh, and so does a crash
// or a file the run cannot write. `e2e/surfaceMatrix.control.spec.ts` holds which steps are which
// in a browser, and `tests/rails/test_visual_baselines_refresh_tree.py` the workflow's checks.

describe('what a step finding does', () => {
  it('fails a compare, held to a baseline or to zero', () => {
    expect(stepVerdict(['+ heading "New"'], true, false)).toBe('failed')
    expect(stepVerdict(['probe-mouse-only'], false, false)).toBe('failed')
  })

  it('is the change a refresh writes, for a step held to a baseline', () => {
    expect(stepVerdict(['+ heading "New"', '- heading "Old"'], true, true)).toBe('rewritten')
  })

  it('still fails a refresh, for a step held to zero', () => {
    expect(stepVerdict(['probe-mouse-only'], false, true)).toBe('failed')
  })

  it('is nothing when there is no finding', () => {
    for (const [baselined, rewriting] of [[true, true], [true, false], [false, true], [false, false]]) {
      expect(stepVerdict([], baselined, rewriting)).toBe('clean')
    }
  })
})

describe('the changed baselines list', () => {
  let root: string
  let web: string
  const git = (...args: string[]) =>
    execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', ...args], { cwd: root, stdio: 'pipe' })
  const put = (rel: string, body: string) => {
    mkdirSync(dirname(join(root, rel)), { recursive: true })
    writeFileSync(join(root, rel), body)
  }

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'baseline-changes-'))
    web = join(root, 'web')
    put('web/e2e/__aria__/notifications-linux.aria.yml', '- heading "Notifications"\n')
    put('web/e2e/__ui-baselines__/notifications-linux.json', '{}\n')
    put('web/e2e/__screenshots__/visual.spec.ts/terminal-light-linux.png', 'png')
    put('web/e2e/visual-environment.json', '{}\n')
    put('web/src/app.ts', 'export {}\n')
    git('init', '-q', '-b', 'main')
    git('add', '-A')
    git('commit', '-q', '-m', 'the committed set')
  })
  afterEach(() => rmSync(root, { recursive: true, force: true }))

  it('lists each file the refresh added, changed or deleted, and nothing else', () => {
    put('web/e2e/__aria__/notifications-linux.aria.yml', '- heading "Notifications"\n- button "Mark read"\n')
    put('web/e2e/__ui-baselines__/inbox-linux.json', '{}\n')
    unlinkSync(join(root, 'web/e2e/__screenshots__/visual.spec.ts/terminal-light-linux.png'))
    put('web/src/app.ts', 'export const changed = 1\n')
    const out = join(root, 'summary.md')
    const log: string[] = []
    expect(main({ web, env: { GITHUB_STEP_SUMMARY: out }, log: (line) => log.push(line) })).toBe(0)
    const listed = [
      '- changed `web/e2e/__aria__/notifications-linux.aria.yml`',
      '- deleted `web/e2e/__screenshots__/visual.spec.ts/terminal-light-linux.png`',
      '- added `web/e2e/__ui-baselines__/inbox-linux.json`',
    ]
    expect(readFileSync(out, 'utf8')).toBe(`### 3 baseline files changed\n\n${listed.join('\n')}\n\n`)
    expect(log.join('\n')).toContain(listed.join('\n'))
    expect(log.join('\n')).not.toContain('app.ts')
  })

  it('says so when the committed set already matches', () => {
    const log: string[] = []
    expect(main({ web, env: {}, log: (line) => log.push(line) })).toBe(0)
    expect(log.join('\n')).toContain('No baseline changed')
  })

  it('is red when git cannot be read, never an empty list', () => {
    const log: string[] = []
    const run = () => ({ status: 128, stdout: '', stderr: 'fatal: not a git repository' })
    expect(main({ web, env: {}, log: (line) => log.push(line), run })).toBe(1)
    expect(log.join('\n')).toContain('::error title=baseline changes::git status could not be read: fatal: not a git repository')
  })

  it('reads a status line by its code', () => {
    expect(parseStatus(' M b\0?? a\0 D c\0')).toEqual([
      { kind: 'added', path: 'a' },
      { kind: 'changed', path: 'b' },
      { kind: 'deleted', path: 'c' },
    ])
    expect(summary([{ kind: 'changed', path: 'x' }])).toBe('### 1 baseline file changed\n\n- changed `x`\n')
  })
})

describe('the goldens a refresh drew', () => {
  let dir: string
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'visual-coverage-'))
    for (const key of expectedKeys()) for (const p of CAPTURED_PLATFORMS) writeFileSync(join(dir, `${key}-${p}.png`), 'png')
  })
  afterEach(() => rmSync(dir, { recursive: true, force: true }))

  it('pass when every surface the visual spec snapshots has one', () => {
    const log: string[] = []
    expect(coverage({ dir, log: (line) => log.push(line) })).toBe(0)
    expect(log.join('\n')).toContain(`a golden for each of the ${expectedKeys().length} surfaces`)
  })

  it('are red when a surface has none, naming it', () => {
    const [first] = expectedKeys()
    unlinkSync(join(dir, `${first}-${CAPTURED_PLATFORMS[0]}.png`))
    const log: string[] = []
    expect(coverage({ dir, log: (line) => log.push(line) })).toBe(1)
    expect(log.join('\n')).toContain(`${CAPTURED_PLATFORMS[0]}: 1/${expectedKeys().length} missing → ${first}`)
  })

  it('are red when the folder cannot be read', () => {
    const log: string[] = []
    expect(coverage({ dir: join(dir, 'gone'), log: (line) => log.push(line) })).toBe(1)
    expect(log.join('\n')).toContain('the goldens could not be read')
  })
})

// ── What an update run rewrites ──────────────────────────────────────────────────────────────────
//
// `run-pinned.mjs --update` passes UPDATE_SNAPSHOTS. Under `all` Playwright rewrote every golden
// whose bytes moved, inside the tolerance or not, so a refresh rewrote 37 shots no compare would
// have failed (run 40150040941). This runs the real `playwright test` on three planted goldens, at
// the tolerance `playwright.config.ts` gives `toHaveScreenshot`, no browser needed: one inside the
// tolerance stays as committed, a failing one is rewritten, a missing one is written. The same plant
// under `all` rewrites the first, so the check can tell the two modes apart.

const WEB = resolve(__dirname, '../..')
const TOLERANCE = Number(/toHaveScreenshot: \{ maxDiffPixelRatio: ([\d.]+)/.exec(readFileSync(join(WEB, 'playwright.config.ts'), 'utf8'))?.[1])

/** A 100 x 100 PNG, white but for the pixels `dark` names. */
function png(dark: (x: number, y: number) => boolean): Buffer {
  const size = 100
  const raw = Buffer.alloc((size * 4 + 1) * size, 255)
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0
    for (let x = 0; x < size; x++) if (dark(x, y)) raw.fill(0, y * (size * 4 + 1) + 1 + x * 4, y * (size * 4 + 1) + 1 + x * 4 + 3)
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, 'latin1'), data])
    const head = Buffer.alloc(4)
    head.writeUInt32BE(data.length)
    const tail = Buffer.alloc(4)
    tail.writeUInt32BE(crc32(body))
    return Buffer.concat([head, body, tail])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(size, 0)
  ihdr.writeUInt32BE(size, 4)
  ihdr.set([8, 6, 0, 0, 0], 8)
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

describe('what an update run rewrites', () => {
  const white = png(() => false)
  // 5 of 10,000 pixels: inside the tolerance. Half the image: outside it.
  const speck = png((x, y) => y === 0 && x < 5)
  const half = png((x) => x < 50)
  let dir: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'update-mode-'))
    // The spec resolves @playwright/test from here, the same copy the runner loads.
    symlinkSync(resolve(WEB, '..', 'node_modules'), join(dir, 'node_modules'))
    mkdirSync(join(dir, 'goldens'))
    mkdirSync(join(dir, 'actual'))
    writeFileSync(join(dir, 'goldens', 'inside.png'), white)
    writeFileSync(join(dir, 'goldens', 'failing.png'), white)
    writeFileSync(join(dir, 'actual', 'inside.png'), speck)
    writeFileSync(join(dir, 'actual', 'failing.png'), half)
    writeFileSync(join(dir, 'actual', 'missing.png'), white)
    writeFileSync(
      join(dir, 'playwright.config.mjs'),
      `export default { testDir: '.', snapshotPathTemplate: '{testDir}/goldens/{arg}{ext}', workers: 1, retries: 0, reporter: 'line', expect: { toMatchSnapshot: { maxDiffPixelRatio: ${TOLERANCE} } } }\n`,
    )
    writeFileSync(
      join(dir, 'goldens.spec.mjs'),
      [
        "import { test, expect } from '@playwright/test'",
        "import { readFileSync } from 'node:fs'",
        "for (const name of ['inside', 'failing', 'missing']) {",
        "  test(name, () => expect(readFileSync(new URL(`./actual/${name}.png`, import.meta.url))).toMatchSnapshot(`${name}.png`))",
        '}',
        '',
      ].join('\n'),
    )
  })
  afterEach(() => rmSync(dir, { recursive: true, force: true }))

  const update = (mode: string) => {
    const done = spawnSync(process.execPath, [playwrightCli(WEB), 'test', '--config', join(dir, 'playwright.config.mjs'), mode], {
      cwd: dir,
      encoding: 'utf8',
      env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? dir },
    })
    expect(done.status, `${done.stdout}\n${done.stderr}`).toBe(0)
  }
  const golden = (name: string) => readFileSync(join(dir, 'goldens', `${name}.png`))

  it('reads the tolerance from the config', () => {
    expect(TOLERANCE).toBeGreaterThan(0.0005)
    expect(TOLERANCE).toBeLessThan(0.5)
  })

  it('leaves a golden inside the tolerance, rewrites a failing one and writes a missing one', () => {
    update(UPDATE_SNAPSHOTS)
    expect(golden('inside').equals(white), 'a golden inside the tolerance was rewritten').toBe(true)
    expect(golden('failing').equals(half), 'a failing golden was not rewritten').toBe(true)
    expect(existsSync(join(dir, 'goldens', 'missing.png')) && golden('missing').equals(white), 'a missing golden was not written').toBe(true)
  }, 60_000)

  it('is not `all`, which rewrites a golden inside the tolerance too', () => {
    expect(UPDATE_SNAPSHOTS).toBe('--update-snapshots=changed')
    update('--update-snapshots=all')
    expect(golden('inside').equals(speck)).toBe(true)
  }, 60_000)
})
