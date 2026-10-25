import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  LOOPBACK,
  MANIFEST,
  PIN_FILE,
  checkPin,
  compareEnvironment,
  dockerArgs,
  hostMounts,
  lockVersions,
  main,
  playwrightCli,
  playwrightCoreCli,
  startServer,
  type Pin,
  type RunResult,
} from '../../e2e/run-pinned.mjs'

// ── The pinned browser image: one environment for every container-made baseline (#3943) ─────────
//
// `e2e/run-pinned.mjs` runs one Playwright invocation against the browser of the image
// `e2e/visual-image.json` pins by its linux/amd64 digest, after probing the image and holding the
// probe to the baselines' manifest. Each refusal is planted here through `main()` with fake
// processes, so no container runs: the image itself runs only on CI.

const WEB = join(__dirname, '..', '..')
const PIN: Pin = JSON.parse(readFileSync(join(WEB, PIN_FILE), 'utf8'))
const LOCK = JSON.parse(readFileSync(join(WEB, '..', 'package-lock.json'), 'utf8'))
const PROBED = {
  image: PIN.image,
  platform: 'linux/amd64',
  playwright: PIN.playwright,
  browser: 'chromium 156.0.0.0',
  renderer: 'ANGLE (SwiftShader)',
  fonts: { count: 130, sha256: 'a'.repeat(64) },
}

describe('the pin', () => {
  it('names the image by its linux/amd64 digest, with the Playwright the lock resolves', () => {
    expect(checkPin(PIN, LOCK)).toEqual([])
    const locked = lockVersions(LOCK)
    expect(locked).toEqual({ test: PIN.playwright, playwright: PIN.playwright, core: PIN.playwright })
  })

  it('reads the nested playwright-core, not the top-level one another package holds', () => {
    const lock = {
      packages: {
        'node_modules/@playwright/test': { version: '1.64.1' },
        'node_modules/playwright': { version: '1.64.1' },
        'node_modules/playwright-core': { version: '1.64.0' },
        'node_modules/playwright/node_modules/playwright-core': { version: '1.64.1' },
      },
    }
    expect(lockVersions(lock).core).toBe('1.64.1')
  })

  it.each([
    ['a tag', { image: 'mcr.microsoft.com/playwright:v1.64.1-noble' }, 'named by its sha256 digest'],
    ['arm64', { platform: 'linux/arm64' }, 'platform is linux/arm64'],
    ['another Playwright', { playwright: '1.64.0', tag: 'v1.64.0-noble' }, 'the lock resolves'],
    ['a tag for another version', { tag: 'v1.64.0-noble' }, 'is not v1.64.1-noble'],
    ['an index that is no digest', { index: 'latest' }, 'is no digest'],
    ['a key too many', { pulled: 'yesterday' }, 'holds image, index, platform, playwright, pulled, tag'],
  ])('refuses %s', (_name, change, says) => {
    expect(checkPin({ ...PIN, ...change }, LOCK).join('\n')).toContain(says)
  })
})

describe('the environment check', () => {
  it('passes the environment the baselines were made in', () => {
    expect(compareEnvironment(PROBED, PROBED, PIN)).toEqual([])
  })

  it.each([
    ['browser', 'chromium 157.0.0.0'],
    ['renderer', 'ANGLE (Vulkan)'],
    ['fonts', { count: 131, sha256: 'b'.repeat(64) }],
  ])('names a %s that moved', (field, value) => {
    const problems = compareEnvironment(PROBED, { ...PROBED, [field]: value }, PIN)
    expect(problems.join('\n')).toContain(`${field}: the baselines were made with`)
  })

  it('names a manifest made in another image before anything else', () => {
    const problems = compareEnvironment({ ...PROBED, image: 'mcr.microsoft.com/playwright@sha256:' + '0'.repeat(64) }, PROBED, PIN)
    expect(problems).toEqual([expect.stringContaining('not the pinned')])
  })

  it('names a manifest that misses a field', () => {
    const { fonts: _fonts, ...partial } = PROBED
    expect(compareEnvironment(partial, PROBED, PIN).join('\n')).toContain('missing fonts')
  })
})

describe('the container', () => {
  const root = '/runner/work/PersonalClaw'
  const args = (more: { network: 'none' | 'host'; name?: string }) =>
    dockerArgs({
      root,
      mounts: [root, '/opt/node'],
      pin: PIN,
      uid: 1001,
      gid: 118,
      execPath: '/opt/node/bin/node',
      command: ['/opt/node/bin/node', 'x.mjs'],
      ...more,
    })
  const envOf = (argv: string[]) => argv.flatMap((arg, i) => (argv[i - 1] === '-e' ? [arg] : []))

  it('probes the pinned digest on linux/amd64 with no network, as the caller, in web/', () => {
    const argv = args({ network: 'none' })
    expect(argv.slice(0, 7)).toEqual(['run', '--rm', '--init', '--platform', 'linux/amd64', '--network', 'none'])
    expect(argv).toContain('1001:118')
    expect(argv.slice(argv.indexOf('-w'), argv.indexOf('-w') + 3)).toEqual(['-w', `${root}/web`, PIN.image])
    expect(argv).not.toContain('--interactive')
  })

  it('serves the browser on the host network, named, with its stdin held open', () => {
    const argv = args({ network: 'host', name: 'pinned-browser-7' })
    expect(argv.slice(0, 6)).toEqual(['run', '--rm', '--name', 'pinned-browser-7', '--interactive', '--init'])
    expect(argv.slice(argv.indexOf('--network'), argv.indexOf('--network') + 2)).toEqual(['--network', 'host'])
  })

  it('mounts the checkout and this Node read-only, and gives the image none of the caller\'s variables', () => {
    const argv = args({ network: 'host', name: 'x' })
    expect(argv.flatMap((arg, i) => (argv[i - 1] === '-v' ? [arg] : []))).toEqual([`${root}:${root}:ro`, '/opt/node:/opt/node:ro'])
    expect(envOf(argv).map((pair) => pair.split('=')[0])).toEqual(['HOME', 'PATH', 'LANG', 'TZ', 'VISUAL_IMAGE'])
  })

  it('refuses a checkout with no @playwright/test', () => {
    const made = new Set(['/w/node_modules/@playwright/test'])
    expect(hostMounts({ root: '/w', execPath: '/opt/node/bin/node', exists: (file) => made.has(file) })).toEqual(['/w', '/opt/node'])
    expect(() => hostMounts({ root: '/w', execPath: '/n/bin/node', exists: () => false })).toThrow('npm ci')
  })

  it('accepts a browser server on 127.0.0.1 alone', () => {
    expect(LOOPBACK.test('ws://127.0.0.1:41234/')).toBe(true)
    for (const elsewhere of ['ws://0.0.0.0:41234/', 'ws://[::]:41234/', 'ws://localhost:41234/', 'ws://10.1.0.4:41234/']) {
      expect(LOOPBACK.test(elsewhere)).toBe(false)
    }
  })
})

describe('the playwright command', () => {
  it("is @playwright/test's own bin, which its exports do not resolve", () => {
    const cli = playwrightCli(WEB)
    expect(cli).toMatch(/node_modules\/@playwright\/test\/cli\.js$/)
    expect(existsSync(cli)).toBe(true)
  })

  it('serves the browser from the playwright-core that @playwright/test loads', () => {
    const cli = playwrightCoreCli(WEB)
    expect(existsSync(cli)).toBe(true)
    const version = JSON.parse(readFileSync(join(cli, '..', 'package.json'), 'utf8')).version
    expect(version).toBe(PIN.playwright)
  })
})

describe('the browser server', () => {
  const node = process.execPath
  const log: string[] = []

  it('resolves with the endpoint it announces, whole, and stops when its stdin closes', async () => {
    const script =
      "process.stdout.write('Listening on ws://127.0.0.1:4'); setTimeout(() => process.stdout.write('1234/\\n'), 50); process.stdin.resume(); process.stdin.on('end', () => process.exit(0))"
    const server = await startServer(node, ['-e', script], { log: (line) => log.push(line) })
    expect(server.endpoint).toBe('ws://127.0.0.1:41234/')
    await server.stop()
  })

  it('refuses a server that exits before it listens', async () => {
    await expect(startServer(node, ['-e', "console.log('no browser here'); process.exit(4)"], { log: (line) => log.push(line) })).rejects.toThrow(
      'exited (4) before it listened: no browser here',
    )
  })
})

describe('main', () => {
  let root: string
  let web: string
  let lines: string[]
  let calls: { command: string; args: string[]; capture: boolean; env?: Record<string, string | undefined>; cwd?: string }[]
  let served: string[][]
  let stopped: number

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'pinned-run-'))
    web = join(root, 'web')
    mkdirSync(join(web, 'e2e'), { recursive: true })
    writeFileSync(join(web, PIN_FILE), JSON.stringify(PIN))
    writeFileSync(join(root, 'package-lock.json'), JSON.stringify(LOCK))
    lines = []
    calls = []
    served = []
    stopped = 0
  })
  afterEach(() => rmSync(root, { recursive: true, force: true }))

  const fake =
    (probe: RunResult, suite: RunResult = { status: 0 }) =>
    async (command: string, args: string[], options: { capture?: boolean; env?: Record<string, string | undefined>; cwd?: string } = {}) => {
      calls.push({ command, args, capture: !!options.capture, env: options.env, cwd: options.cwd })
      return options.capture ? probe : suite
    }
  const server = (endpoint = 'ws://127.0.0.1:41234/') => async (_command: string, args: string[]) => {
    served.push(args)
    return { endpoint, stop: async () => void stopped++ }
  }
  const go = (argv: string[], run: ReturnType<typeof fake>, more: Record<string, unknown> = {}) =>
    main(argv, {
      web,
      platform: 'linux',
      arch: 'x64',
      execPath: '/opt/node/bin/node',
      env: { CI: 'true', PATH: '/usr/bin' },
      log: (line) => lines.push(line),
      mounts: () => [root],
      cli: () => '/w/node_modules/@playwright/test/cli.js',
      coreCli: () => '/w/node_modules/playwright/node_modules/playwright-core/cli.js',
      run,
      serve: server(),
      ...more,
    })
  const probed = (env: unknown = PROBED): RunResult => ({ status: 0, stdout: JSON.stringify(env) })

  it('writes the manifest from the probe and rewrites the baselines on an update', async () => {
    expect(await go(['--update', '--', 'e2e/visual.spec.ts'], fake(probed()))).toBe(0)
    expect(JSON.parse(readFileSync(join(web, MANIFEST), 'utf8'))).toEqual(PROBED)
    expect(calls.at(-1)?.args.slice(-3)).toEqual(['test', 'e2e/visual.spec.ts', '--update-snapshots=changed'])
  })

  it('runs playwright on the host against the image\'s browser, served on loopback, and stops it', async () => {
    writeFileSync(join(web, MANIFEST), JSON.stringify(PROBED))
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed()))).toBe(0)
    expect(served).toHaveLength(1)
    expect(served[0].slice(-6)).toEqual(['/w/node_modules/playwright/node_modules/playwright-core/cli.js', 'run-server', '--host', '127.0.0.1', '--port', '0'])
    expect(served[0].slice(served[0].indexOf('--network'), served[0].indexOf('--network') + 2)).toEqual(['--network', 'host'])
    const suite = calls.at(-1)
    expect(suite?.command).toBe('/opt/node/bin/node')
    expect(suite?.cwd).toBe(web)
    expect(suite?.env).toEqual({ CI: 'true', PATH: '/usr/bin', PW_TEST_CONNECT_WS_ENDPOINT: 'ws://127.0.0.1:41234/' })
    expect(suite?.args.at(-1)).toBe('--update-snapshots=none')
    expect(stopped).toBe(1)
  })

  it('probes the image with no network before the browser is served', async () => {
    await go(['--update', '--', 'e2e/visual.spec.ts'], fake(probed()))
    const probe = calls.find((c) => c.capture)
    expect(probe?.command).toBe('docker')
    expect(probe?.args.slice(probe.args.indexOf('--network'), probe.args.indexOf('--network') + 2)).toEqual(['--network', 'none'])
  })

  it('refuses a browser server that listens beyond loopback, and stops it', async () => {
    writeFileSync(join(web, MANIFEST), JSON.stringify(PROBED))
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed()), { serve: server('ws://0.0.0.0:41234/') })).toBe(2)
    expect(lines.join('\n')).toContain('not on 127.0.0.1')
    expect(calls.filter((c) => !c.capture)).toEqual([])
    expect(stopped).toBe(1)
  })

  it("passes playwright's own failure through, and still stops the browser", async () => {
    writeFileSync(join(web, MANIFEST), JSON.stringify(PROBED))
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed(), { status: 1 }))).toBe(1)
    expect(lines.join('\n')).toContain('failed against the pinned browser')
    expect(stopped).toBe(1)
  })

  it.each([
    ['a Mac', { platform: 'darwin', arch: 'arm64' }, 'only on a linux/amd64 host', 2],
    ['an arm64 Linux', { platform: 'linux', arch: 'arm64' }, 'only on a linux/amd64 host', 2],
  ])('refuses %s and says where the runs happen', async (_name, host, says, status) => {
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed()), host)).toBe(status)
    expect(lines.join('\n')).toContain(says)
    expect(lines.join('\n')).toContain('visual-baselines.yml')
    expect(calls).toEqual([])
  })

  it('refuses a compare with no manifest, before any browser is served', async () => {
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed()))).toBe(1)
    expect(lines.join('\n')).toContain('no environment manifest')
    expect(calls.map((c) => c.capture)).toEqual([true])
    expect(served).toEqual([])
  })

  it('refuses a compare in an environment that moved, before any browser is served', async () => {
    writeFileSync(join(web, MANIFEST), JSON.stringify(PROBED))
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed({ ...PROBED, browser: 'chromium 157' })))).toBe(1)
    expect(lines.join('\n')).toContain('browser: the baselines were made with')
    expect(calls.map((c) => c.capture)).toEqual([true])
    expect(served).toEqual([])
  })

  it.each([
    ['no docker', { status: null, error: Object.assign(new Error('spawn docker ENOENT'), { code: 'ENOENT' }) }, 'needs docker'],
    ['a failed probe', { status: 3, stderr: 'boom' }, 'the environment probe failed (exit 3)'],
    ['a probe that printed no JSON', { status: 0, stdout: 'hello' }, 'printed no JSON'],
  ])('refuses %s', async (_name, probe, says) => {
    expect(await go(['--update', '--', 'e2e/visual.spec.ts'], fake(probe as RunResult))).toBe(2)
    expect(lines.join('\n')).toContain(says)
    expect(served).toEqual([])
  })

  it.each([
    [['e2e/visual.spec.ts'], 'unknown argument'],
    [['--update'], 'name what to run'],
    [['--', 'e2e/visual.spec.ts', '--update-snapshots=all'], 'run-pinned decides that'],
    [['--', 'e2e/visual.spec.ts', '-u'], 'run-pinned decides that'],
  ])('refuses the arguments %j', async (argv, says) => {
    expect(await go(argv, fake(probed()))).toBe(2)
    expect(lines.join('\n')).toContain(says)
  })

  it('refuses a pin the lock does not hold', async () => {
    writeFileSync(join(web, PIN_FILE), JSON.stringify({ ...PIN, playwright: '1.63.0', tag: 'v1.63.0-noble' }))
    expect(await go(['--', 'e2e/visual.spec.ts'], fake(probed()))).toBe(2)
    expect(lines.join('\n')).toContain('the lock resolves')
    expect(calls).toEqual([])
  })
})
