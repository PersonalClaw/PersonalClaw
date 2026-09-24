import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A settings-hub tile that cannot load says so — every tile, derived, not listed ────────────
//
// 🔴 WHAT THIS FILE USED TO BE, AND WHY IT HAD TO STOP. It held `CAN_FAIL`, a FIVE-MEMBER list of
// tiles required to render a failure line, plus a floor asserting that **18 or more hub hooks still
// substituted a value for a failed read**. So its two halves pulled in opposite directions: it
// mandated honesty for five named tiles and simultaneously pinned the other ~30 in place. Its own
// header said so, in the clearest terms an enumerated rail ever gets:
//
//     "the `>= 4` floors below cannot see a tile that starts shimmering forever.
//      Re-measuring which of the 13 genuinely back a failable tile is its own pass, not this one."
//
// That pass is this one. The hub's 21 remaining fetcher swallows are gone, the hand-rolled failure
// captions are gone, and `BentoCard` owns ONE treatment behind a `failed` prop. So the rail inverts
// with them: instead of naming the tiles that must be honest, it DERIVES every tile that reads
// server data and requires the prop on each. A thirty-eighth tile added tomorrow is covered on the
// day it is written, which is the property a list structurally cannot have.
//
// 🪤 THE THREE THINGS A TILE USED TO DO ON A FAILED READ, all of which `failed` replaces:
//   · claim a number      `.catch(() => null)` resolved the fetcher, so the body rendered against a
//                         substitute: "0 archived sessions", "Nothing installed yet".
//   · shimmer forever     drop the swallow alone and `data === undefined` still satisfies the
//                         skeleton branch. This file used to call that the accepted cost.
//   · a muted caption     the 15 tiles that DID bind `error` each hand-rolled
//                         `<div data-type="caption" class="text-on-surface-low">Couldn't load …`.
//                         Body ink, no glyph, no retry, fifteen wordings.
//
// The tree-wide count that makes a NEW swallow anywhere in `web/src` fail CI lives in
// `ui/loadErrorState.test.tsx` §B (a per-file budget that may only fall). This file is the hub's
// half: the RENDER side, which a count of fetchers cannot see.

const SRC = join(process.cwd(), 'src')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
// Comments stripped: this file and its subject both QUOTE the shapes they no longer use, deliberately,
// because the contrast is the documentation. A scan that counts prose as code has been wrong five
// times in this area alone.
const widgets = strip(readFileSync(join(SRC, 'pages/settings/settingsWidgets.tsx'), 'utf8'))
const bento = strip(readFileSync(join(SRC, 'pages/settings/bento.tsx'), 'utf8'))

/** The hub's data hooks: `const useX = () => useQuery(…)` / `const useX = () => { … useQuery(…) }`.
 *  Derived from the file, so adding a hook adds it here. */
const DATA_HOOKS: string[] = [...widgets.matchAll(/const (use[A-Z]\w*) = \(\) =>([\s\S]{0,600}?)(?=\nconst |\n\/\/|\nexport )/g)]
  .filter((m) => /\buseQuery\s*[<(]/.test(m[2]))
  .map((m) => m[1])

/** Every `render(query, go) { … }` body, brace-matched — never a character window. A `{0,N}` slice
 *  from one tile runs into the next and reports its neighbour's props as its own; this area has been
 *  bitten by that at least three times (once reporting 2 sites of 4). */
function renderBodies(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/\n    render\(query, go\) \{/g)) {
    let i = src.indexOf('{', m.index! + 5)
    let depth = 0
    let j = i
    for (; j < src.length; j++) {
      if (src[j] === '{') depth++
      else if (src[j] === '}' && --depth === 0) break
    }
    out.push(src.slice(i, j + 1))
  }
  return out
}

/** A tile's `<BentoCard …>` opening tag, scanned brace-aware to its own closing `>`. A non-greedy
 *  scan to the first `>` stops inside `onClick={() => go('apps')}` and truncates the tag before the
 *  props this rail is about — the exact trap the previous version of this file recorded. */
function bentoTag(body: string): string | null {
  const m = /<BentoCard\b/.exec(body)
  if (!m) return null
  let depth = 0
  let k = m.index + m[0].length
  for (; k < body.length; k++) {
    const c = body[k]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) break
  }
  return body.slice(m.index, k)
}

const TILES = renderBodies(widgets).map((body) => ({
  body,
  tag: bentoTag(body) ?? '',
  title: (bentoTag(body) ?? '').match(/title="([^"]+)"/)?.[1] ?? '(untitled)',
  /** Does this tile read server data at all? Derived from the hook census, not from a list. */
  reads: DATA_HOOKS.filter((h) => new RegExp(`\\b${h}\\(`).test(body)),
}))

describe('the hub census sees what it claims to', () => {
  it('VACUITY: finds the hooks, the tiles, and the data-backed subset', () => {
    // Three floors, all well under the live numbers so ordinary growth does not trip them. Without
    // these, a broken regex reads exactly like a perfectly honest hub.
    expect(DATA_HOOKS.length, 'the hook census found nothing').toBeGreaterThanOrEqual(25)
    expect(TILES.length, 'the tile census found nothing').toBeGreaterThanOrEqual(30)
    expect(TILES.filter((t) => t.reads.length).length, 'no tile appears to read data')
      .toBeGreaterThanOrEqual(25)
    // And it must find the tiles that read NOTHING, or the discriminator is not discriminating:
    // Account, Design, Diagnostics and Import/Export are pure nav cards.
    expect(TILES.filter((t) => !t.reads.length).length, 'the no-data tiles must be recognised as such')
      .toBeGreaterThanOrEqual(3)
  })

  it('every tile has a real `<BentoCard>` tag the scanner could read', () => {
    const blind = TILES.filter((t) => !t.tag).map((t) => t.title)
    expect(blind, 'a render body with no BentoCard tag — the scanner would skip it silently').toEqual([])
  })
})

describe('a tile that reads server data says when the read failed', () => {
  it('passes `failed` — derived from the hook census, so a NEW tile is covered on day one', () => {
    const silent = TILES.filter((t) => t.reads.length && !/\bfailed=\{/.test(t.tag))
      .map((t) => `${t.title} (reads ${t.reads.join(', ')})`)
    expect(
      silent,
      'these tiles read server data and never say the read failed, so a 500 renders as an empty or '
      + 'shimmering card. Pass `failed={<status> === \'error\'} error={<err>} onRetry={<refresh>}` — '
      + '`BentoCard` owns the treatment.',
    ).toEqual([])
  })

  it('and hands over the rejection itself, so the band can show the server\'s words', () => {
    // `failed` alone would render "The server didn't respond." over a backend message that said
    // something useful ("name is required"). The prop is cheap; the sentence is not recoverable later.
    const mute = TILES.filter((t) => t.reads.length && /\bfailed=\{/.test(t.tag) && !/\berror=\{/.test(t.tag))
      .map((t) => t.title)
    expect(mute, 'pass `error={…}` too').toEqual([])
  })

  it('and offers a retry, because every one of these reads has a `refresh`', () => {
    // `useQuery` returns a STABLE `refresh`; there is no tile here that cannot offer one, so an
    // omission is an oversight rather than a design choice. (If one ever genuinely cannot, that is a
    // deliberate change to this assertion with a reason — not a silent gap.)
    const stuck = TILES.filter((t) => t.reads.length && /\bfailed=\{/.test(t.tag) && !/\bonRetry=\{/.test(t.tag))
      .map((t) => t.title)
    expect(stuck, 'pass `onRetry={refresh}`').toEqual([])
  })

  it('and does NOT pass it on a tile that reads nothing — no dead props', () => {
    const pointless = TILES.filter((t) => !t.reads.length && /\bfailed=\{/.test(t.tag)).map((t) => t.title)
    expect(pointless, 'a nav-only card has no read that can fail').toEqual([])
  })
})

describe('one treatment, owned by the card', () => {
  it('the hand-rolled muted caption is GONE from the hub', () => {
    // 🔑 The fifteen copies are the point. The previous version of this rail asserted a FLOOR on them
    // ("every one of them uses the same muted type as the original"), which locked the duplication in
    // as the standard. Same fact, opposite direction: the idiom must not come back, because the card
    // now owns it and a sixteenth copy would be a second answer to one question.
    const captions = [...widgets.matchAll(/data-type="caption"[^>]*>Couldn&rsquo;t load/g)]
    expect(captions.map((m) => m[0]), 'reach for BentoCard\'s `failed` instead').toEqual([])
  })

  it('the card checks `failed` BEFORE `loading`, or the band is unreachable', () => {
    // 🪤 THE REACHABILITY TRAP, and it is the whole reason the "shimmer forever" state existed:
    // `data === undefined` is true for the loading AND the failed read, so a card that tests
    // `loading` first can never render the failure however many props it is passed. Assert the
    // ORDER, not the presence.
    const failAt = bento.search(/\{failed\s*$|\{failed\s*\n|\{failed\b/m)
    const loadAt = bento.search(/\?\s*<CardSkeleton\b|:\s*loading\b/)
    expect(failAt, 'BentoCard must branch on `failed`').toBeGreaterThan(-1)
    expect(loadAt, 'BentoCard must still have a loading branch').toBeGreaterThan(-1)
    expect(failAt, 'the failure branch must precede the skeleton').toBeLessThan(loadAt)
  })

  it('the band is a FAILURE, not a hint — it takes the shared danger paint', () => {
    // Not a literal wash: `design/errorTreatments` single-sources `ERROR_SURFACE_PAINT` precisely
    // because hand-written copies of it had already diverged into two, one of which named tokens that
    // are DEFINED NOWHERE and therefore rendered a transparent "alert" with plain body ink.
    expect(bento, 'the failure band must use the single-sourced paint').toMatch(/ERROR_SURFACE_PAINT/)
    expect(bento, 'and route the message through the readable-text filter').toMatch(/readableErrText/)
  })

  it('but it is NOT a live region — 22 tiles shimmer at once on a cold open', () => {
    // 🔑 THE MEASUREMENT, restated because it is the reason this differs from `ui/LoadError`, which
    // IS an alert. Sampled on a cold `#/settings`: 28 nav buttons, peak **22 simultaneous** in-flight
    // tiles, last settling at 3.6s. A `role="alert"` per tile is up to 22 assertive announcements for
    // one page load. The failure reaches AT as an `aria-describedby` description on the nav button —
    // which adds the fact WITHOUT renaming the control, the ruling this component already applies to
    // its loading state.
    expect(bento, 'no per-tile alert').not.toMatch(/role="alert"/)
    expect(bento, 'the failure is described on the reachable control').toMatch(/aria-describedby=\{failed/)
    // And `aria-busy` must yield: a failed tile is not a busy tile.
    expect(bento, 'aria-busy must not stay true on a failed read').toMatch(/aria-busy=\{\(loading && !failed\)/)
  })

  it('a failed tile drops its footer instead of contradicting the band', () => {
    // The footer describes data the tile no longer has ("Embedder: …"), so leaving it under a
    // "couldn't load" band prints a value and denies having it in the same card.
    expect(bento).toMatch(/footer && !failed/)
  })
})

describe('the hub no longer fabricates values for a failed read', () => {
  it('only the documented key-mirroring swallows remain', () => {
    // Segment per hook, never a character window: a `{0,600}` scan from one `const useX = () =>` runs
    // past its own definition into the next hook's `.catch` and reports it as a swallower.
    const starts = [...widgets.matchAll(/const (use\w+) = \(\) =>/g)]
    const swallowing = starts
      .filter((m, i) => /\.catch\(\(\)\s*=>\s*\(?\s*(\[\]|null|undefined|\{\}|'')/
        .test(widgets.slice(m.index!, starts[i + 1]?.index ?? m.index! + 700)))
      .map((m) => m[1])
    // EXACTLY these two, and each is explained at its definition. What they have in common is the
    // line worth remembering, because it is narrower than "this read is unimportant": in both the
    // surface makes NO CLAIM about the value.
    //
    // 🔑 `usePacksInstalled` WAS THE THIRD AND IS GONE (#532). Its entry here said the honest fix is
    // "one key, not two disagreeing fetchers, and that is the panel's change to make" — which was an
    // argument about ORDER, not a defence of the swallow, and the panel's change is now made. Both
    // fetchers were de-swallowed in one commit, because the byte-identity that made this hook's
    // fallback defensible is exactly what made it impossible to fix either side alone: leaving `[]`
    // here would have primed `settings:packs:installed` and made the panel's new error branch
    // unreachable on every hub→panel journey. The packs TILE gained the other half — its `failed` is
    // now `pStatus === 'error' || iStatus === 'error'`, because the big "N installed packs" stat is a
    // COUNT, and a count is the one thing a failed read does not have.
    //   useAgentDefaults    a MIXED hook: its governing config read has no fallback (the panel's
    //                       honesty depends on that), while its DECORATING read of the default
    //                       agent's name keeps `.catch(() => '')` and renders '—'.
    //   useToolsSavings     an optional meter; the Tool-output tile headlines the RULE count when
    //                       there is no savings number, so an absent meter is a designed state. Held
    //                       by a prior ruling with its own rail (`dashboard/healthUnknown.test.ts`),
    //                       and de-swallowing it would have been a no-op on screen anyway —
    //                       `savedTokens` derives from `savings?.…`, so a rejection and a null render
    //                       identically. It was removed during this change and put back for exactly
    //                       that reason.
    // 🪤 The previous version of this rail could not express that distinction — it asked only "does
    // this hook contain any `.catch(() => `" and so counted `useAgentDefaults` as a swallower both
    // before and after its fix. The list is exact now, which is what makes a THIRD arrival visible.
    expect(swallowing.sort(), 'a hub hook started substituting a value for a failed read again')
      .toEqual(['useAgentDefaults', 'useToolsSavings'])
  })

  it('and no tile keeps a branch that only a fabricated value could reach', () => {
    // The five `Couldn't check` pills were reachable only while a swallow turned a rejection into
    // `null`. With the card owning failure, `children` renders only when the read succeeded, so
    // `!data` is false for every array/object response — the branches were dead on arrival.
    expect(widgets, "the `Couldn't check` pill was unreachable dead code").not.toMatch(/Couldn't check/)
  })
})
