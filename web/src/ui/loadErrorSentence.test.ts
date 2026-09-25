import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── LoadError's `what` is interpolated into prose, so it has a grammar ───────────────────────────
//
// `<LoadError what={x} />` renders `Couldn't load your {x}`. That is a copy surface wearing a prop:
// every value has to read as a sentence, and NO per-call assertion catches this because they all
// match a noun regex — cycle ux-669 shipped `what="the inbox"` with every test green and the screen
// reading "Couldn't load your the inbox". This rail reads the composed sentence instead of the prop.
//
// The contract (also in the prop's doc comment): lowercase, no leading article. The headline always
// renders, so an article there is always visible. Plurality is NOT required anymore — the fallback
// body was reworded off the noun (see below), so "Couldn't load your project" is correct and "Your
// project are safe" no longer exists to be wrong.
//
// Census this cycle (dev gateway + a proxy 500-ing only each surface's endpoint):
//   #/settings/guardrails  "Couldn't load your the autonomy ladder"   → fixed to "autonomy ladder"
//   #/apps                 "…your the Store catalog"                   → "Store catalog"
//   #/settings/packs       "…your the pack catalog"                    → "pack catalog"
// Nine singular values ("project", "audit log", …) were left AS-IS on purpose: the primitive change
// makes them grammatical without touching a single caller — the canonical "converge on the primitive"
// move rather than editing nine files.

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) out.push(p)
  }
  return out
}

/** Every literal and expression value passed to a LoadError `what`, with its file. */
function whatValues(): { rel: string; value: string }[] {
  const out: { rel: string; value: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(/<LoadError\b[^>]*?\bwhat=(?:"([^"]*)"|\{([^}]*)\})/gs)) {
      const rel = abs.slice(SRC.length + 1)
      if (m[1] != null) out.push({ rel, value: m[1] })
      // An expression noun (`collectionTok ? 'a' : 'b'`, `isSnips ? 'x' : 'y'`) — pull each string literal.
      else for (const lit of (m[2] ?? '').matchAll(/'([^']+)'|"([^"]+)"/g)) out.push({ rel, value: lit[1] ?? lit[2] })
    }
  }
  return out
}

describe("LoadError's what composes a grammatical headline", () => {
  const values = whatValues()

  it('finds the population — the scan is not vacuous', () => {
    // 57 call sites today; strings + both arms of every expression. A floor well under that so a
    // refactor that renames the component does not silently pass an empty census.
    expect(values.length, 'LoadError what= values across the tree').toBeGreaterThanOrEqual(45)
  })

  it('no value carries a leading article OR a leading `your` — the headline always renders', () => {
    // 🪤 `your` WAS THE HOLE, AND THE TEMPLATE ITSELF IS WHAT MAKES IT ONE (#3394, 2026-09-24). The
    // headline is `Couldn't load your {what}`, so the determiner the rail has to worry about is not
    // only the four articles — it is anything the template already supplies, and it supplies exactly
    // one: `your`. A value of `"your edit history"` renders **"Couldn't load your your edit
    // history"**, which is the same defect as `"the store catalog"` and was not caught, because the
    // list of banned words had been written from the one instance that had occurred rather than from
    // the sentence. Measured when it was widened: **2** sites in the tree, both landing in the same
    // commit that widened it, and **0** pre-existing — so this cost nobody a rewrite and would have
    // cost two if it had waited.
    //
    // ⚠️ SCOPED TO `LoadError`, AND THAT IS LOAD-BEARING. `whatValues()` matches `<LoadError\b`, which
    // does NOT match `<InlineLoadError` (no word boundary between `e` and `L`), and the distinction is
    // real rather than incidental: `InlineLoadError` composes through `loadErrorMessage`, which emits
    // `Couldn't load {what}.` with no possessive — so `what="your agents"` is CORRECT there and is in
    // the tree. Banning `your` tree-wide across both components would red a grammatical site.
    const leadingDeterminer = /^(the|this|a|an|your)\s/i
    // POSITIVE CONTROL ON THE PREDICATE, not just on the population. A zero here is only news if the
    // regex can still fire, and a negated assertion that cannot match reads exactly like a clean tree.
    // Every banned word gets a hit, and the near-misses get a miss — `a` must not swallow `agents`,
    // and `your` must not swallow `yourself`.
    for (const w of ['the store catalog', 'this prompt', 'a project', 'an agent', 'your edit history']) {
      expect(leadingDeterminer.test(w), `the predicate must still catch "${w}"`).toBe(true)
    }
    for (const w of ['agents', 'theme settings', 'answers', 'anagram list', 'projects']) {
      expect(leadingDeterminer.test(w), `and must NOT catch "${w}"`).toBe(false)
    }
    const bad = values.filter((v) => leadingDeterminer.test(v.value))
      .map((v) => `${v.rel}: "Couldn't load your ${v.value}"`)
    expect(bad, `a leading article or possessive makes the headline ungrammatical:\n${bad.join('\n')}`).toEqual([])
  })

  it('every value is lowercase-leading unless it is a proper noun', () => {
    // "Store catalog" is allowed (Store is the product surface's name); a value that is ALL-lowercase
    // except an accidental capital is what this would catch. Kept light — proper nouns are legitimate.
    const shouty = values.filter((v) => /^[A-Z]{2,}/.test(v.value)).map((v) => `${v.rel}: ${v.value}`)
    expect(shouty, 'no SHOUTING nouns').toEqual([])
  })

  it('the fallback reassurance no longer interpolates the noun', () => {
    // The bug this rail exists for was in TWO templates; the headline is asserted above, and the body
    // is asserted here by pinning that it dropped the `${what}` that made it "Your <noun> are safe".
    // 🪤 Strip comments first — the doc comment QUOTES the old "Your ${what} are safe" to explain the
    // change, and a raw scan would flag its own explanation (the same trap ux-669's rails hit twice).
    const scaffold = readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = /The server didn't respond[^`"]*/.exec(scaffold)?.[0] ?? ''
    expect(body, 'the fallback must still exist').toContain('load error')
    expect(body, 'and must not put a caller noun into "are safe"').not.toMatch(/\$\{\s*what/)
    expect(scaffold, 'no "<noun> are safe" survives in the primitive CODE').not.toMatch(/\$\{what\}\s+are safe/)
  })
})
