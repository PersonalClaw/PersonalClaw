// @module-tag tree-scan
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { filesUnder, readSource } from './sourceTree'

// ── One walker and one reader for every test that scans the tree ────────────────────────────────
//
// The first half proves the walker lists what a recursive walk lists, in the order every private
// walker had, and that the walker and the reader remember. The second half is the rail that keeps
// it the ONLY walker: a test that walks the tree with a recursion of its own reads the tree again
// for itself, outside the cache and outside the tree-scan budget, which is how one such test came
// to take 22.7 s against a 20 s budget and fail a run in which no assertion failed.

const WEB = process.cwd()
const SRC = join(WEB, 'src')
/** This file, excluded from the census below: its controls spell out the walker shapes it bans. */
const SELF = join(SRC, 'test', 'sourceTree.test.ts')
const READER = join(SRC, 'test', 'sourceTree.ts')

/** Scratch trees, one per check that needs one, each removed after its test. */
const roots: string[] = []
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

function scratch(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), 'source-tree-'))
  roots.push(root)
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true })
    writeFileSync(join(root, rel), text)
  }
  return root
}

describe('filesUnder lists a tree the way every private walker did', () => {
  it('depth first, each directory by name, a folder\'s files where the folder is', () => {
    const root = scratch({ 'b.ts': '', 'a.ts': '', 'a/z.tsx': '', 'a/c/d.ts': '', '_x/y.ts': '' })
    expect(filesUnder(root).map((p) => relative(root, p).split(sep).join('/'))).toEqual([
      '_x/y.ts',
      'a/c/d.ts',
      'a/z.tsx',
      'a.ts',
      'b.ts',
    ])
  })

  it('joins onto the folder as it was given, and hands `keep` each file\'s name and path', () => {
    const root = scratch({ 'one.ts': '', 'two.tsx': '', 'deep/three.tsx': '' })
    const seen: [string, string][] = []
    const kept = filesUnder(root, (name, path) => {
      seen.push([name, path])
      return name.endsWith('.tsx')
    })
    expect(kept).toEqual([join(root, 'deep', 'three.tsx'), join(root, 'two.tsx')])
    expect(seen).toEqual([
      ['three.tsx', join(root, 'deep', 'three.tsx')],
      ['one.ts', join(root, 'one.ts')],
      ['two.tsx', join(root, 'two.tsx')],
    ])
    // A relative folder gives relative paths, as `join(dir, name)` did.
    const fromHere = relative(process.cwd(), root)
    expect(filesUnder(fromHere, (name) => name === 'one.ts')).toEqual([join(fromHere, 'one.ts')])
  })

  it('a folder inside one already listed comes out the same as listed on its own', async () => {
    const root = scratch({ 'a/one.ts': '', 'a/b/two.ts': '', 'c/three.ts': '' })
    const outerFirst = [filesUnder(root), filesUnder(join(root, 'a'))]
    vi.resetModules()
    const fresh = await import('./sourceTree')
    const innerFirst = [fresh.filesUnder(join(root, 'a')), fresh.filesUnder(root)]
    expect(outerFirst[1]).toEqual([join(root, 'a', 'b', 'two.ts'), join(root, 'a', 'one.ts')])
    expect(innerFirst[0]).toEqual(outerFirst[1])
    expect(innerFirst[1]).toEqual(outerFirst[0])
  })

  it('lists a link as a file and never follows it, so a link cannot make the walk loop', () => {
    const root = scratch({ 'a/one.ts': '' })
    symlinkSync(root, join(root, 'a', 'back'))
    expect(filesUnder(root).map((p) => relative(root, p).split(sep).join('/'))).toEqual(['a/back', 'a/one.ts'])
  })

  it('remembers a listing for the rest of the test file', () => {
    const root = scratch({ 'one.ts': '' })
    expect(filesUnder(root)).toHaveLength(1)
    writeFileSync(join(root, 'two.ts'), '')
    expect(filesUnder(root), 'the folder was listed again').toHaveLength(1)
  })
})

describe('readSource reads a file once', () => {
  it('returns the text, and the same text after the file changes', () => {
    const root = scratch({ 'one.ts': 'first' })
    expect(readSource(join(root, 'one.ts'))).toBe('first')
    writeFileSync(join(root, 'one.ts'), 'second')
    expect(readSource(join(root, 'one.ts')), 'the file was read again').toBe('first')
    // One entry per file, however its path is spelled.
    expect(readSource(relative(process.cwd(), join(root, 'one.ts')))).toBe('first')
    expect(readFileSync(join(root, 'one.ts'), 'utf8'), 'the control: the file did change').toBe('second')
  })
})

// ── The rail: there is one walker, and every tree scan runs on the tree-scan budget ─────────────

/** Comments blanked, so a sentence ABOUT a walker is not counted as one. */
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/(^|[^:])\/\/[^\n]*/g, '$1')

/** Whether `text` walks a directory tree itself: it lists a directory AND either asks which of
 *  its entries are directories (to descend into them) or asks the listing to recurse. A listing
 *  of one folder does neither. */
function walksTheTree(text: string): boolean {
  const c = code(text)
  const lists = /\b(?:readdirSync|readdir|opendirSync|opendir)\s*\(/.test(c)
  const descends = /\.isDirectory\(\)/.test(c)
  const recurses = /\b(?:readdirSync|readdir)\s*\([^)]*\brecursive\s*:/.test(c)
  return lists && (descends || recurses)
}

const SOURCES = filesUnder(SRC, (name) => /\.tsx?$/.test(name))
const SCANNED = SOURCES.filter((path) => path !== SELF)

/** Relative imports of a module, resolved to files under `src`. */
function importsOf(path: string): string[] {
  const out: string[] = []
  for (const m of code(readSource(path)).matchAll(/\bfrom\s+'(\.[^']+)'|\bimport\(\s*'(\.[^']+)'\s*\)/g)) {
    const spec = resolve(dirname(path), m[1] ?? m[2])
    const hit = [spec, `${spec}.ts`, `${spec}.tsx`, join(spec, 'index.ts'), join(spec, 'index.tsx')]
      .find((candidate) => SOURCES.includes(candidate))
    if (hit) out.push(hit)
  }
  return out
}

/** Every module that reads the tree through the shared reader, directly or through a module that
 *  does: the closure of "imports the reader" over the import graph. */
function treeReaders(): Set<string> {
  const readers = new Set([READER])
  for (let grew = true; grew;) {
    grew = false
    for (const path of SOURCES) {
      if (!readers.has(path) && importsOf(path).some((dep) => readers.has(dep))) {
        readers.add(path)
        grew = true
      }
    }
  }
  readers.delete(READER)
  return readers
}

const TAGGED = /(?:\/\/|\*)\s*@module-tag\s+tree-scan\b/

describe('the census of tree walks', () => {
  it('reads a real tree, and leaves out only this file', () => {
    expect(SOURCES.length, 'the walk found almost nothing — run vitest from web/').toBeGreaterThan(1500)
    expect(SOURCES).toContain(SELF)
    expect(SOURCES.length - SCANNED.length).toBe(1)
    expect(SCANNED).toContain(READER)
  })

  it('recognises every shape a private walker took, and not a listing of one folder', () => {
    // The control: without it, "no walker found" cannot be told from "the detector finds nothing".
    const walkers = [
      `const walk = (d: string): string[] =>
        readdirSync(d).flatMap((n) => {
          const p = join(d, n)
          if (statSync(p).isDirectory()) return walk(p)
          return /\\.tsx$/.test(n) ? [p] : []
        })`,
      `function walk(dir: string, out: string[] = []): string[] {
        for (const e of readdirSync(dir, { withFileTypes: true })) {
          if (e.isDirectory()) walk(join(dir, e.name), out)
          else out.push(join(dir, e.name))
        }
        return out
      }`,
      `const readSafe = (dir: string) => { try { return readdirSync(dir, { withFileTypes: true }) } catch { return [] } }
       const walkDir = (dir: string): void => { for (const e of readSafe(dir)) if (e.isDirectory()) walkDir(join(dir, e.name)) }`,
      `const all = readdirSync(SRC, { recursive: true })`,
      `for (const e of require('node:fs').readdirSync(d, { withFileTypes: true })) if (e.isDirectory()) walk(e)`,
    ]
    for (const walker of walkers) expect(walksTheTree(walker), walker).toBe(true)
    expect(walksTheTree(`const files = readdirSync(SETTINGS).filter((f) => /Panel\\.tsx$/.test(f))`)).toBe(false)
    expect(walksTheTree(`// a walker would call readdirSync(d) and then isDirectory()`)).toBe(false)
  })

  it('no file walks the tree itself: the shared walker is the only one', () => {
    const own = SCANNED.filter((path) => path !== READER && walksTheTree(readSource(path)))
    expect(
      own.map((path) => relative(WEB, path)),
      'these walk the tree with a recursion of their own; list it through filesUnder() from src/test/sourceTree.ts',
    ).toEqual([])
    // The control: the shared walker itself is found by the same detector.
    expect(walksTheTree(readSource(READER))).toBe(true)
  })

  it('every test file that reads the tree is tagged, and only those are', () => {
    const readers = treeReaders()
    const tests = SCANNED.filter((path) => /\.test\.tsx?$/.test(path))
    const readingTests = tests.filter((path) => readers.has(path))
    // The control: the population is the one this rail was written over, and a reader reached
    // only through another module is in it.
    expect(readingTests.length, 'almost no test reads the tree through the shared reader').toBeGreaterThan(150)
    expect(readingTests).toContain(join(SRC, 'pages', 'chat', 'indexTabRetired.test.tsx'))
    expect(readingTests).toContain(join(SRC, 'design', 'consistencyAudit.test.ts'))
    const untagged = readingTests.filter((path) => !TAGGED.test(readSource(path)))
    expect(untagged.map((path) => relative(WEB, path)), 'add `// @module-tag tree-scan` to these').toEqual([])
    const strays = tests.filter((path) => TAGGED.test(readSource(path)) && !readers.has(path))
    expect(strays.map((path) => relative(WEB, path)), 'tagged, but read no tree through the shared reader').toEqual([])
  })

  it('and the tag buys a budget longer than every other test\'s', ({ task }) => {
    const config = readSource(join(WEB, 'vitest.config.ts'))
    const general = Number(/\btestTimeout:\s*([\d_]+)/.exec(config)?.[1].replace(/_/g, ''))
    expect(general, 'vitest.config.ts states no testTimeout').toBeGreaterThan(0)
    expect(task.tags).toContain('tree-scan')
    expect(task.timeout, 'the tree-scan tag gives no budget of its own').toBeGreaterThan(general)
  })
})
