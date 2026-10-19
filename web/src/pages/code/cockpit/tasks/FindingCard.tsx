import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { type CodeFinding } from '../../../../lib/api'
import { FilesTouched } from '../FilesTouched'

// ── A finding in the cockpit ───────────────────────────────────────────────────────────────────
//
// One cycle's finding on a task: its summary and insight, the files it touched, and its evidence
// behind a toggle.

/** One cycle's finding: summary + key insight + touched-file chips, with the
 *  worker's EVIDENCE (test output / command results — the proof it didn't
 *  self-certify) behind a collapsed disclosure since it can be verbose. */
// Evidence may arrive as a plain string, or as a dict/array of checks (e.g.
// {py_compile: "…", directory_listing: "…"}) depending on how the worker recorded
// the cycle — render any shape as readable text instead of crashing on .trim().
function evidenceToText(ev: unknown): string {
  if (ev == null) return ''
  if (typeof ev === 'string') return ev.trim()
  if (Array.isArray(ev)) return ev.map((v) => evidenceToText(v)).filter(Boolean).join('\n')
  if (typeof ev === 'object') {
    return Object.entries(ev as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n').trim()
  }
  return String(ev)
}

export function FindingCard({ finding: f, ws }: { finding: CodeFinding; ws: string }) {
  const [showEvidence, setShowEvidence] = useState(false)
  const evidence = evidenceToText(f.evidence)
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container/50 p-2.5">
      <div data-type="caption" className="mb-1 flex items-center gap-1.5 text-on-surface-low">
        <span className="rounded-pill bg-surface-high px-1.5 tabular-nums">cycle {f.cycle}</span>
        {f.stage && <span className="rounded-pill bg-surface-high px-1.5">{f.stage}</span>}
      </div>
      {f.summary && <p data-type="body-s" className="text-on-surface-var">{f.summary}</p>}
      {f.key_insight && <p data-type="caption" className="mt-1 text-on-surface-low">→ {f.key_insight}</p>}
      <FilesTouched files={f.files_touched} ws={ws} />
      {evidence && (
        <div className="mt-1.5">
          <button type="button" onClick={() => setShowEvidence((v) => !v)} aria-expanded={showEvidence}
            aria-label={showEvidence ? 'Hide evidence' : 'Show evidence'}
            data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low hover:text-on-surface">
            {showEvidence ? <ChevronDown size={11} /> : <ChevronRight size={11} />} evidence
          </button>
          {showEvidence && (
            <pre data-type="caption" className="mt-1 max-h-48 overflow-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{evidence}</pre>
          )}
        </div>
      )}
    </div>
  )
}
