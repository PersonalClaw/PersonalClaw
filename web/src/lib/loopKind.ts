import { Code2, Target, Palette, Telescope, Repeat, type LucideIcon } from 'lucide-react'
import type { LoopKind } from './api'

/** Canonical identity (icon + label) for each loop kind — the ONE source so the
 *  in-chat progress card, the Loops list + cockpits, the Code section, the Projects
 *  "New" menu, and the nav all render the SAME glyph + word for the same kind.
 *  (These had drifted: `code` showed a branching GitBranch in the chat widget but
 *  Code2 everywhere else; `general` was Repeat in the card/list but RefreshCw in the
 *  Projects menu — so one kind read as two different features depending on surface.)
 *
 *  `noun` is the standalone label ("Code", "Goal Loop"); `short` is the terse menu
 *  label ("Code", "Goal"); `phrase` builds the cockpit-link verb ("Open in Code"). */
export interface LoopKindMeta { icon: LucideIcon; noun: string; short: string }

const LOOP_KIND_META: Record<LoopKind, LoopKindMeta> = {
  code: { icon: Code2, noun: 'Code', short: 'Code' },
  goal: { icon: Target, noun: 'Goal Loop', short: 'Goal' },
  design: { icon: Palette, noun: 'Design', short: 'Design' },
  research: { icon: Telescope, noun: 'Research', short: 'Research' },
  general: { icon: Repeat, noun: 'Loop', short: 'Loop' },
}

/** Identity for a loop kind; falls back to the generic loop identity for an unknown
 *  or missing kind (never blanks — a future kind still renders a glyph + word). */
export function loopKindMeta(kind: string | undefined): LoopKindMeta {
  return (kind && LOOP_KIND_META[kind as LoopKind]) || LOOP_KIND_META.general
}
