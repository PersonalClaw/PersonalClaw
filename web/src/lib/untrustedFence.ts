/** The text a person reads from content the gateway fenced for a model.
 *
 *  Text from outside (a fetched page, a search hit, a message) reaches the model inside
 *  `<untrusted_content …>` markers, which tell the model the span is data and never an
 *  instruction (`security.fence_untrusted`). The markers are for the model. On screen they are
 *  noise around the text they wrap, so a display takes them off and keeps everything between
 *  them, rendered as text. The model's copy is never touched: this runs only where text is shown.
 *
 *  Only a real marker comes off. The fence escapes any marker found inside the text it wraps
 *  (`&lt;untrusted_content&gt;`), so the wrapped text is shown exactly as it arrived. A result
 *  handed to the model as JSON carries the markers inside its strings, where the line break that
 *  follows the open marker and precedes the close one is written `\n`; both spellings are taken
 *  off with the marker, so the JSON stays valid and its other fields are untouched.
 */
const OPEN_MARKER = /<untrusted_content(?:\s[^<>]*)?>(?:\\n|\r?\n)?/gi
const CLOSE_MARKER = /(?:\\n|\r?\n)?<\/untrusted_content\s*>/gi

export function withoutFence(text: string): string {
  if (!text || !/<\/?untrusted_content/i.test(text)) return text
  return text.replace(OPEN_MARKER, '').replace(CLOSE_MARKER, '')
}
