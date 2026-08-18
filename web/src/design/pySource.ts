/** Reading the Python backend from a frontend rail, without a character window.
 *
 *  🔑 WHY THESE LIVE HERE. Several rails now assert that a piece of UI copy is TRUE by checking the
 *  handler that implements it (`blastRadiusIsVerified`, `promisedMechanismsExist`,
 *  `routingTelemetryPromise`). Reading the source is the easy part; bounding the REGION is where those
 *  rails kept going wrong.
 *
 *  🪤 `.slice(0, N)` IS NOT A SCOPE, and it fails in both directions: too small and the assertion misses
 *  a line that sits further down than you guessed (a call 235 lines into a method, past a 12 000-char
 *  slice — that one failed on correct code); too large and it spills into the next function, so deleting
 *  the thing being asserted still finds a match nearby. Bound the region by what ENDS it. */

/** One Python method/function body: from its `def` line to the next `def` at the SAME indentation.
 *
 *  Pass the header with its real indentation — `'    def build_daily_digest'` for a method,
 *  `'def enabled()'` for a module-level function — because the indentation is what defines the end. */
export function pyMethod(src: string, header: string): string {
  const start = src.indexOf(header)
  if (start < 0) return ''
  const indent = (header.match(/^\s*/) ?? [''])[0]
  const rest = src.slice(start + header.length)
  const next = rest.search(new RegExp(`\\n${indent}(async )?def `))
  return next < 0 ? rest : rest.slice(0, next)
}

/** The region between two anchors, for a branch or block that no `def` bounds.
 *
 *  Returns '' when either anchor is missing, so a caller's first assertion (that the region contains
 *  something known) fails loudly instead of a later one passing against an empty string. */
export function pyBetween(src: string, from: string, to: string): string {
  const a = src.indexOf(from)
  if (a < 0) return ''
  const b = src.indexOf(to, a + from.length)
  return b < 0 ? '' : src.slice(a, b)
}
