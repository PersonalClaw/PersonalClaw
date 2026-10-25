import { describe, expect, it } from 'vitest'
import { decide, main } from '../../e2e/on-shard.mjs'

// ── A shard leg's own step runs on one shard alone (#3939) ─────────────────────────────────────
//
// `e2e/on-shard.mjs` is how each `e2e-shard` leg runs what runs on a home of its own: the idle
// probe on shard 1, the stateful set on shard 2, each a step on both legs. Run where it is not its
// shard, it must run nothing and pass; on its shard, run the command and pass its exit through; and
// refuse what it cannot read rather than guess, which would run the stateful set twice or never.

describe('on-shard', () => {
  it('runs the command on its own shard and passes its exit through', () => {
    const ran: string[][] = []
    const run = (command: string, args: string[]) => {
      ran.push([command, ...args])
      return { status: 3 }
    }
    expect(main(['2', '2', '--', 'npx', 'playwright', 'test', 'e2e/staleWrite.spec.ts'], run)).toBe(3)
    expect(ran).toEqual([['npx', 'playwright', 'test', 'e2e/staleWrite.spec.ts']])
  })

  it('runs nothing on another shard and passes', () => {
    const ran: string[][] = []
    const run = (command: string, args: string[]) => {
      ran.push([command, ...args])
      return { status: 0 }
    }
    expect(main(['1', '2', '--', 'npx', 'playwright', 'test'], run)).toBe(0)
    expect(ran).toEqual([])
    expect(decide(['1', '2', '--', 'x'])).toEqual({ skip: 'shard 1: this step runs on shard 2 alone' })
  })

  it.each([
    [['{shard}', '1', '--', 'x']],
    [['1', '--', 'x']],
    [['1', '1']],
    [['1', '1', 'extra', '--', 'x']],
    [['0', '1', '--', 'x']],
  ])('refuses %j and runs nothing', (argv) => {
    const ran: string[][] = []
    expect(main(argv, (c, a) => (ran.push([c, ...a]), { status: 0 }))).toBe(2)
    expect(ran).toEqual([])
  })

  it('names a command it could not start', () => {
    expect(main(['1', '1', '--', 'nope'], () => ({ status: null, error: new Error('spawn nope ENOENT') }))).toBe(2)
  })
})
