import { useEffect, useState } from 'react'
import { Zap, FileText, ChevronRight, Trash2, ArrowLeft, Pencil, Save, X, ShieldCheck, ShieldAlert, ShieldQuestion, GraduationCap } from 'lucide-react'
import hljs from 'highlight.js/lib/common'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { LoadError, Skeleton } from '../../ui/ListScaffold'
import { confirmDelete } from '../../ui/dialog'
import { TextArea, FieldError } from '../../ui/forms'
import { FeedbackThumbs } from '../../ui/FeedbackThumbs'
import { useQuery, invalidateKeys } from '../../lib/data'
import { api, type SkillItem, type SkillFile, type SkillIntegrity } from '../../lib/api'
import { SOURCE_TONE, noBaselineReason, provenanceMeta } from './skillMeta'
import { toneChipSkin } from '../../design/accent'
import { reportingWrite } from '../../app/reportingWrite'

/** Installed-skill inspector for the SidePanel: metadata + the skill's real file
 *  list, each openable to read its content (SKILL.md rendered as markdown,
 *  other files as highlighted code). Edit (SKILL.md) + Delete are offered for
 *  local/installed skills; bundled ones are protected. */
export function SkillInspector({ skill, onDeleted, onSaved }: { skill: SkillItem; onDeleted: () => void; onSaved?: () => void }) {
  const [openFile, setOpenFile] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const tone = SOURCE_TONE[skill.source] ?? 'var(--color-on-surface-low)'
  const prov = provenanceMeta(skill.provenance)
  // `source`, NOT `provenance`: a taught skill is a `local` skill and is exactly as editable
  // as a hand-placed one. Reading provenance here instead would lock the user out of editing
  // the skill their own session just taught (#576).
  const editable = skill.source !== 'bundled'

  const { data: files } = useQuery<SkillFile[]>(`skill:files:${skill.name}`, () => api.skillFiles(skill.name).then((d) => d.files ?? []).catch(() => []), { persist: true })

  // Reset the sub-views when switching to a different skill.
  useEffect(() => { setOpenFile(null); setEditing(false) }, [skill.name])

  async function del() {
    if (!(await confirmDelete('skill', skill.name, { body: 'This removes it from disk. This cannot be undone.' }))) return
    // The dialog above promises "This removes it from disk. This cannot be undone." — the
    // strongest consent this app asks for. Swallowing the failure after that left the panel
    // open, the skill present, and nothing said: the only reasonable read is that the click
    // missed. `onDeleted()` stays GATED, per reportingWrite's own rule — closing the inspector
    // on a failed delete would claim the removal it did not make.
    if (!(await reportingWrite(`delete the skill "${skill.name}"`, () => api.deleteSkill(skill.name)))) return
    onDeleted()
  }

  if (editing) return <SkillEditor name={skill.name} onBack={() => setEditing(false)} onSaved={() => { setEditing(false); onSaved?.() }} />
  if (openFile) return <FileView name={skill.name} path={openFile} onBack={() => setOpenFile(null)} />

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        {/* Same registry, same defect, one strength up: 16% measures 3.52 for coral by the family's
            own table. Its `always loaded` sibling keeps its raw warn tint deliberately — semantic
            tones clear AA at 14-16% and have no `<tone>-container` to pair with. */}
        <span className="rounded-pill px-m h-7 inline-flex items-center text-[0.8125rem]" style={toneChipSkin(tone, 16)}>{skill.source}</span>
        <span className="text-on-surface-low text-[0.8125rem]">{skill.type}</span>
        {/* The tier chip above says WHERE this skill lives; this says how it got there (issue 576).
            The inspector is the surface a reviewer opens to decide whether to keep what a
            session taught, so it carries the sentence and not just the word the row shows. */}
        {prov && <span data-type="label-s" className={`inline-flex items-center gap-1.5 ${prov.tone}`} title={prov.title}><GraduationCap size={13} /> {prov.label}</span>}
        {skill.always && <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}><Zap size={13} /> always loaded</span>}
        {/* A synthesized skill IS an AI judgment — the extractor decided this procedure was
            worth keeping — so it earns thumbs, and they are the only way the synthesizer can
            ever be attributed: 👎s here are what let `skills.surfacing` stop surfacing a
            persistently-wrong auto skill (issue 1783). The inspector, not the list row: the row is
            one big click target that opens this panel, so buttons inside it fight the row.
            Rendered only when the server stamped `feedback_producer`, which it does for
            `auto` provenance alone — a taught skill is the user's own call. */}
        {skill.feedback_producer && (
          <FeedbackThumbs targetKind="synthesized_skill" targetId={skill.key}
            producer={skill.feedback_producer}
            snapshot={{ description: (skill.description ?? '').slice(0, 200) }}
            className="ml-auto" />
        )}
      </div>

      <p className="text-on-surface text-[0.9375rem] leading-relaxed">{skill.description}</p>

      {skill.loaded_by_agents.length > 0 && (
        <Section label="Used by">
          <div className="flex flex-wrap gap-1.5">{skill.loaded_by_agents.map((a) => <span key={a} className="rounded-pill bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{a}</span>)}</div>
        </Section>
      )}

      <Section label="Files">
        {files === undefined ? <div className="flex flex-col gap-1.5"><Skeleton className="h-9 w-full rounded-md" /><Skeleton className="h-9 w-full rounded-md" /></div>
          : files.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">No files.</p>
          : (
            <div className="flex flex-col gap-1">
              {files.map((f) => (
                <button key={f.path} onClick={() => setOpenFile(f.path)} className="flex items-center gap-s rounded-md bg-surface-container px-m py-2 text-left hover:bg-surface-high transition-colors">
                  <FileText size={14} className="text-primary shrink-0" />
                  <span className="flex-1 truncate font-mono text-on-surface text-[0.8125rem]">{f.path}</span>
                  <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{fmtSize(f.size)}</span>
                  <ChevronRight size={14} className="text-on-surface-low shrink-0" />
                </button>
              ))}
            </div>
          )}
      </Section>

      <IntegritySection skill={skill} />

      {skill.path && <div className="flex items-start gap-s text-on-surface-low text-[0.75rem]"><FileText size={13} className="shrink-0 mt-0.5" /><span className="font-mono break-all">{skill.path}</span></div>}

      {editable && (
        <div className="flex items-center gap-s">
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit SKILL.md</Button>
          <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete skill</Button>
        </div>
      )}
    </div>
  )
}

type ReverifyOutcome =
  | { kind: 'idle' }
  | { kind: 'ok'; at: number; data: SkillIntegrity }
  | { kind: 'error'; message: string }

/** S6 integrity: shows the install-time status from the list, plus a Re-verify action
 *  that re-hashes on-disk files against the .pclaw-lock.json baseline and reports drift.
 *  A skill with no lock (not installed from a marketplace) is "unverified" — expected, not an
 *  error — and the line says why there is none as far as the skill's origin is recorded. */
function IntegritySection({ skill }: { skill: SkillItem }) {
  const [outcome, setOutcome] = useState<ReverifyOutcome>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  // The row already carries an install-time status; a successful re-verify supersedes it.
  // A failed re-verify leaves that valid install-time fact visible beside the failure reason.
  const status = outcome.kind === 'ok' ? outcome.data.integrity : skill.integrity ?? 'unverified'

  async function verify() {
    setBusy(true)
    try {
      const data = await api.verifySkill(skill.name)
      setOutcome({ kind: 'ok', at: Date.now(), data })
    }
    catch (e) { setOutcome({ kind: 'error', message: (e as Error).message || 'Re-verification failed' }) }
    setBusy(false)
  }

  const tone = status === 'intact' ? 'var(--color-ok)' : status === 'tampered' ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  const Icon = status === 'intact' ? ShieldCheck : status === 'tampered' ? ShieldAlert : ShieldQuestion
  const label = status === 'intact' ? 'Verified — matches install baseline'
    : status === 'tampered' ? 'Tampered — files changed since install'
    : `Unverified — no install baseline (${noBaselineReason(skill)})`
  const drift = outcome.kind === 'ok' && (outcome.data.mutated.length + outcome.data.missing.length + outcome.data.added.length > 0)
  const outcomeLine = outcome.kind === 'idle' ? ''
    : outcome.kind === 'error' ? outcome.message
    : `checked ${new Date(outcome.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`

  return (
    <Section label="Integrity">
      <div className="flex items-center gap-s">
        <span className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: tone }}><Icon size={14} /> {label}</span>
        <div className="ml-auto flex items-center gap-s">
          {outcomeLine && <span data-type="caption" className="text-on-surface-low">{outcomeLine}</span>}
          <Button size="sm" variant="ghost" onClick={verify} loading={busy}><ShieldCheck size={14} /> Re-verify</Button>
        </div>
      </div>
      {outcome.kind === 'ok' && drift && (
        <div className="mt-2 flex flex-col gap-1 text-[0.75rem] font-mono">
          {outcome.data.mutated.map((f) => <div key={`m${f}`} className="text-danger">changed: {f}</div>)}
          {outcome.data.missing.map((f) => <div key={`x${f}`} className="text-danger">missing: {f}</div>)}
          {outcome.data.added.map((f) => <div key={`a${f}`} className="text-warn">added: {f}</div>)}
        </div>
      )}
    </Section>
  )
}

/** Inline SKILL.md editor → GET content, PUT /api/skills/{name} {content}. */
function SkillEditor({ name, onBack, onSaved }: { name: string; onBack: () => void; onSaved: () => void }) {
  // Cache the fetched SKILL.md so reopening the editor paints instantly; local
  // `content` is the editable copy, seeded from the cache when it lands.
  //
  // 🔴 NO FALLBACK. This read seeds an editor whose Save PUTs the whole document, and
  // `.catch(() => '')` made a failed read an empty SKILL.md: the field empty, Save enabled, no
  // error — one click replaced the skill's instructions with nothing. It also persisted that ''
  // under the key `FileView` reads for the same file. The failure is shown with a retry, and the
  // editor waits for a real read.
  const { data: fetched, error: fetchErr, refresh } = useQuery<string>(`skill:content:${name}:SKILL.md`, () => api.skillContent(name), { persist: true })
  const [content, setContent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (fetched !== undefined) setContent(fetched) }, [fetched])

  async function save() {
    if (content === null) return
    setBusy(true); setErr('')
    try {
      await api.updateSkill(name, content)
      invalidateKeys(`skill:content:${name}:SKILL.md`); invalidateKeys(`skill:files:${name}`)
      onSaved()
    }
    catch (e) { setErr((e as Error).message || 'Save failed'); setBusy(false) }
  }

  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{name} · SKILL.md</div>
      {content === null
        ? (fetchErr
          ? <LoadError what="SKILL.md" error={fetchErr} onRetry={refresh} />
          : <Skeleton className="h-72 w-full" />)
        : <TextArea value={content} onChange={setContent} rows={18} mono />}
      {err && <FieldError>{err}</FieldError>}
      <div className="flex justify-end gap-s">
        <Button size="sm" variant="ghost" onClick={onBack}><X size={14} /> Cancel</Button>
        <Button size="sm" onClick={save} loading={busy} disabled={busy || content === null}><Save size={14} /> Save</Button>
      </div>
    </div>
  )
}

function FileView({ name, path, onBack }: { name: string; path: string; onBack: () => void }) {
  const { data: content, error } = useQuery<string>(`skill:content:${name}:${path}`, () => api.skillFiles(name, path).then((d) => d.content ?? ''), { persist: true })
  const err = error ? (error instanceof Error ? error.message : 'failed to load') : ''

  const isMd = path.toLowerCase().endsWith('.md')
  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back to files</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{path}</div>
      {content === undefined && !err ? <Skeleton className="h-48 w-full" />
        : err ? <FieldError>{err}</FieldError>
        : isMd ? <Markdown>{content!}</Markdown>
        : <Code text={content!} />}
    </div>
  )
}

function Code({ text }: { text: string }) {
  let html = ''
  try { html = hljs.highlightAuto(text).value } catch { html = text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!)) }
  return <pre className="overflow-x-auto rounded-md bg-surface-low px-m py-2 text-[0.75rem] leading-relaxed"><code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} /></pre>
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`
  return `${(b / 1024).toFixed(1)} KB`
}
