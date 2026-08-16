import { api } from '../../lib/api'

/** ONBOARDING-UX S1 T1.3 (OU-3) — the three first-success "try one" flows.
 *
 *  **These are not pre-filled forms.** Each flow is a short chain of the REAL
 *  endpoints the product uses, run to a real visible outcome, and every fact the
 *  card shows is read back out of a real response rather than assumed from the
 *  request. A card that navigated to a seeded form would be a tour; the point of
 *  a first success is that the system already did something for you.
 *
 *  **No paid inference on any of the three.** A first run must not spend a
 *  stranger's tokens to prove the install works, so each flow was chosen for a
 *  path whose outcome is real and observable without a completion call:
 *
 *   · knowledge — `POST /api/knowledge/items` writes the note synchronously and
 *     `GET /api/knowledge/search-for-context` answers from SQLite FTS5 plus graph
 *     traversal, with the embedder wired in only when one is configured
 *     (`handlers/knowledge.py` builds `HybridRetriever(store, embedder=None)`
 *     otherwise). So "ingest, then ask your own note a question" is a complete
 *     round trip offline. The ingest node-graph's model-backed nodes (summary,
 *     entities, AI title) degrade to `skipped` — enrichment is a bonus, never
 *     what the card claims.
 *   · reminder — the `notify` action provider calls `state.notify(...)` and
 *     nothing else, so creating the trigger and firing it once produces a real
 *     dashboard notification with no model in the path.
 *   · loop — `manager.start` writes `status: running` BEFORE any agent work and
 *     only arms a timer; the first model turn fires after `idle_secs`. So the
 *     card's own outcome ("it is running") costs nothing, and the loop is capped
 *     at ONE cycle so the work it goes on to do is bounded.
 *
 *  Flows live here, apart from the card chrome, so the executed behaviour is
 *  testable without rendering the step — and so a reader can check what each card
 *  actually calls in one screen. */

/** Which card. Matches `first_success` in `personalclaw/onboarding.py` exactly, so
 *  the completion flag a card writes is named by the same key the backend stores. */
export type TryOneId = 'knowledge' | 'trigger' | 'loop'

/** One read-back fact a completed card shows. `value` always comes off a real
 *  response — never echoed from the request body, because echoing the request
 *  proves the request was BUILT, not that anything happened. */
export interface OutcomeFact { label: string; value: string }

/** What a card renders once its flow reached the end. */
export interface TryOneOutcome {
  /** One line naming what actually happened, in the past tense. */
  headline: string
  facts: OutcomeFact[]
  /** The in-app hash path (no leading `#/`) of the thing that now exists. */
  href: string
  linkLabel: string
}

/** The seeded note. Deliberately about the product's own storage model: the answer
 *  the retrieval step returns then teaches the user something true about their
 *  install, so the demo content is not filler. */
export const KNOWLEDGE_SEED = {
  title: 'How PersonalClaw stores your data',
  content:
    'Everything PersonalClaw knows lives in one home directory on this machine. Knowledge, memory, '
    + 'chat history, triggers and app state are all files under that home, so copying the home '
    + 'directory copies the whole agent, and deleting it deletes everything. Nothing is stored '
    + 'anywhere else unless you connect a provider yourself.',
  question: 'Where does PersonalClaw keep my data?',
} as const

export const REMINDER_SEED = {
  name: 'Daily check-in',
  /** 9:00 every morning — a real recurring reminder the user keeps, not a throwaway. */
  cron: '0 9 * * *',
  title: 'Your daily check-in',
  body: 'What is worth handing to your agent today?',
} as const

export const LOOP_SEED = {
  /** `general` has no launch blocker and its only validator screens an optional
   *  verify command, so a fresh home can always start it. Must be >= 12 chars. */
  kind: 'general',
  task: 'Draft a short note describing what I could use an agent for this week.',
} as const

/** A thrown api-client error's text. `ApiError.message` is already the gateway's own
 *  `{"error": ...}` string (`lib/errText.ts` extracts it), so this only unwraps the
 *  Error and never rewrites the words — the failure path's whole value is showing
 *  the server's actual sentence rather than a friendly guess. */
export function failureText(e: unknown): string {
  // An `Error` is checked FIRST and never falls through to `String(e)`: an Error with an empty
  // message stringifies to the literal word "Error", which would be shown to a user as the
  // gateway's explanation. A message-less failure has to say it has no message.
  if (e instanceof Error) return e.message.trim() || 'The call failed without a message.'
  const s = String(e ?? '').trim()
  return s || 'The call failed without a message.'
}

/** Does this failure look like the provider refusing a real call?
 *
 *  This is the state the atom names: the essentials step's Test passed, so the
 *  credential reached the provider and came back OK — and then the first real call
 *  is rejected anyway (a key scoped to the wrong thing, an empty balance, a model
 *  the account cannot see). The user cannot debug that from a card, so the card
 *  stops guessing and hands them the surface that owns the credential.
 *
 *  Matching is on the SERVER's words plus the HTTP status. It is deliberately
 *  generous: pointing at provider settings when the truth was something else costs
 *  a wasted click, while missing the provider case leaves someone stuck on a first
 *  run with a working-looking key. */
const PROVIDER_MARKERS = [
  /\bunauthor/i, /\bforbidden\b/i, /\bauthenticat/i, /\bcredential/i,
  /\bapi[ _-]?key\b/i, /\btoken\b.*\b(invalid|expired|revoked)\b/i,
  /\bno provider\b/i, /\bprovider\b.*\b(not|fail|refus|unavailable|error)/i,
  /\bquota\b/i, /\brate limit/i, /\bbilling\b/i, /\binsufficient\b/i,
  // "does not exist or you do not have access to it" is the single most common real one, and it
  // is the reason this list is written from forwarded provider sentences rather than invented.
  /\bmodel\b.*\b(not found|not available|does not exist|unknown|unsupported|no access|access to)\b/i,
  /\bcould not resolve\b/i, /\bno (chat )?model\b/i,
]

export function isProviderFailure(message: string, status?: number): boolean {
  if (status === 401 || status === 402 || status === 403) return true
  // Underscores are word characters, so `\b` does NOT split a snake_case error CODE — and
  // snake_case is exactly how providers name these (`insufficient_quota`, `invalid_api_key`,
  // `rate_limit_exceeded`, `authentication_error`). Without this, the most machine-readable
  // half of the vocabulary silently failed to match while the prose half passed, which is the
  // worst way for a classifier to be wrong: it looks right on every example you read aloud.
  const flat = message.replace(/[_]+/g, ' ')
  return PROVIDER_MARKERS.some((re) => re.test(flat))
}

/** Where a failed card sends the user. Two branches only, both real Settings
 *  subpage ids (`SUBPAGES` in `pages/settings/SettingsPage.tsx`), because a
 *  per-card destination table would be a third place to keep honest. */
export interface SettingsTarget {
  /** Hash path without the leading `#/` — `SettingsPage` reads the sub-segment. */
  path: string
  label: string
  /** Why this is the right surface, said in one sentence. */
  because: string
}

export function settingsTargetFor(message: string, status?: number): SettingsTarget {
  if (isProviderFailure(message, status)) {
    return {
      path: 'settings/providers',
      label: 'Open model provider settings',
      because: 'The provider passed its test and then refused this call — its key or plan is what to check.',
    }
  }
  return {
    path: 'settings/doctor',
    label: 'Open Settings → Doctor',
    because: 'Doctor checks the parts of your install this call depends on.',
  }
}

/** `next_run_ts` is unix SECONDS (`triggers/schedule_view.py`), not millis. */
function whenNext(nextRunTs?: number | null): string {
  if (!nextRunTs) return 'not scheduled yet'
  return new Date(nextRunTs * 1000).toLocaleString()
}

function clip(s: string, max = 200): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}

/** Ingest a note for real, then ask the corpus a question and show the passage that
 *  answered it. Throws if the note ingests but nothing comes back — an item that is
 *  stored and unfindable is a failure, and claiming success there would be the
 *  loudest possible lie on this surface. */
export async function runKnowledgeFlow(): Promise<TryOneOutcome> {
  const item = await api.createKnowledgeItem({
    type: 'note',
    title: KNOWLEDGE_SEED.title,
    content: KNOWLEDGE_SEED.content,
    tags: ['first-run'],
  })
  const res = await api.knowledgeSearchForContext(KNOWLEDGE_SEED.question, 1200)
  const hit = res.results.find((r) => r.id === item.id)
  if (!hit) {
    throw new Error(
      `The note saved (${item.id}) but asking "${KNOWLEDGE_SEED.question}" did not return it. `
      + 'The knowledge index is not answering.',
    )
  }
  return {
    headline: 'Your note is in Knowledge — and it answered a question about itself.',
    facts: [
      { label: 'You asked', value: KNOWLEDGE_SEED.question },
      { label: 'It answered from', value: hit.title },
      { label: 'The passage', value: clip(hit.content || hit.summary || '') },
      { label: 'Matched by', value: `${hit.match_type || 'search'} · ${hit.tokens} tokens of context` },
    ],
    href: `knowledge/item/${item.id}`,
    linkLabel: 'Open the note',
  }
}

/** Create a real recurring reminder, fire it once so the user SEES what it does,
 *  and read the notification back out of the notification store. `ok: false` on a
 *  200 means the action did not run (`TriggerRunResult` carries the reason in
 *  `refused`/`result`) — that is a failure, not a completed run. */
export async function runReminderFlow(): Promise<TryOneOutcome> {
  const created = await api.createSchedule({
    name: REMINDER_SEED.name,
    cron: REMINDER_SEED.cron,
    action: {
      provider: 'notify',
      config: {
        title_template: REMINDER_SEED.title,
        body_template: REMINDER_SEED.body,
        kind: 'success',
      },
    },
  })
  const trigger = created.trigger
  const run = await api.runSchedule(trigger.raw_id)
  if (!run.ok) {
    throw new Error(
      run.refused || (typeof run.result === 'string' && run.result)
      || 'The reminder was created but firing it did not run the action.',
    )
  }
  const { notifications } = await api.notifications()
  const landed = notifications.find((n) => n.title === REMINDER_SEED.title)
  if (!landed) {
    throw new Error(
      `The reminder fired but no "${REMINDER_SEED.title}" notification reached the dashboard.`,
    )
  }
  return {
    headline: 'Your reminder is set — and this is what it will say.',
    facts: [
      { label: 'It said', value: `${landed.title} — ${landed.body}` },
      { label: 'Cadence', value: trigger.schedule || REMINDER_SEED.cron },
      { label: 'Next time', value: whenNext(trigger.next_run_ts) },
    ],
    href: 'notifications',
    linkLabel: 'See it in notifications',
  }
}

/** Create and start a real loop. `status` is the outcome: `manager.start` writes it
 *  before any agent work, so "running" is a fact about the system and not a hope.
 *  Capped at one cycle — a first run should show the machinery, not open an
 *  unbounded spend. */
export async function runLoopFlow(): Promise<TryOneOutcome> {
  const loop = await api.createULoop({
    kind: LOOP_SEED.kind,
    task: LOOP_SEED.task,
    max_cycles: 1,
  })
  const started = await api.uLoopAction(loop.id, 'start')
  if (started.status !== 'running') {
    throw new Error(`The loop was created but did not start — it is "${started.status}".`)
  }
  return {
    headline: 'Your first loop is running.',
    facts: [
      { label: 'Working on', value: started.task || LOOP_SEED.task },
      { label: 'Status', value: started.status },
      { label: 'Budget', value: `${started.max_cycles} cycle — it stops on its own` },
    ],
    href: `loops/${started.id}`,
    linkLabel: 'Watch it work',
  }
}

/** The three cards, in the order the step renders them. Cheapest and most legible
 *  first: a note you can read, then a reminder you keep, then a loop that runs. */
export const TRY_ONE_FLOWS: Record<TryOneId, () => Promise<TryOneOutcome>> = {
  knowledge: runKnowledgeFlow,
  trigger: runReminderFlow,
  loop: runLoopFlow,
}
