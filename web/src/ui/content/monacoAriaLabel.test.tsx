import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Both Monaco editors announced the same generic name ────────────────────────
//
// Monaco DOES name its editing surface by default — but identically everywhere. Measured on the
// running build, driving each surface (both need a click: the Files editor only mounts in EDIT view,
// the gist form only after a type-tile click):
//
//     BEFORE   #/files → open a file → "Edit"     role=textbox  aria-label="Editor content"
//              #/knowledge/new → "Gist" tile      role=textbox  aria-label="Editor content"
//     AFTER                                                     "design-notes.md — editor"
//                                                               "Gist content (typescript)"
//
// So this is NOT an unnamed-control defect — it is a SPECIFICITY one. A screen-reader user moving
// between two open editors, or between a file and a gist, got no signal about which document they
// had landed in. Both mounts said the same four words.
//
// It is also not drift BETWEEN the two consumers: they were identical. It is one gap in a shared
// third-party integration, fixed at both call sites through Monaco's own `ariaLabel` option
// (`IEditorOptions.ariaLabel`, confirmed in monaco-editor's own .d.ts).
//
// Each name is derived from what actually distinguishes that editor:
//   · ContentSurface → `${title} — editor`, so it says WHICH file.
//   · GistEditor     → `Gist content (${language})`, the only thing telling two gist editors apart.
//
// GistEditor deliberately does NOT take an `ariaLabel` prop: both of its call sites pass `language`,
// so the derived name serves them both, and an override nothing passes would be a declared-but-
// unused surface (the writer-first rule).
//
// 🪤 TWO MEASUREMENT TRAPS, both hit for real before the numbers above were trustworthy:
//   1. The only `<textarea>` inside a Monaco mount is `.ime-text-area` — `readonly`, `tabindex=-1`,
//      `aria-hidden=true`. It is an IME helper, correctly hidden from assistive tech. A textarea
//      sweep "finds an unnamed control" here and it is a FALSE POSITIVE; naming it would be wrong.
//   2. This Monaco version uses `native-edit-context` (a DIV with `role=textbox`), not the older
//      `.inputarea` textarea. Querying by TAG finds nothing at all. Query by ROLE — which is what
//      assistive tech does.
// Together those two made the editor look unnamed when it was merely generically named.
//
// Why this test asserts the OPTION rather than the rendered name: Monaco needs real layout plus a
// web worker and does not run under jsdom, so a render assertion here would prove nothing. The
// rendered proof is the DOM probe above; this pins the wiring, and the RAIL below pins that no
// future Monaco mount can ship without a specific name.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('both Monaco consumers name their editing surface', () => {
  it('ContentSurface names the editor from the document title', () => {
    const src = read('ui/content/ContentSurface.tsx')
    expect(src).toMatch(/ariaLabel: `\$\{title\} — editor`/)
  })

  it('GistEditor names the editor from the gist language', () => {
    const src = read('pages/knowledge/GistEditor.tsx')
    expect(src).toMatch(/ariaLabel: language \? `Gist content \(\$\{language\}\)` : 'Gist content'/)
  })

  it('GistEditor exposes no ariaLabel PROP — nothing would pass it', () => {
    // Both call sites pass `language`. A prop with no caller is a surface that reads as configurable
    // while being dead; if a third consumer ever needs an override, it can add the prop then.
    const src = read('pages/knowledge/GistEditor.tsx')
    expect(/ariaLabel\?: string/.test(src), 'no callerless ariaLabel prop').toBe(false)
    // Match the whole element, NOT `<GistEditor[^>]*language=`: a negated-`>` class cannot cross
    // the `=>` inside `onChange={(v) => …}`, so that pattern silently fails on a call site that IS
    // correct. Grab the tag and check the attribute within it.
    for (const rel of ['pages/knowledge/KnowledgeCreatePage.tsx', 'pages/knowledge/KnowledgeDetail.tsx']) {
      const tag = read(rel).match(/<GistEditor\b[\s\S]*?\/>/)
      expect(tag, `${rel} should mount GistEditor`).toBeTruthy()
      expect(tag![0], `${rel} must pass language so the derived name is specific`).toMatch(/language=\{/)
    }
  })
})

describe('the rail: every Monaco mount names itself specifically', () => {
  it('no file mounts MonacoEditor without an ariaLabel', () => {
    // Comments are stripped first — the notes above NAME `ariaLabel` and `MonacoEditor`, and a bare
    // text search would count the explanation as compliance (the inverse of the trap from the last
    // three cycles, and just as wrong).
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const code = readFileSync(abs, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '')
      if (!/<MonacoEditor\b/.test(code)) continue
      if (!/ariaLabel:/.test(code)) offenders.push(abs.slice(SRC.length + 1))
    }
    expect(
      offenders,
      `These files mount MonacoEditor with no ariaLabel, so the editor falls back to Monaco's ` +
        `generic "Editor content" and announces the same name as every other editor:\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail actually finds the Monaco mounts (it is not vacuously green)', () => {
    // A rail that matches nothing passes forever. Pin that both known consumers are in scope.
    const mounting = walk(SRC).filter((abs) => /<MonacoEditor\b/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1)).sort()
    expect(mounting).toEqual(['pages/knowledge/GistEditor.tsx', 'ui/content/ContentSurface.tsx'])
  })
})
