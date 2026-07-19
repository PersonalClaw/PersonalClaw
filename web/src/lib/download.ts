/** Trigger a browser download of in-memory text content as a named file.
 *  Creates a transient object URL, clicks a synthetic anchor, then revokes it. */
export function downloadText(filename: string, content: string, mime = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke after the click has been handled (next tick).
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** Slugify a title into a safe file basename (keeps unicode letters/digits). */
export function safeFilename(name: string, fallback = 'download'): string {
  const base = (name || '').trim().replace(/[\s/\\:*?"<>|]+/g, '-').replace(/^-+|-+$/g, '')
  return base || fallback
}
