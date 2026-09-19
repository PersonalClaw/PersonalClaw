/**
 * Whitespace hygiene on the frontend, enforced by CI instead of by whoever reads the diff
 * (#1106).
 *
 * There is no JS/TS linter in this repo — no eslint, prettier or biome in
 * `web/package.json` — which is deliberate (#1106 declines to add one: a large dependency
 * plus a style argument, and a first run that rewrites hundreds of files, is not worth it
 * for whitespace). So the gap it leaves is closed the way the other 18 rails in this
 * directory close theirs: walk the tree with `node:fs` and assert per file, no new
 * dependency. `.editorconfig` beside `web/package.json` is the other half — it stops an
 * editor PRODUCING the problem; this turns it red when one ships anyway.
 *
 * The census comes from `git ls-files --cached --others --exclude-standard` rather than a
 * glob over `src`, because the regression that motivated this (PR #578 shipping two files
 * with no final newline) is not confined to `src` — `e2e/`, `scripts/` and the config files
 * at the workspace root are all hand-edited too, and a glob that named a subset would have
 * been green for a file it never looked at. `--others` is what makes a file added in THIS
 * working tree count: a rail that only saw committed files could not fail until after the
 * commit it exists to prevent. Ignored paths (`node_modules`, `dist`) are excluded by
 * `--exclude-standard`, and binary files are dropped by the NUL test below — without it the
 * PNG visual snapshots and the woff2 fonts fail all three checks on their payload bytes.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// `npm run test:web` runs vitest with cwd = web/ (the workspace), so the repository root is
// one level up. Resolving the census from the repo root is what lets the pathspec below be
// `web`, which keeps the reported offender paths repo-relative and clickable.
const WEB = process.cwd()
const REPO = join(WEB, '..')

const files = execFileSync(
  'git',
  ['ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'web'],
  { cwd: REPO },
)
  .toString('utf8')
  .split('\0')
  .filter(Boolean)

const textFiles = files
  .map((relativePath) => ({
    relativePath,
    contents: readFileSync(join(REPO, relativePath)),
  }))
  .filter(({ contents }) => !contents.includes(0))

describe('web text-file whitespace rail', () => {
  it('scans the whole web tree rather than a hand-picked source subset', () => {
    // Vacuity floor. Every assertion below is over `textFiles`, so a census that silently
    // collapsed — a renamed pathspec, a git invocation that returned nothing, a NUL filter
    // that ate the tree — would make all three pass while checking zero files. Measured at
    // 1558 text files of 1574 tracked-or-untracked paths when this landed.
    expect(textFiles.length, 'the tracked/unignored web text-file census collapsed').toBeGreaterThan(
      1_000,
    )
  })

  it('requires every non-empty text file to end with a newline', () => {
    const offenders = textFiles
      .filter(({ contents }) => contents.length > 0 && contents.at(-1) !== 0x0a)
      .map(({ relativePath }) => relativePath)

    expect(
      offenders,
      `Web text files missing a final newline:\n${offenders.join('\n')}`,
    ).toEqual([])
  })

  it('refuses spaces or tabs at the end of a line', () => {
    const offenders: string[] = []

    for (const { relativePath, contents } of textFiles) {
      contents
        .toString('utf8')
        .split('\n')
        .forEach((rawLine, index) => {
          // A CRLF file's `\r` is not trailing whitespace — it is a line ending, and the
          // check below is the one that owns it. Counting it here would report every line
          // of such a file twice and point at the wrong rule.
          const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
          if (/[ \t]+$/.test(line)) offenders.push(`${relativePath}:${index + 1}`)
        })
    }

    expect(
      offenders,
      `Web text lines with trailing whitespace:\n${offenders.join('\n')}`,
    ).toEqual([])
  })

  it('refuses CRLF line endings', () => {
    // The third check #1106 asks for, and the one with a knock-on: `.editorconfig` declares
    // `end_of_line = lf`, and a CRLF file also makes every "missing final newline" report
    // read oddly (the file ends `\r\n`, so it passes that check while still being wrong).
    const offenders = textFiles
      .filter(({ contents }) => contents.includes('\r\n'))
      .map(({ relativePath }) => relativePath)

    expect(offenders, `Web text files with CRLF line endings:\n${offenders.join('\n')}`).toEqual([])
  })
})
