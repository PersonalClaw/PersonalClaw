// ── One shard leg's own step: run a command on one shard, pass on the others ─────────────────────
//
// `node e2e/on-shard.mjs <this shard> <runs on> -- <command> [args...]`, from web/. A catalogue
// step runs on every shard of its job, and its one hook is `{shard}`, so each `e2e-shard` leg's run
// on a home of its own is a step on both legs that runs on one: the idle-cost probe on shard 1, the
// stateful set on shard 2 (#3939). On its shard it runs the command with this process's
// environment and exits with its exit; on any other it says so and exits 0, having run nothing.
//
// An argument it cannot read is an exit 2, and the command does not run: a guard that guessed would
// run the stateful set twice, or not at all.
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const SHARD = /^[1-9][0-9]*$/

/** What to do for `argv`: `{ run: [command, ...args] }`, `{ skip: why }`, or `{ refuse: why }`. */
export function decide(argv) {
  const split = argv.indexOf('--')
  const [shard, on, ...extra] = split < 0 ? argv : argv.slice(0, split)
  const command = split < 0 ? [] : argv.slice(split + 1)
  if (!SHARD.test(shard ?? '') || !SHARD.test(on ?? '') || extra.length > 0 || command.length === 0) {
    return { refuse: 'usage: node e2e/on-shard.mjs <this shard> <runs on> -- <command> [args...]' }
  }
  if (shard !== on) return { skip: `shard ${shard}: this step runs on shard ${on} alone` }
  return { run: command }
}

export function main(argv, run = (command, args) => spawnSync(command, args, { stdio: 'inherit' })) {
  const decided = decide(argv)
  if (decided.refuse) {
    console.error(decided.refuse)
    return 2
  }
  if (decided.skip) {
    console.log(decided.skip)
    return 0
  }
  const [command, ...args] = decided.run
  const done = run(command, args)
  if (done.error) {
    console.error(`${command} could not be started: ${done.error.message}`)
    return 2
  }
  return done.status ?? 1
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2))
}
