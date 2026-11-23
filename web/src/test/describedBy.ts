/** What a control's description says: the text of the elements its `aria-describedby` names, as a
 *  screen reader reads it after the control's name. A `Button` or `QuietButton` that is unavailable
 *  says its `disabledReason` there, through the themed Tooltip's hidden copy, and in no native
 *  title. */
export function describedBy(el: Element): string {
  const ids = (el.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
  return ids.map((id) => el.ownerDocument.getElementById(id)?.textContent ?? '').join(' ').trim()
}

/** The `ignore` a text query takes to pass over a tooltip's hidden copy (`ui/Tooltip`), which says
 *  again, for a screen reader, what a control's tooltip shows: a reason shown on the page too is
 *  then found once, where it is shown. */
export const TIP_COPY = 'script, style, [role=tooltip]'
