import type { ReactNode } from 'react'
import type { AgentDef, AgentProvider, DiscoveredAgent, ModelItem, ApprovalMode, TaskMode, ReasoningEffort } from '../../lib/api'

/** Which controls a composer instance surfaces. Chat enables most; the goal
 *  composer enables only the shared core (send + optimize) and supplies its own
 *  chip-form for goal params. This is the single configurability seam. */
export interface ComposerControls {
  agent?: boolean
  model?: boolean
  approval?: boolean
  reasoning?: boolean      // only meaningful if the bound provider supports it
  attach?: boolean
  mic?: boolean
  optimize?: boolean
  slash?: boolean          // "/"-command autocomplete in the input (chat only)
}

export interface ComposerValue {
  agent: string            // bound agent label/id
  model: string
  approval: ApprovalMode
  taskMode: TaskMode       // orthogonal to approval — gates which tools + framing
  reasoning: ReasoningEffort
}

export interface ComposerData {
  agents: AgentDef[]
  providers: AgentProvider[]
  discovered: Record<string, DiscoveredAgent[]>
  models: ModelItem[]
}

export interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  streaming?: boolean
  /** Send button shows a circular spinner (no stop/send) — for a one-shot
   *  pre-send processing step like the goal composer's analyze pass. */
  processing?: boolean
  onStop?: () => void
  placeholder?: string
  controls?: ComposerControls
  data?: ComposerData
  /** current selections + setters (wired to the session by the host page) */
  selection?: ComposerValue
  onSelect?: (patch: Partial<ComposerValue>) => void
  onAttach?: (files: File[]) => void
  /** Open the saved-prompt palette. When set, the "+" toolbar menu offers "Saved
   *  prompts" — replacing the old floating chip that overlapped the composer edge. */
  onOpenPrompts?: () => void
  /** Extra items rendered at the bottom of the "+" menu (host-owned, session-scoped
   *  affordances like Auto-nudge). Receives `close` to dismiss the menu on action. */
  plusMenuExtra?: (close: () => void) => ReactNode
  onFocusChange?: (focused: boolean) => void
  /** workspace dir to scope the @-mention file search (optional) */
  mentionProject?: string
  /** notified when a file is picked via @-mention (host records the path for send) */
  onMentionFile?: (file: { path: string; name: string }) => void
  /** notified when a knowledge item is picked via @-mention (host records the id) */
  onMentionKnowledge?: (item: { id: string; name: string }) => void
  /** large-paste handler — host collapses it to an attachment + inline marker.
   *  Return true if the paste was consumed (Composer then suppresses default). */
  onLargePaste?: (text: string) => boolean
  /** Optimize the current draft via the prompt optimizer; host swaps `value`. */
  onOptimize?: () => void
  /** True while an optimize round-trip is in flight (spinner + disabled). */
  optimizing?: boolean
  /** Prior user messages, oldest→newest, for ↑/↓ history recall in an empty draft. */
  history?: string[]
  /** Transcribe a recorded audio blob to text (host inserts it into the draft).
   *  `opts.duplex` marks a hands-free capture so the host can ask the backend to
   *  filter the assistant's own speech back out. */
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>
  /** Report a voice-input failure (mic blocked / no STT) so the host can surface it. */
  onMicError?: (msg: string) => void
  /** Hands-free voice loop (MULTIMODAL-IO §4). Supplying it adds the hands-free
   *  toggle beside the mic: dictation accumulates in the draft and is sent only when
   *  a confirmation phrase lands. Without it the mic stays push-to-talk. */
  handsFree?: {
    confirmationPhrases: readonly string[]
    exitPhrases: readonly string[]
    /** True while a spoken reply plays — mutes the mic and drops its buffered audio. */
    speaking?: boolean
    /** True when the loop should mute during playback at all (voice.duplex_mute_enabled). */
    muteWhileSpeaking?: boolean
  }
  /** Called with the accumulated dictation once a confirmation phrase fires the
   *  turn. Required for the hands-free toggle to appear. */
  onHandsFreeSubmit?: (text: string) => void
  /** Screen-context sharing (MULTIMODAL-IO §5.2). The host owns the display stream
   *  (it also owns the header chip that must stay lit for its duration), so the
   *  composer only renders the control. Absent or `available:false` → no control at
   *  all, which is what the OFF-by-default config flag produces. */
  screenShare?: {
    /** `dashboard.screen_share_enabled` is on. */
    available: boolean
    /** A display stream is live now. */
    sharing: boolean
    /** Non-empty → render the control disabled carrying this as its reason. */
    disabledReason?: string
    onToggle: () => void
  }
  /** Natural voice (PT-7) — plainer, less machine-sounding prose, per conversation.
   *  Absent → no control (the goal composer has no conversation to scope one to).
   *  `effective`/`source` are the BACKEND's resolution; the composer displays them and
   *  never re-derives the order. */
  naturalVoice?: {
    choice: '' | 'on' | 'off'
    effective: boolean
    source: string
    agentDefault: boolean
    onSelect: (choice: '' | 'on' | 'off') => void
  }
  /** When true, the send button becomes a "queue" affordance — the host runs the
   *  message after the in-flight turn finishes instead of dropping it. */
  canQueue?: boolean
  /** 0–100 context-window usage for the bound session → ring on the model pill.
   *  `undefined` means the backend reported NO measurement — the pill then shows a
   *  plain dot rather than a fabricated 0%. A measured `0` renders a 0% ring. */
  contextPct?: number
  /** Minimum trimmed length before Send enables (default 1). The goal composer
   *  needs ≥20 chars to plan, so the button stays disabled (not a silent no-op)
   *  until the draft is long enough. */
  minChars?: number
  /** Monotonic counters — incrementing opens the model / agent / reasoning pill
   *  popover. Drives the "/model", "/agent" and "/effort" GUI-affordance slash commands. */
  openModelSignal?: number
  openAgentSignal?: number
  openReasoningSignal?: number
}
