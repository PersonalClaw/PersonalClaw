import { FileText, ShieldQuestion, Undo2, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type AutonomyLadder, type AutonomyType } from './api'
import { useCachedData } from './useCachedData'

/** The earned-autonomy ladder, frontend side (AUTONOMY-GUARDRAILS §6.1).
 *
 *  The WORDING of a rung is the server's (`rung_meta` on the wire) so a chip, the ladder
 *  panel and the inbox proposal that offered a promotion all say the same thing. What lives
 *  here is presentation the backend has no business owning: an icon and a tone per rung.
 *
 *  The tone scale is INTENSITY, not health. A trigger row already spends warn/danger on
 *  whether the automation is failing (`triggerHealthMeta`), so reusing those here would put
 *  two unrelated meanings on one colour in one row: "runs on its own" is not a warning, it
 *  is the top of a permission scale, and it reads as more prominent than "drafts only"
 *  without claiming anything is wrong. */
export const RUNG_PRESENTATION: Record<string, { icon: LucideIcon; tone: string }> = {
  draft_only: { icon: FileText, tone: 'var(--color-on-surface-low)' },
  one_tap: { icon: ShieldQuestion, tone: 'var(--color-on-surface-var)' },
  auto_with_undo: { icon: Undo2, tone: 'var(--color-info)' },
  autonomous: { icon: Zap, tone: 'var(--color-primary)' },
}

export interface RungMeta { key: string; label: string; hint: string; icon: LucideIcon; tone: string }

/** Humanize a rung key for the case where the server's label list is missing.
 *
 *  Deliberately NOT a second copy of the wording — `auto_with_undo` becomes "auto with
 *  undo", which is legibly the raw key rather than a competing phrase that could drift from
 *  "runs with undo". */
function humanizeRung(key: string): string {
  return key ? key.replace(/_/g, ' ') : 'unknown'
}

/** Everything needed to render one rung: the server's words + this file's presentation. */
export function rungMeta(key: string, ladder: AutonomyLadder | null): RungMeta {
  const wire = ladder?.rung_meta?.find((r) => r.key === key)
  const pres = RUNG_PRESENTATION[key] ?? { icon: Zap, tone: 'var(--color-on-surface-low)' }
  return { key, label: wire?.label ?? humanizeRung(key), hint: wire?.hint ?? '', ...pres }
}

/** The one-glance answer for a chip's tooltip: what the rung does + where it came from.
 *
 *  Both halves, always. The rung alone ("runs on its own") says what happens but not why it
 *  is allowed to, and `authority` alone reads as trivia — together they are the answer to
 *  "why is this allowed to run by itself?", which is what the chip exists for. */
export function rungReason(t: AutonomyType, ladder: AutonomyLadder | null): string {
  const meta = rungMeta(t.resolved_rung, ladder)
  const hint = meta.hint ? `${meta.hint} ` : ''
  return `${t.key} — ${hint}${t.authority}`
}

/** provider name → the action type governing it. A dispatch surface (a trigger row) holds a
 *  provider name and nothing else, exactly like the backend seams; the mapping lives on the
 *  declaration and travels on the wire, so the UI never guesses which type owns a provider. */
export function providerRungIndex(ladder: AutonomyLadder | null): Map<string, AutonomyType> {
  const index = new Map<string, AutonomyType>()
  for (const t of ladder?.types ?? []) for (const p of t.providers) index.set(p, t)
  return index
}

/** The ladder, cached. `persist: true` — declarations and grants change on a click, not on a
 *  clock.
 *
 *  Consumers split on purpose: the Settings panel OWNS this data and renders `LoadError`
 *  when the read fails, while a trigger row shows no chip at all. A row's own content loaded
 *  fine, and the honest degradation for a missing annotation is absence — the one thing it
 *  must never do is fall back to a rung, because "runs on its own" is a claim about what
 *  your automation may do unattended. */
export function useAutonomyLadder() {
  const { data, error, refresh } = useCachedData('autonomy:ladder', () => api.autonomyLadder(), { persist: true })
  return { ladder: data ?? null, error, refresh }
}
