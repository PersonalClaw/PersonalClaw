// @module-tag tree-scan
// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { brotliDecompressSync } from 'node:zlib'
import { filesUnder, readSource } from '../test/sourceTree'

// ── Every @font-face says what its file carries ─────────────────────────────────────────────
// fonts.css told the browser that inter.woff2 and google-sans-code.woff2 covered weight ranges,
// and both were static Regular files. A browser takes the declaration at its word: it does not
// synthesize a bold for a face that "covers" 700, and `font-variation-settings: "wght" 600`,
// which is how this app sets nearly every weight (`fvs()`), does nothing on a static file. So with
// Inter picked in Appearance every weight in the UI rendered at 400. And google-sans-code.woff2
// was Google Fonts' adlam slice, 50 code points of spaces, punctuation and symbols, so the
// terminal, which lists it first, drew every letter and digit in a fallback font.
//
// These rails read the files themselves, through the small WOFF2 reader below, and hold the CSS
// to them: the weight range, the stretch range, and the characters each face must draw.

const WEB = process.cwd()
const SRC = join(WEB, 'src')
const FONTS_CSS = readSource(join(SRC, 'design/fonts.css'))

// ── A WOFF2 reader: just enough to read what @font-face has to agree with ────────────────────
// W3C WOFF2 §5: a 48-byte header, a table directory, then one Brotli stream holding every table
// back to back in directory order. Only glyf, loca and hmtx are ever transformed, and fvar, OS/2
// and cmap are read here as they are.
const KNOWN_TAGS = (
  'cmap head hhea hmtx maxp name OS/2 post cvt_ fpgm glyf loca prep CFF_ VORG EBDT EBLC gasp hdmx ' +
  'kern LTSH PCLT VDMX vhea vmtx BASE GDEF GPOS GSUB EBSC JSTF MATH CBDT CBLC COLR CPAL SVG_ sbix ' +
  'acnt avar bdat bloc bsln cvar fdsc feat fmtx fvar gvar hsty just lcar mort morx opbd prop trak ' +
  'Zapf Silf Glat Gloc Feat Sill'
).split(' ').map((tag) => tag.replace('_', ' '))

function woff2Tables(path: string): Map<string, Buffer> {
  const file = readFileSync(path)
  if (file.toString('latin1', 0, 4) !== 'wOF2') throw new Error(`${path} is not a WOFF2 file`)
  const numTables = file.readUInt16BE(12)
  const compressedSize = file.readUInt32BE(20)
  let at = 48
  const base128 = () => {
    let value = 0
    for (let i = 0; i < 5; i++) {
      const byte = file[at++]
      value = value * 128 + (byte & 0x7f)
      if (!(byte & 0x80)) return value
    }
    throw new Error(`${path}: a UIntBase128 longer than 5 bytes`)
  }
  const directory: { tag: string; length: number }[] = []
  for (let i = 0; i < numTables; i++) {
    const flags = file[at++]
    const tag = (flags & 0x3f) === 63 ? file.toString('latin1', at, (at += 4)) : KNOWN_TAGS[flags & 0x3f]
    const version = flags >> 6
    const length = base128()
    // glyf and loca are transformed at version 0; every other table only at a non-zero version.
    const transformed = tag === 'glyf' || tag === 'loca' ? version === 0 : version !== 0
    directory.push({ tag, length: transformed ? base128() : length })
  }
  const data = brotliDecompressSync(file.subarray(at, at + compressedSize))
  const tables = new Map<string, Buffer>()
  let offset = 0
  for (const { tag, length } of directory) {
    tables.set(tag, data.subarray(offset, offset + length))
    offset += length
  }
  return tables
}

type Axis = { min: number; max: number }

interface FontFile {
  axes: Record<string, Axis>
  /** OS/2 usWeightClass: a static face's one weight. */
  weight: number
  codePoints: Set<number>
}

function readFont(path: string): FontFile {
  const tables = woff2Tables(path)
  const axes: Record<string, Axis> = {}
  const fvar = tables.get('fvar')
  if (fvar) {
    const first = fvar.readUInt16BE(4)
    const size = fvar.readUInt16BE(10)
    for (let i = 0; i < fvar.readUInt16BE(8); i++) {
      const o = first + i * size
      axes[fvar.toString('latin1', o, o + 4)] = { min: fvar.readInt32BE(o + 4) / 65536, max: fvar.readInt32BE(o + 12) / 65536 }
    }
  }
  const cmap = tables.get('cmap')!
  const codePoints = new Set<number>()
  for (let i = 0; i < cmap.readUInt16BE(2); i++) {
    const sub = cmap.readUInt32BE(4 + i * 8 + 4)
    const format = cmap.readUInt16BE(sub)
    if (format === 12) {
      for (let g = 0; g < cmap.readUInt32BE(sub + 12); g++) {
        const o = sub + 16 + g * 12
        for (let c = cmap.readUInt32BE(o); c <= cmap.readUInt32BE(o + 4); c++) codePoints.add(c)
      }
    } else if (format === 4) {
      const segments = cmap.readUInt16BE(sub + 6) / 2
      const ends = sub + 14
      const starts = ends + segments * 2 + 2
      const deltas = starts + segments * 2
      const ranges = deltas + segments * 2
      for (let s = 0; s < segments; s++) {
        const start = cmap.readUInt16BE(starts + s * 2)
        const delta = cmap.readInt16BE(deltas + s * 2)
        const rangeOffset = cmap.readUInt16BE(ranges + s * 2)
        for (let c = start; c <= cmap.readUInt16BE(ends + s * 2) && c !== 0xffff; c++) {
          const glyph = rangeOffset
            ? cmap.readUInt16BE(ranges + s * 2 + rangeOffset + (c - start) * 2)
            : (c + delta) & 0xffff
          if (glyph) codePoints.add(c)
        }
      }
    }
  }
  return { axes, weight: tables.get('OS/2')!.readUInt16BE(4), codePoints }
}

// ── fonts.css, and the faces it declares ──────────────────────────────────────────────────────
interface Face {
  family: string
  weight: string
  stretch?: string
  file: FontFile
}

const FACES: Face[] = [...FONTS_CSS.matchAll(/@font-face\s*\{([^}]*)\}/g)].map(([, body]) => {
  const descriptor = (name: string) => body.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))?.[1].trim()
  const url = descriptor('src')?.match(/url\(["']?(\/fonts\/[^"')]+)["']?\)/)?.[1]
  if (!url) throw new Error(`an @font-face with no /fonts/ source: ${body}`)
  return {
    family: descriptor('font-family')!.replace(/["']/g, ''),
    weight: descriptor('font-weight')!,
    stretch: descriptor('font-stretch'),
    file: readFont(join(WEB, 'public', url)),
  }
})
const face = (family: string) => {
  const found = FACES.find((f) => f.family === family)
  if (!found) throw new Error(`fonts.css declares no "${family}"`)
  return found
}
/** The weights a face can draw, from its file: a variable wght axis, or its one static weight. */
const weights = (f: Face): Axis => f.file.axes.wght ?? { min: f.file.weight, max: f.file.weight }

const ASCII = Array.from({ length: 0x7e - 0x20 + 1 }, (_, i) => 0x20 + i)
const BOX_DRAWING = Array.from({ length: 0x80 }, (_, i) => 0x2500 + i)
const missing = (f: Face, codePoints: number[]) =>
  codePoints.filter((c) => !f.file.codePoints.has(c)).map((c) => `U+${c.toString(16).toUpperCase().padStart(4, '0')}`)

describe('the WOFF2 reader reads what fontTools reads', () => {
  // The vacuity floor: a reader that returned no axes and no code points would pass every rail
  // below that only forbids. These are fontTools' readings of the bundled files.
  it('reads DM Sans as wght 100–1000 and opsz 9–40, and JetBrains Mono as 976 code points', () => {
    expect(FACES.map((f) => f.family)).toEqual(['DM Sans', 'Inter', 'JetBrains Mono', 'Google Sans Flex', 'Google Sans Code'])
    expect(face('DM Sans').file.axes).toEqual({ opsz: { min: 9, max: 40 }, wght: { min: 100, max: 1000 } })
    expect(face('JetBrains Mono').file.codePoints.size).toBe(976)
  })
})

describe('every @font-face declares what its file carries', () => {
  it.each(FACES.map((f) => [f.family, f]))('%s declares the weights its file can draw, no more', (_family, f) => {
    const { min, max } = weights(f)
    expect(f.weight).toBe(min === max ? `${min}` : `${min} ${max}`)
  })

  it.each(FACES.map((f) => [f.family, f]))('%s declares a stretch range only if its file has a width axis', (_family, f) => {
    const width = f.file.axes.wdth
    expect(f.stretch).toBe(width ? `${width.min}% ${width.max}%` : undefined)
  })

  it.each(FACES.map((f) => [f.family, f]))('%s draws every printable ASCII character', (_family, f) => {
    expect(missing(f, ASCII)).toEqual([])
  })
})

describe("the terminal draws in its own font", () => {
  const source = readSource(join(SRC, 'pages/terminal/TerminalView.tsx'))
  const stack = source.match(/fontFamily:\s*'([^']+)'/)?.[1] ?? ''
  const own = stack.split(',')[0].trim().replace(/"/g, '')

  it('puts a bundled face first, and that face has the letters, digits and box drawing it prints', () => {
    expect(own).toBe('Google Sans Code')
    expect(missing(face(own), ASCII)).toEqual([])
    expect(missing(face(own), BOX_DRAWING)).toEqual([])
  })

  it("covers xterm's weights: it sets neither, so they are its defaults, normal and bold", () => {
    expect(source).not.toMatch(/fontWeight(Bold)?\s*:/)
    const { min, max } = weights(face(own))
    for (const w of [400, 700]) expect(w >= min && w <= max, `${own} cannot draw ${w} (${min}–${max})`).toBe(true)
  })
})

describe('every weight the app asks for exists in every face that can set its text', () => {
  // How this app asks for a weight: `fvs(n)`/`withWeight(s, n)`, a `"wght" n` variation setting,
  // a numeric `font-weight`/`fontWeight`, or a Tailwind weight class.
  const TAILWIND: Record<string, number> = {
    thin: 100, extralight: 200, light: 300, normal: 400, medium: 500, semibold: 600, bold: 700, extrabold: 800, black: 900,
  }
  const files = (dir: string): string[] => filesUnder(dir, (name) => /\.(tsx?|css)$/.test(name) && !/\.(test|doc)\.tsx?$/.test(name) && name !== 'fonts.css')
  const asked = new Set<number>()
  for (const path of files(SRC)) {
    const text = readSource(path)
    for (const m of text.matchAll(/\b(?:fvs\(|withWeight\([^,]+,\s*)(\d+)\)/g)) asked.add(Number(m[1]))
    for (const m of text.matchAll(/["']wght["']\s+(\d+)/g)) asked.add(Number(m[1]))
    for (const m of text.matchAll(/(?:font-weight|fontWeight)\s*:\s*['"]?(\d+)/g)) asked.add(Number(m[1]))
    for (const m of text.matchAll(/\bfont-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)\b/g)) {
      asked.add(TAILWIND[m[1]])
    }
  }
  // The faces the UI's text can be set in: the design tokens' --font-* (including the cli
  // density mode's) and every Appearance choice. Google Sans Code is only the terminal's.
  const tokens = readSource(join(SRC, 'design/tokens.css'))
  const appearance = readSource(join(SRC, 'app/appearance.tsx'))
  const named = [...tokens.matchAll(/--font-[\w-]+:\s*"([^"]+)"/g), ...appearance.matchAll(/:\s*'"([^"]+)"/g)].map((m) => m[1])
  const uiFaces = [...new Set(named)].filter((family) => FACES.some((f) => f.family === family))

  it('found the weights and the faces it is supposed to check', () => {
    expect(uiFaces.sort()).toEqual(['DM Sans', 'Inter', 'JetBrains Mono'])
    for (const w of [400, 470, 500, 550, 600]) expect(asked.has(w), `no call site asks for ${w}; the scan is broken`).toBe(true)
  })

  it.each(['DM Sans', 'Inter', 'JetBrains Mono'])('%s can draw every weight the app asks for', (family) => {
    const { min, max } = weights(face(family))
    expect([...asked].filter((w) => w < min || w > max).sort((a, b) => a - b)).toEqual([])
  })
})
