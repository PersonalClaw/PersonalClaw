import { useCallback, useEffect, useState } from 'react'
import { Globe, FolderGit2, Lock } from 'lucide-react'
import { api, type AlwaysOnItem, type AlwaysOnResponse, type ProjectItem } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'

/** Always-on conventions (PEP-10) — what EVERY session receives, before you type anything.
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
  const [draft, setDraft] = useState('')
  const [loadingDoc, setLoadingDoc] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback((pid: string) => {
    setError(null)
    api.alwaysOn(pid).then(setData).catch((e) => setError(e as Error))
  }, [])

  useEffect(() => { load(projectId) }, [load, projectId])
  useEffect(() => { api.projects().then(setProjects).catch(() => setProjects([])) }, [])

  // Close any open editor when the project changes — keeping a draft from another project
  // open would let a save land on a document the user is no longer looking at.
  useEffect(() => { setOpenId(''); setDraft('') }, [projectId])

  const openEditor = async (item: AlwaysOnItem) => {
    if (openId === item.id) { setOpenId(''); return }
    setOpenId(item.id)
    setDraft('')
    setLoadingDoc(true)
    try {
      // Fetch the VERBATIM body. The list carries a redacted preview, and saving a redacted
      // preview back would write the redaction over the user's real text.
      const doc = await api.alwaysOnDoc(item.id, item.project_id)
      setDraft(doc.body ?? '')
    } catch (e) {
      notify(`Couldn't open ${item.name}: ${String((e as Error)?.message || e)}`, 'error')
      setOpenId('')
    } finally {
      setLoadingDoc(false)
    }
  }

  const save = async (item: AlwaysOnItem) => {
    setSaving(true)
    try {
      const res = await api.saveAlwaysOnDoc(item.id, item.project_id, draft)
      // Render what the store now holds, not what we hoped we wrote.
      setDraft(res.item.body ?? '')
      notify(`Saved ${item.name} — every session in this project now receives it.`, 'success')
      load(projectId)
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
            className="rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
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
                onToggle={() => openEditor(item)}
                editor={openId === item.id ? (
                  <div className="mt-3">
                    {loadingDoc ? (
                      <p className="text-on-surface-low text-[0.8125rem]">Loading the exact text a session receives…</p>
                    ) : (
                      <>
                        <label className="sr-only" htmlFor={`always-on-editor-${item.id}`}>{item.name}</label>
                        <textarea
                          id={`always-on-editor-${item.id}`}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          rows={10}
                          spellCheck={false}
                          className="w-full rounded-md bg-surface-high px-3 py-2 font-mono text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
                        />
                        <div className="mt-2 flex items-center gap-2">
                          <Button size="sm" loading={saving} disabled={saving} onClick={() => save(item)}>
                            {saving ? 'Saving…' : 'Save'}
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => { setOpenId(''); setDraft('') }}>
                            Cancel
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                ) : undefined}
              />
            ))}
          </div>
        )}
      </Section>
    </>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
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
            <span className="text-on-surface text-[0.8125rem]">{item.name}</span>
            <span className="rounded bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.6875rem]">{item.source}</span>
            <span className="text-on-surface-low text-[0.6875rem]">{item.chars.toLocaleString()} chars</span>
          </div>
          <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-on-surface-low text-[0.75rem]">{item.preview}</pre>
          {!item.editable && item.read_only_reason && (
            <p className="mt-1 flex items-start gap-1.5 text-on-surface-low text-[0.75rem]">
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
