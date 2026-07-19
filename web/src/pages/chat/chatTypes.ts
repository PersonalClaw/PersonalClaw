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
  ok?: boolean            // tool-call outcome — only present (false) when it FAILED, for color-coding
}

export interface ApprovalSegment {
  kind: 'approval'
  id: string              // approval id / request_id
  tool: string
  input?: string
  purpose?: string
  risk?: 'safe' | 'caution' | 'destructive'  // effective per-invocation risk indicator
  resolved?: 'approved' | 'rejected'
}

/** Coarse activity line — the native loop emits `activity_event {kind,text}`
 *  (e.g. "Thinking…") but NOT individual tool_call/tool_result frames. We
 *  surface these as a quiet inline
 *  line so native tool turns aren't blank. ACP turns get full ToolSegments
 *  instead, so we suppress activity lines once a turn has real tool cards. */
export interface ActivitySegment { kind: 'activity'; text: string; activityKind?: string }

/** A turn-level error (the model/provider rejected the turn, e.g. a Bedrock
 *  ValidationException). Surfaced as a distinct red callout so a failed turn is
 *  never silently blank. Arrives live via the `chat_message` WS frame (role
 *  `error`) and is rehydrated from history on reload. */
export interface ErrorSegment { kind: 'error'; text: string }

export type Segment = TextSegment | ToolSegment | ApprovalSegment | ActivitySegment | ErrorSegment

export interface ChatTurn {
  role: 'user' | 'assistant'
  segments: Segment[]     // user turns are a single text segment
  ts?: string             // source message timestamp (for edit-resend by ts)
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

export interface HistMsg { role: string; content: string; ts?: string; variants?: { content: string; ts?: string }[]; variant_idx?: number; meta?: { tool_call_id?: string; approval_id?: string; input?: string; tool_input?: string; purpose?: string; risk?: string; output?: string; done?: boolean; tool?: string; detail?: string; resolved?: string; content_type?: string; raw_ref?: string; truncated?: boolean; original_length?: number; recovery_hints?: string[]; ok?: boolean; pastes?: { seq: number; lines: number; content: string }[]; files?: string[]; original?: string } }

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
 *  one user bubble + one assistant turn carrying every tool card. */
export function hydrateTurns(messages: HistMsg[], running = false): ChatTurn[] {
  const turns: ChatTurn[] = []
  const toolIndex = new Map<string, ToolSegment>()  // tool_call_id → segment ref (merge results in place)
  let lastUserText = ''
  let assistantTextSinceUser = false  // distinguishes a re-injection from a real repeat

  const lastAssistant = (): ChatTurn => {
    const t = turns[turns.length - 1]
    if (t && t.role === 'assistant') return t
    const nt = assistantTurn(); turns.push(nt); return nt
  }

  for (const m of messages) {
    if (m.role === 'user') {
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
      turns.push(userTurn(display, m.ts, pastes?.length ? pastes : undefined, files, original ? m.content : undefined))
      lastUserText = text; assistantTextSinceUser = false
    } else if (m.role === 'assistant') {
      const at = lastAssistant()
      at.segments.push({ kind: 'text', text: m.content })
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
        if (m.meta?.ok === false) existing.ok = false
      } else {
        const seg: ToolSegment = { kind: 'tool', id, tool: toolName(m.meta, m.content), detail: m.meta?.detail, input: m.meta?.input, output: m.meta?.output, purpose: m.meta?.purpose, done: !!m.meta?.done, contentType: m.meta?.content_type, rawRef: m.meta?.raw_ref, truncated: m.meta?.truncated, originalLength: m.meta?.original_length, recoveryHints: m.meta?.recovery_hints, ok: m.meta?.ok === false ? false : undefined }
        toolIndex.set(id, seg)
        lastAssistant().segments.push(seg)
      }
    } else if (m.role === 'permission') {
      // A permission row carries its outcome in meta.resolved once the user
      // (or a trust rung) acts on it. If it's missing, the request is still
      // pending — persisted before the await — so render an actionable card
      // rather than falsely showing it approved. The card posts back by
      // approval_id/request_id, so prefer that for the segment id.
      const resolved = m.meta?.resolved === 'rejected' ? 'rejected'
        : m.meta?.resolved === 'approved' ? 'approved'
        : undefined
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
