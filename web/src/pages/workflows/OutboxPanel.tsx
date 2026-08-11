import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { FileDiff, Inbox, Package, TriangleAlert, Upload } from 'lucide-react'
import { SidePanel } from '../../ui/SidePanel'
import { Segmented } from '../../ui/Segmented'
import { Skeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { QuietButton } from '../../ui/QuietButton'
import { Button } from '../../ui/Button'
import { ContentSurface } from '../../ui/content/ContentSurface'
import { resolveContentType } from '../../ui/content/contentTypes'
import {
  api,
  ApiError,
  type Artifact,
  type WorkflowDropStatus,
  type WorkflowOutboxEntry,
} from '../../lib/api'

// The artifact compare surface, REUSED rather than re-derived: it already owns the per-kind
// comparison rule (binary → side by side, else a real text diff) and the label discipline the
// versions route needs. A second diff renderer here would be a second set of those decisions, and
// the two would disagree the first time either changed.
const ArtifactCompare = lazy(() =>
  import('../artifacts/ArtifactCompare').then((m) => ({ default: m.ArtifactCompare })),
)

/** The run cockpit's artifact panel: the §2.5 outbox, structured version diffs, and multi-view
 *  output tabs (WORK-CONTAINERS §2.2d / R17).
 *
 *  Three questions in one place, because they are asked in sequence: WHAT did this run publish,
 *  WHAT CHANGED in the latest revision, and (the other direction) what can I hand the run.
 *
 *  **Every view rides the `contentTypes` registry.** The Rendered/Source split comes from the
 *  resolved type's own preview + edit capabilities via <ContentSurface>, and Compare defers to
 *  <ArtifactCompare>, which resolves the same registry. So a newly registered artifact kind previews,
 *  reads, and diffs here with no edit to this file — the plan's stated reason for choosing that
 *  registry as the extension seam instead of a local kind switch.
 *
 *  **Read-only on purpose.** A published artifact is a record of what a run produced; editing it
 *  here would silently rewrite history the version diff is the evidence for. The artifact's own page
 *  is where a human edits it, with the snapshot machinery that implies. */
export function OutboxPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [files, setFiles] = useState<WorkflowOutboxEntry[] | null>(null)
  const [drop, setDrop] = useState<WorkflowDropStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string>('')
  const [tab, setTab] = useState<'rendered' | 'source' | 'compare'>('rendered')
  const [detail, setDetail] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [detailError, setDetailError] = useState('')
  const [dropBusy, setDropBusy] = useState(false)
  const [dropNote, setDropNote] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    // Both halves load together: the drop status decides whether the inbound affordance renders at
    // all, and a panel that showed the outbox first and then grew an upload row would move under the
    // cursor of someone already reading it.
    Promise.all([api.workflowRunOutbox(runId), api.workflowRunDropStatus(runId)])
      .then(([out, st]) => {
        if (!live) return
        setFiles(out.files)
        setDrop(st)
      })
      .catch((e) => {
        if (!live) return
        if (e instanceof ApiError && e.status === 404) {
          setError('This run could not be found. It may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not read this run’s artifacts.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  // The artifact body + its version list load only when one is actually selected — most runs
  // publish something nobody opens, and fetching every body to render a list would cost the
  // gateway a read per row for a panel that shows names.
  useEffect(() => {
    if (!selected) { setDetail(null); setVersions([]); return }
    let live = true
    setDetailError('')
    Promise.all([api.artifact(selected), api.artifactVersions(selected)])
      .then(([art, v]) => { if (live) { setDetail(art); setVersions(v.versions ?? []) } })
      .catch((e) => {
        if (live) setDetailError(e instanceof Error ? e.message : 'Could not load this artifact.')
      })
    return () => { live = false }
  }, [selected])

  const ctype = useMemo(
    () => (detail ? resolveContentType({ kind: detail.kind }) : null),
    [detail],
  )

  async function sendFiles(picked: File[]) {
    if (!picked.length) return
    setDropBusy(true)
    setDropNote('')
    try {
      // First attempt WITHOUT confirm, so the gate answers with what it would accept rather than the
      // UI guessing. An auto-accepted MIME lands on this call; anything else comes back 428 and the
      // operator sees the name and size before approving.
      const st = await api.workflowRunDrop(runId, picked)
      setDrop(st)
      setDropNote(`Accepted ${picked.length === 1 ? picked[0].name : `${picked.length} files`}.`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 428) {
        const names = picked.map((f) => f.name).join(', ')
        const total = picked.reduce((n, f) => n + f.size, 0)
        const { confirm } = await import('../../ui/dialog')
        const ok = await confirm({
          title: 'Hand these files to the run?',
          body: `${names} (${fmtBytes(total)}). The run can read them but not change them.`,
          confirmLabel: 'Approve',
        })
        if (!ok) { setDropNote('Nothing was handed over.'); setDropBusy(false); return }
        try {
          setDrop(await api.workflowRunDrop(runId, picked, true))
          setDropNote(`Accepted ${picked.length === 1 ? picked[0].name : `${picked.length} files`}.`)
        } catch (inner) {
          setDropNote(inner instanceof Error ? inner.message : 'The drop failed.')
        }
      } else {
        setDropNote(e instanceof Error ? e.message : 'The drop failed.')
      }
    } finally {
      setDropBusy(false)
    }
  }

  return (
    <SidePanel
      fillHeight
      title="Artifacts"
      icon={<Package size={18} />}
      onClose={onClose}
    >
      {loading ? (
        <div className="flex flex-col gap-s p-m"><Skeleton /><Skeleton /><Skeleton /></div>
      ) : error ? (
        <div className="p-m"><InlineError icon multiline>{error}</InlineError></div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-m p-m">
          <section className="flex flex-col gap-xs">
            <h3 className="font-medium text-[0.8125rem]">Published by this run</h3>
            {!files?.length ? (
              <p className="text-on-surface-low text-[0.8125rem]">
                Nothing published yet. A node that declares <code>publish:</code> registers its
                output here as an artifact.
              </p>
            ) : (
              <ul className="flex flex-col gap-2xs">
                {files.map((f) => (
                  <li key={f.slug}>
                    <Button
                      variant="ghost"
                      size="xs"
                      shape="squircle"
                      onClick={() => setSelected(f.slug === selected ? '' : f.slug)}
                      ariaPressed={f.slug === selected}
                      className={`w-full justify-start gap-2 px-2${f.slug === selected ? ' bg-surface-high' : ''}`}
                    >
                      <span className="min-w-0 flex-1 truncate">{f.artifact || f.slug}</span>
                      {/* `noop` is a real outcome, not a failure: a converged refinement round
                          published nothing new, and hiding that makes the artifact look abandoned
                          by the run that owns it. */}
                      <span className="shrink-0 text-on-surface-low text-[0.6875rem]">
                        {f.action === 'noop' ? 'unchanged' : f.action}
                      </span>
                      {!f.self_contained && (
                        <span
                          className="inline-flex shrink-0 items-center gap-1 text-warning text-[0.6875rem]"
                          title="A referenced local file could not be copied in, so this version depends on the workspace still existing."
                        >
                          <TriangleAlert size={12} aria-hidden="true" />
                          not self-contained
                        </span>
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {selected && (
            <section className="flex min-h-0 flex-1 flex-col gap-xs">
              {detailError ? (
                <InlineError icon multiline>{detailError}</InlineError>
              ) : !detail || !ctype ? (
                <Skeleton />
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Segmented
                      ariaLabel="Artifact view"
                      value={tab}
                      onChange={(v) => setTab(v as 'rendered' | 'source' | 'compare')}
                      options={[
                        { key: 'rendered', label: 'Rendered' },
                        { key: 'source', label: 'Source' },
                        // Offered only with something to compare against — a Compare tab over one
                        // version is a tab that can only disappoint.
                        ...(versions.length > 1
                          ? [{ key: 'compare', label: 'Compare' }]
                          : []),
                      ]}
                    />
                    {files?.find((f) => f.slug === selected)?.change_note && (
                      <span className="min-w-0 truncate text-on-surface-low text-[0.75rem]">
                        {files.find((f) => f.slug === selected)?.change_note}
                      </span>
                    )}
                  </div>
                  <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline/40">
                    {tab === 'compare' ? (
                      <Suspense fallback={<Skeleton />}>
                        <ArtifactCompare art={detail} versions={versions} />
                      </Suspense>
                    ) : (
                      <ContentSurface
                        type={ctype}
                        content={detail.content ?? ''}
                        title={detail.name || selected}
                        docId={selected}
                        readOnly
                        // 'preview' is the type's own renderer, 'edit' its Monaco source view — the
                        // registry supplies both, so Rendered/Source is one declaration, not two
                        // renderers written here.
                        initialView={tab === 'source' ? 'edit' : 'preview'}
                        key={tab}
                      />
                    )}
                  </div>
                </>
              )}
            </section>
          )}

          <section className="flex flex-col gap-xs border-t border-outline/40 pt-m">
            <h3 className="flex items-center gap-2 font-medium text-[0.8125rem]">
              <Inbox size={14} aria-hidden="true" />
              Hand files to this run
            </h3>
            {!drop?.enabled ? (
              <p className="text-on-surface-low text-[0.8125rem]">
                {drop?.reason || 'This run does not accept files.'}
              </p>
            ) : (
              <>
                {/* The real file picker below is the control; this button only forwards to it. The
                    picker keeps its own accessible name and stays keyboard-reachable, because this
                    is the one affordance in the panel that must work without a mouse. */}
                <QuietButton
                  onClick={() => fileInputRef.current?.click()}
                  title="Choose files to hand to this run"
                >
                  <Upload size={13} /> {dropBusy ? 'Handing over…' : 'Choose files'}
                </QuietButton>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  disabled={dropBusy}
                  aria-label="Choose files to hand to this run"
                  className="sr-only"
                  onChange={(e) => {
                    const picked = Array.from(e.target.files ?? [])
                    e.target.value = ''
                    void sendFiles(picked)
                  }}
                />
                {dropNote && <p className="text-on-surface-low text-[0.75rem]">{dropNote}</p>}
                {drop.files.length > 0 && (
                  <ul className="flex flex-col gap-2xs">
                    {drop.files.map((f) => (
                      <li
                        key={f.filename}
                        className="flex items-center gap-2 text-on-surface-low text-[0.75rem]"
                      >
                        <FileDiff size={12} aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                        <span className="shrink-0 tabular-nums">{fmtBytes(f.size)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </SidePanel>
  )
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
