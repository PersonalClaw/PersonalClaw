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

/** Save what the gateway serves at *url* (an export route) as a file, and stay on the page.
 *
 *  A link click the browser handles itself, so a long transcript or an archive of several
 *  megabytes never has to be held in memory here, and the route's own `Content-Disposition`
 *  names the file. The `download` attribute is what keeps the app on screen: pointing the tab
 *  at the URL (`window.location.href = …`) replaced the whole app with whatever came back
 *  whenever the response was not an attachment, until the user pressed Back. */
export function downloadFrom(url: string): void {
  const a = document.createElement('a')
  a.href = url
  a.download = ''
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** Slugify a title into a safe file basename (keeps unicode letters/digits). */
export function safeFilename(name: string, fallback = 'download'): string {
  const base = (name || '').trim().replace(/[\s/\\:*?"<>|]+/g, '-').replace(/^-+|-+$/g, '')
  return base || fallback
}
