import { act, fireEvent } from '@testing-library/react'

/** What a control's themed tooltip (`ui/Tooltip`) says: the bubble a keyboard focus shows, read and
 *  closed again. A control that moved its native `title` into the tooltip is read through here. */
export function tipOf(el: HTMLElement): string {
  act(() => { fireEvent.focus(el) })
  const text = document.querySelector('[data-tooltip-bubble]')?.textContent ?? ''
  act(() => { fireEvent.blur(el) })
  return text
}

/** The control whose tooltip matches *tip*, among *controls*. */
export function withTip(controls: HTMLElement[], tip: RegExp | string): HTMLElement {
  const found = controls.find((el) => {
    const said = tipOf(el)
    return typeof tip === 'string' ? said === tip : tip.test(said)
  })
  if (!found) throw new Error(`no control's tooltip says ${String(tip)}`)
  return found
}
