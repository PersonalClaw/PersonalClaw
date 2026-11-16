// ── The kinds an intent checks ────────────────────────────────────────────────────────────────────

/** The kinds an intent can be limited to, as the form names them, with the item types each holds. */
export const INTENT_KINDS: readonly { key: string; label: string; types: readonly string[] }[] = [
  { key: 'notes', label: 'Notes', types: ['note', 'fleeting', 'journal', 'gist'] },
  { key: 'documents', label: 'Documents', types: ['pdf', 'document', 'sheet', 'slides'] },
  { key: 'links', label: 'Links', types: ['bookmark'] },
  { key: 'images', label: 'Images', types: ['image'] },
  { key: 'media', label: 'Audio and video', types: ['audio', 'video'] },
]

/** The form's kinds for an intent's `enabled_for`: every kind one of its types names. */
export function kindsOf(enabledFor: readonly string[]): string[] {
  return INTENT_KINDS.filter((k) => k.types.some((t) => enabledFor.includes(t))).map((k) => k.key)
}

/** The item types the chosen kinds hold. */
export function typesOf(kinds: readonly string[]): string[] {
  return INTENT_KINDS.filter((k) => kinds.includes(k.key)).flatMap((k) => k.types)
}
