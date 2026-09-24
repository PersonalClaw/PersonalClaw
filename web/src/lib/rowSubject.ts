/** The subject a row-scoped control names: enough of the row to tell it from its siblings, and no
 *  more than that.
 *
 *  🔑 ONE RULE, TWO FAILURE MODES, both measured in Chrome's computed accessibility tree during
 *  cycles 139-142. **Too little:** 83 notification rows sharing three names ("Delete", "Mark
 *  unread", "Investigate in chat"), and 30 task checkboxes all saying "Select task". **Too much:**
 *  five artifact tiles named by 438-695 characters of their own rendered markdown, and — from the
 *  first fix's own output — dashboard row actions at **107 characters** each, sixteen of them.
 *
 *  The cap was chosen by measurement rather than taste. On `#/notifications`, worst duplicate versus
 *  interactive names over 80 characters app-wide:
 *
 *    (no cap)  ×83 → ×3     50 → 219
 *    60        ×3           50 → 114
 *    **55**    ×3           50 → **45**
 *
 *  55 keeps every distinction an uncapped name buys while landing the finished name (verb + subject)
 *  around 76 characters.
 *
 *  🪤 THE FIRST PART IS NOT ALWAYS THE DISTINGUISHING ONE. Twice this session a name composed from
 *  the obvious title field left the ambiguity intact — inbox proposals whose `title` is the channel
 *  ("Reply: skills" ×8), notifications whose `title` is the kind ("…: Refine a skill" ×35). Pass the
 *  parts in identity order and let this join them; then RE-MEASURE.
 *
 *  🔑 WHAT THIS IS NOT FOR: a row whose own title is simply long. `#/knowledge` has 29 names over 80
 *  characters because its items are titled that way ("ZFS Resilver Time Calculator — third-party
 *  dRAID drag-factor grid (1.42-1.69x), and why it oversells…"), and the visible label truncates
 *  while the name does not — so a screen-reader user currently gets MORE than a sighted one.
 *  Capping data is not the same as bounding a name you assembled.
 *
 *  🪤 INTERNAL WHITESPACE IS COLLAPSED, not just trimmed. Measured on `#/dashboard` after the widget
 *  rows adopted this: names arrived as `"skills — Refine a skill\n\nloop-worker — When pr…"`, because
 *  an Action Center entry's `sub` carries the newlines of the message it came from. The visible row is
 *  ONE truncated line, so the name has to be one line too — and a `\n\n` inside the 55-character
 *  budget spends it on nothing. Same correction cycle 161 made for the inbox row, moved to where the
 *  rule lives so every consumer inherits it.
 */
export function rowSubject(parts: (string | null | undefined)[], cap = 55): string {
  const seen: string[] = []
  for (const raw of parts) {
    const part = (raw ?? '').replace(/\s+/g, ' ').trim()
    // Skip a part that repeats one already used — an inbox entry whose summary starts with its own
    // title would otherwise say it twice inside a 55-character budget.
    if (!part || seen.some((s) => s === part || s.startsWith(part) || part.startsWith(s))) continue
    seen.push(part)
  }
  const full = seen.join(' — ')
  return full.length > cap ? `${full.slice(0, cap - 1)}…` : full
}
