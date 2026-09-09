/** Read the remediations out of a refused-start error envelope.
 *
 *  `POST /api/workflows/runs` refuses a start that cannot succeed and answers
 *  `error.detail.preflight.findings[]`, each finding carrying `message` (what is wrong) and
 *  `remediation` (what to do). `workflows/preflight.py`'s `Finding` states why those are two
 *  fields: "collapsing them leaves the user with a diagnosis and no next step".
 *
 *  The envelope's `message` is the diagnosis, already joined and already shown. This returns the
 *  other half, so a caller can show both.
 *
 *  🔑 SEVERITY IS NOT FILTERED HERE. A warning-severity finding did not block this start, but its
 *  remediation is still the true answer to "what do I do about it", and a start refused for one
 *  reason routinely lists others worth fixing in the same visit.
 *
 *  Every field is checked rather than trusted: this parses a wire body, and one route answering a
 *  shape this does not expect must yield an empty list, never a crash on the error path — the
 *  worst possible place to throw is while reporting a failure.
 */
export function preflightRemediations(detail: unknown): string[] {
  if (!detail || typeof detail !== 'object') return []
  const preflight = (detail as Record<string, unknown>).preflight
  if (!preflight || typeof preflight !== 'object') return []
  const findings = (preflight as Record<string, unknown>).findings
  if (!Array.isArray(findings)) return []
  const out: string[] = []
  const seen = new Set<string>()
  for (const f of findings) {
    if (!f || typeof f !== 'object') continue
    const r = (f as Record<string, unknown>).remediation
    if (typeof r !== 'string' || !r.trim()) continue
    // 🔑 ONE REMEDIATION PER `kind`, NOT PER FINDING. Measured on the real refusal: three unset
    // model use cases produce three findings whose remediation differs only in the use-case name
    // and whose destination is identical, so appending all three read "…in Settings → Models, or
    // change the node's model_tier" three times. This message goes into a `role="alert"` region
    // and is spoken, which is the bar `errText` raised for exactly this reason — a wall is worse
    // aloud than on screen. The diagnosis half already enumerates every unmet requirement by
    // name, so the instruction only has to say WHERE once per category.
    //
    // Keyed on the wire's own `kind` field, never on the prose: grouping by "these sentences look
    // similar" is the heuristic that eventually collapses two genuinely different instructions.
    // A finding with no `kind` falls back to its own text, which keeps exact-duplicate collapsing
    // for the older checks that do not set one and never merges two unlike sentences.
    const kind = (f as Record<string, unknown>).kind
    const key = typeof kind === 'string' && kind.trim() ? `kind:${kind.trim()}` : `text:${r.trim()}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r.trim())
  }
  return out
}
