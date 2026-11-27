/** How the web orders two versions: the release forms PersonalClaw ships, in the order PEP 440
 *  gives them, as the gateway (`personalclaw.versions`) and the desktop app (`desktop/appUpdate.js`
 *  `compareVersions`) order them. `X.Y.Z.devN < X.Y.ZaN < X.Y.ZbN < X.Y.ZrcN < X.Y.Z < X.Y.Z.postN`,
 *  a tag's `v` and `-rc.1` spelling included. The three read one table of rows in their own tests
 *  (`tests/fixtures/version_order.json`), and the pattern is the desktop's, character for character
 *  (`versions.test.ts`). */

/** The desktop app's own pattern (`desktop/appUpdate.js` `VERSION`). */
export const VERSION = /^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:[-.]?(a|alpha|b|beta|c|rc|pre|preview)[-.]?(\d{0,6}))?(?:[-.]?post[-.]?(\d{1,6}))?(?:[-.]?dev[-.]?(\d{0,6}))?$/i
const PRE_RANK: Record<string, number> = { a: 0, alpha: 0, b: 1, beta: 1, c: 2, rc: 2, pre: 2, preview: 2 }

interface Parsed { release: number[]; pre: [number, number] | null; post: number | null; dev: number | null }

function parse(text: unknown): Parsed | null {
  if (typeof text !== 'string' || text.length > 64) return null
  const m = VERSION.exec(text.trim())
  if (!m) return null
  return {
    release: [Number(m[1]), Number(m[2]), Number(m[3])],
    pre: m[4] ? [PRE_RANK[m[4].toLowerCase()], Number(m[5] || 0)] : null,
    post: m[6] === undefined ? null : Number(m[6]),
    dev: m[7] === undefined ? null : Number(m[7] || 0),
  }
}

function orderKey(v: Parsed): number[] {
  if (v.pre) return [...v.release, 1, ...v.pre, v.post === null ? -1 : v.post, v.dev === null ? Infinity : v.dev]
  if (v.post !== null) return [...v.release, 3, v.post, v.dev === null ? Infinity : v.dev]
  if (v.dev !== null) return [...v.release, 0, v.dev]
  return [...v.release, 2]
}

/** -1, 0 or 1 as *a* is older than, the same as, or newer than *b*; null when either is not a
 *  version this reads. */
export function compareVersions(a: unknown, b: unknown): -1 | 0 | 1 | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  const kx = orderKey(x)
  const ky = orderKey(y)
  for (let i = 0; i < Math.max(kx.length, ky.length); i++) {
    const p = i < kx.length ? kx[i] : -Infinity
    const q = i < ky.length ? ky[i] : -Infinity
    if (p !== q) return p < q ? -1 : 1
  }
  return 0
}
