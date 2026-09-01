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
 *  (some endpoints answer in text and it is useful) — but never on a 5xx, where a plain-text body
 *  is the framework's crash page rather than anyone's message — and otherwise the status. Never
 *  markup, never a wall, never a stack the server printed to itself.
 *
 *  🔑 THE ENVELOPE CARRIES TWO THINGS AND THIS USED TO KEEP ONE. `{"error": {"code", "message"}}`
 *  is parsed here already; only the sentence survived, and the `code` — the half that exists
 *  precisely so a client can BRANCH — was discarded at the funnel. Four learning panels then
 *  matched their code against the human sentence (`message.includes('evals_disabled')`), which
 *  the message never contains, so six deliberate branches shipped unreachable. `errEnvelope`
 *  returns both; `errText` is it minus the code, byte for byte, so nothing a user reads moves.
 */

/** Beyond this, a body is a document rather than a message. A `FieldError` is one line. */
const MAX_INLINE = 200

/** A failed response, split into the part a human reads and the part code branches on. */
export interface ErrEnvelope {
  /** What the user is told. Exactly what `errText` returns. Never markup, never a wall. */
  message: string
  /** The backend's stable `error.code` (`http_errors.py`'s registry), or `''` when the body
   *  carried none. Lifted even when the `message` was unusable and the status was substituted:
   *  a code-only envelope is still a legible fact for a caller, just not a sentence for a user. */
  code: string
}

/** Read a failed `Response` ONCE and return both halves of its envelope. The body is a stream,
 *  so this cannot be composed from two passes — every caller that wants the code takes this,
 *  and everyone who only wants the sentence takes `errText` below. */
export async function errEnvelope(r: Response): Promise<ErrEnvelope> {
  const text = (await r.text().catch(() => '')).trim()
  // 🔴 A 5xx body is a report of a crash, not a sentence written for a user. The markup and
  // length guards below caught the proxy pages but not the framework's OWN, because aiohttp's
  // default is short plain text. Measured against a real unhandled handler exception:
  // `text/plain; charset=utf-8`, 55 bytes, `'500 Internal Server Error\n\nServer got itself in
  // trouble'` — not JSON, no leading `<`, well under `MAX_INLINE`. So it satisfied every guard
  // here and a user saving a form read "Server got itself in trouble" in the red line under
  // their field, announced aloud by `role="alert"` (issue 637).
  //
  // `HTTP 500` is the better answer despite being terser: it is at least true, and the status is
  // the only actionable part of an unshaped crash. A 5xx message worth reading is one the backend
  // SHAPED, and those come through the JSON branch below untouched.
  //
  // Scoped to 5xx deliberately. The plain-text passthrough exists for client errors that answer
  // in text (the chunked uploader's "upload part 3 rejected"), which are a handler deliberately
  // saying what the caller did wrong.
  const isServerErr = r.status >= 500
  let wasJson = false
  let code = ''
  let message = ''
  try {
    const parsed = JSON.parse(text)
    wasJson = true
    if (parsed && typeof parsed === 'object') {
      // `error` is what this gateway sends (239 sites); `detail` is the Starlette/FastAPI
      // default that a handful of routes still return. Both are the backend talking to a user.
      for (const key of ['error', 'detail'] as const) {
        const v = (parsed as Record<string, unknown>)[key]
        if (typeof v === 'string' && v.trim()) { message = v.trim(); break }
        // 🔴 THE ENVELOPE THE PLATFORM DECLARES WAS THE ONE SHAPE THIS DROPPED. `errors.py` states the
        // wire contract — "`AGENTS.md` §"Shared conventions" owns the *wire* shape for API-route errors —
        // `{"error": {"code": "<lowercase_snake>", "message": ...}}`" — and 115 sites return it. Because
        // the object is not a string, every one of them fell through to `HTTP <status>`: a user saving a
        // malformed workflow got "HTTP 400" while the backend had written "invalid JSON body", and a
        // failed extraction read "HTTP 500" instead of "could not read the document".
        //
        // Only a non-empty string `message` is taken. A code-only object still becomes the status, which
        // is the existing rule and the right one — `{"error": {"code": 7}}` read aloud is worse than
        // "HTTP 502" — so this widens the funnel by exactly the sentence a human wrote, and nothing else.
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          const c = (v as Record<string, unknown>).code
          if (!code && typeof c === 'string' && c.trim()) code = c.trim()
          const msg = (v as Record<string, unknown>).message
          if (typeof msg === 'string' && msg.trim()) { message = msg.trim(); break }
        }
      }
    }
  } catch { /* not JSON — the plain-text rules below decide */ }
  // A body that PARSED as JSON but carried no usable message must not be printed: serialized
  // JSON is never a sentence, and `{"error": {"code": 7}}` read aloud is worse than the status.
  // Only genuinely non-JSON text may pass through, and only when it is short, not markup, and
  // not a server error page (see the note at the top of this function).
  if (!message) {
    message = wasJson || !text || text.startsWith('<') || text.length > MAX_INLINE || isServerErr
      ? `HTTP ${r.status}`
      : text
  }
  return { message, code }
}

export async function errText(r: Response): Promise<string> {
  return (await errEnvelope(r)).message
}
