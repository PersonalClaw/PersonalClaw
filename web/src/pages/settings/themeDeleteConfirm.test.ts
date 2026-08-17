import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Deleting a saved theme asks first, and says what it costs ───────────────────────────────────
//
// Driven on a real saved theme at 1440×1000: clicking the hover-revealed trash icon made the tile
// vanish with NO dialog, and `GET /api/themes` then returned `[]`. The handler does `target.unlink()`
// — the file is gone from disk, with no soft-delete and nothing to restore from.
//
// A theme is deliberately authored: a name, an emoji and a color for both modes. That is exactly the
// kind of small-but-hand-made object `confirmDelete` already guards fourteen times over — a chat
// starter, a memory, a lesson, a schedule, an artifact, a provider.
//
// 🔑 THE BODY IS CONDITIONAL, because overstating a loss teaches people to dismiss the dialog. Measured:
// deleting the theme you are USING reverts the app to its defaults (`--color-primary` went `#4fd1c5`
// back to `#ff6b5b`), which is worth a sentence; deleting one you are not using costs only the theme,
// so it says less. Same discipline as the intent-delete confirm, which counts the matches it will take.
//
// 🪤 WHAT THIS CYCLE ALSO FALSIFIED, recorded so it is not "found" again. The ux-605 census listed six
// ungated delete sites. Four of them (`deleteULoop` in the loops list, both cockpits and the SDLC card)
// are NOT ungated: they use a deliberate two-step arm-then-confirm, each with a comment saying why —
// "so a hover misclick can't destroy a finished loop's history". The census's regex looked for
// `confirmDelete(` / `await confirm(` within 14 lines and cannot see a state-based guard. Two shipped
// confirmation idioms, both defensible: an owner call, not a defect. The fifth, the annotation `X`, is
// small and high-frequency — closer to a dismissal, where a dialog would be friction. Only the theme
// was a real one-click loss.

const SRC = readFileSync(join(process.cwd(), 'src/pages/settings/DesignPanel.tsx'), 'utf8')
const CODE = SRC.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a saved theme is not deleted on one click', () => {
  it('the tile delete goes through the confirm, not straight to the API', () => {
    expect(CODE, 'the tile calls the guarded helper').toMatch(/onDelete=\{isCustom\(s\.id\) \? \(\) => removeScheme\(s\) : undefined\}/)
    expect(CODE, 'and nothing calls the delete unguarded any more')
      .not.toMatch(/onDelete=\{isCustom\(s\.id\) \? \(\) => deleteCustomScheme/)
  })

  it('the confirm precedes the delete, and a cancel stops it', () => {
    expect(CODE).toMatch(/const ok = await confirmDelete\('theme', s\.label/)
    expect(CODE, 'an unconfirmed delete returns early').toMatch(/if \(!ok\) return\s*\n\s*await deleteCustomScheme\(s\.id\)/)
  })

  it('it uses the app-wide helper rather than a bespoke dialog', () => {
    expect(SRC).toMatch(/import \{ confirmDelete \} from '\.\.\/\.\.\/ui\/dialog'/)
  })

  it('the body tells the truth about the in-use case, and only then', () => {
    // The measured consequence: deleting the ACTIVE theme reverts the app's colors. Saying that
    // unconditionally would overstate the loss for the other tiles.
    expect(CODE).toMatch(/const inUse = activeScheme === s\.id/)
    expect(CODE).toMatch(/inUse\s*\n?\s*\? 'You are using this theme, so the app goes back to its default colors/)
    expect(CODE, 'and the not-in-use branch says less').toMatch(/: 'It cannot be undone — a saved theme is a file, not a snapshot\.'/)
  })

  it('and spells it the American way, like the rest of the shipped copy', () => {
    // 🪤 The first draft said "colours" and `exclusiveChoiceNamed`'s spelling rail caught it. That rail
    // scans this whole file; this one pins the two strings this cycle added, so a future edit to them
    // fails here — next to the copy — rather than in a test about mode pills.
    // Anchored on the phrase BOTH bodies share. A wider net (`undone|colors`) caught six strings —
    // this file legitimately ships `colors` in token-group labels.
    const bodies = CODE.match(/'[^']*cannot be undone[^']*'/g) || []
    expect(bodies.length, 'both dialog bodies found').toBe(2)
    for (const b of bodies) expect(b, `${b} uses the repo's spelling`).not.toMatch(/colour/i)
  })

  it('only a CUSTOM tile offers deletion at all — the vacuity floor', () => {
    // A built-in scheme has no file to unlink; if that guard ever goes, the confirm would be offered
    // for something that cannot be deleted.
    expect(CODE).toMatch(/isCustom\(s\.id\) \? \(\) => removeScheme\(s\) : undefined/)
  })

  it('the control is still the same hover-revealed trash, not a new affordance', () => {
    // The fix is a confirmation, not a redesign: the tile keeps its icon button and its title.
    expect(CODE).toMatch(/title="Delete saved theme"/)
  })
})
