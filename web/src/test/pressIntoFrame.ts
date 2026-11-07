/** What a click or tap in a widget's frame does to the page around it, as jsdom cannot: the pointer
 *  moves over the page and arrives over the iframe, focus moves into it, and the page's window
 *  loses focus to it. A chat fills its composer from a frame's action only when the owner gave the
 *  frame its focus (`useWidgetWire`'s `earned`), which is what a real press in it gives. */
export function pressIntoFrame(frame: HTMLIFrameElement | null | undefined): void {
  if (!frame) return
  document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
  frame.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
  frame.focus()
  window.dispatchEvent(new Event('blur'))
}

/** A press in *to* straight from a press in *from*: the pointer leaves one frame for the other and
 *  focus follows it. The page's window had already lost focus to *from*, so it sees no blur or
 *  focus of its own for the move. */
export function pressFromFrame(from: HTMLIFrameElement | null | undefined, to: HTMLIFrameElement | null | undefined): void {
  if (!from || !to) return
  from.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
  document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
  to.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
  to.focus()
}

/** An assistive technology's activation of a control in *frame* (a screen reader's or voice
 *  control's): focus moves into the frame with no Tab and no pointer, and the page's window loses
 *  focus to it. */
export function activateInFrame(frame: HTMLIFrameElement | null | undefined): void {
  if (!frame) return
  frame.focus()
  window.dispatchEvent(new Event('blur'))
}
