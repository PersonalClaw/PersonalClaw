/** A generated image in the artifact library: `/api/artifacts/<slug>/raw`, optionally with a
 *  query (`?version=N`) — the one path on this gateway that rendered text and previewed markup may
 *  load as an image. */
const ARTIFACT_IMAGE = /^\/api\/artifacts\/[^/?#\s\\]+\/raw(?:\?[^#\s]*)?$/

const HERE = 'gateway.invalid'

/** *v* as the URL parser reads it before anything else: tab, LF and CR taken out wherever they are,
 *  and C0 controls and spaces stripped at its ends. Nothing else is stripped and nothing is
 *  lower-cased, so a no-break space before a `#` or a `/` stays, as it does for the parser, and
 *  the address is then a path of its own. */
export function asTheUrlParserReadsIt(v: string): string {
  return v.replace(/[\t\n\r]/g, '').replace(/^[\u0000-\u0020]+|[\u0000-\u0020]+$/g, '')
}

/** Whether *src* is an artifact library image, read as the browser reads it: the parsed path is
 *  exactly `api`, `artifacts`, one slug and `raw`, with no backslash, so a slug that is a dot
 *  segment once decoded (`%2e%2e`) cannot climb out to another path of the API. */
export function isArtifactImage(src: string): boolean {
  const s = asTheUrlParserReadsIt(src)
  if (!ARTIFACT_IMAGE.test(s)) return false
  let url: URL
  try { url = new URL(s, `https://${HERE}/`) } catch { return false }
  const parts = url.pathname.split('/')
  if (url.host !== HERE || parts.length !== 5 || parts[0] !== '' || parts[1] !== 'api' || parts[2] !== 'artifacts' || parts[4] !== 'raw') return false
  let slug: string
  try { slug = decodeURIComponent(parts[3]) } catch { return false }
  return slug !== '' && slug !== '.' && slug !== '..' && !/[/\\]/.test(slug)
}
