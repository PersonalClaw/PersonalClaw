// Prints, as JSON, the environment the pinned browser runs render in (#3943): the image, its
// platform and the Playwright version, the browser build, the WebGL renderer, and the installed
// fonts (their count and one sha256 over the sorted list of font files and their sizes). It runs
// inside the pinned Playwright image (e2e/visual-image.json), started by e2e/run-pinned.mjs,
// which writes it into the baselines' manifest (e2e/visual-environment.json) on a refresh and
// compares it with the manifest on every other run, before any baseline is read.
//
// The Playwright version is the one `@playwright/test` resolves: the lock also holds an older
// top-level playwright-core for another package, which is not the one that runs the browser.
// The fonts are read from the font folders rather than through fc-list, so the probe needs
// nothing the image may not ship. The same fields as the website's probe
// (personalclaw.dev, scripts/visual-environment.mjs).
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FONT_DIRECTORIES = ['/usr/share/fonts', '/usr/local/share/fonts']
const FONT_FILE = /\.(?:ttf|otf|ttc|pfb|pcf(?:\.gz)?|woff2?)$/i

/** The playwright-core the runner loads: @playwright/test's playwright's own. */
export function resolvedPlaywrightCore(from = import.meta.url) {
  const fromTest = createRequire(createRequire(from).resolve('@playwright/test/package.json'))
  const fromPlaywright = createRequire(fromTest.resolve('playwright/package.json'))
  return fromPlaywright.resolve('playwright-core/package.json')
}

function fontFiles(directory, found = []) {
  if (!fs.existsSync(directory)) return found
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) fontFiles(file, found)
    else if (FONT_FILE.test(entry.name)) found.push(`${file} ${fs.statSync(file).size}`)
  }
  return found
}

async function main() {
  const core = resolvedPlaywrightCore()
  const playwright = JSON.parse(fs.readFileSync(core, 'utf8')).version
  const fonts = FONT_DIRECTORIES.flatMap((directory) => fontFiles(directory)).sort()
  if (fonts.length === 0) throw new Error(`no font files under ${FONT_DIRECTORIES.join(' or ')}`)
  const { chromium } = createRequire(core)('playwright-core')
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage()
    const renderer = await page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl')
      if (!gl) return 'none'
      const info = gl.getExtension('WEBGL_debug_renderer_info')
      return String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER))
    })
    process.stdout.write(
      `${JSON.stringify({
        image: process.env.VISUAL_IMAGE ?? null,
        platform: `${process.platform}/${process.arch === 'x64' ? 'amd64' : process.arch}`,
        playwright,
        browser: `chromium ${browser.version()}`,
        renderer,
        fonts: { count: fonts.length, sha256: createHash('sha256').update(fonts.join('\n')).digest('hex') },
      })}\n`,
    )
  } finally {
    await browser.close()
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main()
}
