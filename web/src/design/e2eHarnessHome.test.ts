import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A spec reaches the gateway's home only through the run, never the default home ─────────────
//
// `staleWrite.spec.ts` writes the harness gateway's home with the CLI. It takes that home from
// `harnessHome(process.env)`, which refuses two things before the CLI starts:
//   · PW_E2E_HOME unset: the CLI would fall back to the default home and write it;
//   · PW_E2E_HOME at, or under, the default home (`os.homedir()/.personalclaw`), compared after
//     realpath where the folder exists, so a symlink into it is refused too.
// `os.homedir()` is stubbed to a temp folder here, so this file never touches a real home.

const stub = vi.hoisted(() => ({ home: '' }))
vi.mock('node:os', async (orig) => {
  const real = await orig<typeof import('node:os')>()
  return { ...real, homedir: () => stub.home }
})

const { harnessHome, choosePlaces } = await import('../../e2e/runPlaces')
const { tmpdir } = await import('node:os')

let dir = ''
let cli = ''
let marker = ''
beforeEach(() => {
  dir = realpathSync(mkdtempSync(join(tmpdir(), 'pw-harness-home-')))
  stub.home = join(dir, 'user')
  mkdirSync(join(stub.home, '.personalclaw'), { recursive: true })
  // A stand-in CLI: all it does is leave a mark, so a test can tell whether it started.
  marker = join(dir, 'cli-started')
  cli = join(dir, 'stand-in-cli.sh')
  writeFileSync(cli, '#!/bin/sh\ntouch "$CLI_MARK"\n')
  chmodSync(cli, 0o755)
})
afterEach(() => { rmSync(dir, { recursive: true, force: true }) })

/** The spec's two statements, in its order: take the home, then start the CLI on it. */
function writeLikeTheSpec(env: Record<string, string | undefined>) {
  const home = harnessHome(env)
  execFileSync(cli, [], { env: { ...process.env, CLI_MARK: marker, PERSONALCLAW_HOME: home }, stdio: 'pipe' })
}

describe('harnessHome: the planted runs', () => {
  it('throws when PW_E2E_HOME is unset, before the CLI starts', () => {
    expect(() => writeLikeTheSpec({})).toThrow(/PW_E2E_HOME is unset/)
    expect(() => writeLikeTheSpec({ PW_E2E_HOME: '' })).toThrow(/PW_E2E_HOME is unset/)
    expect(existsSync(marker), 'the CLI started').toBe(false)
  })

  it('throws when PW_E2E_HOME is the stand-in default home, before the CLI starts', () => {
    expect(() => writeLikeTheSpec({ PW_E2E_HOME: join(stub.home, '.personalclaw') })).toThrow(/default PersonalClaw home/)
    expect(existsSync(marker), 'the CLI started').toBe(false)
  })

  it('throws for a folder under the default home, one not made yet, and the same path spelled another way', () => {
    for (const home of [
      join(stub.home, '.personalclaw', 'workspace'),
      join(stub.home, '.personalclaw', 'not-made-yet', 'x'),
      join(stub.home, 'other', '..', '.personalclaw') + '/',
    ]) {
      expect(() => writeLikeTheSpec({ PW_E2E_HOME: home }), home).toThrow(/default PersonalClaw home/)
    }
    expect(existsSync(marker)).toBe(false)
  })

  it('throws for a symlink into the default home, read after realpath', () => {
    const link = join(dir, 'looks-harmless')
    symlinkSync(join(stub.home, '.personalclaw'), link)
    expect(() => writeLikeTheSpec({ PW_E2E_HOME: link })).toThrow(/default PersonalClaw home/)
    expect(() => writeLikeTheSpec({ PW_E2E_HOME: join(link, 'sub') })).toThrow(/default PersonalClaw home/)
    expect(existsSync(marker)).toBe(false)
  })

  it('lets a run home through, and the CLI then starts on it (the control)', () => {
    const home = join(dir, 'personalclaw-e2e-run')
    mkdirSync(home)
    writeLikeTheSpec({ PW_E2E_HOME: home })
    expect(existsSync(marker), 'the CLI started on the run home').toBe(true)
    // A sibling whose name only starts like the default home is not under it.
    rmSync(marker)
    const sibling = join(stub.home, '.personalclaw-e2e')
    mkdirSync(sibling)
    writeLikeTheSpec({ PW_E2E_HOME: sibling })
    expect(existsSync(marker)).toBe(true)
  })
})

describe('choosePlaces refuses the same homes for the gateway', () => {
  it('a PW_E2E_HOME at or under the default home never becomes the gateway home', async () => {
    await expect(choosePlaces({ PW_E2E_HOME: join(stub.home, '.personalclaw') }, { tmp: dir })).rejects.toThrow(/default PersonalClaw home/)
    await expect(choosePlaces({ PW_E2E_HOME: join(stub.home, '.personalclaw', 'x') }, { tmp: dir })).rejects.toThrow(/default PersonalClaw home/)
  })
})
