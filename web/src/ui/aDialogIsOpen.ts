/** Whether a dialog is open over the page (`[aria-modal=true]`, a `Modal` or another trapped
 *  surface). A dialog owns Escape and the page's own keys while it is open, so a panel docked beside
 *  the chat and a page shortcut yield to it, as the turn's Stop key does (`TurnStatusRow`). One
 *  place for the question, so a docked panel never has to name a dialog role it does not have. */
export function aDialogIsOpen(): boolean {
  return document.querySelector('[aria-modal=true]') !== null
}
