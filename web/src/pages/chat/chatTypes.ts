/** Chat conversation model — a turn is an ordered list of SEGMENTS so an
 *  assistant turn can interleave streamed text with tool cards and approval
 *  prompts, driven by the live WS events (tool_call → tool_result by id;
 *  approval → approval_resolved by id). */

// `approvalMeta` imports ApprovalSegment from here as `import type`, which is erased at
// compile time, so this value import creates no runtime cycle.
import { approvalRiskOf, blastRadiusOf, type BlastRadius } from './approvalMeta'
import { turnErrorText } from './turnError'
import type { ImageDelivery } from './imageAttachments'

export interface TextSegment { kind: 'text'; text: string }

export interface ToolSegment {
  kind: 'tool'
  id: string              // tool_call_id — correlates tool_call ↔ tool_result
  tool: string            // STABLE tool name (e.g. "Terminal", "Read") — kept scannable
  detail?: string         // refined one-line summary (the command / file+range); 2ndary
  toolKind?: string       // '' on native; populated on ACP
  input?: string          // input_preview (args)
  inputObj?: unknown      // structured input object (native) — drives schema-driven field rendering
  output?: string         // tool_result.output (undefined until it lands)
  purpose?: string        // '' on native; ACP fills it
  auto?: boolean          // auto-approved
  done: boolean
  // Typed I/O metadata (tool-io-rendering + projection). All optional; absent →
  // the renderer falls back to raw text exactly as before.
  contentType?: string    // output content type (log/diff/json/test/csv/markdown/generic)
  rawRef?: string         // tool-result-store id for the "show full result" affordance
  truncated?: boolean     // output was projected/capped
  originalLength?: number // raw char length when truncated
  recoveryHints?: string[] // TC5: concrete next-steps on a failed tool call
  agentError?: AgentError  // Coded WHAT/WHY/FIX envelope on a failed call
  ok?: boolean            // tool-call outcome — only present (false) when it FAILED, for color-coding
  /** The call ran without anyone asking her: the agent CLI ran it on its own, with no approval
   *  request. The gateway's sentence saying so and why (`ungated_call_note`), from the live
   *  card's `tool_call` update frame or the persisted row's `meta.ungated`. */
  ungated?: string
}

/** The structured error envelope carried on a failed
 *  tool result's meta (`agent_error`). `code` is a stable, append-only key the
 *  UI (and external clients) branch on; what/why/fix are the rendered lines. */
export interface AgentError {
  code: string
  what: string
  why: string
  fix: string
  suggestions?: string[]
}

export interface ApprovalSegment {
  kind: 'approval'
  id: string              // approval id / request_id
  tool: string
  input?: string
  purpose?: string
  // The effective per-invocation risk (`task_modes.read_call`): a declared level, or
  // `unchecked` for a shell command the screen could not vouch for.
  risk?: 'safe' | 'caution' | 'destructive' | 'unchecked'
  // What the call can touch, as the backend composed it from the same reading as `risk`
  // (`approval_brief.call_blast_radius`), decoded by `blastRadiusOf`. Absent on a row that
  // carries none.
  blastRadius?: BlastRadius
  // The agent a "This agent" grant would be SAVED ON, resolved by the backend
  // (`agents.defaults.persistable_grant_target`) at the moment the prompt was raised.
  // Empty or absent means the grant cannot persist — a reserved system agent, a name with no
  // profile, or an ACP-bound chat — so the card must not promise future chats (#541). Absence
  // is deliberately read as "cannot", never as "can": under-claiming a persistence is the
  // only safe direction for a promise, and over-claiming it is the bug this field fixes.
  grantAgent?: string
  // The settled outcome, as the backend persisted it. Typed as the raw wire `string`
  // (not the ApprovalResolution union) because a session persisted by another build
  // can carry an outcome this one doesn't know — approvalOutcome() maps the known set
  // explicitly and renders an unknown value honestly rather than as a denial.
  // Absent = still pending → the actionable card.
  resolved?: string
  // What PersonalClaw answered the agent with when it refused the call — the agent's own option
  // ("Answered “No”") — from the line the gateway writes for the refused step (`foldStepLine`).
  detail?: string
}

/** Coarse activity line — the native loop emits `activity_event {kind,text}`
 *  (e.g. "Thinking…") but NOT individual tool_call/tool_result frames. We
 *  surface these as a quiet inline
 *  line so native tool turns aren't blank. ACP turns get full ToolSegments
 *  instead, so we suppress activity lines once a turn has real tool cards. */
export interface ActivitySegment {
  kind: 'activity'; text: string; activityKind?: string
  // Which learning path produced a `learned` activity.
  // All three captures — the preference facet, the after-turn lesson review, and the
  // skill-ladder proposal pass — share `activityKind: 'learned'`, so this discriminator
  // is the only thing that can route a tap on the learned chip to the surface that can
  // approve or edit THAT artifact. Typed as the raw wire `string` (not a union) because
  // an older session, or a future emitter, legitimately arrives without it — see
  // `learnedSurface()`, which degrades an unrecognised value to a non-tappable chip
  // rather than guessing a surface.
  origin?: string
  // What undoing a `learned` artifact needs: a learned preference's key (`origin: 'facet'`).
  // Absent for the other origins, which are reviewed where the chip links rather than undone
  // from it.
  ref?: string
}

/** A line the gateway wrote to say what happened to the conversation (`role: 'notice'`) — a turn
 *  moved to another agent, which is now answering — shown where it happened, live and on reload,
 *  as a quiet activity line: it is not the agent's answer and not an error. */
export const noticeSegment = (text: string): ActivitySegment => ({ kind: 'activity', text, activityKind: 'notice' })

/** A turn-level error (the model/provider rejected the turn, e.g. a Bedrock
 *  ValidationException). Surfaced as a distinct red callout so a failed turn is
 *  never silently blank. Arrives live via the `chat_message` WS frame (role
 *  `error`) and is rehydrated from history on reload. `settings` is the Settings page (a route
 *  id) the failure is lifted on, when the gateway names one (a spend cap's refusal names where
 *  the cap is changed), and the notice links it. */
export interface ErrorSegment { kind: 'error'; text: string; settings?: string }

/** Live model reasoning streamed over the `chat_thinking` WS frame. Rendered
 *  as a collapsible muted block ONLY while `show_thinking_inline` is on; the frames
 *  are dropped at ingestion when it is off. Never persisted — history rehydration
 *  (`hydrateTurns`) knows nothing of this kind, so a reload shows the transcript
 *  exactly as before: thinking is a live-stream affordance, not part of the record. */
export interface ThinkingSegment { kind: 'thinking'; text: string }

export type Segment = TextSegment | ToolSegment | ApprovalSegment | ActivitySegment | ErrorSegment | ThinkingSegment

/** Fold one `chat_thinking` chunk into an assistant turn's segments: extend the
 *  trailing thinking block if the reasoning stream is uninterrupted, else open a
 *  new one. Interleaving rule: any non-thinking segment (streamed text, a tool
 *  card…) closes the current block, so a later thinking chunk starts a fresh block
 *  rather than retroactively growing one that visually sits ABOVE newer content. */
export const appendThinking = (segs: Segment[], chunk: string): Segment[] => {
  if (!chunk) return segs
  const last = segs[segs.length - 1]
  if (last?.kind === 'thinking') return [...segs.slice(0, -1), { kind: 'thinking', text: last.text + chunk }]
  return [...segs, { kind: 'thinking', text: chunk }]
}

/** One episodic memory — or, with `kind: 'lesson'`, one recalled lesson — surfaced into an
 *  assistant turn's prompt, resolvable from the `[Memory N]` (`[Lesson N]`) citation the reply
 *  emits. `id` is the episode's stable record id, or the lesson's rule as the Memory studio lists
 *  it (used to deep-link the memory studio); it may be null when the recall layer had no
 *  per-record id, in which case the chip degrades to a non-navigable label. */
export interface MemoryCitation { n: number; id: string | null; preview?: string; kind?: 'lesson' }

/** One skill whose content actually reached this turn's prompt (LEARNING-VISIBILITY
 *  T2.1). Rides the `meta.skills_used` of the message that STARTED the turn — the user's, or
 *  the row a loop's nudge, an automation or a subagent's report started it with; the message
 *  the skill was attached to (`joinedSkillsOf`) — and arrives live as `activity_event {kind:
 *  "skills"}` the moment the turn is put together, so the chip needs no channel of its own.
 *
 *  🔴 It rode the assistant message's meta, stamped when a reply's TEXT settled. A turn
 *  that only called tools, or was stopped before it said a word, therefore never showed
 *  which skill had joined it — measured on a turn that ran twenty minutes of failing calls
 *  with a family-trip skill wrapped around the user's message, and nothing on screen said so.
 *
 *  `state` is the allocator's load state, typed as the raw wire `string` rather than a
 *  union for the same reason `ApprovalSegment.resolved` is: a session persisted by
 *  another build can carry a state this one doesn't know. Only two ever arrive today —
 *  `admitted` (the skill's body loaded) and `reduced` (only a summary fit). A REFUSED
 *  skill is deliberately never in this list: it was NAMED to the agent but none of its
 *  content loaded, so counting it would overstate the turn. */
export interface SkillUsed { name: string; state: string; loaded_tokens: number }

/** The roles a turn is started with, as the backend's `_TURN_DISPATCH_ROLES` (`chat_persistence`):
 *  the user's message, and the rows an automation, a subagent's report and a loop's nudge start
 *  one with. */
const TURN_DISPATCH_ROLES: ReadonlySet<string> = new Set(['user', 'inject', 'subagent', 'nudge'])

/** The skills that joined the turn `m` started, or `undefined` — absent on a turn no skill joined,
 *  on a message that started no turn, and on every message persisted before the record moved to
 *  the turn's own message. The chip then simply does not render, rather than reading "used 0
 *  skills". The one reader of the record, for the chat and the loop cockpit alike. */
export function joinedSkillsOf(m: { role: string; meta?: { skills_used?: SkillUsed[] } }): SkillUsed[] | undefined {
  const s = m.meta?.skills_used
  return TURN_DISPATCH_ROLES.has(m.role) && Array.isArray(s) && s.length ? s : undefined
}

/** How many skill names the chip spells out before it counts the rest. */
const NAMED_SKILLS = 3

/** A skill's name as a person reads it: its key's last part (`imported/claude_code/trip-research`
 *  → `trip-research`). The whole key is in the hover text. */
function skillShortName(s: SkillUsed): string {
  const name = s.name || '(unnamed skill)'
  return name.split('/').pop() || name
}

/** The chip's own words — it NAMES the skills. A count alone ("used 1 skill") left the one fact
 *  a reader needs, WHICH skill joined the turn, behind a hover that a keyboard, a touch screen
 *  and a screen reader never reach. N counts every entry — `admitted` and `reduced` alike,
 *  because both put content in the prompt (a `reduced` skill loaded a summary, not nothing).
 *  Returns '' for an empty list so a caller can't render a truthful-looking "used 0
 *  skills" for a turn that loaded none: the backend omits the key entirely in that case,
 *  and the chip must be absent, not zeroed. */
export function skillsUsedLabel(skills: SkillUsed[]): string {
  const n = skills.length
  if (!n) return ''
  const named = skills.slice(0, NAMED_SKILLS).map(skillShortName).join(', ')
  const more = n > NAMED_SKILLS ? ` +${n - NAMED_SKILLS} more` : ''
  return `used skill${n === 1 ? '' : 's'} ${named}${more}`
}

/** Hover text for the chip: the skill names in the ALLOCATOR'S OWN ORDER (the order they
 *  were admitted — never re-sorted here, which would invent a ranking the backend never
 *  stated). A `reduced` skill is marked, because presenting a summary-only load as a full
 *  one is the one thing this chip must not do. */
export function skillsUsedTitle(skills: SkillUsed[]): string {
  if (!skills.length) return ''
  const lines = skills.map((s) => {
    const name = s.name || '(unnamed skill)'
    return s.state === 'reduced' ? `${name} — summary only` : name
  })
  return `Skills used this turn:\n${lines.join('\n')}`
}

/** Stamp `origin` (and `ref`, when the emitter sent one) onto the activity segment
 *  `insertActivity` just created, given the arrays before (`prev`) and after (`next`) that call.
 *
 *  Exists so `TextRunOwnership.activity` doesn't have to widen `insertActivity`'s signature (and
 *  re-baseline its K42/K44/K45 suite) just to carry one optional field. It identifies the new
 *  segment by REFERENCE, not by matching text: `insertActivity` returns `prev` untouched on
 *  both its early-outs (a turn with tool cards, an adjacent duplicate line), so the only
 *  activity segment present in `next` and absent from `prev` is the one it spliced in — a
 *  fresh object literal no previous render holds, which is what makes writing to it safe.
 *
 *  Returns `next` either way; a falsy origin is a no-op, which is the pre-T2.2 wire and every
 *  non-`learned` activity kind. */
export function stampActivityOrigin(prev: Segment[], next: Segment[], origin?: string, ref?: string): Segment[] {
  if (!origin || next === prev) return next
  const added = next.find((sg) => sg.kind === 'activity' && !prev.includes(sg))
  if (added) {
    (added as ActivitySegment).origin = origin
    if (ref) (added as ActivitySegment).ref = ref
  }
  return next
}

/** Where a tap on the learned chip lands, keyed on the emitter's `origin`
 *  (LEARNING-VISIBILITY T2.2). Verified against what each surface actually renders, not
 *  against the artifact's name:
 *
 *  - `proposal` → the skill-ladder writes a template PROPOSAL that `SkillProposals`
 *    (mounted by the Skills page's `?mode=proposals` view) lists with approve/reject.
 *    The BARE `#/skills` route lands on Installed skills, which shows no proposal at all —
 *    hence the query param.
 *  - `lesson` → the after-turn review calls `service.write_lesson()`, so the artifact is a
 *    LESSON in the lesson store. The Memory Studio (Settings → Memory) reads exactly that
 *    store (`api.lessons()`) and its inspector edits/deletes a lesson. It deliberately
 *    does NOT route to the Learning page: that page is the `/api/learning/proposals`
 *    inbox, a different artifact class (flywheel `lesson_batch` proposals), which can
 *    neither show nor edit an after-turn lesson.
 *  - `facet` → `upsert_facet` writes a learned preference, and Settings → Memory → Learned
 *    preferences is where one is pinned or forgotten — the link opens that list at the row
 *    (`?pref=<key>`). The veto branch writes a lesson instead, and its chip says `lesson`.
 *
 *  Returns null for an absent or unrecognised origin. That is the graceful-degrade
 *  contract, not an oversight: every message persisted, and anything a future
 *  emitter adds, arrives without a mapping, and a chip that guessed a surface would send
 *  the user somewhere the artifact isn't. The chip still renders — it just isn't a link. */
export interface LearnedSurface { href: string; label: string }

/** One entry of an assistant message's `meta.learned` — what the turn learned, as its chip said
 *  (`chat_session_map.LEARNED_KEY`). `ref` is a learned preference's key. */
export interface LearnedRecord { origin?: string; text: string; ref?: string }
export function learnedSurface(origin?: string | null, ref?: string | null): LearnedSurface | null {
  switch (origin) {
    case 'proposal':
      return { href: '#/skills?mode=proposals', label: 'Review in Skill proposals →' }
    case 'lesson':
      return { href: '#/settings/memory?tab=studio', label: 'Review lessons in Memory →' }
    case 'facet':
      return {
        href: `#/settings/memory?tab=settings${ref ? `&pref=${encodeURIComponent(ref)}` : ''}`,
        label: 'Manage in Learned preferences →',
      }
    default:
      return null
  }
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  segments: Segment[]     // user turns are a single text segment
  ts?: string             // source message timestamp (for edit-resend by ts)
  // Episodic memory citations surfaced into THIS assistant turn. The reply
  // cites facts inline as `[Memory N]`; the Markdown renderer resolves each token
  // against this list into a deep-link to the episode. Absent on turns with no
  // episodic recall (the vast majority) and on user turns.
  citations?: MemoryCitation[]
  // Skills whose content fed THIS assistant turn — the skills chip's input. Read off the
  // message that started the turn (`joinedSkillsOf`), so it is absent on the turns that
  // loaded no skill (and on every user turn) rather than an empty array.
  skillsUsed?: SkillUsed[]
  // The reply stopped at the model's OUTPUT cap and ends mid-sentence — the assistant
  // message's `meta.finish_reason === 'length'`. Absent (never `false`-by-default noise) on
  // the turns that finished on their own, which is nearly all of them.
  cutOff?: boolean
  // The reply came from ANOTHER model than the one chosen for it — the agent's pinned model or
  // the chat's own pick could not run. The sentence, "Ran on X instead of Y: …", from the assistant
  // message's `meta.model_substitution`. Absent when the chosen model answered.
  modelSubstitution?: string
  // paste blocks referenced by `[Paste #N]` markers in this turn's text, kept so
  // the bubble can render the markers as inspectable chips after send.
  pastes?: { seq: number; lines: number; content: string }[]
  // attachment file paths (uploads + @-mentions) sent WITH this turn, so the
  // sent user bubble shows them as chips the user can open/preview after send.
  files?: string[]
  // How this turn's attached IMAGES reached the model — as an image, or as the text read from
  // it — from the user message's `meta.image_delivery`. Absent on turns with no image, and on
  // the just-sent turn until the server has decided (`chat_done` grafts it on).
  imageDelivery?: ImageDelivery
  // When the prompt was optimized before sending (via /optimize or the optimize
  // control), this holds the OPTIMIZED text the model actually received; the
  // turn's text segment keeps the ORIGINAL the user typed. The bubble shows the
  // original with the optimized in a collapsed, expandable section.
  optimized?: string
  // The saved prompt this USER turn ran (`@name` or `/prompts get name`) and the text the agent
  // was sent in its place, from the message's `meta.ran_prompt` — live, from the
  // `activity_event {kind: "prompt"}` the expansion announces. The bubble keeps what she typed;
  // the prompt's text sits folded under it, labelled as the prompt's.
  ranPrompt?: RanPrompt
  // Regenerated answer variants for an ASSISTANT turn. When a reply is regenerated
  // the backend keeps the prior answer(s) and appends the new one, storing every
  // version on the message. The UI only needs how MANY there are (`variantCount`)
  // and which is active (`variantIdx`) to render the ‹n/N› switcher; the active
  // variant's body is already this turn's text segment. Navigation is server-driven
  // — the switcher posts switchVariant(idx) and the chat_variant_switch WS echo
  // swaps the text + index in place. `variantCount` ≤ 1 → no switcher.
  variantCount?: number
  variantIdx?: number
  // True rewind: when this USER turn was edited-and-replayed, the
  // discarded tail(s) are retained here so the divider chip can show "N messages
  // kept in history" and the read-only disclosure can render them. Each snapshot's
  // `messages` begins with the edited turn's OLD content. Absent = never rewound.
  rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]
  // Branch mechanic: the index of this turn's LAST message in the
  // BACKEND's visible user/assistant list — the coordinate `POST .../fork` and
  // `edit-resend` speak (`at_message_index`, inclusive). It is NOT the turn's array
  // position: hydrateTurns collapses native loop re-injections and merges consecutive
  // assistant messages into one turn, so on any tool-using transcript the two diverge
  // and drift further with every merge. Stamped by hydrateTurns; absent on turns built
  // live from WS frames (see branchIndexOf, which derives those).
  visibleIndex?: number
}

/** THE UI COORDINATE of a turn — what identifies a turn to everything that scrolls to one:
 *  `ChatPage`'s `turnNodes` DOM registry, every `SessionMark.visibleIndex`, and the
 *  Activity → Index jump anchors.
 *
 *  🔑 IT IS A CONTRACT BETWEEN MODULES, WHICH IS WHY IT IS A FUNCTION AND NOT AN INLINE `?? i`.
 *  The Session Map rail owns no scroll machinery: it hands a mark's coordinate to `onJumpTo` and
 *  reads the page's node registry AT THAT COORDINATE. So the registry and the marks must be
 *  keyed by the same rule, and a rule written twice in two files is a rule that drifts — which
 *  is precisely what it did: registered under the array position, a mark jump on any tool-using
 *  transcript resolved an EARLIER turn's node, because `hydrateTurns` collapses re-injections and
 *  merges consecutive assistant messages so array position runs behind `visibleIndex`.
 *
 *  🪤 AND IT IS NOT `branchIndexOf`. That answers the OTHER question about a turn — "which
 *  backend message does it BRANCH at" — and it is deliberately not reused here: its walk-back
 *  derivation counts turns that have emitted TEXT, so a streaming assistant turn's value changes
 *  mid-answer. A DOM registry keyed on it would strand the node it registered under the previous
 *  value. This rule is stable for the life of a turn, which is what a registry needs. Two
 *  questions, two rules, both right — do not collapse them.
 */
export function markCoordOf(turn: Pick<ChatTurn, 'visibleIndex'>, arrayIndex: number): number {
  return turn.visibleIndex ?? arrayIndex
}

/** The saved prompt a message ran, and the text the agent was sent in its place. */
export interface RanPrompt { name: string; text: string }

/** The saved prompt a message ran (`meta.ran_prompt`, or the live announcement's `prompt`), or
 *  undefined when there is none or it is not the shape the turn builder records. */
export function ranPromptOf(raw: unknown): RanPrompt | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const { name, text } = raw as { name?: unknown; text?: unknown }
  return typeof name === 'string' && name && typeof text === 'string' && text ? { name, text } : undefined
}

/** A user message's persisted image delivery (`meta.image_delivery`), or undefined. */
export function imageDeliveryOf(meta: HistMsg['meta'] | undefined): ImageDelivery | undefined {
  const byPath = meta?.image_delivery
  if (!byPath || typeof byPath !== 'object' || !Object.keys(byPath).length) return undefined
  return { byPath, reason: meta?.image_delivery_reason || undefined }
}

/** Convenience: a user turn from plain text. `optimized` records the optimized
 *  variant sent to the model when the original was rewritten before sending. */
export const userTurn = (text: string, ts?: string, pastes?: ChatTurn['pastes'], files?: string[], optimized?: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }], ts, pastes, files: files?.length ? files : undefined, optimized: optimized || undefined })
/** Convenience: an assistant turn seeded with (optional) text. */
export const assistantTurn = (text = ''): ChatTurn => ({ role: 'assistant', segments: text ? [{ kind: 'text', text }] : [] })

/** Flatten a turn's text segments (for Copy / history hydration). */
export function turnText(t: ChatTurn): string {
  return t.segments.filter((s): s is TextSegment => s.kind === 'text').map((s) => s.text).join('\n').trim()
}

/** How many of a turn's work steps failed: a tool call whose result says so (`ok` false, the one
 *  bit a result carries for that) and an error the turn met on the way. The folded work says this
 *  number, so a step that failed under a reply saying it worked is visible without opening it. */
export function failedStepCount(work: Segment[]): number {
  return work.filter((s) => (s.kind === 'tool' && s.ok === false) || s.kind === 'error').length
}

/** How many of a turn's steps the agent CLI ran without asking her (`ToolSegment.ungated`). */
export function unaskedStepCount(work: Segment[]): number {
  return work.filter((s) => s.kind === 'tool' && !!s.ungated).length
}

/** A subagent spawned during this session — driven by the subagent_spawn /
 *  subagent_tool / subagent_done WS events (fire-and-forget async subagents).
 *  Shown as live cards in the activity panel's Subagents tab. */
export interface SubagentCard {
  id: string
  task: string
  agent: string
  lastTool?: string      // most recent tool title (subagent_tool)
  done: boolean
  error?: string | null
  elapsed?: number       // seconds (on done)
  result?: string        // accumulated/final output (on done)
  costUsd?: number       // per-child cost in USD (on done)
  tokens?: number        // per-child total tokens (on done)
}

// ── activity-panel derivation (Files / Links) — all client-side from turns ──
//
// There is no `index` here. The panel used to derive a user-message outline whose rows jumped to
// a turn; the Session Map (the `sessionMapMarks`) is that index now — it marks tool calls,
// approvals, errors and subagents as well as user turns, and it is always on screen rather than
// behind a panel tab. SSM-13 deleted the outline and this model with it, so the session has ONE
// index rather than two that have to be kept saying the same thing.
export interface FileEntry { path: string; name: string }
export interface LinkEntry { url: string; label: string }
export interface ChatActivity { files: FileEntry[]; links: LinkEntry[] }

// file-ish path: /a/b.ext, ~/a/b.ext, or workspace-relative a/b.ext (has an ext).
const ACT_FILE_RE = /(?:^|[\s(`'"])((?:~|\/)[\w./\-]+\.\w{1,8}|[\w./\-]+\/[\w./\-]+\.\w{1,8})/g
const ACT_URL_RE = /\bhttps?:\/\/[^\s)<>"'`\]]+/g
const baseNameOf = (p: string) => p.replace(/\/+$/, '').split('/').pop() || p
// git-diff artifacts that look like paths but aren't openable files.
const DIFF_NOISE = /^(?:[ab]\/|\/dev\/null$)/

/** Derive the activity-panel data from the conversation turns:
 *   - Files: file paths from tool inputs/outputs + paths mentioned in assistant
 *     text (deduped, first-seen order).
 *   - Links: http(s) URLs surfaced in assistant text (deduped).
 *  User turns contribute neither (they are the reader's own text), so they are skipped
 *  whole — see the `role === 'user'` early return below. */
export function deriveActivity(turns: ChatTurn[]): ChatActivity {
  const files = new Map<string, FileEntry>()
  const links = new Map<string, LinkEntry>()

  const addFile = (raw: string) => {
    let p = raw.trim().replace(/[).,;:]+$/, '')
    if (!p || DIFF_NOISE.test(p)) return            // skip a/ b/ /dev/null diff noise
    p = p.replace(/^[ab]\//, '')                    // defensive: strip a/ b/ if it slipped through
    if (!files.has(p)) files.set(p, { path: p, name: baseNameOf(p) })
  }

  turns.forEach((t) => {
    // A user turn carries no tool output and no assistant prose, so neither tab reads it.
    if (t.role === 'user') return
    for (const seg of t.segments) {
      if (seg.kind === 'tool') {
        // tool input/output often carry file paths (read/edit/write/terminal).
        for (const src of [seg.input, seg.output, seg.detail]) {
          if (!src) continue
          for (const m of src.matchAll(ACT_FILE_RE)) addFile(m[1])
        }
      } else if (seg.kind === 'text') {
        for (const m of seg.text.matchAll(ACT_FILE_RE)) addFile(m[1])
        for (const m of seg.text.matchAll(ACT_URL_RE)) {
          const url = m[0].replace(/[).,;:]+$/, '')
          if (!links.has(url)) { try { links.set(url, { url, label: new URL(url).hostname.replace(/^www\./, '') }) } catch { links.set(url, { url, label: url }) } }
        }
      }
    }
  })
  return { files: [...files.values()], links: [...links.values()] }
}

export interface HistMsg { role: string; content: string; ts?: string; variants?: { content: string; ts?: string }[]; variant_idx?: number; rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]; meta?: { tool_call_id?: string; approval_id?: string; input?: string; tool_input?: string; purpose?: string; risk?: string; kind?: string; blast_radius?: unknown; grant_agent?: string; output?: string; done?: boolean; tool?: string; detail?: string; resolved?: string; content_type?: string; raw_ref?: string; truncated?: boolean; original_length?: number; recovery_hints?: string[]; agent_error?: AgentError; ok?: boolean; pastes?: { seq: number; lines: number; content: string }[]; files?: string[]; image_delivery?: Record<string, 'image' | 'text'>; image_delivery_reason?: string; ran_prompt?: { name?: unknown; text?: unknown }; original?: string; ui_label?: string; memory_citations?: MemoryCitation[]; skills_used?: SkillUsed[]; finish_reason?: string; model_substitution?: string; turn_telemetry?: { line?: string }; learned?: LearnedRecord[]; ungated?: string } }

/** Re-collapse a persisted user message: the stored content has paste markers
 *  expanded to full text (the model saw that), but meta.pastes lets us swap each
 *  block's content back to `[Paste #N]` so the bubble renders inspectable chips
 *  on reload (matching the live-send experience). */
function recollapsePastes(content: string, pastes: { seq: number; lines: number; content: string }[]): string {
  let out = content
  // Longest content first so a block that CONTAINS another doesn't mis-replace; ties
  // broken by ascending seq so two blocks of identical content claim their occurrences
  // in send order (#1 the first, #2 the second).
  const ordered = [...pastes].sort((a, b) => b.content.length - a.content.length || a.seq - b.seq)
  for (const p of ordered) {
    if (!p.content) continue
    // ONE occurrence per block, not `split().join()`. A global replace rewrote EVERY
    // occurrence, so two identical pastes both became "[Paste #1]" — the second block's
    // marker was never emitted and `PasteChip` dropped the unresolvable chip, losing the
    // user's second block entirely (#380). Consuming one occurrence per block makes the
    // marker count match the block count, so `recollapse(expand(x)) === x` holds for
    // duplicates as well as for nesting.
    const at = out.indexOf(p.content)
    if (at === -1) continue
    out = out.slice(0, at) + markerForSeq(p.seq) + out.slice(at + p.content.length)
  }
  return out
}
const markerForSeq = (seq: number) => `[Paste #${seq}]`

/** Fold a line the gateway wrote about a step (a `tool` row with no call id) into the turn's
 *  steps, the same way live and after a reload. It is no step of its own. The one it can add to
 *  is the approval it follows: a refused approval's line says how the call ended, which the
 *  approval's own row already says, and may name the agent's option the refusal was sent as
 *  (`meta.detail`), which goes on the approval's line. */
export function foldStepLine(segs: Segment[], meta: { detail?: unknown } | undefined): Segment[] {
  const last = segs[segs.length - 1]
  const detail = typeof meta?.detail === 'string' ? meta.detail.trim() : ''
  if (!detail || last?.kind !== 'approval') return segs
  return [...segs.slice(0, -1), { ...last, detail }]
}

/** The sentence saying a call ran without anyone asking her (`ToolSegment.ungated`), or nothing.
 *  The live frame and the persisted row both carry it as a string; anything else says nothing. */
export function ungatedOf(src: { ungated?: unknown } | undefined): string | undefined {
  const note = src?.ungated
  return typeof note === 'string' && note.trim() ? note : undefined
}

/** Resolve a tool name: prefer meta.tool, else the turn content. Also strips any
 *  leading pictographic + space so sessions persisted before the status-sentinel
 *  removal (which prefixed tool content with a status glyph) still render clean. */
function toolName(meta: HistMsg['meta'], content: string): string {
  return (meta?.tool || content || 'tool').replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]+\s*/u, '').trim() || 'tool'
}

/** Build the turn/segment model from persisted history so a refreshed / revisited
 *  / streaming-done session renders IDENTICALLY to a live one (`liveToolFrames` is the
 *  live half) — tool calls become ToolSegments (deduped by tool_call_id, call+result
 *  merged in place), permission rows become resolved ApprovalSegments, text stays text,
 *  and a line the gateway wrote about a step is no step of its own (see the `tool`
 *  branch). A `tool`-role turn's name comes from meta.tool (or its content); see toolName.
 *
 *  The native ReAct loop re-injects the SAME user prompt each cycle, so history
 *  reads `user, tool, user, tool, assistant` — we collapse those repeats (a user
 *  message equal to the last one with NO assistant text emitted since = a loop
 *  re-injection, not a genuine repeat question) so a multi-tool turn renders as
 *  one user bubble + one assistant turn carrying every tool card.
 *
 *  Because of those two collapses, a turn's ARRAY POSITION is not the backend's
 *  message coordinate. Every user/assistant message consumes one slot in the
 *  backend's visible list (`[m for m in messages if m["role"] in ("user","assistant")]`
 *  — what `at_message_index` indexes) whether or not it produces a turn, so each turn
 *  carries `visibleIndex`: the slot of the LAST message folded into it. Last, not
 *  first, because `at_message_index` is INCLUSIVE — branching at an assistant turn
 *  must carry the whole answer, not just its opening message. */
export function hydrateTurns(messages: HistMsg[], running = false): ChatTurn[] {
  const turns: ChatTurn[] = []
  const toolIndex = new Map<string, ToolSegment>()  // tool_call_id → segment ref (merge results in place)
  let lastUserText = ''
  let assistantTextSinceUser = false  // distinguishes a re-injection from a real repeat
  // Backend visible-list cursor. Advances for EVERY user/assistant message — including
  // a collapsed re-injection that produces no turn — because the backend counts it.
  let visible = -1
  // Whether the turn being read is one the gateway ran: it holds a call row carrying its call's
  // id, or an approval. That decides what a `tool` row with no id is (see the `tool` branch).
  let ranHere = false

  // The skills that joined a turn ride the message that started it (`joinedSkillsOf`), and are
  // shown on the answer that follows it.
  let joinedSkills: SkillUsed[] | undefined
  const lastAssistant = (): ChatTurn => {
    let t = turns[turns.length - 1]
    if (!t || t.role !== 'assistant') { t = assistantTurn(); turns.push(t) }
    // Also onto an answer already open: a turn a loop's nudge, an automation or a subagent's report
    // started has no bubble of its own, so its answer joins the one before it — and the skills that
    // joined it show there, as they did live.
    if (joinedSkills) { t.skillsUsed = joinedSkills; joinedSkills = undefined }
    return t
  }

  for (const m of messages) {
    if (m.role === 'user') {
      visible += 1
      const text = m.content.trim()
      if (text === lastUserText && !assistantTextSinceUser) continue  // loop re-injection
      // The turn before this one got no answer, tool call or error at all — stopped before its
      // first word — so it has no answer to show its skills on yet: give it the empty one the
      // live page showed.
      if (joinedSkills) lastAssistant()
      // re-collapse expanded pastes → markers so chips render on reload.
      const pastes = m.meta?.pastes
      // An optimized turn persisted the OPTIMIZED text as content (the model saw
      // it); meta.original is what the user typed. Show the original as primary,
      // the optimized in the collapsed section — same as the live send.
      const original = m.meta?.original
      // A genui widget action persisted the MACHINE payload as content and its
      // `humanFriendlyMessage` as meta.ui_label. Show the label — and, unlike an
      // optimized turn, do NOT hand the payload to the collapsed disclosure: the
      // transcript must not render raw JSON on either the live or the reload path.
      const uiLabel = m.meta?.ui_label
      const primary = uiLabel ?? original ?? m.content
      const display = pastes?.length ? recollapsePastes(primary, pastes) : primary
      const files = Array.isArray(m.meta?.files) ? m.meta!.files : undefined
      const ut = userTurn(display, m.ts, pastes?.length ? pastes : undefined, files, original ? m.content : undefined)
      // Rewind tails retained on this user turn → drive the divider
      // chip + read-only disclosure. Tolerant: absent on pre-rewind sessions.
      if (Array.isArray(m.rewound) && m.rewound.length) ut.rewound = m.rewound
      const delivery = imageDeliveryOf(m.meta)
      if (delivery) ut.imageDelivery = delivery
      const ran = ranPromptOf(m.meta?.ran_prompt)
      if (ran) ut.ranPrompt = ran
      joinedSkills = joinedSkillsOf(m)
      ut.visibleIndex = visible
      turns.push(ut)
      lastUserText = text; assistantTextSinceUser = false
      ranHere = false
    } else if (m.role === 'assistant') {
      visible += 1
      const at = lastAssistant()
      // LAST wins: consecutive assistant messages merge into this one turn, so the
      // stamp walks forward to the final one — an inclusive branch then carries the
      // complete answer rather than truncating it mid-turn.
      at.visibleIndex = visible
      at.segments.push({ kind: 'text', text: m.content })
      // Episodic memory citations ride the assistant message's meta; carry them
      // onto the turn so the Markdown renderer can resolve `[Memory N]` tokens. Tolerant:
      // absent on turns with no episodic recall (almost all of them).
      if (Array.isArray(m.meta?.memory_citations) && m.meta!.memory_citations.length) {
        at.citations = m.meta!.memory_citations
      }
      // A reply cut at the model's output cap. The backend stamps it on the turn's LAST
      // assistant message, and consecutive assistant messages merge into this turn with the
      // last one winning — so the mark is re-decided per message, not latched by an earlier one.
      if (m.meta?.finish_reason === 'length') at.cutOff = true
      else delete at.cutOff
      // Re-decided per message for the same reason: it describes the turn that message ends.
      if (m.meta?.model_substitution) at.modelSubstitution = m.meta.model_substitution
      else delete at.modelSubstitution
      // The turn's "Turn complete" sentence, on the same last message. Live, it arrives
      // as an `activity_event`; this is the same sentence persisted with the turn's telemetry,
      // so the turn's details still show it after a reload. A record written before the
      // sentence was persisted has none, and the turn shows no telemetry row, as before.
      const statsLine = m.meta?.turn_telemetry?.line
      if (typeof statsLine === 'string' && statsLine) at.segments.push({ kind: 'activity', text: statsLine, activityKind: 'stats' })
      // What the turn learned, from the same last message: live it arrives as the learned chip's
      // `activity_event`, which a reload — or a restart mid-turn — never replays, so a preference
      // could be saved with nothing on the page saying so. One segment per entry, in order.
      for (const l of Array.isArray(m.meta?.learned) ? m.meta!.learned : []) {
        if (!l || typeof l.text !== 'string' || !l.text) continue
        at.segments.push({ kind: 'activity', text: `Learned: ${l.text}`, activityKind: 'learned', origin: l.origin, ...(l.ref ? { ref: l.ref } : {}) })
      }
      // Regenerated answers persist as ONE assistant message carrying every version
      // in `variants` (the active one's content == m.content). Carry the count + index
      // onto the turn so the ‹n/N› switcher rehydrates on reload.
      if (Array.isArray(m.variants) && m.variants.length > 1) {
        at.variantCount = m.variants.length
        at.variantIdx = typeof m.variant_idx === 'number' ? m.variant_idx : m.variants.length - 1
      }
      assistantTextSinceUser = true
    } else if (m.role === 'tool') {
      const callId = m.meta?.tool_call_id
      // The gateway writes every call with its id. A row without one, in a turn it ran, is a
      // line it wrote ABOUT a step: how the step's approval ended ("… (rejected)", which the
      // approval's own row already says), why a gate refused the call, a loop-breaker warning.
      // The live page draws no card for any of them, so a reload draws none either; each folds
      // as it does live (`foldStepLine`). Read as a call, each was a fake step: an extra row
      // titled with the line's own words put through the tool-name humanizer, reading
      // "completed". An imported conversation's call lines carry no ids at all, so in its turns
      // they are still its calls.
      if (!callId && ranHere) {
        const at = lastAssistant()
        at.segments = foldStepLine(at.segments, m.meta)
        continue
      }
      if (callId) ranHere = true
      const id = callId || `auto-${turns.length}-${lastAssistant().segments.length}`
      const existing = toolIndex.get(id)
      if (existing) {  // result/completion update for an already-seen call → merge
        if (m.meta?.output != null) existing.output = m.meta.output
        if (m.meta?.done) existing.done = true
        if (m.meta?.input) existing.input = m.meta.input
        // Only a POSITIVE declaration refines the kind, matching how the backend
        // correlates it across frames (`SeenToolCall`): a later row for the same
        // tool_call_id that omits the kind must not erase the one the opening row
        // declared.
        if (m.meta?.kind) existing.toolKind = m.meta.kind
        if (m.meta?.detail) existing.detail = m.meta.detail
        if (m.meta?.content_type) existing.contentType = m.meta.content_type
        if (m.meta?.raw_ref) existing.rawRef = m.meta.raw_ref
        if (m.meta?.truncated) { existing.truncated = true; existing.originalLength = m.meta.original_length }
        if (m.meta?.recovery_hints?.length) existing.recoveryHints = m.meta.recovery_hints
        if (m.meta?.agent_error) existing.agentError = m.meta.agent_error
        if (m.meta?.ok === false) existing.ok = false
        const ungated = ungatedOf(m.meta)
        if (ungated) existing.ungated = ungated
      } else {
        const seg: ToolSegment = { kind: 'tool', id, tool: toolName(m.meta, m.content), detail: m.meta?.detail, toolKind: m.meta?.kind, input: m.meta?.input, output: m.meta?.output, purpose: m.meta?.purpose, done: !!m.meta?.done, contentType: m.meta?.content_type, rawRef: m.meta?.raw_ref, truncated: m.meta?.truncated, originalLength: m.meta?.original_length, recoveryHints: m.meta?.recovery_hints, agentError: m.meta?.agent_error, ok: m.meta?.ok === false ? false : undefined, ungated: ungatedOf(m.meta) }
        toolIndex.set(id, seg)
        lastAssistant().segments.push(seg)
      }
    } else if (m.role === 'permission') {
      // A permission row carries its outcome in meta.resolved once the user
      // (or a trust rung) acts on it. If it's missing, the request is still
      // pending — persisted before the await — so render an actionable card
      // rather than falsely showing it approved. The card posts back by
      // approval_id/request_id, so prefer that for the segment id.
      //
      // Pass the outcome through verbatim: only ABSENCE means pending. Matching an
      // allowlist here silently dropped `trust`/`trust_reads`/`yolo` to undefined, so a
      // reloaded transcript re-armed live Allow/Deny buttons for a call that had already
      // run. approvalOutcome() owns interpreting the value, in one place, for both paths.
      const resolved = m.meta?.resolved || undefined
      ranHere = true
      // The risk and the radius are decoded, never cast: a row another build wrote can carry
      // a level or a shape this one cannot read, and that must be no claim at all.
      lastAssistant().segments.push({ kind: 'approval', id: m.meta?.approval_id || m.meta?.tool_call_id || `perm-${turns.length}`, tool: toolName(m.meta, m.content), input: m.meta?.input || m.meta?.tool_input, purpose: m.meta?.purpose, risk: approvalRiskOf(m.meta?.risk), blastRadius: blastRadiusOf(m.meta?.blast_radius), grantAgent: m.meta?.grant_agent, resolved })
    } else if (m.role === 'error') {
      // a failed turn (provider/model error) — surface it instead of a blank turn.
      const settings = (m.meta as { settings?: unknown } | undefined)?.settings
      lastAssistant().segments.push({ kind: 'error', text: turnErrorText(m.content), ...(typeof settings === 'string' && settings ? { settings } : {}) })
    } else if (m.role === 'notice') {
      lastAssistant().segments.push(noticeSegment(m.content))
    } else if (m.role === 'streaming') {
      // The answer being written RIGHT NOW — the gateway keeps it as ONE `streaming` entry,
      // grown in place until it settles. Skipping it cut off everything a turn had written
      // before a reload (measured on two reloads: the 170- and 209-char partials, gone from
      // answers of 6,580 and 3,857 chars until a second reload after the turn ended). It
      // renders as the answer's text; `livePartialOf` names it for the resume that continues
      // it. Not a visible-list slot: the backend counts user/assistant rows only.
      lastAssistant().segments.push({ kind: 'text', text: m.content })
      assistantTextSinceUser = true
    } else if (TURN_DISPATCH_ROLES.has(m.role)) {
      // A turn a loop's nudge, an automation or a subagent's report started. The row itself is not
      // rendered; the skills that joined its turn are. A turn before it that got no answer at all
      // keeps its own first.
      if (joinedSkills) lastAssistant()
      joinedSkills = joinedSkillsOf(m)
    }
    // other roles (queued/system): skip.
  }
  // A turn read before its answer began, or stopped before it said anything, still shows the
  // skills that joined it.
  if (joinedSkills) lastAssistant()
  // A finished session has nothing in flight: the native path persists tool calls
  // without ever flagging done, so any lingering pending card would spin forever.
  // Mark all tools done; if still running, leave only the very last one pending.
  if (!running) {
    for (const seg of toolIndex.values()) seg.done = true
  } else {
    const tools = [...toolIndex.values()]
    tools.slice(0, -1).forEach((seg) => { seg.done = true })
  }
  return turns
}

/** The roles `hydrateTurns` renders — every other row is invisible to the transcript. */
const TRANSCRIPT_ROLES = new Set(['user', 'assistant', 'streaming', 'tool', 'permission', 'error', 'notice'])

/** The text of the answer still being written when this history was read — the `streaming`
 *  entry, when it is the last row the transcript renders — or `null`. That entry paints the
 *  hydrated transcript's tail segment, so a resume can continue the live answer IN it instead
 *  of beside it. Behind a rendered row (a card appended while the answer was arriving) it is
 *  not the tail, and the live text lands after that row as a fresh segment, as it does live. */
export function livePartialOf(messages: HistMsg[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (TRANSCRIPT_ROLES.has(m.role)) return m.role === 'streaming' ? m.content : null
  }
  return null
}
