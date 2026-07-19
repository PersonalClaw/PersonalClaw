import { useState } from 'react'
import { ChevronRight, Loader2, Check, Zap, Maximize2, Lightbulb, AlertTriangle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { spring } from '../../design/motion'
import type { ToolSegment } from './chatTypes'
import { renderToolInput, renderToolOutput, iconForTool, labelForTool, resolveInputObj } from './toolRenderers/registry'
import { requestToolResultFull } from './toolResultBridge'

/** Inline tool-call card. The STABLE tool name leads (with a per-kind icon) so
 *  cards stay scannable when many tools are in play; the agent's refined summary
 *  (the command, the file+range, …) follows as muted secondary detail. Expands
 *  to the full input + result. Pending while tool_result hasn't landed; ✓ when
 *  done. Subagent spawns render the same way. */
export function ToolCard({ seg }: { seg: ToolSegment }) {
  const [open, setOpen] = useState(false)
  const Icon = iconForTool(seg)
  const label = labelForTool(seg)
  const detail = secondaryDetail(seg)
  // Screen-reader label: the status + tool + detail are conveyed only by icon
  // and visual layout, so spell them out for the disclosure button.
  const status = seg.done ? (seg.ok === false ? 'failed' : 'completed') : 'running'
  const srLabel = `Tool ${label}${detail ? ` ${detail}` : ''} — ${status}${seg.auto ? ', auto-approved' : ''}. ${open ? 'Collapse' : 'Expand'} details`

  return (
    <div className="my-1 overflow-hidden border border-outline-variant/40 bg-surface-low/40"
      style={{ borderRadius: 'var(--radius-md)' }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
        aria-expanded={open} aria-label={srLabel}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-low/70">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 text-on-surface-low">
          <ChevronRight size={13} />
        </motion.span>
        <Icon size={14} className="shrink-0 text-primary" />
        {/* stable tool name — the identity you scan for */}
        <span className="shrink-0 text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>{label}</span>
        {/* refined summary (command / file) — muted, mono, gives way under pressure */}
        {detail && (
          <>
            <span className="shrink-0 text-on-surface-low/50 text-[0.8125rem]">·</span>
            <span className="min-w-0 flex-1 truncate font-mono text-on-surface-low text-[0.75rem]">{detail}</span>
          </>
        )}
        <span className={detail ? 'shrink-0' : 'flex-1'} />
        {seg.auto && <Zap size={11} className="shrink-0 text-on-surface-low" aria-hidden />}
        {seg.done
          ? seg.ok === false
            ? <AlertTriangle size={14} className="shrink-0" aria-hidden style={{ color: 'var(--color-danger)' }} />
            : <Check size={14} className="shrink-0" aria-hidden style={{ color: 'var(--color-ok)' }} />
          : <Loader2 size={13} className="shrink-0 animate-spin text-on-surface-low" aria-hidden />}
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ height: spring.spatialFast, opacity: { duration: 0.15 } }}>
            <div className="border-t border-outline-variant/30 px-3 py-2">
              {seg.purpose && <p className="mb-1.5 text-on-surface-var text-[0.75rem]">{seg.purpose}</p>}
              {/* Schema-driven / native-override INPUT (raw fallback inside). */}
              {renderToolInput(seg)}
              {/* Type-aware / native-override OUTPUT. */}
              {renderToolOutput(seg)}
              {seg.done && (seg.output == null || seg.output === '') && (
                <p className="text-on-surface-low text-[0.7rem]">No output.</p>
              )}
              {/* Truncation chip + "show full result" (rawRef → tool_result_get). */}
              {seg.truncated && (
                <div className="mt-1.5 flex items-center gap-2 text-on-surface-low text-[0.68rem]">
                  <span>
                    showing a projection{seg.originalLength ? ` of ${seg.originalLength.toLocaleString()} chars` : ''}
                  </span>
                  {seg.rawRef && (
                    <button type="button" onClick={() => requestToolResultFull(seg.rawRef!, seg.tool)}
                      className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var transition-colors hover:bg-surface-highest hover:text-on-surface">
                      <Maximize2 size={11} /> Show full result
                    </button>
                  )}
                </div>
              )}
              {/* TC5: concrete next-steps on a failed tool — the contract's recovery_hints,
                  surfaced so the user (and the reading agent) sees how to recover. */}
              {seg.recoveryHints && seg.recoveryHints.length > 0 && (
                <div className="mt-1.5 rounded-md bg-surface-container px-2.5 py-1.5">
                  <div className="mb-0.5 flex items-center gap-1 text-on-surface-low text-[0.68rem] uppercase tracking-wide">
                    <Lightbulb size={11} /> Next steps
                  </div>
                  <ul className="flex flex-col gap-0.5">
                    {seg.recoveryHints.map((h, i) => (
                      <li key={i} className="text-on-surface-var text-[0.75rem] leading-snug">{h}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** The secondary line: a short, HUMAN summary of the call — never raw JSON.
 *  Prefer the agent's refined detail; else summarize the structured input
 *  (the lone scalar value, or `key=val · key=val` for a few fields); else a
 *  short non-JSON scalar string; else nothing (the name stands alone). The
 *  hard rule: a `{...}` JSON blob must NEVER reach the header — that was the
 *  raw-JSON-in-tool-cards bug. */
function secondaryDetail(seg: ToolSegment): string {
  if (seg.detail) return seg.detail.replace(/\s+/g, ' ').trim()
  const obj = resolveInputObj(seg)
  if (obj) return summarizeInputObj(obj)
  const inp = (seg.input ?? '').trim()
  if (!inp || inp.startsWith('{') || inp.startsWith('[')) return ''  // never dump raw JSON
  const flat = inp.replace(/\s+/g, ' ').trim()
  return flat.length <= 80 ? flat : ''
}

/** Compact one-line summary of a structured input object for the card header.
 *  Lone scalar → its value; few scalars → `key=val · …`; otherwise empty (the
 *  expanded body shows the full fields). */
function summarizeInputObj(obj: Record<string, unknown>): string {
  const entries = Object.entries(obj).filter(([, v]) => v != null && v !== '')
  const scalars = entries.filter(([, v]) => typeof v !== 'object')
  if (scalars.length === 1) {
    const s = String(scalars[0][1]).replace(/\s+/g, ' ').trim()
    return s.length <= 80 ? s : s.slice(0, 77) + '…'
  }
  if (scalars.length >= 2 && scalars.length <= 4) {
    const parts = scalars.map(([k, v]) => `${k}=${String(v).replace(/\s+/g, ' ').trim()}`)
    const joined = parts.join(' · ')
    if (joined.length <= 80) return joined
    // too long joined → just the first scalar value, abbreviated
    const first = String(scalars[0][1]).replace(/\s+/g, ' ').trim()
    return first.length <= 80 ? first : first.slice(0, 77) + '…'
  }
  return ''
}
