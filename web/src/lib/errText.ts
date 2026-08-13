/** Turn a failed `Response` into the sentence a user should read.
 *
 *  Every API failure message in the app funnels through here — `api.ts`'s helpers, its
 *  hand-rolled fetches (streams, file reads) and the chunked uploader — so this is the one
 *  place that decides what a user is told when something fails. Since the failure line now
 *  carries `role="alert"` (`ui/forms.tsx`'s `FieldError`), whatever this returns is also read
 *  ALOUD, which raises the bar on what may pass through.
 *
 *  Measured against the three body shapes a real deployment produces, driving a save failure on
 *  `#/settings/design`:
 *
 *    {"error": "name is required"}   → "name is required"          ✅ the gateway's own shape
 *    an nginx 502 HTML page          → 158 chars of raw markup     ❌ announced verbatim
 *    an empty body                   → "HTTP 502"                  ✅ terse but honest
 *
 *  The middle row is the defect this closes: PersonalClaw is reachable through tunnels and
 *  reverse proxies, and a front door that is up while the gateway is down answers with an HTML
 *  error page. That page is not a message for a user, and it certainly is not one to speak.
 *
 *  So: the backend's own text when there is one, otherwise a short plain-text body verbatim
 *  (some endpoints answer in text and it is useful), and otherwise the status — never markup,
 *  never a wall.
 */

/** Beyond this, a body is a document rather than a message. A `FieldError` is one line. */
const MAX_INLINE = 200

export async function errText(r: Response): Promise<string> {
  const text = (await r.text().catch(() => '')).trim()
  let wasJson = false
  try {
    const parsed = JSON.parse(text)
    wasJson = true
    if (parsed && typeof parsed === 'object') {
      // `error` is what this gateway sends (239 sites); `detail` is the Starlette/FastAPI
      // default that a handful of routes still return. Both are the backend talking to a user.
      for (const key of ['error', 'detail'] as const) {
        const v = (parsed as Record<string, unknown>)[key]
        if (typeof v === 'string' && v.trim()) return v.trim()
      }
    }
  } catch { /* not JSON — the plain-text rules below decide */ }
  // A body that PARSED as JSON but carried no usable message must not be printed: serialized
  // JSON is never a sentence, and `{"error": {"code": 7}}` read aloud is worse than the status.
  // Only genuinely non-JSON text may pass through, and only if it is short and not markup.
  // aiohttp default error pages are plain text like '500 Internal Server Error

Server got itself in trouble'
  const isAiohttpDefault = /^\d{3} [A-Z]/.test(text) && text.split('
').length <= 4
  if (wasJson || !text || text.startsWith('<') || text.length > MAX_INLINE || isAiohttpDefault) return `HTTP ${r.status}`
  return text
}
