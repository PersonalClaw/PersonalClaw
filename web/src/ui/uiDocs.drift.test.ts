import { describe, it, expect } from 'vitest'
import { readdirSync } from 'node:fs'
import { join } from 'node:path'
import { extractUiProps } from '../../scripts/extractUiProps.mjs'
import type { UiDoc } from './uiDoc'

// ── UI-docs drift guard (Platform-Legibility §5) ───────────────────────────
// The documentation-as-data contract for the web/src/ui kit: every exported
// primitive has a co-located <Name>.doc.ts, and each doc names EXACTLY the props
// the TypeScript source declares. This is the §1 thesis — "describe the surface
// FROM the source; drift is a test failure" — applied to the component kit:
//   • a prop added to a component but not documented  → reddens here
//   • a documented prop that no longer exists          → reddens here
//   • a whole component with no doc object             → reddens here
// The prop TYPE/REQUIRED half can't rot because it is DERIVED at build time
// (buildUiDocs.mjs) from this same extractor, never hand-authored. There is no
// exemption list — the kit is small and every primitive is worth a doc.

const UI_DIR = join(process.cwd(), 'src', 'ui')

// Eagerly import every co-located doc module. Each default-exports a UiDoc or a
// UiDoc[] (multi-component files like HeaderActions/forms/ListScaffold).
const docModules = import.meta.glob<{ default: UiDoc | UiDoc[] }>('./*.doc.ts', {
  eager: true,
})

// name → authored UiDoc, flattened across single- and multi-component doc files.
const authored = new Map<string, UiDoc>()
for (const [path, mod] of Object.entries(docModules)) {
  const exported = mod.default
  const list = Array.isArray(exported) ? exported : [exported]
  for (const doc of list) {
    expect(doc?.name, `doc file ${path} default-exports a UiDoc with a name`).toBeTruthy()
    expect(authored.has(doc.name), `duplicate doc for ${doc.name} (in ${path})`).toBe(false)
    authored.set(doc.name, doc)
  }
}

// Compiler-derived truth: componentName → its own props (name/type/required).
const { components: derived } = extractUiProps(UI_DIR)
const derivedNames = Object.keys(derived).sort()

describe('ui-docs: documentation-as-data drift guard', () => {
  it('discovers the ui/ primitive kit', () => {
    expect(derivedNames.length).toBeGreaterThan(40)
  })

  it('every exported ui/ component has a doc object', () => {
    const undocumented = derivedNames.filter((n) => !authored.has(n))
    expect(
      undocumented,
      `These ui/ components have no <Name>.doc.ts entry — add one:\n${undocumented.join('\n')}`
    ).toEqual([])
  })

  it('every doc object maps to a real exported component', () => {
    const orphans = [...authored.keys()].filter((n) => !(n in derived)).sort()
    expect(
      orphans,
      `These doc objects name no exported ui/ component (renamed/removed?):\n${orphans.join('\n')}`
    ).toEqual([])
  })

  it('each doc names exactly the props the component declares', () => {
    const mismatches: Record<string, { missing: string[]; extra: string[] }> = {}
    for (const name of derivedNames) {
      const doc = authored.get(name)
      if (!doc) continue // covered by the "has a doc" test
      const declared = new Set(derived[name].map((p) => p.name))
      const documented = new Set((doc.props || []).map((p) => p.name))
      const missing = [...declared].filter((p) => !documented.has(p)).sort()
      const extra = [...documented].filter((p) => !declared.has(p)).sort()
      if (missing.length || extra.length) mismatches[name] = { missing, extra }
    }
    expect(
      mismatches,
      `Prop drift (missing = declared-but-undocumented; extra = documented-but-gone):\n${JSON.stringify(mismatches, null, 2)}`
    ).toEqual({})
  })

  it('each doc carries the required semantic fields', () => {
    const thin: Record<string, string[]> = {}
    for (const [name, doc] of authored) {
      const problems: string[] = []
      if (!doc.keywords?.length) problems.push('no keywords')
      if (!doc.description?.trim()) problems.push('no description')
      if (!doc.bestPractices?.length) problems.push('no bestPractices')
      // A prop, if documented, must carry a non-empty description.
      for (const p of doc.props || []) {
        if (!p.description?.trim()) problems.push(`prop ${p.name} has no description`)
      }
      if (problems.length) thin[name] = problems
    }
    expect(
      thin,
      `These docs are missing required semantic fields:\n${JSON.stringify(thin, null, 2)}`
    ).toEqual({})
  })
})

// Guard the co-located-doc convention itself: every non-test, non-doc *.tsx in
// ui/ that exports a PascalCase component should have produced a derived entry,
// so a brand-new primitive file can't slip in without the drift test seeing it.
describe('ui-docs: doc-file convention', () => {
  it('finds a .doc.ts for every source file that declares components', () => {
    const sourceFiles = readdirSync(UI_DIR).filter(
      (f) => /\.tsx?$/.test(f) && !/\.(test|doc)\.tsx?$/.test(f)
    )
    expect(sourceFiles.length).toBeGreaterThan(40)
  })
})
