import { useCallback, useEffect, useRef, useState } from 'react'
import { Globe, FolderGit2, Lock } from 'lucide-react'
import { api, type AlwaysOnDocument, type AlwaysOnItem, type AlwaysOnResponse, type ProjectItem } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { HELD_CHANGE_REASON, rebaseText, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'

/** Always-on conventions — what EVERY session receives, before you type anything.
 *
 *  The list is NOT this component's idea of the conventions: the server slices it out of the
 *  same producer strings the session composer feeds into the prompt (`SkillsLoader.get_context`
 *  and the project context block). That is deliberate — a viewer that computed its own answer
 *  would drift silently while the user trusted it, which is worse than having no viewer.
 *
 *  Two tiers, each its own `Section` so both headings sit at the same rung as the rest of the
 *  Legibility page (a nested sub-heading here would be the only h3 on the page): global
 *  `always: true` skills, and the project instruction docs a project-bound session inlines.
 *  Only the project overview is editable — a ledger is append-only history and a skill's body is
 *  its SKILL.md, so both say WHY they are read-only instead of just omitting a control.
 */
export function AlwaysOnConventions() {
  const [projectId, setProjectId] = useState('')
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [data, setData] = useState<AlwaysOnResponse | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [openId, setOpenId] = useState('')

  const load = useCallback((pid: string) => {
    setError(null)
    api.alwaysOn(pid).then(setData).catch((e) => setError(e as Error))
  }, [])

  useEffect(() => { load(projectId) }, [load, projectId])
  useEffect(() => { api.projects().then(setProjects).catch(() => setProjects([])) }, [])

  // Close any open editor when the project changes — keeping a draft from another project
  // open would let a save land on a document the user is no longer looking at.
  useEffect(() => { setOpenId('') }, [projectId])

  // One line, deliberately: the loading-noun ratchet pairs a skeleton's `what` to a LoadError
  // noun found on a SINGLE line, so splitting this across lines makes the skeleton's noun read
  // as invented from nowhere.
  if (!data && error) return <LoadError what="always-on conventions" error={error} onRetry={() => load(projectId)} />
  if (!data) return <ListSkeleton rows={3} what="always-on conventions" />

  const skills = data.items.filter((i) => i.kind === 'always_skill')
  const instructions = data.items.filter((i) => i.kind === 'project_instruction')

  return (
    <>
      {/* 🔴 `iconTone="muted"`: coral means "the agent is alive / this is active / this is the
          primary action", so a decorative CATEGORY glyph in coral spends the accent on nothing —
          the rule `settingsUI`'s own `iconTone` doc states, and the reason `ProvidersPanel`'s nine
          entity glyphs are muted. Measured across the settings area: 9 muted section glyphs against
          7 coral, and 4 of those 7 are `DesignPanel`'s control sections, which that doc names as the
          legitimate `primary` case. These two were the drift. */}
      <Section
        title="Always-on skills"
        icon={Globe}
        iconTone="muted"
        hint="Skills injected into every session in full, before you type. Read from the same string the session itself receives, so this list cannot drift from the real prompt."
      >
        {skills.length === 0 ? (
          <Empty>
            No skill is always-on yet. Set &ldquo;{data.always_skill_mechanism}&rdquo; to inject one
            into every session; every other skill loads only when it&rsquo;s relevant.
          </Empty>
        ) : (
          <div className="flex flex-col gap-2">
            {skills.map((item) => <ItemRow key={item.id} item={item} open={false} onToggle={() => undefined} />)}
          </div>
        )}
      </Section>

      <Section
        title="Project instructions"
        icon={FolderGit2}
        iconTone="muted"
        hint="Documents a project-bound session inlines into every turn. The overview is current state and editable here; the ledgers are append-only history."
        right={
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            aria-label="Show project instructions for"
            data-type="body-s" className="rounded-md bg-surface-high px-2 py-1.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
          >
            <option value="">Choose a project…</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        }
      >
        {instructions.length === 0 ? (
          <Empty>
            {projectId
              ? 'This project has no instruction documents yet. Its overview and ledgers appear here once they have content.'
              : 'Pick a project to see the instruction documents its sessions receive.'}
          </Empty>
        ) : (
          <div className="flex flex-col gap-2">
            {instructions.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                open={openId === item.id}
                onToggle={() => setOpenId(openId === item.id ? '' : item.id)}
                editor={openId === item.id ? (
                  <InstructionEditor key={item.id} item={item} onClose={() => setOpenId('')} onSaved={() => load(projectId)} />
                ) : undefined}
              />
            ))}
          </div>
        )}
      </Section>
    </>
  )
}

/** An item's verbatim body as the editor round-trip serves it, with the revision of that body. */
const bodyOf = (doc: AlwaysOnDocument): Revisioned<string> => ({ value: doc.body ?? '', revision: doc.revision })

/** The editor for one editable project instruction — mounted per open document, so a save the
 *  gateway refused (and the change it holds) belongs to that document and closes with it.
 *
 *  🔴 SAVED OVER THE COPY IT WAS BUILT FROM. The overview is not this page's alone: every workflow
 *  run that completes in the project appends a line to it. Save used to write the editor's copy
 *  straight over it — a run that finished while the editor was open lost its line without a word.
 *  The save now names the revision of the body it was seeded from, a stale one is refused, and the
 *  edit is re-applied onto what is stored (`ui/StaleWriteNotice`). */
function InstructionEditor({ item, onClose, onSaved }: {
  item: AlwaysOnItem
  onClose: () => void
  /** The save landed: the list re-reads what every session now receives. */
  onSaved: () => void
}) {
  const [draft, setDraft] = useState('')
  // The copy `draft` was seeded from — the base its save names. `null` while the body is read.
  const [base, setBase] = useState<Revisioned<string> | null>(null)
  const [saving, setSaving] = useState(false)
  const seed = (doc: Revisioned<string>) => { setBase(doc); setDraft(doc.value) }
  // What the gateway reported storing, from the guard's own reads and writes. A landed save renders
  // THIS — what the store now holds (it trims and caps the overview), not what we hoped we wrote —
  // and a discard re-seeds from the re-read the refusal made.
  const latest = useRef<Revisioned<string> | null>(null)
  const guard = useStaleWriteGuard<string>({
    read: () => api.alwaysOnDoc(item.id, item.project_id).then((doc) => { latest.current = bodyOf(doc); return latest.current }),
    write: (next, revision) => api.saveAlwaysOnDoc(item.id, item.project_id, next, revision).then((res) => { latest.current = bodyOf(res.item) }),
    onSaved: () => {
      if (latest.current) seed(latest.current)
      notify(`Saved ${item.name} — every session in this project now receives it.`, 'success')
      onSaved()
    },
    onDiscard: () => { if (latest.current) seed(latest.current) },
  })
  const held = guard.conflict !== null

  useEffect(() => {
    let alive = true
    // Fetch the VERBATIM body. The list carries a redacted preview, and saving a redacted
    // preview back would write the redaction over the user's real text.
    api.alwaysOnDoc(item.id, item.project_id)
      .then((doc) => { if (alive) seed(bodyOf(doc)) })
      .catch((e) => {
        if (!alive) return
        notify(`Couldn't open ${item.name}: ${String((e as Error)?.message || e)}`, 'error')
        onClose()
      })
    return () => { alive = false }
    // Keyed by the item at its mount site, so this runs once per opened document.
  }, [item.id, item.project_id])

  const save = async () => {
    if (!base) return
    setSaving(true)
    try {
      // `false` is a refusal the notice below now holds, with the draft still on screen.
      await guard.save(base, draft, rebaseText(base.value, draft))
    } catch (e) {
      // A failed write must never read as a save. The server rejects rather than answering
      // ok:true, so the user's draft stays on screen — it is their only copy of the edit.
      notify(
        `Couldn't save ${item.name}: ${String((e as Error)?.message || e)}. Your edit is still here — it was NOT saved.`,
        'error',
      )
    } finally {
      setSaving(false)
    }
  }

  if (!base) {
    return <p data-type="body-s" className="mt-m text-on-surface-low">Loading the exact text a session receives…</p>
  }
  return (
    <div className="mt-m">
      <label className="sr-only" htmlFor={`always-on-editor-${item.id}`}>{item.name}</label>
      {/* Read-only while a refused save waits for the user's choice: the notice re-applies the text
          it kept, so anything typed meanwhile would be dropped by that save. */}
      <textarea
        id={`always-on-editor-${item.id}`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        readOnly={held}
        rows={10}
        spellCheck={false}
        data-type="body-s" className="w-full rounded-md bg-surface-high px-m py-s font-mono text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
      />
      <StaleWriteNotice guard={guard} what={`This project's ${item.name}`} className="mt-s" />
      <div className="mt-s flex items-center gap-s">
        <Button size="sm" loading={saving} disabled={saving || held} onClick={save}
          disabledReason={held ? HELD_CHANGE_REASON : undefined}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        <Button size="sm" variant="secondary" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">
      {children}
    </p>
  )
}

function ItemRow({ item, open, onToggle, editor }: {
  item: AlwaysOnItem; open: boolean; onToggle: () => void; editor?: React.ReactNode
}) {
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span data-type="body-s" className="text-on-surface">{item.name}</span>
            <span data-type="caption" className="rounded bg-surface-high px-1.5 py-0.5 text-on-surface-low">{item.source}</span>
            <span data-type="caption" className="text-on-surface-low">{item.chars.toLocaleString()} chars</span>
          </div>
          <pre data-type="caption" className="mt-1 whitespace-pre-wrap break-words font-mono text-on-surface-low">{item.preview}</pre>
          {!item.editable && item.read_only_reason && (
            <p data-type="caption" className="mt-1 flex items-start gap-1.5 text-on-surface-low">
              <Lock size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
              {item.read_only_reason}
            </p>
          )}
        </div>
        {item.editable && (
          <Button size="sm" variant="secondary" onClick={onToggle} ariaExpanded={open} className="shrink-0">
            {open ? 'Close' : 'Edit'}
          </Button>
        )}
      </div>
      {editor}
    </div>
  )
}
