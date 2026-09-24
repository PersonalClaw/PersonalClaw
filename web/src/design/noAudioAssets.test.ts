/**
 * ZERO AUDIO FILES SHIP (PERSONALITY-THEMES §S2, T2.1 — the plan's "CI grep").
 *
 * Sound cues are SYNTHESISED: three oscillator recipes, a few hundred bytes of
 * arithmetic. The alternative — bundling samples — would put audio bytes in every
 * user's first load for a feature that is off by default and that most people will
 * never turn on. That is the whole reason `soundCues.ts` looks the way it does, so
 * it needs a rail rather than a comment.
 *
 * Two claims, and a vacuity floor under each, because a pattern that matches
 * nothing looks exactly like a pattern that passes:
 *
 *   1. No audio asset exists in any tree that contributes bytes to the bundle
 *      (`web/public`, `web/src`, and `web/dist` when it has been built). Floor: the
 *      matcher is proven against a planted filename, and the walk is proven to have
 *      found a real tree.
 *   2. The cue module reaches for no audio FILE by any route — no
 *      `HTMLAudioElement`, no asset import, no fetch-and-decode. Floor: comments are
 *      stripped first (the module's own header discusses the things it must not do,
 *      which is precisely the false pass this guards against) and the stripper is
 *      itself asserted.
 */

import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// vitest runs from web/.
const WEB = process.cwd()

/** Unambiguous audio containers. Video containers are deliberately NOT here: the
 *  risk this rail exists for is a bundled cue SAMPLE, and tripping a future
 *  screen-recording asset would be friction with no defect behind it. */
const AUDIO_EXT = [
  'mp3', 'wav', 'wave', 'ogg', 'oga', 'opus', 'weba', 'm4a', 'm4b', 'aac',
  'flac', 'mid', 'midi', 'aiff', 'aifc', 'aif', 'wma', 'au', 'snd', 'caf', 'amr',
]
const AUDIO_RE = new RegExp(`\\.(${AUDIO_EXT.join('|')})$`, 'i')

function isAudioAsset(path: string): boolean {
  return AUDIO_RE.test(path)
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out
}

/** Every tree whose contents can end up in the shipped bundle. `dist/` is included
 *  when it exists (the post-build lane) but its absence does not turn this into a
 *  skipped test: the two trees that PRODUCE dist are always scanned. */
function bundleTrees(): string[] {
  return ['public', 'src', 'dist'].map((d) => join(WEB, d)).filter((d) => existsSync(d))
}

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

describe('no audio file ships in the bundle', () => {
  it('the extension matcher matches audio and only audio', () => {
    // Floor for the assertion below: without this, an over-escaped or empty pattern
    // would report "zero audio files" on a tree full of them.
    for (const planted of ['public/ding.mp3', 'src/cue.WAV', 'dist/assets/blip-9f3a.ogg', 'a/b.midi']) {
      expect(isAudioAsset(planted), planted).toBe(true)
    }
    for (const fine of ['public/favicon.svg', 'src/design/soundCues.ts', 'dist/index.html', 'a/notes.mp3.md']) {
      expect(isAudioAsset(fine), fine).toBe(false)
    }
  })

  it('finds the trees it claims to scan', () => {
    const trees = bundleTrees()
    // public/ + src/ always exist; dist/ only after a build. A run that scanned
    // nothing would otherwise pass loudly.
    expect(trees.length, 'public/ and src/ must both be found').toBeGreaterThanOrEqual(2)
    expect(trees.some((t) => t.endsWith('public'))).toBe(true)
    expect(trees.some((t) => t.endsWith('src'))).toBe(true)
  })

  it('every bundle tree is free of audio assets', () => {
    const files = bundleTrees().flatMap(walk)
    // Vacuity floor on the WALK (deliberately loose — it guards against an empty
    // listing, it is not a population count).
    expect(files.length, 'the walker must find the tree').toBeGreaterThan(200)
    const found = files.filter(isAudioAsset).map((f) => relative(WEB, f))
    expect(
      found,
      'Sound cues are synthesised — an audio asset here would ship bytes to every ' +
        'user for a feature that is off by default. Synthesise it in soundCues.ts instead.',
    ).toEqual([])
  })
})

describe('the cue module reaches for no audio file', () => {
  const raw = readFileSync(join(WEB, 'src/design/soundCues.ts'), 'utf8')
  const code = stripComments(raw)

  it('the comment stripper works, or every check below is vacuous', () => {
    expect(stripComments('x /* new Audio( */ y')).not.toMatch(/new Audio\(/)
    expect(stripComments('x // new Audio(\ny')).not.toMatch(/new Audio\(/)
    expect(stripComments('const a = new Audio()')).toMatch(/new Audio\(/)
  })

  it("the module's own comments DO discuss the banned APIs — so stripping is load-bearing", () => {
    // Not decoration: this is the measured reason the checks strip comments first. If
    // the header stops naming the alternatives, the stripper stops being necessary and
    // this rail should be re-read rather than silently weakened.
    expect(raw).toMatch(/HTMLAudioElement/)
    expect(code).not.toMatch(/HTMLAudioElement/)
  })

  it('uses no HTMLAudioElement, no <audio>, no asset import, no fetch-and-decode', () => {
    expect(code, 'an <audio> element means a file').not.toMatch(/new Audio\(|<audio|HTMLAudioElement/)
    expect(code, 'an audio import would emit an asset').not.toMatch(
      new RegExp(`from ['"][^'"]+\\.(${AUDIO_EXT.join('|')})['"]`, 'i'),
    )
    expect(code, 'no cue is fetched or decoded — every one is built from oscillators').not.toMatch(
      /decodeAudioData|fetch\(|XMLHttpRequest/,
    )
  })

  it('builds its tones from oscillator nodes — the positive half of the same claim', () => {
    // Vacuity floor for the negatives above: a module that did nothing at all would
    // satisfy every "does not contain" check.
    expect(code).toMatch(/createOscillator\(\)/)
    expect(code).toMatch(/createGain\(\)/)
  })
})
