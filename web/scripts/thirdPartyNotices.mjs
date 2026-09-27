// Writes the licence notices of every npm package whose code the web build emits:
// web/dist/THIRD_PARTY_NOTICES_NPM.txt for people, and THIRD_PARTY_NOTICES_NPM.json, the same
// census for tools and for `scripts/check_asset_licenses.py --built-web`.
//
// The minifier removes every comment, so the licence headers the packages ship never reach
// web/dist, and no package's LICENSE file does either. This file puts them beside the bundle.
//
// The census is what the BUNDLER emitted, not what package-lock.json lists. A package installed
// only for the build ships nothing, and one package name can be bundled from two installed
// copies at two versions (katex is, under rehype-katex and under mermaid). So it is read from
// each output chunk's own module list, in three places:
//
//   * the app build, and each web-worker build: Monaco's five workers are separate Rolldown
//     builds, which is why `worker.plugins` registers `workerPlugin()` as well;
//   * the stylesheets. A CSS module renders no JavaScript, so it is attributed to the CSS files
//     its chunk imports. A package stylesheet that a CSS compiler inlines through `@import`
//     (Tailwind's theme and preflight) never enters the module graph at all, so every `@import`
//     of a package is resolved here too;
//   * sw.js, which esbuild bundles on its own (`recordOutput`, from esbuild's metafile).
//
// A module belongs to the installed package it sits in: the longest package-lock.json key that
// prefixes its path, so a nested copy is told apart from the top-level one. Code a package ships
// inside a `node_modules` directory of its OWN archive (@antv/layout carries copies of lodash,
// dagre and five more) belongs to that copied library, and takes its licence from the copy of
// the library the carrying package resolves to. The modules the bundler itself injects (Vite's
// preload helper, Rolldown's runtime) belong to Vite and Rolldown.
//
// Anything it cannot attribute fails the build: a module outside the repository, a virtual
// module that renders code and names no package, a package with no licence id or no licence
// text. Where a package's archive leaves a licence fact out, web/npm-license-records.json
// supplies it, keyed by name@version, so an upgrade fails until the record is reviewed again.
//
// Node/ESM build tool; NOT part of the shipped SPA bundle.
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

export const NOTICES_TXT = 'THIRD_PARTY_NOTICES_NPM.txt'
export const NOTICES_JSON = 'THIRD_PARTY_NOTICES_NPM.json'
export const RECORDS_FILE = 'npm-license-records.json'

const RULE = '='.repeat(78)
const SUBRULE = '-'.repeat(78)
const WIDTH = 78
const LABEL = 9 // `Licence: ` — every header label is padded to this width

/** A file in a package's root that carries its licence terms or notices. */
const LICENSE_FILE =
  /^(?:licen[cs]e|copying|unlicense|notice|third[-_ ]?party[-_ ]?(?:licen[cs]es?|notices?))(?:[-_.][\w.-]*)?$/i
const NOT_A_LICENSE_FILE = /\.(?:[cm]?[jt]sx?|json|map)$/i

/** The virtual modules the bundler injects into the output, by id prefix, and the installed
 *  package whose code each one is. A chain is resolved name by name, each from the previous
 *  package's directory: Rolldown is the one Vite runs. */
const VIRTUAL_ORIGINS = [
  ['\0vite/', ['vite']], // preload-helper.js, modulepreload-polyfill.js
  ['__vite-browser-external', ['vite']], // the stub Vite substitutes for a Node built-in
  ['\0rolldown/runtime', ['vite', 'rolldown']],
]

const CSS_MODULE = /\.(?:css|less|sass|scss|styl|stylus|pcss|postcss|sss)$/
const CSS_IMPORT = /@import\s+(?:url\(\s*)?(['"])([^'"]+)\1/g
/** The built files a package's code can land in (scripts/check_asset_licenses.py's _CODE_SUFFIXES). */
const CODE_FILE = /\.(?:[cm]?js|css)$/i
const RECORD_FIELDS = new Set(['license', 'license_text', 'license_text_source', 'note'])

const toPosix = (path) => path.split(sep).join('/')
const stripQuery = (id) => id.split('?')[0]
const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))

/** A licence text as the notices carry it, and as the checker compares it: no byte-order mark,
 *  LF line endings, no trailing blanks on a line, no trailing blank lines. */
export function normalizeText(text) {
  return text.replace(/^\ufeff/, '').replace(/\r\n?/g, '\n').replace(/[ \t]+$/gm, '').replace(/\n+$/, '')
}

/** The licence a package.json declares, as one SPDX-style expression, or null. */
export function declaredLicense(pkg) {
  const one = (value) =>
    typeof value === 'string' ? value.trim() : typeof value?.type === 'string' ? value.type.trim() : ''
  if (pkg.license !== undefined && pkg.license !== null) return one(pkg.license) || null
  if (Array.isArray(pkg.licenses)) {
    const ids = pkg.licenses.map(one).filter(Boolean)
    if (ids.length === 1) return ids[0]
    if (ids.length > 1) return `(${ids.join(' OR ')})`
  }
  return null
}

/** The licence and notice files in a package's root, the licence itself first. */
export function licenseFilesIn(dir) {
  const rank = (name) => (/^(?:licen[cs]e|copying|unlicense)/i.test(name) ? 0 : /^notice/i.test(name) ? 1 : 2)
  return readdirSync(dir)
    .filter((name) => LICENSE_FILE.test(name) && !NOT_A_LICENSE_FILE.test(name) && statSync(join(dir, name)).isFile())
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

/** Where a package's source is published, as an https URL, from `repository` or `homepage`. */
export function sourceUrl(pkg) {
  const raw = (typeof pkg.repository === 'string' ? pkg.repository : pkg.repository?.url)?.trim()
  if (raw) {
    const short = /^(?:(github|gitlab|bitbucket):)?([\w.-]+\/[\w.-]+)$/.exec(raw)
    if (short) {
      const host = { github: 'github.com', gitlab: 'gitlab.com', bitbucket: 'bitbucket.org' }[short[1] ?? 'github']
      return `https://${host}/${short[2]}`
    }
    const url = raw
      .replace(/^git\+/, '')
      .replace(/^(?:git|ssh):\/\/(?:git@)?/, 'https://')
      .replace(/^git@([^:/]+):/, 'https://$1/')
      .replace(/\.git$/, '')
    if (/^https:\/\//.test(url)) return url
    if (/^http:\/\//.test(url)) return url.replace(/^http:/, 'https:')
  }
  return typeof pkg.homepage === 'string' && /^https?:\/\//.test(pkg.homepage) ? pkg.homepage : null
}

/** The licence and copyright comments in one source file, each as written.
 *
 *  A block comment counts when it is a legal comment by the minifiers' own rule (it opens with
 *  `/*!`, or says `@license` or `@preserve`), or when it states a copyright. It must open a line:
 *  a comment-looking run inside a string or a regular expression starts mid-line, and would
 *  otherwise swallow the code up to wherever the next comment closes. */
export function noticeComments(source) {
  const found = []
  for (const match of source.matchAll(/(?:^|\n)[ \t]*(\/\*[\s\S]*?\*\/)/g)) {
    const comment = match[1]
    if (/^\/\*!/.test(comment) || /@license\b|@preserve\b|\bcopyright\b|\(c\)\s*\d{4}|©/i.test(comment)) {
      found.push(normalizeText(comment))
    }
  }
  return found
}

/** Every script and stylesheet under `dir`, relative to it, sorted. */
function codeFilesIn(dir, prefix = '') {
  if (!existsSync(dir)) return []
  const found = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.isDirectory()) found.push(...codeFilesIn(join(dir, entry.name), rel))
    else if (entry.isFile() && CODE_FILE.test(entry.name)) found.push(rel)
  }
  return found.sort()
}

/** Wrap one sentence under a header label, the continuation lines indented to the value. */
function wrapLabelled(label, text) {
  const lines = []
  let line = ''
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (line && LABEL + line.length + 1 + word.length > WIDTH) {
      lines.push(line)
      line = word
    } else {
      line = line ? `${line} ${word}` : word
    }
  }
  if (line) lines.push(line)
  return lines.map((l, i) => (i === 0 ? label.padEnd(LABEL) : ' '.repeat(LABEL)) + l).join('\n')
}

/**
 * The notices collector the Vite config wires in.
 *
 * @param {string} webDir absolute path to the web/ package root
 * @param {{ repoRoot?: string }} [options] where package-lock.json lives (default: web/..)
 */
export function thirdPartyNotices(webDir, { repoRoot = resolve(webDir, '..') } = {}) {
  /** Output file (relative to the build's outDir) → ids of the modules whose code is in it. */
  let outputs = new Map()
  let outDir = join(webDir, 'dist')

  const addOutput = (file, id) => {
    let ids = outputs.get(file)
    if (!ids) outputs.set(file, (ids = new Set()))
    if (id) ids.add(id)
  }

  /** Record one Rolldown bundle: the app build's or a worker build's. */
  function recordBundle(bundle) {
    for (const out of Object.values(bundle)) {
      if (out.type !== 'chunk') continue
      addOutput(out.fileName)
      const css = [...(out.viteMetadata?.importedCss ?? [])]
      for (const [id, mod] of Object.entries(out.modules)) {
        if (mod.renderedLength > 0) addOutput(out.fileName, id)
        // A stylesheet renders no JavaScript: its code is in the CSS its chunk imports.
        else if (CSS_MODULE.test(stripQuery(id))) for (const file of css.length ? css : [out.fileName]) addOutput(file, id)
      }
    }
  }

  function write() {
    // A script or stylesheet in the output that no build reported (a worker built without
    // `workerPlugin()`, a file copied in verbatim) holds code this census cannot see into.
    const unreported = codeFilesIn(outDir).filter((file) => !outputs.has(file))
    if (unreported.length) {
      throw new Error(
        `third-party notices: ${unreported.length} built file(s) came from no build that reported its modules, ` +
          `so the packages in them are unknown:\n${unreported.map((f) => `  - ${f}`).join('\n')}`,
      )
    }
    const census = buildCensus({ outputs, webDir, repoRoot })
    writeFileSync(join(outDir, NOTICES_TXT), census.text)
    writeFileSync(join(outDir, NOTICES_JSON), `${JSON.stringify(census.json, null, 2)}\n`)
    return census.json
  }

  return {
    /** The app build's plugin. Writes both files once every other plugin has closed the bundle,
     *  because sw.js is built in a `closeBundle` hook and has to be in the census. */
    plugin() {
      return {
        name: 'third-party-notices',
        apply: 'build',
        configResolved(config) {
          outDir = resolve(config.root, config.build.outDir)
        },
        buildStart() {
          outputs = new Map()
        },
        generateBundle(_options, bundle) {
          recordBundle(bundle)
        },
        closeBundle: {
          order: 'post',
          handler(error) {
            if (error) return
            const json = write()
            this.info?.(`${NOTICES_TXT}: ${json.packages.length} packages in ${Object.keys(json.files).length} built files`)
          },
        },
      }
    },
    /** Each worker build's plugin: records the worker's chunks into the same census. */
    workerPlugin() {
      return {
        name: 'third-party-notices:worker',
        apply: 'build',
        generateBundle(_options, bundle) {
          recordBundle(bundle)
        },
      }
    },
    /** Record a file bundled outside Rolldown (sw.js), with the absolute paths of its inputs. */
    recordOutput(file, inputs) {
      addOutput(file)
      for (const input of inputs) addOutput(file, input)
    },
  }
}

/**
 * Attribute every recorded module to its package, gather each package's licence facts, and
 * render the notices. Throws, listing every problem at once, when anything cannot be attributed.
 *
 * @param {{ outputs: Map<string, Set<string>>, webDir: string, repoRoot: string }} input
 * @returns {{ text: string, json: object }}
 */
export function buildCensus({ outputs, webDir, repoRoot }) {
  const lock = readJson(join(repoRoot, 'package-lock.json')).packages ?? {}
  const recordsPath = join(webDir, RECORDS_FILE)
  const records = existsSync(recordsPath) ? readJson(recordsPath).packages ?? {} : {}
  const problems = new Set()
  const usedRecords = new Set()
  const repoRel = (path) => toPosix(relative(repoRoot, path))

  // ── Where a module's code comes from ─────────────────────────────────────────────────────
  const installedKeyOf = (parts) => {
    for (let n = parts.length - 1; n > 0; n--) {
      const key = parts.slice(0, n).join('/')
      if (parts[n - 1] !== 'node_modules' && lock[key] && !lock[key].link && parts.slice(0, n).includes('node_modules')) return key
    }
    return null
  }

  /** The lockfile key of the package `name` as Node resolves it from `fromDir`, or null. */
  const resolveFrom = (name, fromDir) => {
    for (let dir = fromDir; ; dir = dirname(dir)) {
      if (basename(dir) !== 'node_modules') {
        const candidate = join(dir, 'node_modules', name)
        if (existsSync(join(candidate, 'package.json'))) {
          const key = repoRel(candidate)
          return lock[key] && !lock[key].link ? key : null
        }
      }
      if (dir === repoRoot || dirname(dir) === dir) return null
    }
  }

  /** `{ key }` for code in an installed package, `{ key, copyOf, within }` for a library copied
   *  into one; null for the project's own code. Records a problem and returns undefined when the
   *  module cannot be attributed. */
  const attribute = (id) => {
    const bare = stripQuery(id)
    for (const [prefix, chain] of VIRTUAL_ORIGINS) {
      if (!bare.startsWith(prefix)) continue
      let key = null
      let dir = webDir
      for (const name of chain) {
        key = resolveFrom(name, dir)
        if (!key) {
          problems.add(`the bundler injected ${JSON.stringify(id)}, and no installed ${name} supplies it`)
          return undefined
        }
        dir = join(repoRoot, key)
      }
      return { key }
    }
    if (bare.startsWith('\0') || !isAbsolute(bare)) {
      problems.add(`module ${JSON.stringify(id)} renders code, and names no installed package it comes from`)
      return undefined
    }
    const rel = repoRel(bare)
    if (rel.startsWith('../') || isAbsolute(rel)) {
      problems.add(`module ${bare} is outside the repository`)
      return undefined
    }
    const parts = rel.split('/')
    if (!parts.includes('node_modules')) return null
    const key = installedKeyOf(parts)
    if (!key) {
      problems.add(`module ${rel} is under node_modules, in no package package-lock.json installs`)
      return undefined
    }
    const rest = parts.slice(key.split('/').length)
    const inner = rest.lastIndexOf('node_modules')
    if (inner < 0 || inner >= rest.length - 1) return { key }
    const nameLength = rest[inner + 1].startsWith('@') ? 2 : 1
    const copyOf = rest.slice(inner + 1, inner + 1 + nameLength).join('/')
    return { key: [key, ...rest.slice(0, inner + 1 + nameLength)].join('/'), copyOf, within: key }
  }

  /** The stylesheet `@import` resolves to from `fromDir`: a file path, or null. */
  const resolveStylesheet = (spec, fromDir) => {
    if (spec.startsWith('.')) return resolve(fromDir, spec)
    const segments = spec.split('/')
    const nameLength = spec.startsWith('@') ? 2 : 1
    const key = resolveFrom(segments.slice(0, nameLength).join('/'), fromDir)
    if (!key) return null
    const dir = join(repoRoot, key)
    const subpath = segments.slice(nameLength).join('/')
    if (subpath) return join(dir, subpath)
    const pkg = readJson(join(dir, 'package.json'))
    const entry = pkg.exports?.['.']
    return join(dir, (typeof entry === 'object' && typeof entry?.style === 'string' && entry.style) || pkg.style || 'index.css')
  }

  /** Every package stylesheet a stylesheet inlines through `@import`, followed transitively:
   *  `[{ origin, file }]`. Memoized per file; a cycle of imports ends where it started. */
  const imported = new Map()
  const importedStylesheets = (file, visiting = new Set()) => {
    if (imported.has(file)) return imported.get(file)
    if (visiting.has(file)) return []
    visiting.add(file)
    const found = []
    let source = ''
    try {
      source = readFileSync(file, 'utf8')
    } catch {
      // A module id that is not a file on disk imports nothing this can follow.
    }
    for (const match of source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(CSS_IMPORT)) {
      const spec = match[2]
      // A remote stylesheet is fetched by the browser, never bundled; `/…` is served, not inlined.
      if (/^(?:[a-z][\w+.-]*:|\/)/i.test(spec)) continue
      let target = resolveStylesheet(spec, dirname(file))
      if (target && !existsSync(target) && existsSync(`${target}.css`)) target = `${target}.css`
      if (!target || !existsSync(target)) {
        problems.add(`${repoRel(file)} imports ${JSON.stringify(spec)}, which resolves to no installed stylesheet`)
        continue
      }
      const origin = attribute(target)
      if (origin) found.push({ origin, file: target })
      found.push(...importedStylesheets(target, visiting))
    }
    imported.set(file, found)
    return found
  }

  // ── Every recorded module, attributed ────────────────────────────────────────────────────
  /** package path → { origin, sources: Set<absolute source file> } */
  const bundled = new Map()
  const note = (origin, file) => {
    let seen = bundled.get(origin.key)
    if (!seen) bundled.set(origin.key, (seen = { origin, sources: new Set() }))
    if (file) seen.sources.add(file)
  }
  const files = {}
  for (const file of [...outputs.keys()].sort()) {
    const packages = new Set()
    for (const id of [...outputs.get(file)].sort()) {
      const origin = attribute(id)
      const bare = stripQuery(id)
      if (origin) {
        note(origin, bare.startsWith('\0') || !isAbsolute(bare) ? null : bare)
        packages.add(origin.key)
      }
      if (origin !== undefined && CSS_MODULE.test(bare) && isAbsolute(bare)) {
        for (const { origin: sheetOrigin, file: sheet } of importedStylesheets(bare)) {
          note(sheetOrigin, sheet)
          packages.add(sheetOrigin.key)
        }
      }
    }
    files[file] = [...packages].sort()
  }

  // ── Each package's licence facts ─────────────────────────────────────────────────────────
  const installedFacts = new Map()
  const factsOf = (key) => {
    if (installedFacts.has(key)) return installedFacts.get(key)
    const dir = join(repoRoot, key)
    const pkg = readJson(join(dir, 'package.json'))
    const locked = lock[key]
    const label = `${pkg.name}@${pkg.version}`
    if (pkg.version !== locked.version) {
      problems.add(`${key}: package.json says ${pkg.version}, package-lock.json installed ${locked.version}`)
    }
    const record = records[label]
    if (record) {
      usedRecords.add(label)
      for (const field of Object.keys(record)) {
        if (!RECORD_FIELDS.has(field)) problems.add(`${RECORDS_FILE}: ${label} has an unknown field ${JSON.stringify(field)}`)
      }
      if (typeof record.note !== 'string' || !record.note.trim()) problems.add(`${RECORDS_FILE}: ${label} needs a note saying where its facts come from`)
      if (record.license_text && !/^https:\/\//.test(record.license_text_source ?? '')) {
        problems.add(`${RECORDS_FILE}: ${label} supplies a licence text with no https license_text_source`)
      }
    }
    let license = declaredLicense(pkg)
    if (license && record?.license) problems.add(`${RECORDS_FILE}: ${label} declares its licence (${license}) itself; its record must not`)
    license = license ?? record?.license ?? null
    if (!license) problems.add(`${label} (${key}) declares no licence, and ${RECORDS_FILE} records none for it`)
    if (license === 'UNLICENSED') problems.add(`${label} (${key}) is UNLICENSED: it grants no right to ship it`)
    const licenseFiles = licenseFilesIn(dir)
    if (licenseFiles.length && record?.license_text) {
      problems.add(`${RECORDS_FILE}: ${label} ships ${licenseFiles.join(', ')}; its record must not supply a licence text`)
    }
    let texts = licenseFiles.map((name) => ({ title: name, text: normalizeText(readFileSync(join(dir, name), 'utf8')) }))
    if (!licenseFiles.length) {
      if (record?.license_text) texts = [{ title: 'Licence text', text: normalizeText(record.license_text) }]
      else problems.add(`${label} (${key}) ships no licence file, and ${RECORDS_FILE} records no licence text for it`)
    }
    const facts = {
      entry: {
        path: key,
        name: pkg.name,
        version: pkg.version,
        license,
        license_files: licenseFiles,
        from: locked.resolved ?? null,
        source: sourceUrl(pkg),
        ...(record ? { record: label } : {}),
      },
      texts,
      note: record?.note?.trim() || null,
    }
    installedFacts.set(key, facts)
    return facts
  }

  const entries = []
  for (const [key, { origin, sources }] of bundled) {
    let facts
    if (origin.copyOf) {
      const within = factsOf(origin.within)
      const libraryKey = resolveFrom(origin.copyOf, join(repoRoot, origin.within))
      if (!libraryKey) {
        problems.add(
          `${within.entry.name} ${within.entry.version} ships a copy of ${origin.copyOf} (${key}), and no installed ` +
            `${origin.copyOf} gives that copy a licence`,
        )
        continue
      }
      const library = factsOf(libraryKey)
      facts = {
        entry: {
          path: key,
          name: origin.copyOf,
          version: null,
          license: library.entry.license,
          license_files: library.entry.license_files,
          license_from: libraryKey,
          copied_into: origin.within,
          from: within.entry.from,
          source: library.entry.source,
          ...(library.entry.record ? { record: library.entry.record } : {}),
        },
        title: `${origin.copyOf}, as copied into ${within.entry.name} ${within.entry.version}`,
        texts: library.texts,
        note:
          `${within.entry.name} ${within.entry.version} ships this copy of ${origin.copyOf} inside its own archive, ` +
          `with no version or licence file of its own. The licence below is the one ${origin.copyOf} ` +
          `${library.entry.version} ships: the ${origin.copyOf} installed where ${within.entry.name} resolves it ` +
          `(${libraryKey}).`,
      }
    } else {
      const installed = factsOf(key)
      facts = { ...installed, title: `${installed.entry.name} ${installed.entry.version}` }
    }
    const comments = []
    const seenComments = new Set()
    for (const file of [...sources].sort()) {
      let source
      try {
        source = readFileSync(file, 'utf8')
      } catch {
        continue
      }
      for (const comment of noticeComments(source)) {
        if (!seenComments.has(comment)) {
          seenComments.add(comment)
          comments.push(comment)
        }
      }
    }
    entries.push({ ...facts, comments })
  }

  for (const label of Object.keys(records)) {
    if (!usedRecords.has(label)) problems.add(`${RECORDS_FILE}: ${label} matches no bundled package; remove it or correct its version`)
  }
  if (problems.size) {
    throw new Error(
      `third-party notices: ${problems.size} problem(s) with the bundled packages' licences:\n` +
        [...problems].sort().map((p) => `  - ${p}`).join('\n'),
    )
  }

  const order = (e) => `${e.entry.name.toLowerCase()}\0${e.entry.path}`
  entries.sort((a, b) => (order(a) < order(b) ? -1 : order(a) > order(b) ? 1 : 0))
  return { text: renderNotices(entries), json: renderCensus(entries, files) }
}

function renderCensus(entries, files) {
  return {
    schema_version: 1,
    about:
      'Every npm package whose code the web build put into the PersonalClaw dashboard, read from the ' +
      "bundler's own module graph, and each built file's packages. The licence texts are in " +
      `${NOTICES_TXT}. Written by web/scripts/thirdPartyNotices.mjs; ` +
      'scripts/check_asset_licenses.py --built-web checks it against package-lock.json and the build.',
    notices: NOTICES_TXT,
    packages: entries.map((e) => e.entry),
    files,
  }
}

function renderNotices(entries) {
  const header = [
    "Third-party notices for the PersonalClaw dashboard's code",
    '',
    "PersonalClaw's own code is MIT-licensed (see LICENSE in the PersonalClaw repository).",
    `The dashboard's JavaScript and CSS also carry code from the ${entries.length} open-source`,
    'packages below, each under its own licence. The web build writes this file from the',
    'modules the bundler put into the dashboard, so every package listed has code in it, at',
    'the version package-lock.json installed. A library that another package copied into',
    'its own archive is listed as that copy, with a note saying where its licence comes from.',
    '',
    'Each entry names the licence, the archive the package was installed from (From) and',
    'where its source is published (Source), then reproduces its licence files as the',
    "package ships them. Licence and copyright comments found in a package's bundled files",
    'follow as written, because the minifier removes them from the code itself.',
    '',
    "The fonts' notices are in THIRD_PARTY_NOTICES.txt. THIRD_PARTY_NOTICES_NPM.json lists",
    "the same packages, and which built files hold each one's code.",
  ].join('\n')
  const sections = entries.map(({ entry, title, texts, note, comments }) => {
    const lines = [RULE, title, `${'Licence:'.padEnd(LABEL)}${entry.license}`, `${'Path:'.padEnd(LABEL)}${entry.path}`]
    if (entry.from) lines.push(`${'From:'.padEnd(LABEL)}${entry.from}`)
    if (entry.source) lines.push(`${'Source:'.padEnd(LABEL)}${entry.source}`)
    if (note) lines.push(wrapLabelled('Note:', note))
    const blocks = [lines.join('\n')]
    for (const { title: name, text } of texts) blocks.push(`${SUBRULE}\n${name}\n\n${text}`)
    if (comments.length) blocks.push(`${SUBRULE}\nLicence comments in the bundled files\n\n${comments.join('\n\n')}`)
    return blocks.join('\n\n')
  })
  return `${header}\n\n${sections.join('\n\n')}\n`
}
