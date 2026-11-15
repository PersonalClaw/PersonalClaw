/** The row-wide click target and accessible name for a card/row whose wrapper is NOT a
 *  button.
 *
 *  A clickable row that carries its own controls (a delete action, an overflow menu, a
 *  checkbox) cannot be a `role="button"` wrapper: an interactive element containing
 *  interactive descendants is `nested-interactive` (axe, serious) — assistive tech is told
 *  "one button" and then finds a menu inside it. Measured across this app: 72 nodes.
 *
 *  So the hit target is an EMPTY button stretched over the row, a SIBLING of the content
 *  rather than its ancestor. Owning no descendants is the whole point — the alternative
 *  (keep the wrapper interactive, re-expose children with `pointer-events-auto` selectors)
 *  has to enumerate every control type and silently misses the CONDITIONAL ones, e.g. a
 *  delete button that only exists in an `armed` state.
 *
 *  The wrapper keeps `onClick`, and this button's own click bubbles up to it. A nested control's
 *  click bubbles there too (a `Button` stops nothing), so a wrapper whose controls must keep their
 *  own clicks asks where a click began (`openable` in `pages/dashboard/RunningLane`): this button is
 *  marked `data-row-hit-target` so it can tell the two apart.
 *
 *  Usage — the wrapper needs `relative` and, for the focus ring, the `has-` variants:
 *
 *      <motion.div
 *        tabIndex={-1}                      // a framer gesture on the wrapper sets 0 otherwise,
 *        onClick={open}                     //   which would be a second, nameless tab stop
 *        className="group relative … has-[>button:focus-visible]:ring-2
 *                   has-[>button:focus-visible]:ring-inset
 *                   has-[>button:focus-visible]:ring-primary">
 *        <RowHitTarget label={item.name} />
 *        …content, including its own controls…
 *      </motion.div>
 *
 *  The ring is drawn on the WRAPPER, not here: this button sits at `-z-10`, so its own ring
 *  would paint behind the row's background.
 */
/** A current, selected or on state as forced colours draw it (`forced-colors: active`, the matrix's
 *  high-contrast axis): a 2 px outline in the system's `Highlight`, inside the box. Forced colours
 *  drop a fill and a border colour, so a state told by fill or weight alone vanishes there; the
 *  outline is kept. Default off: a control carries it only where it opts in (`TileButton`'s
 *  `current`, `Segmented`'s `markCurrent`). */
export const CURRENT_OUTLINE = 'forced-colors:outline-2 forced-colors:outline-solid forced-colors:outline-[Highlight] forced-colors:-outline-offset-2'

/** The row form of {@link CURRENT_OUTLINE}, for the WRAPPER of a row whose hit target is `selected`
 *  (its button carries `aria-current`): the wrapper draws the outline, since this button sits under
 *  the row's content. Pass it on the wrapper; it draws only while the row is the selected one. */
export const SELECTED_ROW_OUTLINE = [
  'forced-colors:has-[>button[aria-current=true]]:outline-2',
  'forced-colors:has-[>button[aria-current=true]]:outline-solid',
  'forced-colors:has-[>button[aria-current=true]]:outline-[Highlight]',
  'forced-colors:has-[>button[aria-current=true]]:-outline-offset-2',
].join(' ')

export function RowHitTarget({ label, selected = false }: {
  /** What the row IS — normally the entity's title. Without it the row's accessible name
   *  is computed from its whole subtree; measured up to 2001 characters for one inbox row. */
  label: string
  /** The row whose detail is open beside its list: `aria-current`, as `ListRow`'s `selected`.
   *  The wrapper draws what shows it. */
  selected?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-current={selected ? 'true' : undefined}
      data-row-hit-target=""
      // `-z-10` keeps it UNDER the row content, so the row's own controls keep their own
      // clicks with no z-index needed on any child. Empty space still activates the row.
      className="absolute inset-0 -z-10 cursor-pointer outline-none"
    />
  )
}
