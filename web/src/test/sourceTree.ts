import { readdirSync, readFileSync } from 'node:fs'
import { basename, join, resolve, sep } from 'node:path'

// ── The source tree, as the tests that scan it read it ──────────────────────────────────────────
//
// Many tests here read the tree rather than a list of files, because a rail over an enumerated
// list cannot see a file nobody thought to list. Each of them used to walk it with a private
// recursive `readdirSync`, and read every file it kept once per pattern it looked for: one test
// made sixteen passes over the files of `src/` and `e2e/`, reading each file afresh every time. On
// a loaded machine that took 22.7 s against a 20 s budget, and failed a run in which no assertion
// failed.
//
// So there is one walker and one reader, and both remember. A directory is listed once and a file
// is read once per test file: Vitest runs each test file in a worker of its own, so a test file is
// as wide as a cache in memory can be. A test file that scans the tree declares
// `// @module-tag tree-scan`, and its tests get the budget `vitest.config.ts` gives that tag.
//
// The order is the order every private walker had: depth first, each directory's entries sorted by
// name (Node lists them that way), a subdirectory's files in place of its entry. Paths are joined
// onto `dir` as given, as theirs were. A symbolic link is listed as a file and never followed, so
// a link cannot make the walk loop.

const listed = new Map<string, readonly string[]>()
const texts = new Map<string, string>()

function walk(dir: string, out: string[]): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) walk(path, out)
    else out.push(path)
  }
  return out
}

/** Every file under `dir`, listed once per test file. A directory inside one already listed is
 *  read off that listing rather than walked again. */
function everyFileUnder(dir: string): readonly string[] {
  const root = join(dir)
  const known = listed.get(root)
  if (known) return known
  for (const [outer, files] of listed) {
    if (root.startsWith(outer + sep)) {
      const within = files.filter((path) => path.startsWith(root + sep))
      listed.set(root, within)
      return within
    }
  }
  const files = walk(root, [])
  listed.set(root, files)
  return files
}

/** The files under `dir` that `keep` accepts, given each file's name and its path. */
export function filesUnder(dir: string, keep: (name: string, path: string) => boolean = () => true): string[] {
  return everyFileUnder(dir).filter((path) => keep(basename(path), path))
}

/** The UTF-8 text of the file at `path`, read once per test file. */
export function readSource(path: string): string {
  const key = resolve(path)
  let text = texts.get(key)
  if (text === undefined) {
    text = readFileSync(path, 'utf8')
    texts.set(key, text)
  }
  return text
}
