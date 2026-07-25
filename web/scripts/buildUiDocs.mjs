// Builds web/dist/ui-docs.json — the documentation-as-data artifact the gateway
// serves and the Python UiDocsToolProvider (ui_search/ui_get) reads
// (Platform-Legibility §5). Two inputs are fused here:
//
//   1. The hand-authored SEMANTIC layer: every `web/src/ui/<Name>.doc.ts` default-
//      exports a UiDoc (or UiDoc[]) with keywords / prose / per-prop descriptions /
//      Do-Don't / anatomy.
//   2. The compiler-DERIVED structural layer: extractUiProps() reads the TypeScript
//      source and reports each component's own props with their `type`/`required`.
//
// The two are merged by prop name, so `type`/`required` are never hand-maintained
// (they can't rot) and the drift test guarantees the authored prop set equals the
// compiler's — a prop can't be added to a component without being documented.
//
// Also serialized: the design TOKENS registry, so an app-building agent can look up
// the tunable design tokens the same way it looks up components.
//
// Node/ESM build tool (esbuild + typescript); NOT part of the shipped SPA bundle.
import esbuild from 'esbuild'
import { readdirSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { extractUiProps } from './extractUiProps.mjs'

/**
 * @param {string} webDir absolute path to the web/ package root
 * @returns {Promise<{ json: object, path: string, componentCount: number }>}
 */
export async function buildUiDocs(webDir) {
  const uiDir = join(webDir, 'src', 'ui')
  const distDir = join(webDir, 'dist')

  // 1. Compiler-derived props (name/type/required) per component + file→exports map.
  const { components: derived, files } = extractUiProps(uiDir)

  // 2. Bundle every *.doc.ts (+ the TOKENS registry) into one ESM module and import
  //    it via a data: URL — no temp files. The doc files only `import type`, so they
  //    are pure data once TS types are erased; TOKENS is plain data too.
  const docFiles = readdirSync(uiDir)
    .filter((f) => /\.doc\.ts$/.test(f))
    .sort()
  const tokensPath = resolve(webDir, 'src', 'design', 'tokenRegistry.ts')
  const entryLines = [`import { TOKENS } from ${JSON.stringify(tokensPath)}`]
  docFiles.forEach((f, i) => {
    entryLines.push(`import d${i} from ${JSON.stringify(join(uiDir, f))}`)
  })
  entryLines.push(`export const tokens = TOKENS`)
  entryLines.push(
    `export const docs = [${docFiles.map((f, i) => `{ file: ${JSON.stringify(f)}, mod: d${i} }`).join(', ')}]`
  )

  const built = await esbuild.build({
    stdin: { contents: entryLines.join('\n'), resolveDir: webDir, loader: 'ts' },
    bundle: true,
    format: 'esm',
    platform: 'node',
    write: false,
    logLevel: 'silent',
  })
  const code = built.outputFiles[0].text
  const mod = await import(
    'data:text/javascript;charset=utf-8,' + encodeURIComponent(code)
  )
  const tokens = mod.tokens

  // 3. Merge authored docs with derived props (keyed by component name).
  const componentsOut = []
  for (const { file, mod: exported } of mod.docs) {
    const list = Array.isArray(exported) ? exported : [exported]
    for (const doc of list) {
      const derivedProps = derived[doc.name]
      if (!derivedProps) {
        // The drift test is the enforcer; here we just skip a doc with no matching
        // component so a stray/renamed doc can't crash the build.
        continue
      }
      const authoredByName = new Map((doc.props || []).map((p) => [p.name, p]))
      const props = derivedProps.map((dp) => ({
        name: dp.name,
        description: authoredByName.get(dp.name)?.description ?? '',
        type: dp.type,
        required: dp.required,
      }))
      componentsOut.push({
        name: doc.name,
        file,
        source: sourceFileFor(files, doc.name),
        keywords: doc.keywords || [],
        description: doc.description || '',
        props,
        bestPractices: doc.bestPractices || [],
        anatomy: doc.anatomy || [],
      })
    }
  }
  componentsOut.sort((a, b) => a.name.localeCompare(b.name))

  const json = {
    generator: 'web/scripts/buildUiDocs.mjs',
    note: 'Documentation-as-data for the web/src/ui kit. Prop type/required are derived from the TypeScript source; everything else is authored in <Name>.doc.ts.',
    componentCount: componentsOut.length,
    tokens,
    components: componentsOut,
  }

  mkdirSync(distDir, { recursive: true })
  const outPath = join(distDir, 'ui-docs.json')
  writeFileSync(outPath, JSON.stringify(json, null, 2) + '\n')
  return { json, path: outPath, componentCount: componentsOut.length }
}

// Reverse the file→exports map to find which .tsx a component came from.
function sourceFileFor(files, name) {
  for (const [file, names] of Object.entries(files)) {
    if (names.includes(name)) return file
  }
  return ''
}

// Allow `node scripts/buildUiDocs.mjs` for manual regen / debugging.
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const webDir = resolve(process.argv[2] || '.')
  buildUiDocs(webDir).then(({ path, componentCount }) => {
    // eslint-disable-next-line no-console
    console.log(`ui-docs.json: ${componentCount} components → ${path}`)
  })
}
