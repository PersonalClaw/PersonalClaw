import { cx } from './cx'

/** The name of the current destination, as its `<h1>`.
 *
 *  Every page had a visible title and **no heading**. Measured across 20 nav destinations
 *  before this existed: **17 had no `h1` at all, and 13 rendered zero headings of any level.**
 *  A screen-reader user navigating by heading — the H key in NVDA/JAWS, the rotor in VoiceOver,
 *  which is how people skim an unfamiliar page — landed on nothing and could not orient.
 *
 *  The title was there the whole time. It was a `<span data-type="title-l">`, hand-rolled in the
 *  `TopBar` left slot at ~30 sites, so the information existed and only its semantics were
 *  missing. This is that span, with the tag it should always have had.
 *
 *  **Purely semantic — it renders identically.** `data-type="title-l"` carries size, line-height
 *  and weight, and Tailwind's preflight resets heading margins, so an `h1` and a `span` compute
 *  the same box. Measured side by side in the running app with the same `data-type` and class:
 *  font-size 20px, weight 400, line-height 24px, `fvs "wght" 470`, margin 0 and **width 51.02px**
 *  on both — the only difference was `display` (inline vs block), which a flex row blockifies
 *  anyway. Verified as a 0.00% pixel diff across the tier-1 surfaces.
 *
 *  Use it for the destination's own name. NOT for a section heading inside a page (that is an
 *  `h2`), and NOT for a docked panel's header — a side panel is not the page, and giving it an
 *  `h1` would claim the document's title. `ChatPage`'s "Chat history" panel is the worked
 *  example of that exclusion.
 */
export function PageTitle({ children, className }: {
  /** The destination's name. May include trailing chrome the title owns — a count badge, a
   *  pending-items summary — since those read as part of the heading. */
  children: React.ReactNode
  /** Extra classes for the heading itself, e.g. the flex row a title with a badge needs.
   *  Tokens only — no raw hex or px. */
  className?: string
}) {
  return (
    <h1 data-type="title-l" className={cx('text-on-surface', className)}>
      {children}
    </h1>
  )
}
