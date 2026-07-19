import { useEffect, useState } from 'react'
import { Zap, FileText, ChevronRight, Trash2, Loader2, ArrowLeft, Pencil, Save, X, ShieldCheck, ShieldAlert, ShieldQuestion } from 'lucide-react'
import hljs from 'highlight.js/lib/common'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { Skeleton } from '../../ui/ListScaffold'
import { confirmDelete } from '../../ui/dialog'
import { TextArea } from '../tasks/formControls'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type SkillItem, type SkillFile, type SkillIntegrity } from '../../lib/api'
import { SOURCE_TONE } from './skillMeta'

/** Installed-skill inspector for the SidePanel: metadata + the skill's real file
 *  list, each openable to read its content (SKILL.md rendered as markdown,
 *  other files as highlighted code). Edit (SKILL.md) + Delete are offered for
 *  local/installed skills; bundled ones are protected. */
export function SkillInspector({ skill, onDeleted, onSaved }: { skill: SkillItem; onDeleted: () => void; onSaved?: () => void }) {
  const [openFile, setOpenFile] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const tone = SOURCE_TONE[skill.source] ?? 'var(--color-on-surface-low)'
  const editable = skill.source !== 'bundled'

  const { data: files } = useCachedData<SkillFile[]>(`skill:files:${skill.name}`, () => api.skillFiles(skill.name).then((d) => d.files ?? []).catch(() => []), { persist: true })

  // Reset the sub-views when switching to a different skill.
  useEffect(() => { setOpenFile(null); setEditing(false) }, [skill.name])

  async function del() {
    if (!(await confirmDelete('skill', skill.name, { body: 'This removes it from disk. This cannot be undone.' }))) return
    try { await api.deleteSkill(skill.name); onDeleted() } catch { /* ignore */ }
  }

  if (editing) return <SkillEditor name={skill.name} onBack={() => setEditing(false)} onSaved={() => { setEditing(false); onSaved?.() }} />
  if (openFile) return <FileView name={skill.name} path={openFile} onBack={() => setOpenFile(null)} />

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <span className="rounded-pill px-m h-7 inline-flex items-center text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone }}>{skill.source}</span>
        <span className="text-on-surface-low text-[0.8125rem]">{skill.type}</span>
        {skill.always && <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}><Zap size={13} /> always loaded</span>}
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
                  <span className="shrink-0 text-on-surface-low text-[0.7rem] tabular-nums">{fmtSize(f.size)}</span>
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

/** S6 integrity: shows the install-time status from the list, plus a Re-verify action
 *  that re-hashes on-disk files against the .pclaw-lock.json baseline and reports drift.
 *  A skill with no lock (bundled / hand-placed) is "unverified" — expected, not an error. */
function IntegritySection({ skill }: { skill: SkillItem }) {
  const [result, setResult] = useState<SkillIntegrity | null>(null)
  const [busy, setBusy] = useState(false)
  // The row already carries an install-time status; the re-verify result supersedes it.
  const status = result?.integrity ?? skill.integrity ?? 'unverified'

  async function verify() {
    setBusy(true)
    try { setResult(await api.verifySkill(skill.name)) } catch { /* ignore */ }
    setBusy(false)
  }

  const tone = status === 'intact' ? 'var(--color-ok)' : status === 'tampered' ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  const Icon = status === 'intact' ? ShieldCheck : status === 'tampered' ? ShieldAlert : ShieldQuestion
  const label = status === 'intact' ? 'Verified — matches install baseline'
    : status === 'tampered' ? 'Tampered — files changed since install'
    : 'Unverified — no install baseline (bundled or hand-placed)'
  const drift = result && (result.mutated.length + result.missing.length + result.added.length > 0)

  return (
    <Section label="Integrity">
      <div className="flex items-center gap-s">
        <span className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: tone }}><Icon size={14} /> {label}</span>
        <Button size="sm" variant="ghost" onClick={verify} disabled={busy} className="ml-auto">{busy ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />} Re-verify</Button>
      </div>
      {drift && (
        <div className="mt-2 flex flex-col gap-1 text-[0.75rem] font-mono">
          {result!.mutated.map((f) => <div key={`m${f}`} className="text-danger">changed: {f}</div>)}
          {result!.missing.map((f) => <div key={`x${f}`} className="text-danger">missing: {f}</div>)}
          {result!.added.map((f) => <div key={`a${f}`} className="text-warn">added: {f}</div>)}
        </div>
      )}
    </Section>
  )
}

/** Inline SKILL.md editor → GET content, PUT /api/skills/{name} {content}. */
function SkillEditor({ name, onBack, onSaved }: { name: string; onBack: () => void; onSaved: () => void }) {
  // Cache the fetched SKILL.md so reopening the editor paints instantly; local
  // `content` is the editable copy, seeded from the cache when it lands.
  const { data: fetched } = useCachedData<string>(`skill:content:${name}:SKILL.md`, () => api.skillContent(name).catch(() => ''), { persist: true })
  const [content, setContent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (fetched !== undefined) setContent(fetched) }, [fetched])

  async function save() {
    if (content === null) return
    setBusy(true); setErr('')
    try {
      await api.updateSkill(name, content)
      invalidateCache(`skill:content:${name}:SKILL.md`); invalidateCache(`skill:files:${name}`)
      onSaved()
    }
    catch (e) { setErr((e as Error).message || 'Save failed'); setBusy(false) }
  }

  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{name} · SKILL.md</div>
      {content === null
        ? <Skeleton className="h-72 w-full" />
        : <TextArea value={content} onChange={setContent} rows={18} mono />}
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
      <div className="flex justify-end gap-s">
        <Button size="sm" variant="ghost" onClick={onBack}><X size={14} /> Cancel</Button>
        <Button size="sm" onClick={save} disabled={busy || content === null}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Save</Button>
      </div>
    </div>
  )
}

function FileView({ name, path, onBack }: { name: string; path: string; onBack: () => void }) {
  const { data: content, error } = useCachedData<string>(`skill:content:${name}:${path}`, () => api.skillFiles(name, path).then((d) => d.content ?? ''), { persist: true })
  const err = error ? (error instanceof Error ? error.message : 'failed to load') : ''

  const isMd = path.toLowerCase().endsWith('.md')
  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back to files</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{path}</div>
      {content === undefined && !err ? <Skeleton className="h-48 w-full" />
        : err ? <p className="text-danger text-[0.8125rem]">{err}</p>
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
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`
  return `${(b / 1024).toFixed(1)} KB`
}
