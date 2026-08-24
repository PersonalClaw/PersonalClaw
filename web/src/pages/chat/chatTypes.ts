/** Chat conversation model — a turn is an ordered list of SEGMENTS so an
 *  assistant turn can interleave streamed text with tool cards and approval
 *  prompts, driven by the live WS events (tool_call → tool_result by id;
 *  approval → approval_resolved by id). */

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
  agentError?: AgentError  // PLATFORM-LEGIBILITY §2: coded WHAT/WHY/FIX envelope on a failed call
  ok?: boolean            // tool-call outcome — only present (false) when it FAILED, for color-coding
}

/** PLATFORM-LEGIBILITY §2: the structured error envelope carried on a failed
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
  risk?: 'safe' | 'caution' | 'destructive'  // effective per-invocation risk indicator
  // The settled outcome, as the backend persisted it. Typed as the raw wire `string`
  // (not the ApprovalResolution union) because a session persisted by another build
  // can carry an outcome this one doesn't know — approvalOutcome() maps the known set
  // explicitly and renders an unknown value honestly rather than as a denial.
  // Absent = still pending → the actionable card.
  resolved?: string
}

/** Coarse activity line — the native loop emits `activity_event {kind,text}`
 *  (e.g. "Thinking…") but NOT individual tool_call/tool_result frames. We
 *  surface these as a quiet inline
 *  line so native tool turns aren't blank. ACP turns get full ToolSegments
 *  instead, so we suppress activity lines once a turn has real tool cards. */
export interface ActivitySegment {
  kind: 'activity'; text: string; activityKind?: string
  // Which learning path produced a `learned` activity (LEARNING-VISIBILITY T2.2).
  // All three captures — the preference facet, the after-turn lesson review, and the
  // skill-ladder proposal pass — share `activityKind: 'learned'`, so this discriminator
  // is the only thing that can route a tap on the learned chip to the surface that can
  // approve or edit THAT artifact. Typed as the raw wire `string` (not a union) because
  // an older session, or a future emitter, legitimately arrives without it — see
  // `learnedSurface()`, which degrades an unrecognised value to a non-tappable chip
  // rather than guessing a surface.
  origin?: string
}

/** A turn-level error (the model/provider rejected the turn, e.g. a Bedrock
 *  ValidationException). Surfaced as a distinct red callout so a failed turn is
 *  never silently blank. Arrives live via the `chat_message` WS frame (role
 *  `error`) and is rehydrated from history on reload. */
export interface ErrorSegment { kind: 'error'; text: string }

export type Segment = TextSegment | ToolSegment | ApprovalSegment | ActivitySegment | ErrorSegment

/** One episodic memory surfaced into an assistant turn's prompt, resolvable from a
 *  `[Memory N]` citation the reply emits (MEMORY-GRAPH-AND-VAULT §5.4). `id` is the
 *  episode's stable record id (used to deep-link the memory studio); it may be null
 *  when the recall layer had no per-record id, in which case the chip degrades to a
 *  non-navigable label. */
export interface MemoryCitation { n: number; id: string | null; preview?: string }

/** One skill whose content actually reached this turn's prompt (LEARNING-VISIBILITY
 *  T2.1). Rides the assistant message's `meta.skills_used` — the same seam
 *  `memory_citations` uses — so the "used N skills" chip needs no second channel.
 *
 *  `state` is the allocator's load state, typed as the raw wire `string` rather than a
 *  union for the same reason `ApprovalSegment.resolved` is: a session persisted by
 *  another build can carry a state this one doesn't know. Only two ever arrive today —
 *  `admitted` (the skill's body loaded) and `reduced` (only a summary fit). A REFUSED
 *  skill is deliberately never in this list: it was NAMED to the agent but none of its
 *  content loaded, so counting it would overstate the turn. */
export interface SkillUsed { name: string; state: string; loaded_tokens: number }

/** The chip's own words. N counts every entry — `admitted` and `reduced` alike, because
 *  both put content in the prompt (a `reduced` skill loaded a summary, not nothing).
 *  Returns '' for an empty list so a caller can't render a truthful-looking "used 0
 *  skills" for a turn that loaded none: the backend omits the key entirely in that case,
 *  and the chip must be absent, not zeroed. */
export function skillsUsedLabel(skills: SkillUsed[]): string {
  const n = skills.length
  if (!n) return ''
  return `used ${n} skill${n === 1 ? '' : 's'}`
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

/** Stamp `origin` onto the activity segment `insertActivity` just created, given the
 *  arrays before (`prev`) and after (`next`) that call (LEARNING-VISIBILITY T2.2).
 *
 *  Exists so the ChatPage WS handler doesn't have to widen `insertActivity`'s signature (and
 *  re-baseline its K42/K44/K45 suite) just to carry one optional field. It identifies the new
 *  segment by REFERENCE, not by matching text: `insertActivity` returns `prev` untouched on
 *  both its early-outs (a turn with tool cards, an adjacent duplicate line), so the only
 *  activity segment present in `next` and absent from `prev` is the one it spliced in — a
 *  fresh object literal no previous render holds, which is what makes writing to it safe.
 *
 *  Returns `next` either way; a falsy origin is a no-op, which is the pre-T2.2 wire and every
 *  non-`learned` activity kind. */
export function stampActivityOrigin(prev: Segment[], next: Segment[], origin?: string): Segment[] {
  if (!origin || next === prev) return next
  const added = next.find((sg) => sg.kind === 'activity' && !prev.includes(sg))
  if (added) (added as ActivitySegment).origin = origin
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
 *  - `facet` → `upsert_facet` writes a typed facet to the vector store, and the veto branch
 *    writes a lesson instead; the Memory Studio owns both.
 *
 *  Returns null for an absent or unrecognised origin. That is the graceful-degrade
 *  contract, not an oversight: every message persisted before T2.2, and anything a future
 *  emitter adds, arrives without a mapping, and a chip that guessed a surface would send
 *  the user somewhere the artifact isn't. The chip still renders — it just isn't a link. */
export interface LearnedSurface { href: string; label: string }
export function learnedSurface(origin?: string | null): LearnedSurface | null {
  switch (origin) {
    case 'proposal':
      return { href: '#/skills?mode=proposals', label: 'Review in Skill proposals →' }
    case 'lesson':
      return { href: '#/settings/memory?tab=studio', label: 'Review lessons in Memory →' }
    case 'facet':
      return { href: '#/settings/memory?tab=studio', label: 'Manage in Memory →' }
    default:
      return null
  }
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  segments: Segment[]     // user turns are a single text segment
  ts?: string             // source message timestamp (for edit-resend by ts)
  // Episodic memory citations surfaced into THIS assistant turn (§5.4). The reply
  // cites facts inline as `[Memory N]`; the Markdown renderer resolves each token
  // against this list into a deep-link to the episode. Absent on turns with no
  // episodic recall (the vast majority) and on user turns.
  citations?: MemoryCitation[]
  // Skills whose content fed THIS assistant turn (T2.1) — the "used N skills" chip's
  // input. Rides the same meta seam as `citations`, so it is absent on the turns that
  // loaded no skill (and on every user turn) rather than an empty array.
  skillsUsed?: SkillUsed[]
  // paste blocks referenced by `[Paste #N]` markers in this turn's text, kept so
  // the bubble can render the markers as inspectable chips after send.
  pastes?: { seq: number; lines: number; content: string }[]
  // attachment file paths (uploads + @-mentions) sent WITH this turn, so the
  // sent user bubble shows them as chips the user can open/preview after send.
  files?: string[]
  // When the prompt was optimized before sending (via /optimize or the optimize
  // control), this holds the OPTIMIZED text the model actually received; the
  // turn's text segment keeps the ORIGINAL the user typed. The bubble shows the
  // original with the optimized in a collapsed, expandable section.
  optimized?: string
  // Regenerated answer variants for an ASSISTANT turn. When a reply is regenerated
  // the backend keeps the prior answer(s) and appends the new one, storing every
  // version on the message. The UI only needs how MANY there are (`variantCount`)
  // and which is active (`variantIdx`) to render the ‹n/N› switcher; the active
  // variant's body is already this turn's text segment. Navigation is server-driven
  // — the switcher posts switchVariant(idx) and the chat_variant_switch WS echo
  // swaps the text + index in place. `variantCount` ≤ 1 → no switcher.
  variantCount?: number
  variantIdx?: number
  // True rewind (CHAT-CRAFT S1): when this USER turn was edited-and-replayed, the
  // discarded tail(s) are retained here so the divider chip can show "N messages
  // kept in history" and the read-only disclosure can render them. Each snapshot's
  // `messages` begins with the edited turn's OLD content. Absent = never rewound.
  rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]
  // Branch mechanic (CHAT-CRAFT CC-7): the index of this turn's LAST message in the
  // BACKEND's visible user/assistant list — the coordinate `POST .../fork` and
  // `edit-resend` speak (`at_message_index`, inclusive). It is NOT the turn's array
  // position: hydrateTurns collapses native loop re-injections and merges consecutive
  // assistant messages into one turn, so on any tool-using transcript the two diverge
  // and drift further with every merge. Stamped by hydrateTurns; absent on turns built
  // live from WS frames (see branchIndexOf, which derives those).
  visibleIndex?: number
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
  costUsd?: number       // per-child cost in USD (on done, WF2WOR-8 C1.5)
  tokens?: number        // per-child total tokens (on done)
}

// ── activity-panel derivation (Index / Files / Links) — all client-side from turns ──
export interface IndexEntry { turnIndex: number; label: string }
export interface FileEntry { path: string; name: string }
export interface LinkEntry { url: string; label: string }
export interface ChatActivity { index: IndexEntry[]; files: FileEntry[]; links: LinkEntry[] }

// file-ish path: /a/b.ext, ~/a/b.ext, or workspace-relative a/b.ext (has an ext).
const ACT_FILE_RE = /(?:^|[\s(`'"])((?:~|\/)[\w./\-]+\.\w{1,8}|[\w./\-]+\/[\w./\-]+\.\w{1,8})/g
const ACT_URL_RE = /\bhttps?:\/\/[^\s)<>"'`\]]+/g
const baseNameOf = (p: string) => p.replace(/\/+$/, '').split('/').pop() || p
// git-diff artifacts that look like paths but aren't openable files.
const DIFF_NOISE = /^(?:[ab]\/|\/dev\/null$)/

/** Derive the activity-panel data from the conversation turns:
 *   - Index: each user turn → a jump anchor (preview label).
 *   - Files: file paths from tool inputs/outputs + paths mentioned in assistant
 *     text (deduped, first-seen order).
 *   - Links: http(s) URLs surfaced in assistant text (deduped). */
export function deriveActivity(turns: ChatTurn[]): ChatActivity {
  const index: IndexEntry[] = []
  const files = new Map<string, FileEntry>()
  const links = new Map<string, LinkEntry>()

  const addFile = (raw: string) => {
    let p = raw.trim().replace(/[).,;:]+$/, '')
    if (!p || DIFF_NOISE.test(p)) return            // skip a/ b/ /dev/null diff noise
    p = p.replace(/^[ab]\//, '')                    // defensive: strip a/ b/ if it slipped through
    if (!files.has(p)) files.set(p, { path: p, name: baseNameOf(p) })
  }

  turns.forEach((t, i) => {
    if (t.role === 'user') {
      // keep the FULL single-line text (CSS truncates visually) — don't slice the
      // string, or markdown rendering of the label could cut mid-syntax (`**bo`).
      const txt = turnText(t).replace(/\s+/g, ' ').trim()
      if (txt) index.push({ turnIndex: i, label: txt })
      return
    }
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
  return { index, files: [...files.values()], links: [...links.values()] }
}

export interface HistMsg { role: string; content: string; ts?: string; variants?: { content: string; ts?: string }[]; variant_idx?: number; rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]; meta?: { tool_call_id?: string; approval_id?: string; input?: string; tool_input?: string; purpose?: string; risk?: string; output?: string; done?: boolean; tool?: string; detail?: string; resolved?: string; content_type?: string; raw_ref?: string; truncated?: boolean; original_length?: number; recovery_hints?: string[]; agent_error?: AgentError; ok?: boolean; pastes?: { seq: number; lines: number; content: string }[]; files?: string[]; original?: string; memory_citations?: MemoryCitation[]; skills_used?: SkillUsed[] } }

/** Re-collapse a persisted user message: the stored content has paste markers
 *  expanded to full text (the model saw that), but meta.pastes lets us swap each
 *  block's content back to `[Paste #N]` so the bubble renders inspectable chips
 *  on reload (matching the live-send experience). */
function recollapsePastes(content: string, pastes: { seq: number; lines: number; content: string }[]): string {
  let out = content
  // longest content first so a block that contains another doesn't mis-replace.
  for (const p of [...pastes].sort((a, b) => b.content.length - a.content.length)) {
    if (p.content) out = out.split(p.content).join(markerForSeq(p.seq))
  }
  return out
}
const markerForSeq = (seq: number) => `[Paste #${seq}]`

/** Resolve a tool name: prefer meta.tool, else the turn content. Also strips any
 *  leading pictographic + space so sessions persisted before the status-sentinel
 *  removal (which prefixed tool content with a status glyph) still render clean. */
function toolName(meta: HistMsg['meta'], content: string): string {
  return (meta?.tool || content || 'tool').replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]+\s*/u, '').trim() || 'tool'
}

/** Build the turn/segment model from persisted history so a refreshed / revisited
 *  / streaming-done session renders IDENTICALLY to a live one — tool calls become
 *  ToolSegments (deduped by tool_call_id, call+result merged in place), permission
 *  rows become resolved ApprovalSegments, text stays text. A `tool`-role turn's
 *  name comes from meta.tool (or its content); see toolName.
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

  const lastAssistant = (): ChatTurn => {
    const t = turns[turns.length - 1]
    if (t && t.role === 'assistant') return t
    const nt = assistantTurn(); turns.push(nt); return nt
  }

  for (const m of messages) {
    if (m.role === 'user') {
      visible += 1
      const text = m.content.trim()
      if (text === lastUserText && !assistantTextSinceUser) continue  // loop re-injection
      // re-collapse expanded pastes → markers so chips render on reload.
      const pastes = m.meta?.pastes
      // An optimized turn persisted the OPTIMIZED text as content (the model saw
      // it); meta.original is what the user typed. Show the original as primary,
      // the optimized in the collapsed section — same as the live send.
      const original = m.meta?.original
      const primary = original ?? m.content
      const display = pastes?.length ? recollapsePastes(primary, pastes) : primary
      const files = Array.isArray(m.meta?.files) ? m.meta!.files : undefined
      const ut = userTurn(display, m.ts, pastes?.length ? pastes : undefined, files, original ? m.content : undefined)
      // Rewind tails retained on this user turn (CHAT-CRAFT S1) → drive the divider
      // chip + read-only disclosure. Tolerant: absent on pre-rewind sessions.
      if (Array.isArray(m.rewound) && m.rewound.length) ut.rewound = m.rewound
      ut.visibleIndex = visible
      turns.push(ut)
      lastUserText = text; assistantTextSinceUser = false
    } else if (m.role === 'assistant') {
      visible += 1
      const at = lastAssistant()
      // LAST wins: consecutive assistant messages merge into this one turn, so the
      // stamp walks forward to the final one — an inclusive branch then carries the
      // complete answer rather than truncating it mid-turn.
      at.visibleIndex = visible
      at.segments.push({ kind: 'text', text: m.content })
      // Episodic memory citations (§5.4) ride the assistant message's meta; carry them
      // onto the turn so the Markdown renderer can resolve `[Memory N]` tokens. Tolerant:
      // absent on turns with no episodic recall (almost all of them).
      if (Array.isArray(m.meta?.memory_citations) && m.meta!.memory_citations.length) {
        at.citations = m.meta!.memory_citations
      }
      // Skills used (T2.1) ride the same meta as the citations above, so they rehydrate on
      // the same terms: carried onto the turn when present, left absent otherwise. Tolerant
      // for the same reason — every message persisted before T2.1 lacks the key, and the
      // chip must simply not render for those rather than read as "used 0 skills".
      if (Array.isArray(m.meta?.skills_used) && m.meta!.skills_used.length) {
        at.skillsUsed = m.meta!.skills_used
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
      const id = m.meta?.tool_call_id || `auto-${turns.length}-${lastAssistant().segments.length}`
      const existing = toolIndex.get(id)
      if (existing) {  // result/completion update for an already-seen call → merge
        if (m.meta?.output != null) existing.output = m.meta.output
        if (m.meta?.done) existing.done = true
        if (m.meta?.input) existing.input = m.meta.input
        if (m.meta?.detail) existing.detail = m.meta.detail
        if (m.meta?.content_type) existing.contentType = m.meta.content_type
        if (m.meta?.raw_ref) existing.rawRef = m.meta.raw_ref
        if (m.meta?.truncated) { existing.truncated = true; existing.originalLength = m.meta.original_length }
        if (m.meta?.recovery_hints?.length) existing.recoveryHints = m.meta.recovery_hints
        if (m.meta?.agent_error) existing.agentError = m.meta.agent_error
        if (m.meta?.ok === false) existing.ok = false
      } else {
        const seg: ToolSegment = { kind: 'tool', id, tool: toolName(m.meta, m.content), detail: m.meta?.detail, input: m.meta?.input, output: m.meta?.output, purpose: m.meta?.purpose, done: !!m.meta?.done, contentType: m.meta?.content_type, rawRef: m.meta?.raw_ref, truncated: m.meta?.truncated, originalLength: m.meta?.original_length, recoveryHints: m.meta?.recovery_hints, agentError: m.meta?.agent_error, ok: m.meta?.ok === false ? false : undefined }
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
      lastAssistant().segments.push({ kind: 'approval', id: m.meta?.approval_id || m.meta?.tool_call_id || `perm-${turns.length}`, tool: toolName(m.meta, m.content), input: m.meta?.input || m.meta?.tool_input, purpose: m.meta?.purpose, risk: m.meta?.risk as ApprovalSegment['risk'], resolved })
    } else if (m.role === 'error') {
      // a failed turn (provider/model error) — surface it instead of a blank turn.
      lastAssistant().segments.push({ kind: 'error', text: m.content })
    }
    // other roles (chunk/system): skip.
  }
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
