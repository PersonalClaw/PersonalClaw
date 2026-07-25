import { useEffect, useState, type ReactNode } from 'react'
import { Check, Eye, Loader2, Sparkle, Workflow } from 'lucide-react'
import { fvs } from '../design/fontWeight'
import { Modal } from './Modal'
import { Markdown } from './Markdown'
import { api, type SkillItem, type WorkflowItem } from '../lib/api'

/** A selectable capability row (skill or workflow) with a checkbox + suggested chip.
 *  The single picker row shared by the goal-loop and code-loop plan reviews — the
 *  full-width row body toggles selection; an optional peek button (Eye) opens the
 *  {@link CapabilityPeekModal} to study the capability before committing it. */
export function CapRow({ id, name, description, checked, suggested, onToggle, onPeek, icon }: {
  id: string; name: string; description?: string; checked: boolean; suggested: boolean
  onToggle: () => void; onPeek?: () => void; icon: ReactNode
}) {
  return (
    <div key={id}
      className={`group flex w-full items-start gap-s rounded-lg px-m py-2.5 transition-colors ${checked ? 'bg-surface-high ring-1 ring-primary/40' : 'bg-surface-container hover:bg-surface-high'}`}>
      {/* the row body toggles selection; the peek button is separate (stopPropagation) */}
      <button type="button" onClick={onToggle} className="flex flex-1 min-w-0 items-start gap-s text-left">
        <span className="mt-0.5 shrink-0 inline-flex size-4 items-center justify-center rounded-sm border" style={{ borderColor: checked ? 'var(--color-primary)' : 'var(--color-outline-variant)', background: checked ? 'var(--color-primary)' : 'transparent' }}>
          {checked && <Check size={11} className="text-on-primary" />}
        </span>
        <span className="shrink-0 mt-0.5 text-on-surface-low">{icon}</span>
        <span className="flex-1 min-w-0">
          <span className="flex items-center gap-1.5">
            <span className="text-on-surface text-[0.8125rem] truncate" style={fvs(550)}>{name}</span>
            {suggested && <span className="shrink-0 rounded-pill px-1.5 h-4 inline-flex items-center text-[0.75rem] uppercase tracking-wide" style={{ background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)', color: 'var(--color-primary)' }}>suggested</span>}
          </span>
          {description && <span className="block text-on-surface-low text-[0.75rem] truncate">{description}</span>}
        </span>
      </button>
      {onPeek && (
        <button type="button" onClick={(e) => { e.stopPropagation(); onPeek() }} title="Preview — read the full skill/workflow"
          aria-label={`Preview ${name}`}
          className="shrink-0 mt-0.5 rounded-md p-1 text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-on-surface group-hover:opacity-100 focus:opacity-100">
          <Eye size={14} />
        </button>
      )}
    </div>
  )
}

/** Preview the full content of a suggested skill or workflow, so the user can
 *  study it before committing it to the loop. Skills fetch their SKILL.md body;
 *  workflows render their steps from the in-hand item. Paired with {@link CapRow}'s
 *  `onPeek`. */
export function CapabilityPeekModal({ peek, onClose }: {
  peek: { kind: 'skill' | 'workflow'; skill?: SkillItem; workflow?: WorkflowItem }
  onClose: () => void
}) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(peek.kind === 'skill')
  useEffect(() => {
    if (peek.kind !== 'skill' || !peek.skill) return
    let alive = true
    setLoading(true)
    api.skillContent(peek.skill.key)
      .then((c) => { if (alive) setContent(c) })
      .catch(() => { if (alive) setContent('') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [peek])

  const title = peek.kind === 'skill' ? peek.skill?.name : peek.workflow?.name
  const icon = peek.kind === 'skill' ? <Sparkle size={18} className="text-primary" /> : <Workflow size={18} className="text-primary" />
  return (
    <Modal title={title || 'Preview'} icon={icon} onClose={onClose}>
      <div className="max-h-[60vh] overflow-y-auto">
        {peek.kind === 'skill' ? (
          loading ? (
            <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem] py-4"><Loader2 size={14} className="animate-spin" /> Loading skill…</div>
          ) : content ? (
            <Markdown>{content}</Markdown>
          ) : (
            <p className="text-on-surface-low text-[0.8125rem]">{peek.skill?.description || 'No content available.'}</p>
          )
        ) : peek.workflow ? (
          <div className="flex flex-col gap-3">
            {peek.workflow.description && <p className="text-on-surface-var text-[0.8125rem]">{peek.workflow.description}</p>}
            <div className="flex flex-wrap gap-1.5 text-[0.75rem]">
              {peek.workflow.scope && <span className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low">scope: {peek.workflow.scope}</span>}
              {(peek.workflow.tags ?? []).map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low">{t}</span>)}
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">{peek.workflow.steps.length} step{peek.workflow.steps.length === 1 ? '' : 's'}</span>
              {peek.workflow.steps.map((st, i) => (
                <div key={st.id || i} className="rounded-lg bg-surface-container px-m py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.75rem] tabular-nums">{i + 1}</span>
                    <span className="text-on-surface text-[0.8125rem]" style={fvs(550)}>{st.title}</span>
                  </div>
                  {st.instruction && <p className="mt-1 pl-7 text-on-surface-var text-[0.8125rem] whitespace-pre-wrap">{st.instruction}</p>}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
