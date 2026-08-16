/** The one definition of an ACCENT-CARRYING chip — a filter that is on, a mode that is selected,
 *  a count that is nonzero, a tag that names the active project.
 *
 *  🪤 IT USED TO PUT THE ACCENT IN BOTH THE INK AND THE BACKGROUND, and that fails WCAG AA in light
 *  mode at every tint level anyone reached for. Measured (arithmetic validated by reproducing the
 *  3.33:1 that `ux-audit` and axe independently report for the 20% case):
 *
 *      --color-primary as ink over --color-primary at …    14% → 3.62   16% → 3.52
 *                                                          18% → 3.42   20% → 3.33
 *
 *  All below the 4.5 floor. Dark mode was never affected (5.17–5.74): there the tint darkens the
 *  backdrop AWAY from the light accent, while in light mode it lifts the backdrop TOWARD the dark
 *  accent until ink and background converge. **A tint is not symmetric across modes.**
 *
 *  The design system already ships the pair for an accent-tinted surface —
 *  `--color-primary-container` with `--color-on-primary-container` — measuring **13.1:1 in light and
 *  10.43:1 in dark**, and guaranteed for all 12 schemes in both modes by `schemeContrast.test.ts`.
 *  This is the treatment that shipped for the knowledge filter chip and it is now the shared one.
 *
 *  It also settles an inconsistency the sweep exposed: the same "this is active" idea was drawn with
 *  **four different tint strengths** (14 / 16 / 18 / 20%) depending on the file. A container has no
 *  strength to pick, so there is nothing left to drift.
 *
 *  Semantic tones are deliberately NOT routed through here. `info` / `ok` / `warn` / `danger` at
 *  14–16% measure 4.54–4.71 and pass; only ≥18% dips under (4.39–4.43), and they have no
 *  `<tone>-container` sibling to pair with, so they need their own answer rather than the coral one.
 */
export const accentChip = {
  background: 'var(--color-primary-container)',
  color: 'var(--color-on-primary-container)',
} as const

/** The neutral counterpart, for the OFF state of a chip that toggles. Spelled out here so the two
 *  halves of one control live together instead of the off-state being re-typed per call site. */
export const mutedChip = {
  background: 'var(--color-surface-high)',
  color: 'var(--color-on-surface-var)',
} as const

/** A chip whose colour comes from a META REGISTRY rather than being written at the call site —
 *  `{ background: color-mix(<tone> N%), color: <tone> }`, the THIRD spelling of the accent-chip
 *  defect (`accentChipTone.test.tsx`).
 *
 *  Neither of `accentChip.test.ts`'s sweeps can see that spelling: one matches a literal
 *  `var(--color-primary)` in a style object, the other the `bg-primary/N text-primary` class pair, and
 *  an interpolated tone puts no accent token in the source at all. The fix is per-registry, because
 *  whether `<tone>` reaches coral depends on the registry behind it — so this is the one rule those
 *  sites share:
 *
 *    tone is `--color-primary`  → the opaque container pair, 13.1:1 light / 10.43:1 dark
 *    anything else              → keep the tint, at the caller's own strength
 *
 *  Only coral needs it. Measured as ink over a tint of ITSELF, every other tone clears AA
 *  (`on-surface-low` 7.46 · `on-surface-var` 4.99 · `info` 5.13 · `ok`/`warn`/`danger` 4.54-4.71 at
 *  14-16%), and none has a `<tone>-container` sibling to pair with — routing them through the coral
 *  container would be a redesign, which is the same call cycle 146 made when it left 47 semantic
 *  sites alone.
 *
 *  `strength` stays a parameter rather than being unified: the adopters ship 14% and 16% and those
 *  percentages only apply to tones that already pass, so collapsing them would repaint passing chips
 *  for no accessibility reason. The coral branch has no strength to drift, which is the half cycle
 *  146 cared about.
 *
 *  🪤 NOT for a tinted tile behind an ICON (`toneChipBg`'s other two consumers) — non-text carries a
 *  3:1 floor it already clears at every strength, so moving those would repaint five surfaces for
 *  nothing. This is for a chip with a LABEL. */
export function toneChipSkin(tone: string, strength = 14): { background: string; color: string } {
  if (tone === 'var(--color-primary)') return { ...accentChip }
  return { background: `color-mix(in srgb, ${tone} ${strength}%, transparent)`, color: tone }
}
