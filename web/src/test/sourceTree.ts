import { readdirSync, readFileSync } from 'node:fs'
import { basename, join, relative, resolve, sep } from 'node:path'
import { inject } from 'vitest'

// ── The source tree, as the tests that scan it read it ──────────────────────────────────────────
//
// Many tests here read the tree rather than a list of files, because a rail over an enumerated
// list cannot see a file nobody thought to list. Each of them used to walk it with a private
// recursive `readdirSync`, and read every file it kept once per pattern it looked for: one test
// made sixteen passes over the files of `src/` and `e2e/`, reading each file afresh every time. On
// a loaded machine that took 22.7 s against a 20 s budget, and failed a run in which no assertion
// failed.
//
// So there is one walker and one reader, and both remember. A file is read once per test file:
// Vitest runs each test file in a module context of its own, so a test file is as wide as a cache
// in memory can be. A test file that scans the tree declares `// @module-tag tree-scan`, and its
// tests get the budget `vitest.config.ts` gives that tag.
//
// The listing is shared more widely than that. `sourceTreeIndex.ts`, the run's `globalSetup`,
// lists `src/` and `e2e/` ONCE per run with `listTree` below and hands the listing to every test
// file (Vitest's `provide`/`inject`), so a test file that scans a folder under either reads it off
// that listing instead of walking it again. Watch mode lists them again before each rerun. The
// texts are NOT in the index: together they come to 22 MB, which every test file would parse
// whether it read them or not, so each test file still reads only the files it asks for. A folder
// outside the index (a scratch tree a test made) is walked here, once per test file.
//
// The order is the order every private walker had: depth first, each directory's entries sorted by
// name (Node lists them that way), a subdirectory's files in place of its entry. Paths are joined
// onto `dir` as given, as theirs were. A symbolic link is listed as a file and never followed, so
// a link cannot make the walk loop.

/** What the run's `globalSetup` hands every test file: each indexed folder (absolute) and every
 *  file under it, in walk order. */
export type TreeIndex = Record<string, readonly string[]>

declare module 'vitest' {
  interface ProvidedContext {
    sourceTree: TreeIndex
  }
}

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

/** Each folder in `roots` (absolute) and every file under it, walked once: the run's index. */
export function listTree(roots: readonly string[]): TreeIndex {
  return Object.fromEntries(roots.map((root) => [root, walk(root, [])]))
}

/** The run's index, when this module runs in a test file under it: absent outside a test run
 *  (`vitest.config.ts` reads the tree through this module too), or in a run that indexes nothing. */
function runIndex(): TreeIndex | undefined {
  if (!process.env.VITEST_WORKER_ID) return undefined
  return inject('sourceTree')
}

/** The files under `dir` read off the run's index, joined onto `dir` as given, or undefined when
 *  `dir` is in no indexed folder. */
function fromIndex(dir: string): string[] | undefined {
  const index = runIndex()
  if (!index) return undefined
  const absolute = resolve(dir)
  for (const [root, files] of Object.entries(index)) {
    if (absolute !== root && !absolute.startsWith(root + sep)) continue
    const within = absolute === root ? files : files.filter((path) => path.startsWith(absolute + sep))
    return within.map((path) => join(dir, relative(absolute, path)))
  }
  return undefined
}

/** Every file under `dir`, listed once per test file. A directory inside one already listed is
 *  read off that listing rather than walked again, and one inside the run's index is not walked
 *  at all. */
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
  const files = fromIndex(root) ?? walk(root, [])
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
