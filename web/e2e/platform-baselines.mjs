// ── One platform's matrix baselines, written from what a CI run measured ────────────────────────
//
// `node e2e/platform-baselines.mjs <e2e-report.json> <platform> [--into <e2e dir>]`, from web/.
// Under CI every surface of `surfaces.spec.ts` attaches what it measured, in the baseline files'
// own form: `platform-baseline-<id>-<theme>.json` per theme, and `platform-baseline-<id>.aria.yml`
// from the light theme. This writes them as `__aria__/<id>-<platform>.aria.yml` and
// `__ui-baselines__/<id>-<platform>.json`, so the `-linux` set is CI's own measurement, never a Mac's
// (#3905). A reviewer then reads the set as a diff against the `-darwin` one.
//
// 🔑 A SURFACE MISSING A THEME OR ITS TREE IS A RED, NOT A SMALLER SET. A report with no attachment
// at all, or a surface that attached one theme and not the other, writes nothing and exits 1 naming
// each: a half-written set would hold half a surface to a baseline nobody measured.
//
// Deliberately NOT a *.spec.ts: a report tool, like merge-legs.mjs.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const THEMES = ['dark', 'light']
const JSON_NAME = /^platform-baseline-(.+)-(dark|light)\.json$/
const TREE_NAME = /^platform-baseline-(.+)\.aria\.yml$/

/** Every test result of a Playwright JSON report. */
function* results(suites) {
  for (const suite of suites ?? []) {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) yield* test.results ?? []
    }
    yield* results(suite.suites)
  }
}

/** An attachment's text: its inline body (base64 in a JSON report), or the file it names. */
function text(attachment, base) {
  if (typeof attachment.body === 'string') return Buffer.from(attachment.body, 'base64').toString('utf8')
  if (attachment.path) return readFileSync(isAbsolute(attachment.path) ? attachment.path : join(base, attachment.path), 'utf8')
  return null
}

/** The baselines a report's attachments hold, by surface: its tree and each theme's entry. Each
 *  surface's first measurement wins, so a retried test's second result changes nothing. */
export function measuredBaselines(report, base = '.') {
  const surfaces = new Map()
  const at = (id) => surfaces.get(id) ?? surfaces.set(id, { tree: null, themes: {} }).get(id)
  for (const result of results(report.suites)) {
    for (const a of result.attachments ?? []) {
      const json = JSON_NAME.exec(a.name ?? '')
      const tree = TREE_NAME.exec(a.name ?? '')
      if (json) {
        const s = at(json[1])
        if (!(json[2] in s.themes)) s.themes[json[2]] = JSON.parse(text(a, base))
      } else if (tree) {
        const s = at(tree[1])
        if (s.tree === null) s.tree = text(a, base)
      }
    }
  }
  return surfaces
}

/** Why `surfaces` cannot be written as a set: each surface missing a theme or its tree. */
export function incomplete(surfaces) {
  const out = []
  if (!surfaces.size) out.push('the report holds no platform-baseline attachment: was it a CI run of surfaces.spec.ts?')
  for (const [id, s] of [...surfaces].sort(([a], [b]) => a.localeCompare(b))) {
    const missing = THEMES.filter((t) => !(t in s.themes))
    if (missing.length) out.push(`${id} attached no ${missing.join(' or ')} theme`)
    if (s.tree === null) out.push(`${id} attached no accessibility tree`)
  }
  return out
}

/** Write each surface's two files under `into`, as `surfaces.spec.ts`'s writeBaselines writes them. */
export function writeSet(surfaces, platform, into) {
  const wrote = []
  mkdirSync(join(into, '__aria__'), { recursive: true })
  mkdirSync(join(into, '__ui-baselines__'), { recursive: true })
  for (const [id, s] of surfaces) {
    const aria = join(into, '__aria__', `${id}-${platform}.aria.yml`)
    writeFileSync(aria, s.tree.trimEnd() + '\n')
    const sorted = Object.fromEntries(Object.keys(s.themes).sort().map((k) => [k, s.themes[k]]))
    const json = join(into, '__ui-baselines__', `${id}-${platform}.json`)
    writeFileSync(json, JSON.stringify(sorted, null, 2) + '\n')
    wrote.push(aria, json)
  }
  return wrote
}

function main(argv) {
  const intoAt = argv.indexOf('--into')
  const into = intoAt >= 0 ? resolve(argv[intoAt + 1] ?? '') : dirname(fileURLToPath(import.meta.url))
  const [path, platform] = argv.filter((_, i) => i !== intoAt && i !== intoAt + 1)
  if (!path || !/^[a-z0-9]+$/.test(platform ?? '')) {
    console.error('usage: node e2e/platform-baselines.mjs <e2e-report.json> <platform> [--into <e2e dir>]')
    process.exit(2)
  }
  if (!existsSync(path)) {
    console.error(`✗ no report at ${path}`)
    process.exit(1)
  }
  const surfaces = measuredBaselines(JSON.parse(readFileSync(path, 'utf8')), dirname(resolve(path)))
  const problems = incomplete(surfaces)
  if (problems.length) {
    console.error(`\n✗ the report's measurements are not a whole ${platform} set, so nothing was written:`)
    for (const p of problems) console.error(`    ${p}`)
    process.exit(1)
  }
  const wrote = writeSet(surfaces, platform, into)
  console.log(`wrote ${surfaces.size} surfaces' ${platform} baselines (${wrote.length} files) under ${into}`)
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main(process.argv.slice(2))
