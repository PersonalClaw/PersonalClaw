/**
 * A web watch's check vocabulary is TOTAL here, and read from the backend rather than mirrored.
 *
 * `last_check.outcome` carries a `WatchCheck` member (`triggers/web_poll.py`) and the panel names it.
 * A member with no row would render as a bare "Checked", and a member the backend counts among
 * `CANNOT_FIRE` with no `notFiring` line would leave the status line saying nothing about why the
 * watch is not firing — the silence this vocabulary exists to end. Both sets are read out of the
 * Python source at test time, so a new outcome fails here instead of in front of a user.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pyBetween, pyEnumMembers } from '../../design/pySource'
import { WATCH_CHECK_META, watchCheckMeta } from './watchCheck'

const WEB_POLL = readFileSync(join(process.cwd(), '..', 'src', 'personalclaw', 'triggers', 'web_poll.py'), 'utf8')

const checks = (): string[] => pyEnumMembers(WEB_POLL, 'WatchCheck')

/** `CANNOT_FIRE`'s members, as wire values: its `WatchCheck.<NAME>.value` entries resolved by name. */
const cannotFire = (): string[] => {
  const byName = new Map(
    [...pyBetween(`${WEB_POLL}\nclass __EOF__`, 'class WatchCheck', '\nclass ').matchAll(/^\s+([A-Z_]+) = "([a-z_]+)"/gm)]
      .map((m) => [m[1], m[2]]),
  )
  const block = pyBetween(WEB_POLL, 'CANNOT_FIRE: frozenset[str] = frozenset(', '\n)')
  return [...block.matchAll(/WatchCheck\.([A-Z_]+)\.value/g)].map((m) => byName.get(m[1]) ?? `?${m[1]}`)
}

describe('the web watch check vocabulary', () => {
  it('reads both sets out of the Python source', () => {
    expect(checks().length).toBeGreaterThanOrEqual(7)
    expect(cannotFire().length).toBeGreaterThanOrEqual(4)
  })

  it('names every check the backend can record', () => {
    const unnamed = checks().filter((c) => watchCheckMeta(c).label === 'Checked')
    expect(unnamed, 'a check with no row reads as a bare "Checked"').toEqual([])
  })

  it('gives every check the watch cannot fire after a status line, and no other check one', () => {
    const stuck = new Set(cannotFire())
    for (const c of checks()) {
      expect(Boolean(WATCH_CHECK_META[c]?.notFiring), c).toBe(stuck.has(c))
    }
  })

  it('carries no row for a check the backend does not have', () => {
    expect(Object.keys(WATCH_CHECK_META).sort()).toEqual([...checks()].sort())
  })
})
