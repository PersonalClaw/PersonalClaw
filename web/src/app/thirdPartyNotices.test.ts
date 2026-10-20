// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import {
  NOTICES_JSON,
  NOTICES_TXT,
  RECORDS_FILE,
  noticeComments,
  normalizeText,
  thirdPartyNotices,
  type BundleOutput,
  type Census,
} from '../../scripts/thirdPartyNotices.mjs'

// ── The web build's third-party notices (scripts/thirdPartyNotices.mjs) ────────────────────
// The build's minifier strips every licence comment, so the notices of the npm packages it
// bundles ship in dist/THIRD_PARTY_NOTICES_NPM.txt, written from the bundler's module graph.
// Each test lays out a throwaway repository (a package-lock.json, installed packages, a web/
// directory) and drives the real plugin's hooks with the chunks Rolldown would hand it.

const MIT = (holder: string) =>
  `MIT License\n\nCopyright (c) ${holder}\n\nPermission is hereby granted, free of charge, to any person ` +
  'obtaining a copy of this software.\n'

let roots: string[] = []
afterEach(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
  roots = []
})

type Installed = { version: string; license?: string; files?: Record<string, string>; pkg?: Record<string, unknown> }

/** A repository with `installed` packages (lockfile key → package) and extra `files`. */
function repo(installed: Record<string, Installed>, files: Record<string, string> = {}) {
  const root = mkdtempSync(join(tmpdir(), 'pc-notices-'))
  roots.push(root)
  const write = (rel: string, body: string) => {
    mkdirSync(dirname(join(root, rel)), { recursive: true })
    writeFileSync(join(root, rel), body)
  }
  const lock: Record<string, object> = { '': { name: 'fixture' }, web: { name: 'fixture-web' } }
  for (const [key, p] of Object.entries(installed)) {
    const name = key.slice(key.lastIndexOf('node_modules/') + 'node_modules/'.length)
    const pkg = { name, version: p.version, ...(p.license ? { license: p.license } : {}), ...p.pkg }
    write(`${key}/package.json`, JSON.stringify(pkg))
    for (const [file, body] of Object.entries(p.files ?? { LICENSE: MIT(`${name} authors`) })) write(`${key}/${file}`, body)
    lock[key] = {
      version: p.version,
      resolved: `https://registry.npmjs.org/${name}/-/${name.split('/').pop()}-${p.version}.tgz`,
      ...(p.license ? { license: p.license } : {}),
    }
  }
  write('package-lock.json', JSON.stringify({ lockfileVersion: 3, packages: lock }))
  for (const [rel, body] of Object.entries(files)) write(rel, body)
  mkdirSync(join(root, 'web', 'dist'), { recursive: true })
  return root
}

const chunk = (fileName: string, modules: Record<string, number>, importedCss: string[] = []): BundleOutput => ({
  type: 'chunk',
  fileName,
  modules: Object.fromEntries(Object.entries(modules).map(([id, renderedLength]) => [id, { renderedLength }])),
  viteMetadata: { importedCss: new Set(importedCss) },
})

/** Run one build: the app bundle, any worker bundles, and files recorded outside Rolldown. */
function build(
  root: string,
  app: BundleOutput[],
  { workers = [], recorded = {} }: { workers?: BundleOutput[][]; recorded?: Record<string, string[]> } = {},
) {
  const web = join(root, 'web')
  const notices = thirdPartyNotices(web)
  const plugin = notices.plugin()
  plugin.configResolved({ root: web, build: { outDir: 'dist' } })
  plugin.buildStart()
  for (const bundle of workers) notices.workerPlugin().generateBundle({}, Object.fromEntries(bundle.map((o) => [o.fileName, o])))
  plugin.generateBundle({}, Object.fromEntries(app.map((o) => [o.fileName, o])))
  // What Rolldown writes before closeBundle runs: every output file, on disk.
  for (const output of [...workers.flat(), ...app]) {
    mkdirSync(dirname(join(web, 'dist', output.fileName)), { recursive: true })
    writeFileSync(join(web, 'dist', output.fileName), '/* built */')
  }
  for (const [file, inputs] of Object.entries(recorded)) {
    writeFileSync(join(web, 'dist', file), '/* built */')
    notices.recordOutput(file, inputs)
  }
  plugin.closeBundle.handler.call({})
  return {
    text: readFileSync(join(web, 'dist', NOTICES_TXT), 'utf8'),
    census: JSON.parse(readFileSync(join(web, 'dist', NOTICES_JSON), 'utf8')) as Census,
  }
}

/** One package's section of the notices, found by its `Path:` line. */
function section(text: string, path: string): string {
  const found = text.split(/\n={78}\n/).find((s) => s.includes(`\nPath:    ${path}\n`))
  if (!found) throw new Error(`no section for ${path}`)
  return found
}

describe('the notices name every bundled package with its version, licence and full licence text', () => {
  it('writes one section per package with code in a built file, and the census of which file holds it', () => {
    const root = repo({
      'node_modules/left-pad': { version: '1.3.0', license: 'MIT' },
      'node_modules/@scope/widget': { version: '2.0.1', license: 'ISC', files: { LICENSE: 'ISC License\n\nCopyright (c) Widget\n' } },
    })
    const { text, census } = build(root, [
      chunk('assets/index-a1.js', {
        [join(root, 'node_modules/left-pad/index.js')]: 120,
        [join(root, 'node_modules/@scope/widget/dist/widget.mjs')]: 80,
        [join(root, 'web/src/main.tsx')]: 400,
      }),
    ])

    expect(census.packages.map((p) => [p.path, p.name, p.version, p.license, p.license_files])).toEqual([
      ['node_modules/@scope/widget', '@scope/widget', '2.0.1', 'ISC', ['LICENSE']],
      ['node_modules/left-pad', 'left-pad', '1.3.0', 'MIT', ['LICENSE']],
    ])
    expect(census.files).toEqual({ 'assets/index-a1.js': ['node_modules/@scope/widget', 'node_modules/left-pad'] })
    const leftPad = section(text, 'node_modules/left-pad')
    expect(leftPad.split('\n').slice(0, 4)).toEqual([
      'left-pad 1.3.0',
      'Licence: MIT',
      'Path:    node_modules/left-pad',
      'From:    https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz',
    ])
    expect(leftPad).toContain(normalizeText(MIT('left-pad authors')))
    expect(section(text, 'node_modules/@scope/widget')).toContain('ISC License\n\nCopyright (c) Widget')
    expect(text).toMatch(/^Third-party notices for the PersonalClaw dashboard's code\n/)
    expect(text).toContain('code from the 2 open-source\npackages below')
  })

  it("counts only code that landed: a module the bundler tree-shook to nothing is not the package's code", () => {
    const root = repo({
      'node_modules/used': { version: '1.0.0', license: 'MIT' },
      'node_modules/shaken': { version: '1.0.0', license: 'MIT' },
    })
    const { census } = build(root, [
      chunk('assets/index.js', { [join(root, 'node_modules/used/a.js')]: 10, [join(root, 'node_modules/shaken/b.js')]: 0 }),
    ])
    expect(census.packages.map((p) => p.path)).toEqual(['node_modules/used'])
  })

  it('tells a nested copy from the top-level package of the same name, each at its own version', () => {
    // Measured on the real build: the KaTeX CODE comes from two nested 0.16 copies, while only the
    // stylesheet and the fonts are the top-level 0.18 one.
    const root = repo({
      'node_modules/katex': { version: '0.18.7', license: 'MIT' },
      'node_modules/rehype-katex/node_modules/katex': { version: '0.16.47', license: 'MIT' },
      'node_modules/rehype-katex': { version: '7.0.1', license: 'MIT' },
    })
    const { census } = build(root, [
      chunk(
        'assets/index.js',
        {
          [join(root, 'node_modules/rehype-katex/lib/index.js')]: 50,
          [join(root, 'node_modules/rehype-katex/node_modules/katex/dist/katex.mjs')]: 900,
          [join(root, 'node_modules/katex/dist/katex.min.css')]: 0,
        },
        ['assets/index.css'],
      ),
    ])
    expect(census.packages.map((p) => [p.path, p.version])).toEqual([
      ['node_modules/katex', '0.18.7'],
      ['node_modules/rehype-katex/node_modules/katex', '0.16.47'],
      ['node_modules/rehype-katex', '7.0.1'],
    ])
    expect(census.files['assets/index.js']).toEqual(['node_modules/rehype-katex', 'node_modules/rehype-katex/node_modules/katex'])
    expect(census.files['assets/index.css']).toEqual(['node_modules/katex'])
  })
})

describe('stylesheets', () => {
  it('attributes a stylesheet, which renders no JavaScript, to the CSS file its chunk imports', () => {
    const root = repo({ 'node_modules/@xterm/xterm': { version: '6.0.0', license: 'MIT' } })
    const { census } = build(root, [
      chunk('assets/index.js', { [join(root, 'node_modules/@xterm/xterm/css/xterm.css')]: 0 }, ['assets/index-c1.css']),
    ])
    expect(census.files).toEqual({ 'assets/index-c1.css': ['node_modules/@xterm/xterm'], 'assets/index.js': [] })
  })

  it("follows a stylesheet's @import of a package, which a CSS compiler inlines outside the module graph", () => {
    // Tailwind's `@import "tailwindcss"` resolves through the package's `style` entry, and neither
    // it nor what it imports is ever a module id.
    const root = repo(
      {
        'node_modules/tailwindcss': {
          version: '4.3.3',
          license: 'MIT',
          pkg: { style: 'index.css', exports: { '.': { style: './index.css' } } },
          files: { LICENSE: MIT('Tailwind Labs'), 'index.css': '@import "./preflight.css";\n', 'preflight.css': '*{margin:0}\n' },
        },
      },
      { 'web/src/tokens.css': '/* @import "commented-out"; */\n@import "tailwindcss";\n@import "./fonts.css";\n', 'web/src/fonts.css': '' },
    )
    const { census } = build(root, [chunk('assets/index.js', { [join(root, 'web/src/tokens.css')]: 0 }, ['assets/index.css'])])
    expect(census.files['assets/index.css']).toEqual(['node_modules/tailwindcss'])
  })

  it('fails the build on an @import it cannot resolve to an installed stylesheet', () => {
    const root = repo({}, { 'web/src/tokens.css': '@import "not-installed";\n' })
    expect(() => build(root, [chunk('assets/index.js', { [join(root, 'web/src/tokens.css')]: 0 }, ['assets/index.css'])])).toThrow(
      /web\/src\/tokens\.css imports "not-installed", which resolves to no installed stylesheet/,
    )
  })
})

describe('code that is not a package of its own', () => {
  it("gives a library a package copied into its own archive the licence of the copy it resolves to", () => {
    // @antv/layout 2.0.0 ships lodash, dagre and five more inside lib/node_modules/, with no
    // package.json or licence file for any of them.
    const root = repo({
      'node_modules/@antv/layout': { version: '2.0.0', license: 'MIT', files: { LICENSE: MIT('2018 Alipay.inc') } },
      'node_modules/lodash': { version: '4.18.1', license: 'MIT', files: { LICENSE: MIT('OpenJS Foundation') } },
    })
    const { text, census } = build(root, [
      chunk('assets/index.js', {
        [join(root, 'node_modules/@antv/layout/lib/index.js')]: 30,
        [join(root, 'node_modules/@antv/layout/lib/node_modules/lodash/_baseEach.js')]: 70,
      }),
    ])
    const copy = census.packages.find((p) => p.copied_into)
    expect(copy).toMatchObject({
      path: 'node_modules/@antv/layout/lib/node_modules/lodash',
      name: 'lodash',
      version: null,
      license: 'MIT',
      license_from: 'node_modules/lodash',
      copied_into: 'node_modules/@antv/layout',
      from: 'https://registry.npmjs.org/@antv/layout/-/layout-2.0.0.tgz',
    })
    const body = section(text, 'node_modules/@antv/layout/lib/node_modules/lodash')
    expect(body.split('\n')[0]).toBe('lodash, as copied into @antv/layout 2.0.0')
    expect(body).toContain(normalizeText(MIT('OpenJS Foundation')))
    expect(body.replace(/\n +/g, ' ')).toContain('The licence below is the one lodash 4.18.1 ships')
    // The carrying package is listed for its own code, under its own licence.
    expect(section(text, 'node_modules/@antv/layout')).toContain('Copyright (c) 2018 Alipay.inc')
  })

  it("attributes the modules the bundler injects to Vite and to the Rolldown that Vite runs", () => {
    const root = repo({
      'web/node_modules/vite': { version: '8.3.0', license: 'MIT' },
      'node_modules/rolldown': { version: '1.2.8', license: 'MIT' },
    })
    const { census } = build(root, [
      chunk('assets/index.js', { '\0vite/preload-helper.js': 900, '\0rolldown/runtime.js': 400, '__vite-browser-external': 100 }),
    ])
    expect(census.files['assets/index.js']).toEqual(['node_modules/rolldown', 'web/node_modules/vite'])
  })

  it('fails the build on a virtual module that renders code and names no package', () => {
    const root = repo({})
    expect(() => build(root, [chunk('assets/index.js', { '\0some-plugin/virtual.js': 12 })])).toThrow(
      /module "\\u0000some-plugin\/virtual\.js" renders code, and names no installed package it comes from/,
    )
  })

  it('records sw.js from the inputs esbuild reports, so a service worker of project code lists no package', () => {
    const root = repo({})
    const { census } = build(root, [chunk('assets/index.js', {})], { recorded: { 'sw.js': [join(root, 'web/src/sw.ts')] } })
    expect(census.files['sw.js']).toEqual([])
  })

  it('fails the build when a built script came from no build that reported its modules', () => {
    // A web worker built without `workerPlugin()`: its file is on disk, and nothing said what is in it.
    const root = repo({})
    const web = join(root, 'web')
    mkdirSync(join(web, 'dist', 'assets'), { recursive: true })
    writeFileSync(join(web, 'dist', 'assets', 'ts.worker-9f.js'), '/* built */')
    expect(() => build(root, [chunk('assets/index.js', {})])).toThrow(/1 built file\(s\) came from no build[\s\S]*assets\/ts\.worker-9f\.js/)
  })
})

describe('a licence fact the archive leaves out', () => {
  const noLicenceFile = (license?: string) => ({ version: '6.0.0', license, files: { 'readme.md': '# remark-math\n' } })
  const records = (packages: Record<string, object>) => ({
    [`web/${RECORDS_FILE}`]: JSON.stringify({ schema_version: 1, packages }),
  })
  const bundle = (root: string) => [chunk('assets/index.js', { [join(root, 'node_modules/remark-math/index.js')]: 10 })]

  it('fails the build for a package that ships no licence file, until a reviewed record supplies the text', () => {
    const bare = repo({ 'node_modules/remark-math': noLicenceFile('MIT') })
    expect(() => build(bare, bundle(bare))).toThrow(
      /remark-math@6\.0\.0 \(node_modules\/remark-math\) ships no licence file, and npm-license-records\.json records no licence text for it/,
    )

    const recordText = '(The MIT License)\r\n\r\nCopyright (c) 2017 Junyoung Choi   \r\n\r\n'
    const root = repo(
      { 'node_modules/remark-math': noLicenceFile('MIT') },
      records({
        'remark-math@6.0.0': {
          license_text: recordText,
          license_text_source: 'https://github.com/remarkjs/remark-math/blob/d5d0660b/license',
          note: 'The licence at the root of remarkjs/remark-math at the 6.0.0 tag.',
        },
      }),
    )
    const { text, census } = build(root, bundle(root))
    expect(census.packages[0]).toMatchObject({ path: 'node_modules/remark-math', license_files: [], record: 'remark-math@6.0.0' })
    const body = section(text, 'node_modules/remark-math')
    expect(body).toContain('(The MIT License)\n\nCopyright (c) 2017 Junyoung Choi')
    expect(body).toContain('Note:    The licence at the root of remarkjs/remark-math at the 6.0.0 tag.')
  })

  it('fails the build for a package that declares no licence, until a record names it', () => {
    const root = repo({ 'node_modules/khroma': { version: '2.1.0', files: { license: MIT('Fabio Spampinato') } } })
    const khroma = () => build(root, [chunk('assets/index.js', { [join(root, 'node_modules/khroma/dist/index.js')]: 10 })])
    expect(khroma).toThrow(/khroma@2\.1\.0 \(node_modules\/khroma\) declares no licence, and npm-license-records\.json records none for it/)
  })

  it('fails the build for a record that no longer matches, or that supplies what the package ships', () => {
    const root = repo(
      { 'node_modules/remark-math': { version: '6.0.1', license: 'MIT' } },
      records({
        'remark-math@6.0.0': { license_text: 'x', license_text_source: 'https://example.com/license', note: 'For 6.0.0.' },
        'remark-math@6.0.1': { license: 'MIT', note: 'Supplies what the package already declares.' },
      }),
    )
    const run = () => build(root, [chunk('assets/index.js', { [join(root, 'node_modules/remark-math/index.js')]: 10 })])
    expect(run).toThrow(/remark-math@6\.0\.0 matches no bundled package; remove it or correct its version/)
    expect(run).toThrow(/remark-math@6\.0\.1 declares its licence \(MIT\) itself; its record must not/)
  })

  it('fails the build for an UNLICENSED package, which grants no right to ship it', () => {
    const root = repo({ 'node_modules/private-thing': { version: '1.0.0', license: 'UNLICENSED' } })
    expect(() => build(root, [chunk('assets/index.js', { [join(root, 'node_modules/private-thing/index.js')]: 10 })])).toThrow(
      /private-thing@1\.0\.0 \(node_modules\/private-thing\) is UNLICENSED/,
    )
  })
})

describe('licence comments in the bundled files', () => {
  it('keeps the legal and copyright comments the minifier strips, and nothing else', () => {
    const source = [
      '/*! @license DOMPurify 3.4.13 | (c) Cure53 */',
      '/**',
      ' * Returns this iterator.',
      ' */',
      '/*',
      ' * Adapted from code by Björn Ottosson, released under the MIT license:',
      ' * Copyright (c) 2021 Björn Ottosson',
      ' */',
      "const COMMENT = /\\/\\*[^*]*\\*+([^/*][^*]*\\*+)*\\//g; const s = '/* copyright in a string */'",
    ].join('\n')
    expect(noticeComments(source)).toEqual([
      '/*! @license DOMPurify 3.4.13 | (c) Cure53 */',
      '/*\n * Adapted from code by Björn Ottosson, released under the MIT license:\n * Copyright (c) 2021 Björn Ottosson\n */',
    ])
  })

  it("carries each distinct comment once in its package's section", () => {
    const header = '/**\n * @license React\n * Copyright (c) Meta Platforms, Inc. and affiliates.\n */'
    const root = repo(
      { 'node_modules/react': { version: '19.3.0', license: 'MIT' } },
      {
        'node_modules/react/cjs/a.js': `${header}\nexports.a = 1\n`,
        'node_modules/react/cjs/b.js': `${header}\nexports.b = 2\n`,
      },
    )
    const { text } = build(root, [
      chunk('assets/index.js', { [join(root, 'node_modules/react/cjs/a.js')]: 10, [join(root, 'node_modules/react/cjs/b.js')]: 10 }),
    ])
    const body = section(text, 'node_modules/react')
    expect(body).toContain(`Licence comments in the bundled files\n\n${header}`)
    expect(body.split('@license React').length - 1).toBe(1)
  })
})

describe('the comparison form a licence text is written in', () => {
  // scripts/check_asset_licenses.py's normalize_licence_text must agree byte for byte:
  // tests/test_asset_licenses.py asserts the same input gives the same output.
  it('drops a byte-order mark, CRs, trailing blanks and trailing blank lines', () => {
    expect(normalizeText('\ufeffMIT License  \r\n\r\nCopyright (c) X\t\r\n\n\n')).toBe('MIT License\n\nCopyright (c) X')
  })
})
