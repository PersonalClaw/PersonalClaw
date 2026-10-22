// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { dirname, join, relative, resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { readSource } from '../../test/sourceTree'

// ── The machine is pure, and names every duration (#3890) ──────────────────────────────────────
//
// Two scans over the state machine's own files, in the style of the spring rail in
// `web/src/design/reducedMotionAppWide.test.ts`:
//
//   · No duration is a literal: outside `timing.ts`, the module's code holds no number but 0 and 1,
//     so a wait or a hold can only be one of the named tokens.
//   · No I/O: following `machine.ts`'s imports (type-only imports aside, which carry no code), no
//     file imports React or `web/src/lib/api.ts`, or calls `fetch(`. Its words come in a separate
//     file, `lines.ts`, which the machine never imports.
//
// Each is shown to fail on a planted defect.

const BUDDY = join(process.cwd(), 'src', 'lib', 'buddy')
const LIB = join(process.cwd(), 'src', 'lib')
/** The machine's own files: everything that decides what the buddy shows and when. */
const MODULE = ['machine.ts', 'focus.ts', 'alertQueue.ts', 'inputs.ts', 'timing.ts']

/** Code without its comments and its strings, so a number in a sentence is not a literal. */
function codeOf(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '')
    .replace(/`(?:\\.|[^`\\])*`/g, '``')
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""')
}

/** Every number in `source`'s code but 0 and 1. */
function literalsIn(source: string): string[] {
  return [...codeOf(source).matchAll(/(?<![\w$.])\d[\d_]*(?:\.\d+)?(?![\w$])/g)].map((m) => m[0]).filter((n) => n !== '0' && n !== '1')
}

function durationProblems(files: Record<string, string>): string[] {
  return Object.entries(files)
    .filter(([name]) => name !== 'timing.ts')
    .flatMap(([name, source]) => literalsIn(source).map((n) => `${name} holds the literal ${n}: name it in timing.ts`))
}

/** The code imports of `source` (relative ones resolved to files), type-only imports left out. */
function importsOf(file: string, source: string): string[] {
  return [...source.matchAll(/^import (?!type )[\s\S]*?from '([^']+)'/gm)]
    .map((m) => m[1])
    .map((spec) => (spec.startsWith('.') ? resolveFile(resolve(dirname(file), spec)) : spec))
}

function resolveFile(base: string): string {
  return ['.ts', '.tsx'].map((ext) => base + ext).find((f) => existsSync(f)) ?? base
}

/** Every way the code `machine.ts` reaches does I/O, renders, or reads the API. */
function purityProblems(read: (file: string) => string, entry: string): string[] {
  const found: string[] = []
  const seen = new Set<string>()
  const visit = (file: string) => {
    if (seen.has(file)) return
    seen.add(file)
    const source = read(file)
    const name = relative(LIB, file)
    if (/\bfetch\s*\(/.test(codeOf(source))) found.push(`${name} calls fetch(`)
    for (const spec of importsOf(file, source)) {
      if (spec === 'react' || spec.startsWith('react/') || spec === 'react-dom') found.push(`${name} imports React`)
      else if (resolve(spec) === join(LIB, 'api.ts')) found.push(`${name} imports lib/api.ts`)
      else if (spec.startsWith('/')) visit(spec)
    }
  }
  visit(entry)
  return found
}

const own = Object.fromEntries(MODULE.map((name) => [name, readSource(join(BUDDY, name))]))

describe('the state machine names every duration', () => {
  it('holds no literal number but 0 and 1 outside timing.ts', () => {
    expect(durationProblems(own)).toEqual([])
  })

  it('fails on a planted literal duration', () => {
    const planted = { ...own, 'machine.ts': `${own['machine.ts']}\nexport const LATER = (f: () => void) => setTimeout(f, 240)\n` }
    expect(durationProblems(planted)).toEqual(['machine.ts holds the literal 240: name it in timing.ts'])
  })

  it('reads a number in a comment or a string as words, not a literal', () => {
    expect(literalsIn("// waits 240 ms\nconst said = 'in 5 s'")).toEqual([])
    expect(literalsIn('const t = 60_000')).toEqual(['60_000'])
  })
})

describe('the state machine does no I/O', () => {
  const read = (file: string) => readSource(file)

  it('imports no React and no api.ts, and calls no fetch, through every file it reaches', () => {
    expect(purityProblems(read, join(BUDDY, 'machine.ts'))).toEqual([])
  })

  it('never reaches the words, which come through the copy module', () => {
    const reached: string[] = []
    purityProblems((file) => {
      reached.push(relative(BUDDY, file))
      return readSource(file)
    }, join(BUDDY, 'machine.ts'))
    expect(reached, 'the walk reaches the files the machine is made of').toEqual(expect.arrayContaining(['machine.ts', 'focus.ts', 'alertQueue.ts', '../workPhase.ts']))
    expect(reached).not.toContain('lines.ts')
    expect(reached).not.toContain('copy.ts')
  })

  it('fails on a planted React import, an api.ts import or a fetch', () => {
    const planted = (extra: string) => (file: string) => (file.endsWith('focus.ts') ? `${readSource(file)}\n${extra}\n` : readSource(file))
    expect(purityProblems(planted("import { useState } from 'react'"), join(BUDDY, 'machine.ts'))).toEqual(['buddy/focus.ts imports React'])
    expect(purityProblems(planted("import { api } from '../api'"), join(BUDDY, 'machine.ts'))).toEqual(['buddy/focus.ts imports lib/api.ts'])
    expect(purityProblems(planted("export const ask = () => fetch('/api/x')"), join(BUDDY, 'machine.ts'))).toEqual(['buddy/focus.ts calls fetch('])
  })
})
