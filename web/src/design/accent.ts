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
