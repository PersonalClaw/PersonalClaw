// What the composer's Enter key means, as a pure function — no React, no CodeMirror — for the same
// reason `sendButtonState` is one: the decision becomes unit-testable without mounting the
// CodeMirror-heavy editor, and the keymap stays a thin adapter over it.
//
// This particular decision had no test at all, and that is how `send_on_enter` came to be a
// preference persisted through the config, exposed by two separate controls, and read by nothing:
// the branch it belonged in was reachable only through a CodeMirror keymap nobody could drive.

export type EnterAction =
  | 'menu'    // a typeahead menu owns Enter — decline, so its own handler selects the highlighted row
  | 'newline' // insert a line break; sending is the composer's button
  | 'send'
  | 'none'    // nothing to send yet — decline and let CodeMirror do its default

export interface EnterInputs {
  /** the @-mention or "/"-command menu is open */
  menuOpen: boolean
  /** phone-sized viewport: a return key must not fire off a half-typed message */
  mobile?: boolean
  /** the reader's "Send on Enter" preference. Only an explicit `false` changes the key — an
   *  unresolved or failed config read must not take Enter-to-send away from someone who never
   *  turned it off, which is also the config default (`send_on_enter=True`). */
  sendOnEnter?: boolean
  /** the draft meets the composer's minChars */
  canSend: boolean
}

/** Resolve what Enter does. Order matters: an open typeahead menu outranks everything (it owns the
 *  key while it is up), then the two cases that make Enter a newline and leave sending to the
 *  button — a phone keyboard, and the preference turned off — and only then send. */
export function enterKeyAction(s: EnterInputs): EnterAction {
  if (s.menuOpen) return 'menu'
  if (s.mobile || s.sendOnEnter === false) return 'newline'
  return s.canSend ? 'send' : 'none'
}
