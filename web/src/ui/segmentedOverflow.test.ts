import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A registry-fed Segmented needs a collapse strategy ───────────────────────────
//
// `Segmented` renders one pill per option on a single `whitespace-nowrap` row. With no `collapse`
// prop it does not measure anything and cannot shrink, so when the strip is wider than its slot the
// tail is simply cut off by whichever ancestor sets `overflow: hidden`.
//
// Measured on `#/artifacts`, whose kind filter maps `ARTIFACT_KINDS` (16 kinds → 17 tabs, 1152px):
//
//   viewport   tabs off screen   pointer/touch reach          keyboard End
//   1440px     0                 —                            —
//   834px      7 of 17           wheel: no move, swipe: none   page shell scrolls 546px
//   390px      12 of 17          wheel: no move, swipe: none   page shell scrolls 777px
//
// 'SVG' through 'Video' could not be picked by pointer or touch at all: the row is `overflow: hidden`
// with `document.scrollWidth === 390`, so there is no user-initiated scroll. Keyboard `End` did reach
// them — by scrolling `#root` sideways, which dragged the search field and the whole grid off screen
// (`searchLeft: 16 → -761`). `collapse="scroll"` fixes both halves: the strip scrolls in place and
// `#root` stops overflowing.
//
// ⚠️ THE PREDICATE HERE IS A PROXY. What actually matters is RENDERED WIDTH, which no static rail can
// compute — label length dominates. So this rail uses option COUNT, with the threshold set from the
// measured spread rather than picked: `ARTIFACT_KINDS` (16) is nearly 3x the next-largest registry
// feeding a Segmented anywhere in the app (6). A threshold of 10 therefore flags nothing that fits
// today and catches a registry that grows past the point where any label set could fit a phone.
// Between 6 and 10 it stays a per-site measurement — cycle 71 measured the 6-entry Loop-kind slider
// and it fits at 390px, which is exactly why this is not a "every mapped Segmented" rule.

const THRESHOLD = 10
const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

/** Entry count for every `const NAME = [ … ]` array of `{ key: … }` / `{ id: … }` records. Counted
 *  from the opening bracket, NOT the declaration — a type annotation like
 *  `ARTIFACT_KINDS: { key: ArtifactKind; … }[] = [` otherwise contributes a phantom entry. */
function registrySizes(files: string[]): Map<string, number> {
  const sizes = new Map<string, number>()
  for (const f of files) {
    const s = readFileSync(f, 'utf8')
    const re = /(?:export )?const ([A-Za-z_][\w]*)(?::[^=]+)? = \[/g
    let m: RegExpExecArray | null
    while ((m = re.exec(s))) {
      const open = m.index + m[0].length - 1
      let depth = 0, end = open
      for (let i = open; i < s.length; i++) {
        if (s[i] === '[') depth++
        else if (s[i] === ']') { depth--; if (!depth) { end = i; break } }
      }
      const body = s.slice(open, end + 1)
      const n = (body.match(/\{\s*(?:key|id)\s*:/g) ?? []).length
      if (n > (sizes.get(m[1]) ?? 0)) sizes.set(m[1], n)
    }
  }
  return sizes
}

/** Every `<Segmented … />` JSX element, with the registry names its `options` map over. */
function segmentedSites(files: string[]) {
  const sites: { file: string; line: number; collapse: string | null; registries: string[] }[] = []
  for (const f of files) {
    const s = readFileSync(f, 'utf8')
    let i = 0
    while ((i = s.indexOf('<Segmented', i)) !== -1) {
      let depth = 0, end = i
      for (let j = i; j < s.length; j++) {
        const c = s[j]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0 && s[j - 1] === '/') { end = j; break }
      }
      const tag = s.slice(i, end + 1)
      sites.push({
        file: f.slice(SRC.length + 1),
        line: s.slice(0, i).split('\n').length,
        collapse: (tag.match(/collapse="(\w+)"/) ?? [])[1] ?? null,
        registries: [...tag.matchAll(/\b([A-Z][A-Z0-9_]{2,})\.map\(/g)].map((m) => m[1]),
      })
      i = end + 1
    }
  }
  return sites
}

const FILES = walk(SRC)
const SIZES = registrySizes(FILES)
const SITES = segmentedSites(FILES)

describe('a Segmented fed by a large registry declares how it collapses', () => {
  it('the scan found call sites and registries at all', () => {
    // Vacuity floor: a matcher that silently stops matching reads as a clean pass.
    expect(SITES.length).toBeGreaterThan(20)
    expect(SIZES.get('ARTIFACT_KINDS')).toBe(16)
  })

  it(`at least one site is actually governed by the >=${THRESHOLD}-option rule`, () => {
    // If nothing is in scope the rule below is decoration. Today exactly one site qualifies; if that
    // ever drops to zero the threshold has drifted away from the code and should be re-measured.
    const governed = SITES.filter((s) => s.registries.some((r) => (SIZES.get(r) ?? 0) >= THRESHOLD))
    expect(governed.length).toBeGreaterThan(0)
  })

  it(`every Segmented mapping a registry of >=${THRESHOLD} options passes \`collapse\``, () => {
    const offenders = SITES
      .filter((s) => s.registries.some((r) => (SIZES.get(r) ?? 0) >= THRESHOLD))
      .filter((s) => !s.collapse)
      .map((s) => {
        const big = s.registries.map((r) => `${r}=${SIZES.get(r)}`).join(',')
        return `${s.file}:${s.line} (${big})`
      })
    expect(offenders, 'a strip this wide is cut off, not shrunk — pass collapse="scroll" or "menu"').toEqual([])
  })

  it('the artifacts kind filter — the site this rail was measured on — scrolls', () => {
    const site = SITES.find((s) => s.file === 'pages/artifacts/ArtifactsSection.tsx' && s.registries.includes('ARTIFACT_KINDS'))
    expect(site, 'the artifacts kind Segmented moved or stopped mapping ARTIFACT_KINDS').toBeTruthy()
    // 'scroll' rather than 'menu' deliberately: this is a page toolbar, and `collapse="menu"` measures
    // its slot against `parent.clientWidth`, which is wrong in a `flex-wrap` row (children sum across
    // lines, so free space reads negative and the strip latches collapsed at every width). The two
    // existing scroll adopters are both body strips; the menu adopters are all header controls.
    expect(site!.collapse).toBe('scroll')
  })
})
