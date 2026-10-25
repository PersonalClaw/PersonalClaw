// Lists every baseline file a Refresh visual baselines run changed (#3943), for its log and its
// job summary, so the reviewer reads what the artifact holds before downloading it.
//
//     node e2e/baseline-changes.mjs
//
// It reads `git status` over the files a refresh writes: the screenshots, the accessibility trees,
// the matrix's findings and the environment manifest. Each changed file is one line, added,
// changed or deleted, printed and, when GITHUB_STEP_SUMMARY names a file, appended to it. A refresh
// that changed nothing says so. It exits 1 only when git cannot be read: the list is what a
// reviewer goes by, so a list it could not make is a red, never an empty one.
// web/src/design/baselineChanges.test.ts runs it against a planted repository.
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** What a refresh writes, from web/. */
export const WRITTEN = ['e2e/visual-environment.json', 'e2e/__screenshots__', 'e2e/__aria__', 'e2e/__ui-baselines__']

const KINDS = { '??': 'added', A: 'added', M: 'changed', T: 'changed', D: 'deleted' }

/** The changes in `git status --porcelain=v1 -z` output, by path, sorted. */
export function parseStatus(text) {
  const out = []
  for (const entry of text.split('\0')) {
    if (!entry) continue
    const code = entry.slice(0, 2)
    const kind = KINDS[code] ?? KINDS[code.trim()] ?? 'changed'
    out.push({ kind, path: entry.slice(3) })
  }
  return out.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
}

/** The summary's Markdown: a count and one line per changed file. */
export function summary(changes) {
  if (changes.length === 0) {
    return '### No baseline changed\n\nThe committed set already matches what the pinned browser drew.\n'
  }
  const lines = changes.map((c) => `- ${c.kind} \`${c.path}\``)
  const n = changes.length
  return `### ${n} baseline file${n === 1 ? '' : 's'} changed\n\n${lines.join('\n')}\n`
}

export function main(options = {}) {
  const web = options.web ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const env = options.env ?? process.env
  const log = options.log ?? ((line) => console.log(line))
  const run = options.run ?? spawnSync
  const done = run('git', ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--', ...WRITTEN], { cwd: web, encoding: 'utf8' })
  if (done.error || done.status !== 0) {
    log(`::error title=baseline changes::git status could not be read: ${done.error?.message ?? String(done.stderr).trim()}`)
    return 1
  }
  const text = summary(parseStatus(done.stdout))
  log(text)
  if (env.GITHUB_STEP_SUMMARY) fs.appendFileSync(env.GITHUB_STEP_SUMMARY, `${text}\n`)
  return 0
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main()
}
