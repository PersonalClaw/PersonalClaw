import type { ReactNode } from 'react'
import { Blocks, Coins, FolderKanban, GitBranch, MessageCircleQuestion, type LucideIcon } from 'lucide-react'
import { ScreenShareChip } from '../../ui/ScreenShareChip'
import { startedByLabel, startedByTitle } from './StartedByApp'

/** The chat header's context line (`TopBar.below`): what this conversation IS — whose permissions
 *  its turns run under, what it is scoped to, where it came from, what it is investigating, what it
 *  has cost — under the title instead of beside it.
 *
 *  🔴 THESE CHIPS USED TO SHARE THE TITLE'S ROW, AND IN THAT ROW NOTHING COULD HOLD. Each was
 *  `shrink-0` and none had a bound, so the ONE thing in the row that could shrink was the chat's own
 *  title. Measured on `0b487d9c7`, a branched chat with a cost chip: "Branched from <parent>" painted
 *  from x=464 to x=1312 at 1440px — under every control of the cluster and the shell corner — "Copy
 *  chat link" landed past the viewport, and the title was 0-8px wide at every width. A chip on its
 *  own line can only wrap; so here each one also truncates to the line, with its whole sentence in
 *  its tooltip and, when it is a control, in its accessible name.
 */

export interface ChatContext {
  /** Live while a screen is being shared into this chat; its chip is also the off switch. */
  screenShare?: { onStop: () => void } | null
  /** The app that started this conversation, by the name install consent showed — '' for yours. */
  startedBy?: string
  /** The project the chat is scoped to. */
  project?: { name: string; open: () => void } | null
  /** The conversation this one was branched from. `title: ''` = that origin was deleted. */
  branchedFrom?: { title: string; open: () => void } | null
  /** What this chat was opened to investigate (plan 60). No `open` when the source left no link
   *  back — then it is a label, never a button that does nothing. */
  investigate?: { title: string; open?: () => void } | null
  /** What it has cost so far (CATO-7). `priced: false` = the total mixes a model with no price. */
  cost?: { cost: number; tokens: number; priced: boolean } | null
}

/** Compact token count for the cost chip: 940 → "940", 46_000 → "46k", 1_200_000 → "1.2M". */
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

/** One fact about the conversation. A pill that can shrink to the line and truncate, with the full
 *  sentence in `title` — and in the accessible name when it is a control — so a long branch or
 *  project name can neither push the line nor be lost to it. Capped at 24rem even on a wide line:
 *  one parent title ran 780px at 1440 and pushed the cost onto a line of its own. `h-6` holds
 *  WCAG 2.5.8's 24px target for the ones you can press. */
function ContextChip({ icon: Icon, accent, label, title, onClick }: {
  icon: LucideIcon
  /** Coral glyph for a chip that leads somewhere (the header's existing chips' idiom). */
  accent?: boolean
  label: string
  title: string
  onClick?: () => void
}) {
  const skin = 'inline-flex h-6 min-w-0 max-w-[min(100%,24rem)] items-center gap-1 rounded-pill bg-surface-high px-2 text-on-surface-var'
  const body = (
    <>
      <Icon size={12} aria-hidden className={`shrink-0 ${accent ? 'text-primary' : 'text-on-surface-low'}`} />
      <span className="min-w-0 truncate">{label}</span>
    </>
  )
  if (!onClick) return <span data-type="caption" className={skin} title={title}>{body}</span>
  return (
    <button type="button" data-type="caption" onClick={onClick} title={title} aria-label={title}
      className={`${skin} transition-colors hover:text-on-surface`}>
      {body}
    </button>
  )
}

/** The context line's chips, in reading order — EMPTY when the conversation has nothing to say, so
 *  the caller leaves `TopBar.below` unset and the header keeps its single row. */
export function chatContextChips(c: ChatContext): ReactNode[] {
  const chips: ReactNode[] = []
  // Screen sharing (MI-4) first, and in the header at all, because an indicator you can scroll
  // away from is not an indicator; mounted off the LIVE stream, so the browser's own stop clears it.
  if (c.screenShare) chips.push(<ScreenShareChip key="screen" onStop={c.screenShare.onStop} />)
  // Whose permissions a turn here runs under — the one fact on this line that changes what
  // sending a message DOES. The copy is `StartedByApp`'s, the same words as the history row.
  if (c.startedBy) {
    chips.push(<ContextChip key="app" icon={Blocks} label={startedByLabel(c.startedBy)} title={startedByTitle(c.startedBy)} />)
  }
  if (c.project) {
    chips.push(<ContextChip key="project" icon={FolderKanban} accent label={c.project.name}
      title={`Scoped to project: ${c.project.name} — open the project`} onClick={c.project.open} />)
  }
  // Branch lineage (CC-7), read from the PERSISTED `forked_from`, so it names the parent's CURRENT
  // title. A deleted origin has nothing to open, so it degrades to a plain label.
  if (c.branchedFrom) {
    chips.push(c.branchedFrom.title
      ? <ContextChip key="branch" icon={GitBranch} accent label={`Branched from ${c.branchedFrom.title}`}
          title={`Branched from "${c.branchedFrom.title}" — open the original`} onClick={c.branchedFrom.open} />
      : <ContextChip key="branch" icon={GitBranch} label="Branched from a deleted chat"
          title="This chat was branched from a conversation that no longer exists" />)
  }
  if (c.investigate?.title) {
    const { title, open } = c.investigate
    chips.push(<ContextChip key="investigate" icon={MessageCircleQuestion} accent={!!open} label={title}
      title={open ? `Investigating: ${title} — open the source` : `Investigating: ${title}`} onClick={open} />)
  }
  // "unpriced" when the total mixes a model with no price row — honest, never a
  // confidently-complete $0.00. A reading, not a destination, so its glyph is not coral
  // (DESIGN.md's One Voice Rule).
  if (c.cost) {
    const money = c.cost.priced ? `$${c.cost.cost.toFixed(c.cost.cost < 1 ? 4 : 2)}` : 'unpriced'
    chips.push(<ContextChip key="cost" icon={Coins} label={`${money} · ${fmtTokens(c.cost.tokens)} tokens`}
      title={c.cost.priced ? 'What this conversation has cost so far' : 'Cost so far — includes a model with no price row, so this is a partial total'} />)
  }
  return chips
}
