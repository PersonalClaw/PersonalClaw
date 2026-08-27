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
 *  The wrapper keeps `onClick`, because every nested control already stops its own
 *  propagation; this button's own click bubbles up to it.
 *
 *  Usage — the wrapper needs `relative` and, for the focus ring, the `has-` variants:
 *
 *      <motion.div
 *        tabIndex={-1}                      // Motion's whileTap/whileHover sets 0 otherwise,
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
export function RowHitTarget({ label }: {
  /** What the row IS — normally the entity's title. Without it the row's accessible name
   *  is computed from its whole subtree; measured up to 2001 characters for one inbox row. */
  label: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      // `-z-10` keeps it UNDER the row content, so the row's own controls keep their own
      // clicks with no z-index needed on any child. Empty space still activates the row.
      className="absolute inset-0 -z-10 cursor-pointer outline-none"
    />
  )
}
