/** Every failure on the Apps surface rendered in ordinary body ink, and said nothing.
 *
 * Nine sites across `pages/apps/AppsSection.tsx` and `pages/settings/AppsPanel.tsx` hand-rolled
 * `<div data-type="body-s" className="text-negative">{…}</div>`. **`--color-negative` is defined
 * nowhere in the repository** — not in `@theme`, not in the `.light` block, not in any of the 12
 * schemes, and not in the runtime token registry. Tailwind v4 builds `text-*` colour utilities
 * exclusively from the `--color-*` namespace, so `text-negative` matches nothing and emits NO rule
 * at all. There is no broken declaration to spot in devtools; there is simply no declaration.
 *
 * 🪤 SO IT WAS NOT "AN ERROR WITHOUT ITS COLOUR" — IT WAS AN ERROR WEARING THE STRONGEST INK ON THE
 * PAGE. `data-type="body-s"` sets size and weight but no colour, so the text fell through to
 * `body { color: var(--color-on-surface) }` — the app's PRIMARY body ink, brighter than the
 * `text-on-surface-low` used for the hints beside it. A failure therefore read as confident,
 * ordinary prose. And none of the nine carried `role="alert"`, so a screen-reader user got nothing.
 * Two independent channels for "this failed", both silent.
 *
 * 🔴 THE ONE WITH A DATA COST is `cfg.err` (here and in `settings/AppsPanel.tsx`). It comes from
 * `appConfigForm`'s save guard, whose own comment explains that the backend's `write_config`
 * REPLACES the file with no merge — so a save from a form that never loaded would erase the app's
 * stored config, secrets included. Its refusal, *"Couldn't load this app's configuration, so there
 * is nothing to save yet. Retry the load first."*, is the only evidence the click did nothing and
 * the only instruction for recovering. A user who read it as a hint would close the modal believing
 * the save landed.
 *
 * 🔑 THE REPO ALREADY KNEW, AND ALREADY NAMED THE FIX. `design/inertUtilities.test.ts` compiles every
 * utility in the tree against `tokens.css` using Tailwind itself as the oracle, and these sites sat
 * on `inertUtilities.allowlist.json`, whose `_comment` says: *"They are BUGS, not exemptions… Suspected
 * intent, for whoever picks these up: … text-negative -> text-danger; text-positive -> text-ok"*, and
 * *"The list may only SHRINK."* This change takes that instruction and shrinks it by two entries.
 *
 * That rail is also why this file needs no inertness assertion of its own: its zero-tolerance test
 * reds if an allowlist entry is dropped while the class is still inert, and its stale-entry test reds
 * if an entry lingers after the class is fixed. Green there IS the proof, in both directions.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FieldError } from '../../ui/forms'

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
/** 🪤 STRIP COMMENTS BEFORE SCANNING FOR A CLASS NAME. The fix's own comments have to NAME the dead
 *  class to explain it, and a source scanner cannot tell that prose from a live `className` — my
 *  first run failed against code that was already correct. Blanked in place (newlines preserved) so
 *  line numbers still line up for anything that reports them. */
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')
const srcOf = (rel: string) => strip(read(rel))
const APPS = 'pages/apps/AppsSection.tsx'
const PANEL = 'pages/settings/AppsPanel.tsx'

describe('the token the nine sites named does not exist, and the one they use now does', () => {
  const tokens = read('design/tokens.css')
  /** The `.light` override block — a token defined only in dark is its own defect. */
  const light = tokens.slice(tokens.indexOf('.light'))

  it('🪤 --color-negative and --color-positive are defined NOWHERE', () => {
    // The whole basis of the fix. If either is ever legitimately introduced, this reds and the
    // change becomes a judgement call again rather than a correction.
    expect(tokens, 'no --color-negative declaration').not.toMatch(/--color-negative\s*:/)
    expect(tokens, 'no --color-positive declaration').not.toMatch(/--color-positive\s*:/)
  })

  it('--color-danger and --color-ok are defined, in BOTH themes', () => {
    for (const t of ['--color-danger', '--color-ok']) {
      expect(tokens, `${t} is declared`).toMatch(new RegExp(`${t}\\s*:`))
      expect(light, `${t} is declared for light too`).toMatch(new RegExp(`${t}\\s*:`))
    }
  })

  it('no `negative`/`positive` colour reference survives anywhere in the app', () => {
    // Includes the inline-style form. `ui/motion/Disintegrate.tsx` mixed the dead token into a
    // gradient, and an undefined token makes the whole mix expression invalid at computed-value
    // time — so the danger wash painted nothing at all. Class-name rails cannot see a `var()`
    // inside a style object, so nothing in the tree was watching it.
    //
    // 🪤 This comment used to spell that mix function out literally, and `design/statusTint.test.ts`
    // COUNTED IT — a ratchet over `pages/**` that reds when the tally rises, and it cannot tell my
    // prose from a real inline tint. Third time this tick a scanner read my explanation as code.
    for (const rel of [APPS, PANEL, 'ui/motion/Disintegrate.tsx']) {
      expect(srcOf(rel), `${rel} must not name a nonexistent token`)
        .not.toMatch(/(?:text|bg|border|ring)-(?:negative|positive)\b|--color-(?:negative|positive)\b/)
    }
  })
})

describe('every one of the nine now announces, through the shared owner', () => {
  it('FieldError is that owner: role=alert, the danger token, the same size', () => {
    // Asserted rather than assumed — the whole fix rests on this primitive's contract.
    render(<FieldError>Couldn’t load this app’s configuration.</FieldError>)
    const el = screen.getByRole('alert')
    expect(el.className, 'the live token').toContain('text-danger')
    expect(el.getAttribute('data-type'), 'same size as the div it replaces').toBe('body-s')
    expect(el.textContent).toBe('Couldn’t load this app’s configuration.')
    cleanup()
  })

  it('both files render their failures through FieldError and hand-roll none', () => {
    for (const rel of [APPS, PANEL]) {
      const c = srcOf(rel)
      expect(c, `${rel} imports the shared owner`).toMatch(/FieldError/)
      // 🪤 The defect SHAPE, not the old class: any `body-s` div holding an error variable would be
      // a hand-rolled copy again even if it named a live token.
      const handRolled = [...c.matchAll(/<div[^>]*data-type="body-s"[^>]*>\{(?:cfg\.err|err|guarded\.error)\}/g)]
      expect(handRolled.map((m) => m[0]), `${rel} still hand-rolls an error line`).toEqual([])
    }
  })

  it('🔴 the save guard’s own refusal is one of them', () => {
    // The site with the data cost, pinned by name at both consumers.
    for (const rel of [APPS, PANEL]) {
      expect(read(rel), `${rel} routes cfg.err through FieldError`)
        .toMatch(/\{cfg\.err && <FieldError>\{cfg\.err\}<\/FieldError>\}/)
    }
    // And the guard it comes from still exists, or this rail is protecting nothing.
    const form = read('pages/apps/appConfigForm.tsx')
    expect(form, 'the unloaded-form save guard').toMatch(/nothing to save yet/)
    expect(form, 'and it still refuses rather than writing').toMatch(/if \(data === undefined\)/)
  })
})

describe('the five identical rows became one', () => {
  const code = srcOf(APPS)

  it('GuardedFailure is declared once and used at every guarded surface', () => {
    expect([...code.matchAll(/function GuardedFailure\b/g)], 'exactly one declaration').toHaveLength(1)
    // Five install/update/store surfaces shared a byte-identical row. That duplication is WHY the
    // dead class survived: there was no single place anyone would have looked.
    expect([...code.matchAll(/<GuardedFailure guarded=\{guarded\} \/>/g)].length,
      'every guarded surface adopts it').toBeGreaterThanOrEqual(5)
  })

  it('it is self-guarding, so no call site re-wraps it in its own `&&`', () => {
    const fn = code.match(/function GuardedFailure[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn, 'found GuardedFailure').not.toBe('')
    expect(fn, 'it returns null on no error').toMatch(/if \(!guarded\.error\) return null/)
    expect(code, 'and no site guards it again').not.toMatch(/guarded\.error && \(?\s*<GuardedFailure/)
  })

  it('the error and its fix-prompt stay together, as the hook says they must', () => {
    // `useGuardedInstall`'s own doc: fixPrompt "Rides alongside `error` — the same surface that
    // renders it." This component IS that surface, so the pair cannot drift apart again.
    const fn = code.match(/function GuardedFailure[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn).toMatch(/<FieldError>\{guarded\.error\}<\/FieldError>/)
    expect(fn).toMatch(/<FixWithAiButton fixPrompt=\{guarded\.fixPrompt\} \/>/)
    expect(read('lib/useGuardedInstall.ts'), 'the doc this follows')
      .toMatch(/Rides alongside `error`/)
  })
})

describe('the allowlist shrank, which is the only direction it may move', () => {
  const allow = JSON.parse(read('design/inertUtilities.allowlist.json')) as {
    _comment: string
    allow: Record<string, string[]>
  }

  it('neither Apps file is listed any more', () => {
    expect(Object.keys(allow.allow), 'both entries are gone').not.toContain(APPS)
    expect(Object.keys(allow.allow)).not.toContain(PANEL)
  })

  it('no entry anywhere still names a negative/positive utility', () => {
    for (const [file, utils] of Object.entries(allow.allow)) {
      for (const u of utils) {
        expect(u, `${file} still allows ${u}`).not.toMatch(/-(?:negative|positive)$/)
      }
    }
  })

  it('🪤 the four unrelated entries are UNTOUCHED — they are a different change', () => {
    // Deliberately not swept in. `bg-surface-2` and `bg-surface-container-high` need a per-site
    // choice from the surface ramp (the allowlist's own note offers three candidates and picks
    // none), which is a design decision, not a rename. Leaving them keeps this diff arguable.
    for (const f of [
      'pages/loops/LoopCockpitPage.tsx', 'pages/settings/ChatPanel.tsx',
      'pages/settings/DurabilityPanel.tsx', 'pages/settings/OllamaModelManager.tsx',
    ]) expect(Object.keys(allow.allow), `${f} is still listed`).toContain(f)
  })

  it('the rule that makes this the right direction is still written down', () => {
    expect(allow._comment).toMatch(/may only SHRINK/)
    expect(allow._comment, 'and the intent it recorded for these two').toMatch(/text-negative -> text-danger/)
  })
})
