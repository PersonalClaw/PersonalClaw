import { useEffect, useState } from 'react'
import { Zap, FileText, ChevronRight, Trash2, ArrowLeft, Pencil, Save, X, ShieldCheck, ShieldAlert, ShieldQuestion, GraduationCap, Undo2, FilePen, RefreshCw } from 'lucide-react'
import hljs from 'highlight.js/lib/common'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { InlineLoadError, LoadError, Skeleton } from '../../ui/ListScaffold'
import { confirmDelete, confirmDestructive } from '../../ui/dialog'
import { TextArea, FieldError } from '../../ui/forms'
import { FeedbackThumbs } from '../../ui/FeedbackThumbs'
import { useQuery, useMutation, invalidateKeys } from '../../lib/data'
import { api, type SkillItem, type SkillFile, type SkillIntegrity, type SkillDocument, type SkillRefinement } from '../../lib/api'
import { HELD_CHANGE_REASON, rebaseText, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { SOURCE_TONE, holdsRefinementCopy, noBaselineReason, provenanceMeta } from './skillMeta'
import { toneChipSkin } from '../../design/accent'
import { reportingWrite } from '../../app/reportingWrite'

/** The copy of a skill one row describes, as its routes address it: an agent's own copy (an
 *  `agent-local` row) with its `agent`, any other by its name alone — the copy agents get, the one
 *  the list shows. `key` keeps the two apart in the read cache. */
function copyOf(name: string, agent?: string) {
  return {
    key: agent ? `${name}@${agent}` : name,
    files: (path?: string) => (agent ? api.skillFiles(name, path, agent) : api.skillFiles(name, path)),
    document: () => (agent ? api.skillDocument(name, agent) : api.skillDocument(name)),
    update: (content: string, revision: string) =>
      (agent ? api.updateSkill(name, content, revision, agent) : api.updateSkill(name, content, revision)),
    remove: () => (agent ? api.deleteSkill(name, agent) : api.deleteSkill(name)),
    verify: () => (agent ? api.verifySkill(name, agent) : api.verifySkill(name)),
  }
}

/** Installed-skill inspector for the SidePanel: metadata + the skill's real file
 *  list, each openable to read its content (SKILL.md rendered as markdown,
 *  other files as highlighted code). Edit (SKILL.md) is offered for every copy in the
 *  home, and Delete for every one but a bundled skill's. */
export function SkillInspector({ skill, onDeleted, onSaved }: { skill: SkillItem; onDeleted: () => void; onSaved?: () => void }) {
  const [openFile, setOpenFile] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const tone = SOURCE_TONE[skill.source] ?? 'var(--color-on-surface-low)'
  const prov = provenanceMeta(skill.provenance)
  // `source`, NOT `provenance`: a taught skill is a `local` skill and is exactly as editable
  // as a hand-placed one. Reading provenance here instead would lock the user out of editing
  // the skill their own session just taught (#576).
  // A `shared` skill is in the folder AI tools share, outside PersonalClaw's home: read, never
  // edited or deleted from here (the server refuses both), so neither is offered.
  // A `bundled` skill is the library's copy of one that comes with PersonalClaw, the copy agents
  // read: hers to edit, and an update keeps her edit (it offers its own version instead). It is not
  // deleted from here: the next start would put PersonalClaw's copy back.
  const shared = skill.source === 'shared'
  const bundled = skill.source === 'bundled'
  const editable = !shared
  const deletable = !shared && !bundled
  const agent = skill.source === 'agent-local' ? skill.agent : undefined
  const copy = copyOf(skill.name, agent)

  const { data: files } = useQuery<SkillFile[]>(`skill:files:${copy.key}`, () => copy.files().then((d) => d.files ?? []).catch(() => []), { persist: true })

  // Reset the sub-views when switching to a different skill.
  useEffect(() => { setOpenFile(null); setEditing(false) }, [skill.name])

  async function del() {
    if (!(await confirmDelete('skill', skill.name, { body: 'This removes it from disk. This cannot be undone.' }))) return
    // The dialog above promises "This removes it from disk. This cannot be undone." — the
    // strongest consent this app asks for. Swallowing the failure after that left the panel
    // open, the skill present, and nothing said: the only reasonable read is that the click
    // missed. `onDeleted()` stays GATED, per reportingWrite's own rule — closing the inspector
    // on a failed delete would claim the removal it did not make.
    if (!(await reportingWrite(`delete the skill "${skill.name}"`, () => copy.remove()))) return
    onDeleted()
  }

  if (editing) return <SkillEditor name={skill.name} agent={agent} onBack={() => setEditing(false)} onSaved={() => { setEditing(false); onSaved?.() }} />
  if (openFile) return <FileView name={skill.name} agent={agent} path={openFile} onBack={() => setOpenFile(null)} />

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

      {/* An agent-local row is named by its bare name, which this read would resolve to the
          library's skill of that name, not the agent's own. */}
      {skill.source !== 'agent-local' && <RefinementsSection name={skill.name} />}

      <BundledUpdate skill={skill} onChanged={onSaved} />

      <IntegritySection skill={skill} verify={copy.verify} />

      {skill.path && <div className="flex items-start gap-s text-on-surface-low text-[0.75rem]"><FileText size={13} className="shrink-0 mt-0.5" /><span className="font-mono break-all">{skill.path}</span></div>}

      {editable && (
        <div className="flex items-center gap-s">
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit SKILL.md</Button>
          {deletable && <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete skill</Button>}
        </div>
      )}
      {bundled && (
        <p data-type="body-s" className="text-on-surface-low">
          This skill comes with PersonalClaw. Edit it as you like: when PersonalClaw updates, your version is kept, and a newer one is offered here for you to take or decline.
        </p>
      )}
      {shared && (
        <p data-type="body-s" className="text-on-surface-low">
          This skill is in the folder other AI tools share, outside PersonalClaw’s home, so PersonalClaw only reads it. Edit or remove it there.
        </p>
      )}
    </div>
  )
}

type ReverifyOutcome =
  | { kind: 'idle' }
  | { kind: 'ok'; at: number; data: SkillIntegrity }
  | { kind: 'error'; message: string }

/** S6 integrity: shows the install-time status from the list, plus a Re-verify action
 *  that re-hashes on-disk files against the .pclaw-lock.json baseline and names what changed.
 *  A change is the owner's edit, said as one ("Edited"), never as tampering; only a record that
 *  cannot be read is ("Can't verify"). A skill with no lock (not installed from a marketplace) is
 *  "unverified" — expected, not an error — and the line says why there is none as far as the
 *  skill's origin is recorded. */
function IntegritySection({ skill, verify: check }: { skill: SkillItem; verify: () => Promise<SkillIntegrity> }) {
  const [outcome, setOutcome] = useState<ReverifyOutcome>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  // The row already carries an install-time status; a successful re-verify supersedes it.
  // A failed re-verify leaves that valid install-time fact visible beside the failure reason.
  const status = outcome.kind === 'ok' ? outcome.data.integrity : skill.integrity ?? 'unverified'

  async function verify() {
    setBusy(true)
    try {
      const data = await check()
      setOutcome({ kind: 'ok', at: Date.now(), data })
    }
    catch (e) { setOutcome({ kind: 'error', message: (e as Error).message || 'Re-verification failed' }) }
    setBusy(false)
  }

  const tone = status === 'intact' ? 'var(--color-ok)' : status === 'tampered' ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  const Icon = status === 'intact' ? ShieldCheck : status === 'tampered' ? ShieldAlert : status === 'edited' ? FilePen : ShieldQuestion
  const label = status === 'intact' ? 'Verified — matches install baseline'
    : status === 'edited' ? 'Edited — changed since it was installed'
    : status === 'tampered' ? 'Can’t verify — its install record is damaged, so what was installed is unknown'
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
        <div className="mt-s flex flex-col gap-xs text-[0.75rem] font-mono text-on-surface-var">
          {outcome.data.mutated.map((f) => <div key={`m${f}`}>changed: {f}</div>)}
          {outcome.data.missing.map((f) => <div key={`x${f}`}>missing: {f}</div>)}
          {outcome.data.added.map((f) => <div key={`a${f}`}>added: {f}</div>)}
        </div>
      )}
    </Section>
  )
}

/** Inline SKILL.md editor → GET content, PUT /api/skills/{name} {content} over its revision, of the
 *  copy the row describes (`agent`: that agent's own copy). */
function SkillEditor({ name, agent, onBack, onSaved }: { name: string; agent?: string; onBack: () => void; onSaved: () => void }) {
  const copy = copyOf(name, agent)
  // Cache the fetched SKILL.md so reopening the editor paints instantly; local
  // `content` is the editable copy, seeded from the cache when it lands.
  //
  // 🔴 NO FALLBACK. This read seeds an editor whose Save PUTs the whole document, and
  // `.catch(() => '')` made a failed read an empty SKILL.md: the field empty, Save enabled, no
  // error — one click replaced the skill's instructions with nothing. The failure is shown with a
  // retry, and the editor waits for a real read.
  //
  // 🔴 THE SKILL'S OWN TEXT, NOT THE BODY A SESSION LOADS. This editor used to read the loaded body,
  // accepted refinements added on top, and save it back whole, so every refinement it had shown went
  // into SKILL.md as well and loaded twice, and a revert removed only the copy kept beside the skill.
  // The read is now the text the file holds, with that text's revision, and the refinements applied
  // on top come with it to be shown under the editor (`skillDocument`). Its own key, the inspector's
  // too, and not the one an older build cached the loaded body under.
  const key = `skill:own:${copy.key}`
  const { data: fetched, error: fetchErr, refresh } = useQuery<SkillDocument>(key, () => copy.document(), { persist: true })
  // The copy `content` was seeded from — the base a save names, so it moves only with a re-seed.
  const [base, setBase] = useState<Revisioned<string> | null>(null)
  const [content, setContent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // Seeded when the own text changes, not on every read: reverting a refinement re-reads the
  // document with the same text, and a re-seed then would throw away what is being typed.
  useEffect(() => { if (fetched !== undefined) { setBase(fetched); setContent(fetched.value) } }, [fetched?.revision])
  // 🔴 SAVED OVER THE COPY IT WAS BUILT FROM. The gateway rewrites skills on its own — the curator
  // ages them, a session refines them, an app or pack update re-seeds them — and this editor used to
  // save its copy straight over any of that. A stale copy is refused now, and the edit, which is a
  // text change, is re-applied onto what is stored (`ui/StaleWriteNotice`).
  const guard = useStaleWriteGuard<string>({
    read: () => copy.document(),
    write: (next, revision) => copy.update(next, revision),
    onSaved: () => {
      invalidateKeys(key); invalidateKeys(`skill:content:${copy.key}:SKILL.md`); invalidateKeys(`skill:files:${copy.key}`)
      onSaved()
    },
    onDiscard: () => { invalidateKeys(key); refresh() },
  })
  const held = guard.conflict !== null

  async function save() {
    if (content === null || base === null) return
    setBusy(true); setErr('')
    // `false` is a refusal the notice now holds, with the draft still on screen; success closes the editor.
    try { if (!(await guard.save(base, content, rebaseText(base.value, content)))) setBusy(false) }
    catch (e) { setErr((e as Error).message || 'Save failed'); setBusy(false) }
  }

  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{name} · SKILL.md</div>
      {content === null
        ? (fetchErr
          ? <LoadError what="skill file" error={fetchErr} onRetry={refresh} />
          : <Skeleton className="h-72 w-full" />)
        : <TextArea value={content} onChange={setContent} rows={18} mono disabled={held}
            disabledReason={held ? HELD_CHANGE_REASON : undefined} />}
      <StaleWriteNotice guard={guard} what="This skill" />
      {fetched !== undefined && fetched.refinements.length > 0 && (
        <AppliedRefinements name={name} refinements={fetched.refinements} draft={content ?? undefined}
          intro="This is the skill’s own text. When the skill loads, the accepted refinements below are added after it, in this order. Editing this text doesn’t change them, and saving never writes them into SKILL.md. To change one, revert it and write what you want here." />
      )}
      {err && <FieldError>{err}</FieldError>}
      <div className="flex justify-end gap-s">
        <Button size="sm" variant="ghost" onClick={onBack}><X size={14} /> Cancel</Button>
        <Button size="sm" onClick={save} loading={busy} disabled={busy || content === null || held}
          disabledReason={held ? HELD_CHANGE_REASON : undefined}><Save size={14} /> Save</Button>
      </div>
    </div>
  )
}

/** The accepted refinements this skill loads with, shown with its details: nothing for a skill that
 *  has none. The same read, under the same key, as the editor's. */
function RefinementsSection({ name }: { name: string }) {
  const { data: doc, error } = useQuery<SkillDocument>(`skill:own:${name}`, () => api.skillDocument(name), { persist: true })
  if (doc === undefined) {
    return error ? <Section label="Accepted refinements"><InlineLoadError what="this skill’s refinements" error={error} /></Section> : null
  }
  if (doc.refinements.length === 0) return null
  return (
    <Section label="Accepted refinements">
      <AppliedRefinements name={name} refinements={doc.refinements}
        intro="Added after the skill’s own text each time it loads, in this order. Its SKILL.md holds only that text." />
    </Section>
  )
}

/** Each accepted refinement a skill loads with — its block exactly as it is added after the skill's
 *  own text — and the way to revert it. `draft` is the own text being edited: a refinement it holds
 *  word for word is named, with what saving does about it (`holdsRefinementCopy`). */
function AppliedRefinements({ name, refinements, intro, draft }: {
  name: string
  refinements: SkillRefinement[]
  intro: string
  draft?: string
}) {
  const [reverting, setReverting] = useState('')
  // The document's list changes; the file itself does too when the revert takes out a copy an
  // earlier save left in it, so its raw view and its size are re-read as well.
  const revertM = useMutation({
    run: (r: SkillRefinement) => api.revertSkillRefinement(name, r.id),
    invalidates: [`skill:own:${name}`, `skill:content:${name}:SKILL.md`, `skill:files:${name}`],
  })
  async function revert(r: SkillRefinement) {
    const ok = await confirmDestructive(
      `Revert refinement v${r.version} of “${name}”?`,
      'The skill loads without it from now on, and its own text stays as it is. A reverted refinement can’t be brought back.',
      { confirmLabel: 'Revert' },
    )
    if (!ok) return
    setReverting(r.id)
    await reportingWrite(`revert refinement v${r.version} of "${name}"`, () => revertM.mutate(r))
    setReverting('')
  }
  const copied = draft === undefined ? [] : refinements.filter((r) => holdsRefinementCopy(draft, r.text))
  return (
    <div className="flex flex-col gap-s">
      <p data-type="body-s" className="text-on-surface-low">{intro}</p>
      <ol aria-label={`Accepted refinements of ${name}`} className="flex flex-col gap-s">
        {refinements.map((r) => (
          <li key={r.id} className="flex items-start gap-s rounded-md bg-surface-container px-m py-s">
            <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words font-mono text-on-surface text-[0.75rem]">{r.text}</pre>
            <Button size="xs" variant="ghost" ariaLabel={`Revert refinement v${r.version}`}
              loading={reverting === r.id} onClick={() => void revert(r)}>
              <Undo2 size={14} /> Revert
            </Button>
          </li>
        ))}
      </ol>
      {copied.map((r) => (
        <p key={r.id} role="status" data-type="body-s" className="text-warn">
          Your text holds refinement v{r.version} word for word, and v{r.version} is still added on top when the skill loads. Saving leaves this copy out of SKILL.md, so the skill carries it once. To make it part of the skill’s own text, revert v{r.version} first.
        </p>
      ))}
    </div>
  )
}

/** A newer version of a skill that comes with PersonalClaw, kept from the owner's changed copy
 *  (`SkillItem.bundled_update`): hers to take in place of her copy, or to decline. Nothing does
 *  either for her: an update keeps her copy and says so here. */
function BundledUpdate({ skill, onChanged }: { skill: SkillItem; onChanged?: () => void }) {
  const [busy, setBusy] = useState<'' | 'take' | 'keep'>('')
  const offer = skill.bundled_update
  if (!offer) return null
  const settled = () => {
    invalidateKeys(`skill:own:${skill.name}`); invalidateKeys(`skill:content:${skill.name}:SKILL.md`); invalidateKeys(`skill:files:${skill.name}`)
    onChanged?.()
  }
  async function take(digest: string) {
    const ok = await confirmDestructive(
      `Use the version of “${skill.name}” that comes with PersonalClaw?`,
      'It replaces your copy, with any files you added to it, and your changes are not kept.',
      { confirmLabel: 'Use the new version' },
    )
    if (!ok) return
    setBusy('take')
    if (await reportingWrite(`use the new version of "${skill.name}"`, () => api.useBundledSkill(skill.name, digest))) settled()
    setBusy('')
  }
  async function keep(digest: string) {
    setBusy('keep')
    if (await reportingWrite(`keep your copy of "${skill.name}"`, () => api.keepSkillCopy(skill.name, digest))) settled()
    setBusy('')
  }
  return (
    <Section label="Newer version">
      <p data-type="body-s" className="text-on-surface">
        A newer version of this skill comes with PersonalClaw. Your copy differs from it, so PersonalClaw kept yours and didn’t update it.
      </p>
      <div className="mt-s flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={() => void take(offer.digest)} loading={busy === 'take'} disabled={busy !== ''}>
          <RefreshCw size={14} /> Use the new version
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void keep(offer.digest)} loading={busy === 'keep'} disabled={busy !== ''}>
          Keep mine
        </Button>
      </div>
    </Section>
  )
}

function FileView({ name, agent, path, onBack }: { name: string; agent?: string; path: string; onBack: () => void }) {
  const copy = copyOf(name, agent)
  const { data: content, error } = useQuery<string>(`skill:content:${copy.key}:${path}`, () => copy.files(path).then((d) => d.content ?? ''), { persist: true })
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
