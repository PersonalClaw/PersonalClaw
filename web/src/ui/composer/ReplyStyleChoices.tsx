import { useId, useState, type ReactNode } from 'react'
import { ChoiceList, type Choice } from '../ChoiceList'

/** A reply style the section lists: the backend's `GET /api/chat/reply-styles` rows. `by` names the
 *  app that adds it (an app's style only), which the section shows beside its title. */
export type ReplyStyleOption = { id: string; title: string; description: string; by?: string }

/** A style's name as the section says it: an app's carries the app, `Brief (Acme)`. */
export const replyStyleName = (s: ReplyStyleOption) => (s.by ? `${s.title} (${s.by})` : s.title)

/** What the next turn carries for its voice, as the backend previews it (`reply_style.preview`). */
export type ReplyStylePreviewView = {
  applies: 'style' | 'persona' | 'none'
  style: string; persona: string
  instruction: string; first_turn_only: boolean; refused: string
}

/** A persona theme's id as a name: `retro-terminal` → "Retro Terminal". */
const personaName = (id: string) => id.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')

/** The line under the choices: which voice the next turn carries. Rendered as the backend sent it;
 *  nothing here composes an instruction. */
function NextReplyLine({ preview, title }: { preview: ReplyStylePreviewView; title: (id: string) => string }) {
  const said = preview.refused
    ? preview.refused
    : preview.applies === 'style'
      ? `Next reply: the ${title(preview.style)} style, on every turn.`
      : preview.applies === 'persona'
        ? `Next reply: the ${personaName(preview.persona)} voice, on a new chat’s first turn.`
        : 'Next reply: no style instruction is added.'
  return (
    <div className="flex flex-col gap-1">
      <span data-type="body-s">{said}</span>
      {preview.applies === 'style' && preview.persona && !preview.refused && (
        <span data-type="caption" className="text-on-surface-low">It replaces the {personaName(preview.persona)} voice while it is chosen.</span>
      )}
    </div>
  )
}

/** The exact text the next turn appends, on request: the reply style's section and the
 *  explanations' after it, as the backend previews them. Shown once, after both, and only when the
 *  turn appends something. */
export function ShowWhatIsSent({ instruction }: { instruction: string }) {
  const [shown, setShown] = useState(false)
  if (!instruction) return null
  return (
    <div className="flex flex-col gap-1">
      <button type="button" data-type="caption" className="self-start underline text-on-surface-low"
        aria-expanded={shown} onClick={() => setShown((v) => !v)}>
        {shown ? 'Hide what is sent' : 'Show what is sent'}
      </button>
      {shown && (
        <pre data-type="caption" className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-surface-container p-2 text-on-surface-low">{instruction.trim()}</pre>
      )}
    </div>
  )
}

/** Who chose the style, as "Now:" says it. */
const REPLY_STYLE_CHOSEN_BY: Record<string, string> = { conversation: 'for this chat', agent: 'from the agent', platform: 'your default' }

/** Reply-style choices: how the agent writes its replies in this conversation, Chat preferences'
 *  Reply style section.
 *
 *  `""` (Agent default) follows the bound agent's style, then the platform default; each other row is a
 *  style this conversation chooses for itself, `default` included (no style, even when the agent
 *  asks for one). The rows and their one-line descriptions come from the backend.
 *
 *  This component RESOLVES NOTHING. `effective` and `source` arrive decided by the backend, which
 *  owns the one statement of the order (`reply_style.REPLY_STYLE_PRECEDENCE`); a line computed
 *  here from `choice` and the agent's style would be a second statement of it.
 *
 *  "Now:" names the EFFECTIVE style and who chose it, so a change in how replies read can be
 *  traced to a control the owner can see. */
export function ReplyStyleChoices({ choice, effective, source, agentDefault, styles, preview, refused, onSelect, status }: {
  /** What THIS conversation states: a style id, or `''` to follow the agent. */
  choice: string
  /** The style the next turn resolves to (a style id). */
  effective: string
  /** Which scope decided, a member of the backend's precedence tuple, or `''` before a
   *  conversation exists to resolve against. Then "Now:" shows the CHOICE, not an effect. */
  source: string
  /** What the bound agent's definition states, for the reset row's hint. */
  agentDefault: string
  styles: ReplyStyleOption[]
  /** What the next turn carries for its voice, from the backend; absent before the chat exists. */
  preview?: ReplyStylePreviewView | null
  /** The server's sentence for the latest pick it refused, said under the choices. */
  refused?: string
  onSelect: (style: string) => void
  /** The section's own "Saved", from its host. */
  status?: ReactNode
}) {
  const headingId = useId()
  const title = (id: string) => {
    const s = styles.find((x) => x.id === id)
    return s ? replyStyleName(s) : id
  }
  const named = (id: string) => (id === 'default' ? 'No style' : title(id))
  // A style this chat chose that the server no longer lists (its app disabled or without its grant,
  // its file deleted). The pick is kept and the turn uses the next level down; the row says so and
  // cannot be picked again until the style is back. Only once the list has arrived.
  const gone = source !== '' && choice !== '' && styles.length > 0 && !styles.some((s) => s.id === choice)
  const now = source === ''
    ? (choice ? named(choice) : 'Agent default')
    : `${named(effective)}, ${REPLY_STYLE_CHOSEN_BY[source] ?? source}`
  const options: Choice[] = [
    { key: '', label: 'Agent default', hint: agentDefault ? `This agent asks for ${title(agentDefault)}.` : 'Follows this agent, then the platform default.' },
    ...styles.map((s) => ({ key: s.id, label: s.id === 'default' ? 'No style' : replyStyleName(s), hint: s.description })),
    ...(gone ? [{ key: choice, label: `${choice} — no longer available`, disabled: true,
      hint: 'This chat keeps the choice and uses the next level down until the style is back.' }] : []),
  ]
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-s">
      <div className="flex flex-wrap items-baseline justify-between gap-x-s">
        <h2 id={headingId} data-type="title-s" className="text-on-surface">Reply style</h2>
        <span className="relative flex items-center gap-s">
          {status}
          <span data-type="caption" className="text-on-surface-low">Now: {now}</span>
        </span>
      </div>
      <ChoiceList labelledBy={headingId} options={options} value={choice} onChange={onSelect} />
      {refused && <p role="alert" data-type="caption" className="text-on-surface-low">Not saved: {refused}</p>}
      {preview && <NextReplyLine preview={preview} title={title} />}
      <p data-type="caption" className="text-on-surface-low">
        Changes style only, never a fact, a caveat or a refusal. A choice here applies to this conversation and does not edit the agent.
      </p>
    </section>
  )
}
