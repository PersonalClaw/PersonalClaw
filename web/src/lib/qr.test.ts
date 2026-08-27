import { describe, expect, it } from 'vitest'
import { QUIET_ZONE, encodeQr, formatField, qrPath, versionField } from './qr'

// ── Proving a hand-written QR encoder, without re-reading it ─────────────────────────────────────
//
// `CA-2` declined to write this encoder for one stated reason: "hand-rolling Reed-Solomon +
// masking to render a *wrong* QR would be worse than the labelled placeholder". A wrong QR is
// worse precisely because it LOOKS finished — a screenshot of a garbage symbol is
// indistinguishable from a screenshot of a good one, and the person who finds out is the owner
// standing in their kitchen with a phone that will not focus.
//
// So nothing below asserts "the code does what the code says". Everything is an independent
// check, in three layers:
//
//  1. **A DECODER.** `readBack()` recovers the payload from the DRAWN MATRIX using only the
//     published symbol layout — its own function-module map (from a literal alignment-position
//     table, not from `qr.ts`), its own un-mask, its own zigzag walk, its own de-interleave and
//     its own byte-mode parse. If any of those disagree with the encoder, the text comes back
//     wrong or not at all.
//  2. **A SYNDROME CHECK** on the codewords the decoder pulled out of the matrix. A Reed-Solomon
//     codeword evaluates to zero at every generator root; a wrong remainder, a mis-split block or
//     a mis-ordered interleave cannot satisfy that by accident.
//  3. **BCH round-trips** for the format and version fields, brute-forced against every legal
//     alternative rather than compared to a copy of the expected literal.
//
// (`MC-8` additionally round-tripped the RENDERED image through the operating system's barcode
// detector — recorded in the plan log. That leg needs a decoder binary and is not a unit test.)

// ── An independent reader ───────────────────────────────────────────────────────────────────────

/** Alignment-pattern centres per version, from the published table — NOT from `qr.ts`.
 *  A shared formula would make the decoder agree with the encoder about a wrong layout. */
const ALIGN: Record<number, number[]> = {
  1: [],
  2: [6, 18],
  3: [6, 22],
  4: [6, 26],
  5: [6, 30],
  6: [6, 34],
  7: [6, 22, 38],
  8: [6, 24, 42],
  9: [6, 26, 46],
  10: [6, 28, 50],
}

/** ECC codewords per block and blocks per symbol at level M — the published table again. */
const ECC_PER_BLOCK: Record<number, number> = {
  1: 10, 2: 16, 3: 26, 4: 18, 5: 24, 6: 16, 7: 18, 8: 22, 9: 22, 10: 26,
}
const ECC_BLOCKS: Record<number, number> = {
  1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 4, 7: 4, 8: 4, 9: 5, 10: 5,
}

function gfMul(x: number, y: number): number {
  let z = 0
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d)
    z ^= ((y >>> i) & 1) * x
  }
  return z & 0xff
}

/** Every module the symbol layout reserves: finders + separators, timing, format, version,
 *  alignment. Derived from the layout description, independently of the encoder. */
function functionMap(version: number): boolean[][] {
  const size = version * 4 + 17
  const fn = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  const mark = (x: number, y: number) => {
    if (x >= 0 && y >= 0 && x < size && y < size) fn[y][x] = true
  }
  for (let a = 0; a < 8; a++) {
    for (let b = 0; b < 8; b++) {
      mark(a, b) // top-left finder + separator
      mark(size - 1 - a, b) // top-right
      mark(a, size - 1 - b) // bottom-left
    }
  }
  for (let i = 0; i < size; i++) {
    mark(6, i)
    mark(i, 6)
  }
  for (let i = 0; i < 9; i++) {
    mark(8, i)
    mark(i, 8)
  }
  for (let i = 0; i < 8; i++) {
    mark(size - 1 - i, 8)
    mark(8, size - 1 - i)
  }
  if (version >= 7) {
    for (let i = 0; i < 6; i++) {
      for (let j = 0; j < 3; j++) {
        mark(size - 11 + j, i)
        mark(i, size - 11 + j)
      }
    }
  }
  const align = ALIGN[version]
  for (let i = 0; i < align.length; i++) {
    for (let j = 0; j < align.length; j++) {
      const corner =
        (i === 0 && j === 0) ||
        (i === 0 && j === align.length - 1) ||
        (i === align.length - 1 && j === 0)
      if (corner) continue
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) mark(align[j] + dx, align[i] + dy)
      }
    }
  }
  return fn
}

const UNMASK: ((x: number, y: number) => boolean)[] = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
]

/** The mask index the symbol DECLARES, recovered by brute-forcing the 32 legal format fields
 *  rather than by trusting `formatField`'s inverse. Returns -1 when the field is not legal. */
function readMask(modules: boolean[][]): number {
  let bits = 0
  const at = (x: number, y: number, i: number) => {
    if (modules[y][x]) bits |= 1 << i
  }
  for (let i = 0; i <= 5; i++) at(8, i, i)
  at(8, 7, 6)
  at(8, 8, 7)
  at(7, 8, 8)
  for (let i = 9; i < 15; i++) at(14 - i, 8, i)
  for (let mask = 0; mask < 8; mask++) if (formatField(mask) === bits) return mask
  return -1
}

/** The interleaved codeword stream, read out of the matrix along the spec's zigzag. */
function readCodewords(modules: boolean[][], version: number, mask: number): number[] {
  const size = version * 4 + 17
  const fn = functionMap(version)
  const bits: number[] = []
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j
        const upward = ((right + 1) & 2) === 0
        const y = upward ? size - 1 - vert : vert
        if (fn[y][x]) continue
        const raw = modules[y][x]
        bits.push((UNMASK[mask](x, y) ? !raw : raw) ? 1 : 0)
      }
    }
  }
  const out: number[] = []
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    let b = 0
    for (let k = 0; k < 8; k++) b = (b << 1) | bits[i + k]
    out.push(b)
  }
  return out
}

/** Split the interleaved stream back into blocks. Each block is data-then-ECC. */
function deinterleave(stream: number[], version: number): number[][] {
  const numBlocks = ECC_BLOCKS[version]
  const eccLen = ECC_PER_BLOCK[version]
  const total = stream.length
  const numShort = numBlocks - (total % numBlocks)
  const shortLen = Math.floor(total / numBlocks)
  const dataLens = Array.from({ length: numBlocks }, (_v, i) =>
    shortLen - eccLen + (i < numShort ? 0 : 1),
  )
  const blocks: number[][] = Array.from({ length: numBlocks }, () => [])
  let p = 0
  const maxData = Math.max(...dataLens)
  for (let i = 0; i < maxData; i++) {
    for (let j = 0; j < numBlocks; j++) if (i < dataLens[j]) blocks[j].push(stream[p++])
  }
  for (let i = 0; i < eccLen; i++) {
    for (let j = 0; j < numBlocks; j++) blocks[j].push(stream[p++])
  }
  return blocks
}

/** A Reed-Solomon codeword evaluates to zero at α^0 … α^(eccLen-1). Anything else is not one. */
function syndromesZero(block: number[], eccLen: number): boolean {
  let root = 1
  for (let i = 0; i < eccLen; i++) {
    let acc = 0
    for (const b of block) acc = gfMul(acc, root) ^ b
    if (acc !== 0) return false
    root = gfMul(root, 2)
  }
  return true
}

interface ReadBack {
  text: string
  version: number
  mask: number
  allBlocksAreCodewords: boolean
}

/** Recover everything the symbol claims, from the matrix alone. */
function readBack(m: { size: number; version: number; modules: boolean[][] }): ReadBack {
  const mask = readMask(m.modules)
  const stream = readCodewords(m.modules, m.version, mask)
  const blocks = deinterleave(stream, m.version)
  const eccLen = ECC_PER_BLOCK[m.version]
  const allBlocksAreCodewords = blocks.every((b) => syndromesZero(b, eccLen))

  const data: number[] = []
  for (const b of blocks) data.push(...b.slice(0, b.length - eccLen))

  // Byte mode: a 4-bit mode indicator, then the length, then the bytes.
  const bits: number[] = []
  for (const b of data) for (let k = 7; k >= 0; k--) bits.push((b >>> k) & 1)
  const take = (start: number, len: number) =>
    bits.slice(start, start + len).reduce((acc, bit) => (acc << 1) | bit, 0)
  const mode = take(0, 4)
  const countBits = m.version <= 9 ? 8 : 16
  const len = take(4, countBits)
  const bytes: number[] = []
  for (let i = 0; i < len; i++) bytes.push(take(4 + countBits + i * 8, 8))
  const text =
    mode === 0b0100 ? new TextDecoder().decode(Uint8Array.from(bytes)) : `<mode ${mode}>`
  return { text, version: m.version, mask, allBlocksAreCodewords }
}

// ── The assertions ──────────────────────────────────────────────────────────────────────────────

const PAIRING_URL = 'http://192.168.1.5:10000/pair?code=ABCD-EFGH'

describe('encodeQr produces a symbol a decoder can read', () => {
  it('round-trips the pairing URL through an independent decoder', () => {
    const m = encodeQr(PAIRING_URL)
    expect(m, 'a 44-byte URL must fit').not.toBeNull()
    const back = readBack(m!)
    expect(back.text).toBe(PAIRING_URL)
    expect(back.mask, 'the format field must declare a legal mask').toBeGreaterThanOrEqual(0)
    expect(back.allBlocksAreCodewords, 'every block must be a valid RS codeword').toBe(true)
  })

  it('round-trips payloads across the version and block-structure boundaries', () => {
    // Chosen to cross the shapes most likely to be wrong: version 1 (no alignment pattern),
    // versions 4-5 (two blocks), 6-8 (four blocks, and 7 adds the version field), 9-10 (five
    // blocks with UNEQUAL block lengths, and 10 widens the character count to 16 bits).
    for (const len of [1, 14, 26, 42, 62, 84, 106, 122, 152, 180, 213]) {
      const text = Array.from({ length: len }, (_v, i) => String.fromCharCode(33 + (i % 90))).join('')
      const m = encodeQr(text)
      expect(m, `${len} bytes must fit somewhere in versions 1-10`).not.toBeNull()
      const back = readBack(m!)
      expect(back.text, `payload of ${len} bytes`).toBe(text)
      expect(back.allBlocksAreCodewords, `RS codewords for ${len} bytes`).toBe(true)
    }
  })

  it('round-trips non-ASCII, because the payload is UTF-8 bytes and not characters', () => {
    const text = 'http://ハウス.local:10000/pair?code=ABCD-EFGH'
    const back = readBack(encodeQr(text)!)
    expect(back.text).toBe(text)
    expect(back.allBlocksAreCodewords).toBe(true)
  })

  it('picks the SMALLEST version that fits, at the exact capacity boundary', () => {
    // Level-M byte capacities from the published table. Off-by-one here is a symbol that is
    // bigger than it needs to be (harmless) or one byte short (a truncated payload).
    for (const [bytes, version] of [
      [14, 1], [15, 2], [26, 2], [27, 3], [42, 3], [43, 4], [62, 4], [63, 5],
    ] as const) {
      const m = encodeQr('x'.repeat(bytes))
      expect(m!.version, `${bytes} bytes`).toBe(version)
      expect(m!.size).toBe(version * 4 + 17)
    }
  })

  it('REFUSES a payload that does not fit, rather than truncating it', () => {
    // Version 40 at level M holds 2331 bytes. The pair is the point: the refusal must be caused
    // by the capacity and not by the function always refusing.
    expect(encodeQr('x'.repeat(2332)), '2332 bytes cannot fit anywhere').toBeNull()
    const biggest = encodeQr('x'.repeat(2331))
    expect(biggest, '2331 bytes is the largest that fits').not.toBeNull()
    expect(biggest!.version).toBe(40)
  })
})

describe('the fixed patterns a camera looks for', () => {
  const m = encodeQr(PAIRING_URL)!

  it('places a finder pattern in exactly three corners', () => {
    const isFinder = (cx: number, cy: number) => {
      for (let dy = -3; dy <= 3; dy++) {
        for (let dx = -3; dx <= 3; dx++) {
          const dist = Math.max(Math.abs(dx), Math.abs(dy))
          if (m.modules[cy + dy][cx + dx] !== (dist !== 2)) return false
        }
      }
      return true
    }
    expect(isFinder(3, 3), 'top-left').toBe(true)
    expect(isFinder(m.size - 4, 3), 'top-right').toBe(true)
    expect(isFinder(3, m.size - 4), 'bottom-left').toBe(true)
    // The fourth corner must NOT have one — that is how a decoder works out the rotation.
    expect(isFinder(m.size - 4, m.size - 4), 'bottom-right stays empty').toBe(false)
  })

  it('alternates the timing patterns and keeps the dark module dark', () => {
    for (let i = 8; i < m.size - 8; i++) {
      expect(m.modules[6][i], `timing row at ${i}`).toBe(i % 2 === 0)
      expect(m.modules[i][6], `timing column at ${i}`).toBe(i % 2 === 0)
    }
    expect(m.modules[m.size - 8][8], 'the always-dark module').toBe(true)
  })

  it('places the alignment pattern at the version-7 table position and nowhere else', () => {
    const v7 = encodeQr('x'.repeat(110))! // level M holds 106 bytes at v6, 122 at v7
    expect(v7.version).toBe(7)
    const isAlign = (cx: number, cy: number) => {
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          const dist = Math.max(Math.abs(dx), Math.abs(dy))
          if (v7.modules[cy + dy][cx + dx] !== (dist !== 1)) return false
        }
      }
      return true
    }
    // Version 7's centres are 6, 22 and 38; the three finder corners are excluded, so the
    // only alignment pattern is at (22, 22).
    expect(isAlign(22, 22), 'the one alignment pattern').toBe(true)
    expect(isAlign(6, 6), 'not under the top-left finder').toBe(false)
    expect(isAlign(38, 6), 'not under the top-right finder').toBe(false)
    expect(isAlign(6, 38), 'not under the bottom-left finder').toBe(false)
  })

  it('writes a version field that decodes back to the version, only from version 7 up', () => {
    // Brute-forced against all 40 legal fields, so this cannot pass by comparing a value to
    // itself. Versions 1-6 carry no version field at all.
    const read = (m2: { size: number; modules: boolean[][] }) => {
      let bits = 0
      for (let i = 0; i < 18; i++) {
        if (m2.modules[Math.floor(i / 3)][m2.size - 11 + (i % 3)]) bits |= 1 << i
      }
      for (let v = 1; v <= 40; v++) if (versionField(v) === bits) return v
      return -1
    }
    expect(read(encodeQr('x'.repeat(110))!), 'version 7').toBe(7)
    expect(read(encodeQr('x'.repeat(200))!), 'version 10').toBe(10)
    // Version 6's version-field cells are ordinary data, so they must NOT decode as a version.
    const v6 = encodeQr('x'.repeat(100))!
    expect(v6.version).toBe(6)
    expect(read(v6)).toBe(-1)
  })
})

describe('the BCH fields, checked by division rather than by comparison', () => {
  /** Remainder of *value* divided by *gen* over GF(2) — polynomial long division. */
  const polyMod = (value: number, gen: number) => {
    const genBits = 32 - Math.clz32(gen)
    let v = value
    while (32 - Math.clz32(v) >= genBits) v ^= gen << (32 - Math.clz32(v) - genBits)
    return v
  }

  it('every format field is a real BCH(15,5) codeword carrying level M and its mask', () => {
    // The check the encoder cannot fake: strip the 0x5412 XOR and the field must divide by the
    // generator 0x537 exactly, with the top five bits being level M (0) and the mask.
    for (let mask = 0; mask < 8; mask++) {
      const field = formatField(mask)
      expect(polyMod(field ^ 0x5412, 0x537), `mask ${mask} divides by the generator`).toBe(0)
      expect((field ^ 0x5412) >>> 10, `mask ${mask} data bits`).toBe(mask)
    }
    // And the eight are distinct, so a decoder can tell them apart.
    expect(new Set([0, 1, 2, 3, 4, 5, 6, 7].map(formatField)).size).toBe(8)
  })

  it('every version field is a real BCH(18,6) codeword carrying its version', () => {
    for (let v = 7; v <= 40; v++) {
      expect(polyMod(versionField(v), 0x1f25), `version ${v} divides by the generator`).toBe(0)
      expect(versionField(v) >>> 12, `version ${v} data bits`).toBe(v)
    }
  })

  it('writes the SECOND format copy identically to the first', () => {
    // Two copies exist so a damaged corner still yields the mask. A decoder that happened to
    // read the other copy would get a different symbol if these drifted.
    const m = encodeQr(PAIRING_URL)!
    let second = 0
    for (let i = 0; i < 8; i++) if (m.modules[8][m.size - 1 - i]) second |= 1 << i
    for (let i = 8; i < 15; i++) if (m.modules[m.size - 15 + i][8]) second |= 1 << i
    expect(second).toBe(formatField(readMask(m.modules)))
  })
})

describe('qrPath renders every dark module, once, inside the quiet zone', () => {
  it('emits one square per dark module, offset by the quiet zone', () => {
    const m = encodeQr(PAIRING_URL)!
    const squares = [...qrPath(m).matchAll(/M(\d+) (\d+)h1v1h-1z/g)].map((g) => [+g[1], +g[2]])
    let dark = 0
    for (const row of m.modules) for (const c of row) if (c) dark++
    expect(squares.length, 'one square per dark module').toBe(dark)
    expect(new Set(squares.map((s) => s.join(','))).size, 'no duplicates').toBe(dark)
    for (const [x, y] of squares) {
      expect(m.modules[y - QUIET_ZONE][x - QUIET_ZONE], `square at ${x},${y}`).toBe(true)
    }
    // The quiet zone is the whole reason for the offset: nothing may be drawn in it.
    expect(Math.min(...squares.map((s) => s[0]))).toBeGreaterThanOrEqual(QUIET_ZONE)
    expect(Math.min(...squares.map((s) => s[1]))).toBeGreaterThanOrEqual(QUIET_ZONE)
  })
})
