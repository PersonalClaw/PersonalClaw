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

/** Every VALUE of a `str, Enum` class — the vocabulary a wire field can carry.
 *
 *  🔑 DERIVED, so a rail cannot go stale against the backend. The alternative is a hand-copied list
 *  in the test, which is what let `TriggerHealth`'s members outgrow the renderer that draws them
 *  (issue 496): a mirror only catches drift if something proves it is still a mirror. Bounded by the
 *  next `class` at column 0, for the reason this module exists.
 *
 *  Returns [] for an unknown class, so a caller's own "the vocabulary is non-empty" assertion is what
 *  fails when a class is renamed — not a later assertion passing vacuously over nothing. */
export function pyEnumMembers(src: string, cls: string): string[] {
  const body = pyBetween(`${src}\nclass __EOF__`, `class ${cls}`, '\nclass ')
  return [...body.matchAll(/^\s+[A-Z_0-9]+ = "([a-z_]+)"/gm)].map((m) => m[1])
}

/** Every KEY of a module-level `dict[str, str]` literal — e.g. a status→outcome mapping table. */
export function pyDictKeys(src: string, name: string): string[] {
  const body = pyBetween(src, `${name}: dict[str, str] = {`, '\n}')
  return [...body.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1])
}

/** Every `"key": SomeEnum.MEMBER.value` pair of such a dict, as key → the enum member's VALUE.
 *
 *  Resolves the member name against `enumSrc` so the result speaks wire values, which is what a
 *  frontend renderer is handed. A key whose value is not a resolvable enum member is dropped, and the
 *  caller's key-coverage assertion is what reports it. */
export function pyDictToEnumValues(
  src: string,
  name: string,
  enumSrc: string,
  enumCls: string,
): Record<string, string> {
  const body = pyBetween(src, `${name}: dict[str, str] = {`, '\n}')
  const members = new Map(
    [...pyBetween(`${enumSrc}\nclass __EOF__`, `class ${enumCls}`, '\nclass ').matchAll(
      /^\s+([A-Z_0-9]+) = "([a-z_]+)"/gm,
    )].map((m) => [m[1], m[2]]),
  )
  const out: Record<string, string> = {}
  for (const m of body.matchAll(/^\s+"([a-z_]+)":\s*(\w+)\.([A-Z_0-9]+)\.value/gm)) {
    const value = members.get(m[3])
    if (value) out[m[1]] = value
  }
  return out
}
