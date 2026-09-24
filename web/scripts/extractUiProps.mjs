// Shared TypeScript prop extractor for the UI-docs pipeline (Platform-Legibility §5).
//
// The single source of truth for "what props does a `ui/` primitive actually
// accept." Used by BOTH the Vite build plugin (which merges derived type/required
// into web/dist/ui-docs.json) and the vitest drift test (which asserts the
// hand-authored doc objects name exactly these props). Deriving props from the
// compiler — never a hand-maintained list — is the §1 thesis ("describe the
// surface FROM the source; drift is a test failure") applied to UI props: a prop
// added to a component but not documented reddens the build, and the type/required
// half can never go stale because it is computed, not authored.
//
// Node/ESM only (imports `typescript` + `node:fs`); NOT part of the shipped SPA.
import ts from 'typescript'
import { readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

// A prop whose declaration lives outside web/src (a React/DOM attribute pulled in
// by an intersection like `& React.HTMLAttributes<…>`) is inherited plumbing, not
// an authored part of the component's own contract — exclude it. `key`/`ref` are
// React-injected on every element and never authored props.
const REACT_INJECTED = new Set(['key', 'ref'])

/**
 * @param {string} uiDir absolute path to web/src/ui
 * @returns {{ components: Record<string, {name: string, type: string, required: boolean}[]>, files: Record<string, string[]> }}
 *   `components`: componentName → its own authored props (name/type/required).
 *   `files`: relative filename → the component names it exports (drives per-file docs).
 */
export function extractUiProps(uiDir) {
  const srcRoot = resolve(uiDir, '..') + '/' // web/src/ — the "authored here" boundary
  const files = readdirSync(uiDir).filter(
    (f) => /\.tsx?$/.test(f) && !/\.(test|doc)\.tsx?$/.test(f)
  )
  const rootFiles = files.map((f) => join(uiDir, f))

  const program = ts.createProgram(rootFiles, {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    noEmit: true,
    skipLibCheck: true,
    esModuleInterop: true,
    strict: false,
  })
  const checker = program.getTypeChecker()

  // A prop counts as "authored by this component" if at least one of its symbol's
  // declarations sits under web/src (i.e. not node_modules / lib.dom.d.ts). This is
  // what separates `variant` (authored in Button.tsx) from the hundreds of DOM
  // attributes a `& React.HTMLAttributes` spread drags in.
  const isOwnProp = (sym) => {
    const decls = sym.getDeclarations() || []
    return decls.some((d) => {
      const fn = d.getSourceFile().fileName
      return fn.startsWith(srcRoot) && !fn.includes('node_modules')
    })
  }

  // Resolve a props-parameter type for an exported symbol, or null if the symbol
  // is not a function/arrow component (e.g. an exported type alias or const data).
  const propsFor = (sym) => {
    for (const d of sym.getDeclarations() || []) {
      let fnNode
      if (ts.isFunctionDeclaration(d) || ts.isArrowFunction(d) || ts.isFunctionExpression(d)) {
        fnNode = d
      } else if (ts.isVariableDeclaration(d) && d.initializer) {
        fnNode = d.initializer
      }
      if (!fnNode) continue
      const sigs = checker.getTypeAtLocation(fnNode).getCallSignatures()
      if (!sigs.length) continue
      const params = sigs[0].getParameters()
      if (!params.length) return [] // a zero-arg component (SystemWidget, Toaster, …)
      const pSym = params[0]
      const pType = checker.getTypeOfSymbolAtLocation(pSym, pSym.valueDeclaration || fnNode)
      return checker
        .getPropertiesOfType(pType)
        .filter(isOwnProp)
        .filter((p) => !p.getName().startsWith('__') && !REACT_INJECTED.has(p.getName()))
        .map((p) => {
          const t = checker.getTypeOfSymbolAtLocation(p, p.valueDeclaration || fnNode)
          // `?`-optional OR a union that includes undefined ⇒ not required.
          const optional = (p.flags & ts.SymbolFlags.Optional) !== 0
          return {
            name: p.getName(),
            type: normalizeType(checker.typeToString(t)),
            required: !optional,
          }
        })
        .sort((a, b) => a.name.localeCompare(b.name))
    }
    return null
  }

  /** @type {Record<string, {name:string,type:string,required:boolean}[]>} */
  const components = {}
  /** @type {Record<string, string[]>} */
  const fileExports = {}

  for (const rf of rootFiles) {
    const sf = program.getSourceFile(rf)
    if (!sf) continue
    const modSym = checker.getSymbolAtLocation(sf)
    if (!modSym) continue
    const rel = rf.slice(uiDir.length + 1)
    for (const ex of checker.getExportsOfModule(modSym)) {
      const nm = ex.getName()
      if (!/^[A-Z]/.test(nm)) continue // components are PascalCase; skip helpers/hooks/types
      const props = propsFor(ex)
      if (props === null) continue // exported type/const, not a component
      components[nm] = props
      ;(fileExports[rel] ||= []).push(nm)
    }
  }
  return { components, files: fileExports }
}

// The compiler prints `undefined`-including unions verbosely (`"a" | "b" | undefined`)
// and injects whitespace; trim the `| undefined` tail (redundant with `required`)
// and collapse to a compact single-line string for the docs JSON.
function normalizeType(t) {
  return t
    .replace(/\s*\|\s*undefined\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
