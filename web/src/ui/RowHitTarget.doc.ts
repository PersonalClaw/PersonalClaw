import type { UiDoc } from './uiDoc'

// Doc object for RowHitTarget — the row-wide click target for a card/row that carries its
// OWN controls, and therefore cannot be a role="button" wrapper.
const doc: UiDoc = {
  name: 'RowHitTarget',
  keywords: ['row', 'card', 'hit target', 'overlay', 'clickable', 'a11y', 'nested-interactive', 'tab stop'],
  description:
    'The row-wide click target and accessible name for a clickable row/card whose wrapper is NOT a button. A row that carries its own controls (a delete action, an overflow menu, a checkbox) cannot be a role="button" wrapper — an interactive element containing interactive descendants is `nested-interactive` (axe, serious): assistive tech is told "one button" and then finds a menu inside it. So the hit target is an EMPTY button stretched over the row, a SIBLING of the content rather than its ancestor. Owning no descendants is the point: the alternative (keep the wrapper interactive, re-expose children with pointer-events selectors) must enumerate every control type and silently misses the conditional ones.',
  props: [
    { name: 'label', description: "What the row IS — normally the entity's title. Without it the row's accessible name is computed from its whole subtree; measured up to 2001 characters for one inbox row." },
  ],
  bestPractices: [
    { guidance: true, description: 'Give the wrapper `relative` + `tabIndex={-1}` and keep `onClick` on it — Motion\'s whileTap/whileHover sets tabindex="0" otherwise, which would be a second, nameless tab stop per row.' },
    { guidance: true, description: 'Draw the focus ring on the WRAPPER via `has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary` — this button sits at -z-10, so its own ring would paint behind the row background.' },
    { guidance: false, description: "Do not add children — an empty overlay is what keeps the row's own controls out of an interactive ancestor. Nested controls keep working because they already stopPropagation and this button's click bubbles to the wrapper." },
  ],
  anatomy: ['absolutely-positioned inset-0 button at -z-10 (empty, aria-labelled, no outline of its own)'],
}

export default doc
