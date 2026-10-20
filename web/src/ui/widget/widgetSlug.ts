/** Deterministic slug derivation for inline chat widgets.
 *
 *  A widget with no explicit `<widget slug=...>` still needs a STABLE slug so
 *  saving it once, then refreshing, reconciles to the same artifact instead of
 *  creating a duplicate. Derived from (messageTs, widgetIndex) — both stable
 *  across refresh — via two independent FNV-1a passes for a 16-hex handle.
 *
 *  NOTE: Math.imul truncates to 32 bits, so this is two 32-bit FNV-1a hashes
 *  concatenated, not a true 64-bit hash. Intentional + must be preserved —
 *  changing it would shift every derived slug and orphan existing artifacts. */
function fnv1a(input: string, seed: number): string {
  let h = seed >>> 0
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0 // 32-bit FNV prime
  }
  return h.toString(16).padStart(8, '0')
}

export function deriveWidgetSlug(messageTs: string | undefined, widgetIndex: number): string {
  const key = `${messageTs || 'nots'}:${widgetIndex}`
  return 'w-' + fnv1a(key, 0x811c9dc5) + fnv1a(key, 0x7ee3623b)
}

/** Explicit slug from `<widget slug=...>` wins; else derive a stable one. */
export function effectiveWidgetSlug(opts: { explicitSlug?: string; messageTs?: string; widgetIndex: number }): string {
  const explicit = (opts.explicitSlug || '').trim()
  if (explicit) return explicit
  return deriveWidgetSlug(opts.messageTs, opts.widgetIndex)
}
