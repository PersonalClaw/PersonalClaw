// ── Push policy (MOBILE-COMPANION MC-5) ──────────────────────────────────────
//
// Pure, unit-tested decisions for the service worker's `push` and
// `notificationclick` handlers. `sw.ts` stays thin plumbing, exactly as it does
// for `swPolicy.ts` — for the same reason: the rule that matters here is a
// SECURITY rule, and a security rule buried in an event handler that only a
// real browser can run is a rule nobody can test.
//
// THE RULE: a push payload is `{kind, item_id}` and the notification the user
// sees is composed from `kind` ALONE, out of the fixed table below. Nothing the
// backend sends is ever rendered. That is not belt-and-braces — it is the other
// half of the guarantee. The backend refuses to put content IN a payload; this
// file refuses to render anything OUT of one. Either half alone could be
// defeated by a future edit to the other side.

/** The ids-only payload a push carries. There is no third field, by contract. */
export interface PushPayload {
  kind: string
  item_id: string
}

/** Cue voices a per-kind rule may name (MOBILE-COMPANION MC-6). Mirrors `CueName` in
 *  `web/src/design/soundCues.ts` — DUPLICATED here, not imported, because this module is compiled
 *  into the service-worker program too (`tsconfig.sw.json`, `lib: WebWorker` with no `dom`), and
 *  `soundCues.ts` references `AudioContext`/`window`/`document`. `pushPolicy.test.ts` pins this
 *  list to soundCues' `CUES` keys so the two cannot drift. */
export type PushCue = 'turn_complete' | 'approval_needed' | 'error' | 'coin_blip' | 'terminal_bell'
export const PUSH_CUES: readonly PushCue[] = [
  'turn_complete',
  'approval_needed',
  'error',
  'coin_blip',
  'terminal_bell',
]

/** The message a push handler posts to an open client so it can VOICE a cue (a service worker
 *  cannot play audio). Shared by `sw.ts` (sender) and `app/pushCuePlayback.ts` (receiver). */
export const PUSH_CUE_MESSAGE = 'personalclaw:play-cue'

function isPushCue(value: unknown): value is PushCue {
  return typeof value === 'string' && (PUSH_CUES as readonly string[]).includes(value)
}

/** What the user sees. Composed here, never received. */
export interface PushNotification {
  title: string
  body: string
  /** Coalescing key — a second push about the SAME item replaces the first. */
  tag: string
  /** Where a tap goes. Always a same-origin companion URL. */
  url: string
  /** Approvals interrupt; everything else does not. */
  requireInteraction: boolean
  /** The cue voice an open client should play for this push, or absent for a silent one
   *  (MOBILE-COMPANION MC-6). Read from the per-kind rules, never from the wire. */
  sound?: PushCue
}

/** Fixed copy per kind. A kind absent here gets the generic row — never the raw
 *  `kind` string, which would let a malformed payload paint its own text. */
const COPY: Record<string, { title: string; body: string }> = {
  approval: { title: 'Approval needed', body: 'A run is waiting for your decision.' },
  needs_input: { title: 'Loop needs input', body: 'A loop is waiting on you.' },
  inbox_alert: { title: 'Inbox alert', body: 'Something in your inbox matched an alert.' },
  agent_request: { title: 'Agent request', body: 'Your agent is asking for something.' },
}

const GENERIC = { title: 'PersonalClaw', body: 'Something is waiting for you.' }

/** The companion route, hash-based like every other route in this SPA. */
export const COMPANION_PATH = '/#/companion'

/** True when *value* is a payload this worker will act on.
 *
 *  Rejects extra keys as well as missing ones. A payload carrying a third field
 *  did not come from `personalclaw.push` — whatever it is, this worker will not
 *  render it, so refusing it outright is both the safe and the honest branch. */
export function isPushPayload(value: unknown): value is PushPayload {
  if (typeof value !== 'object' || value === null) return false
  const keys = Object.keys(value as Record<string, unknown>).sort()
  if (keys.length !== 2 || keys[0] !== 'item_id' || keys[1] !== 'kind') return false
  const { kind, item_id } = value as Record<string, unknown>
  return typeof kind === 'string' && kind.length > 0 && typeof item_id === 'string'
}

/** Where a tap on a notification for *kind* + *itemId* should land.
 *
 *  `approval` gets `?approval=<id>` so the companion can focus THAT card — the
 *  milestone's whole point is that the tap lands on the decision, not on a list
 *  the user then has to scan while the run stays blocked. `encodeURIComponent`
 *  because an id is data: an unescaped `&` would silently split the query. */
export function deepLinkFor(kind: string, itemId: string): string {
  if (kind === 'approval' && itemId) return `${COMPANION_PATH}?approval=${encodeURIComponent(itemId)}`
  return COMPANION_PATH
}

/** The notification to show for *payload*. Text comes from COPY, never from the wire.
 *
 *  The VOICE is the one part that is a user PREFERENCE, so it is read from the per-kind rules
 *  (*soundByKind*, keyed by the same wire kind as COPY) rather than a fixed table
 *  (MOBILE-COMPANION MC-6). No map, an unconfigured kind, or an unknown voice ⇒ no cue: the push
 *  stays silent. Validated against the closed set here too, so a stale map can never hand the
 *  client an unplayable voice. */
export function notificationFor(
  payload: PushPayload,
  soundByKind?: Readonly<Record<string, string>>,
): PushNotification {
  const copy = COPY[payload.kind] ?? GENERIC
  const configured = soundByKind?.[payload.kind]
  const sound = isPushCue(configured) ? configured : undefined
  return {
    title: copy.title,
    body: copy.body,
    // Keyed on kind+item so two pushes about one approval collapse into one
    // notification, while two DIFFERENT approvals both stay visible. Keying on
    // kind alone would hide the second approval; keying on nothing would stack
    // duplicates every time a retry landed.
    tag: `personalclaw:${payload.kind}:${payload.item_id}`,
    url: deepLinkFor(payload.kind, payload.item_id),
    requireInteraction: payload.kind === 'approval',
    ...(sound ? { sound } : {}),
  }
}

/** Build the `{ wireKind: voice }` map `notificationFor` reads, from the rules document the SW
 *  fetched (MOBILE-COMPANION MC-6). Defensive by contract — the document crosses the network: a
 *  row without a string `wire`, or whose `sound` is not a registered voice, is skipped, so a
 *  malformed or newer-build document degrades to "no cue" rather than an unplayable one. */
export function soundMapFromRules(doc: unknown): Record<string, PushCue> {
  const out: Record<string, PushCue> = {}
  const rules = (doc as { rules?: unknown } | null)?.rules
  if (!Array.isArray(rules)) return out
  for (const row of rules) {
    const wire = (row as { wire?: unknown }).wire
    const sound = (row as { sound?: unknown }).sound
    if (typeof wire === 'string' && wire && isPushCue(sound)) out[wire] = sound
  }
  return out
}

/** Whether an already-open client should be reused for *url* rather than opening
 *  a new window.
 *
 *  Matched on ORIGIN only, deliberately. The companion is a hash route, so every
 *  companion URL shares one document — a path/hash comparison would open a second
 *  window for `?approval=b` while `?approval=a` was already on screen, and the
 *  phone would end up with a window per approval. */
export function shouldFocus(clientUrl: string, workerOrigin: string): boolean {
  try {
    return new URL(clientUrl).origin === workerOrigin
  } catch {
    return false
  }
}
