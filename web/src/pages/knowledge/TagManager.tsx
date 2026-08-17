import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, GitMerge, Loader2, Pencil, Tag as TagIcon, Trash2 } from 'lucide-react'
import { api, type KnowledgeTag } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirmDelete, promptInput } from '../../ui/dialog'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { EmptyState, ListSkeleton } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { fvs } from '../../design/fontWeight'

/** Tag management (KNOWLEDGE-LIBRARY S2, T2.2).
 *
 *  Tags are rows with a real parent/child hierarchy, so this is the surface where the
 *  taxonomy is actually curated: rename, re-parent, merge, delete — with live usage
 *  counts scoped to the non-archived library.
 *
 *  Rendered as a flat, indented list rather than a collapsible tree. The hierarchy is
 *  one level deep in practice and the whole point of this screen is to SEE the shape,
 *  so hiding branches behind disclosure triangles would work against it. Every mutation
 *  re-seeds from the server's returned tree instead of patching local state, because a
 *  rename can move a child, a merge can re-parent one, and a delete re-parents to root
 *  — guessing those locally is how a management UI drifts from its own data. */
export function TagManager({ onChanged }: { onChanged?: () => void }) {
  const [tags, setTags] = useState<KnowledgeTag[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    api.knowledgeTagTree()
      .then((t) => { if (alive) setTags(t) })
      .catch(() => { if (alive) setTags([]) })
    return () => { alive = false }
  }, [])

  /** Apply a mutation, then adopt the server's tree as the new truth. */
  const run = async (
    id: number,
    label: string,
    fn: () => Promise<{ tags?: KnowledgeTag[] } | void>,
  ) => {
    setBusy(id); setNote('')
    try {
      const r = await fn()
      const next = r && 'tags' in r && r.tags ? r.tags : await api.knowledgeTagTree()
      setTags(next)
      setNote(label)
      onChanged?.()   // item rows show tags, so the library list is now stale
    } catch (e) {
      const msg = String((e as Error)?.message || e)
      // Typed codes from the store, surfaced as the actionable sentence rather than
      // the raw code. A cycle and a name clash are both user-correctable.
      notify(
        msg.includes('tag_cycle')
          ? "That would make a tag its own ancestor — pick a different parent."
          : msg.includes('tag_name_taken')
            ? 'A tag with that name already exists. Merge them instead of renaming.'
            : `Couldn't update the tag: ${msg}`,
        'error',
      )
    } finally {
      setBusy(null)
    }
  }

  // Roots first, each followed by its children. One pass, name-ordered within a level.
  const ordered = useMemo(() => {
    if (!tags) return []
    const roots = tags.filter((t) => t.parent_id === null)
    const kids = (pid: number) => tags.filter((t) => t.parent_id === pid)
    const out: { tag: KnowledgeTag; depth: number }[] = []
    for (const root of roots) {
      out.push({ tag: root, depth: 0 })
      for (const kid of kids(root.id)) out.push({ tag: kid, depth: 1 })
    }
    // A tag whose parent was itself nested deeper than one level would otherwise
    // vanish from this list — append any stragglers rather than hide them.
    for (const t of tags) if (!out.some((o) => o.tag.id === t.id)) out.push({ tag: t, depth: 1 })
    return out
  }, [tags])

  if (tags === null) return <ListSkeleton what="tags" />
  if (!tags.length) {
    return (
      <EmptyState icon={TagIcon} title="No tags yet"
        hint="Tag a saved item and it appears here, where you can rename, nest, or merge it." />
    )
  }

  const rename = (t: KnowledgeTag) => run(t.id, `Renamed “${t.name}”`, async () => {
    const name = await promptInput({
      title: `Rename “${t.name}”`, label: 'Tag name', initial: t.name,
    })
    if (!name || name === t.name) return
    return api.renameKnowledgeTag(t.id, { name })
  })

  const reparent = (t: KnowledgeTag, parentId: number | null) =>
    run(t.id, parentId === null ? `“${t.name}” is now top-level` : `Moved “${t.name}”`,
      () => api.renameKnowledgeTag(t.id, { parent_id: parentId }))

  const merge = (t: KnowledgeTag, into: KnowledgeTag) =>
    run(t.id, `Merged “${t.name}” into “${into.name}”`,
      () => api.mergeKnowledgeTag(t.id, into.id))

  const remove = (t: KnowledgeTag) => run(t.id, `Deleted “${t.name}”`, async () => {
    const kids = tags.filter((x) => x.parent_id === t.id)
    const ok = await confirmDelete('tag', t.name, {
      body: [
        t.usage_count
          ? `This removes the tag from ${t.usage_count} item${t.usage_count === 1 ? '' : 's'}. The items themselves are untouched.`
          : 'This tag is on no items.',
        kids.length
          ? ` Its ${kids.length} nested tag${kids.length === 1 ? '' : 's'} become top-level rather than being deleted.`
          : '',
      ].join(''),
    })
    if (!ok) return
    await api.deleteKnowledgeTag(t.id)
  })

  return (
    <div className="flex flex-col gap-1">
      {/* 🔑 ALWAYS MOUNTED, EMPTY AT REST. Rename / nest / make-top-level / merge / delete give no
          other confirmation — the row simply re-renders — so this line is the whole success surface
          (WCAG 4.1.3). It used to be `{note && <div role="status">…}`, i.e. a region created at the
          same moment its content appeared, which this app has already ruled unreliable in three
          places: `settingsUI`'s `SavedToast` ("A live region created at the same moment its content
          appears is not reliably observed"), `ResultAnnouncement`, and `AudioRecorder`, whose own
          rail asserts the always-mounted shape. Measured here before the change: zero `role=status`
          nodes in this panel at rest.
          The visible half stays exactly as it was and is now `aria-hidden`, because the region above
          already carries the sentence — SavedToast's second load-bearing detail, for the same reason:
          leaving both in the tree announces the confirmation twice. */}
      <span role="status" aria-live="polite" className="sr-only">{note}</span>
      {note && (
        <div aria-hidden="true" className="pb-1 text-on-surface-var text-[0.8125rem]">{note}</div>
      )}
      {ordered.map(({ tag, depth }) => {
        const others = tags.filter((x) => x.id !== tag.id)
        const menu: ContextMenuItem[] = [
          { icon: <Pencil size={15} />, label: 'Rename', onSelect: () => rename(tag) },
          ...(tag.parent_id !== null
            ? [{ icon: <TagIcon size={15} />, label: 'Make top-level', onSelect: () => reparent(tag, null) }]
            : []),
          // Only tags that aren't already this tag's own descendants are offered as
          // parents; the server rejects a cycle regardless, but offering an option that
          // can only fail is a worse surface than not offering it.
          ...others
            .filter((o) => o.parent_id !== tag.id && o.id !== tag.parent_id)
            .slice(0, 8)
            .map((o) => ({
              icon: <ChevronRight size={15} />,
              label: `Nest under ${o.name}`,
              onSelect: () => reparent(tag, o.id),
            })),
          ...others.slice(0, 8).map((o) => ({
            icon: <GitMerge size={15} />,
            label: `Merge into ${o.name}`,
            onSelect: () => merge(tag, o),
          })),
          { icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => remove(tag) },
        ]
        return (
          <ContextMenu key={tag.id} items={menu}>
            <div className="group flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2"
              style={{ marginLeft: depth * 20 }}>
              {depth > 0 && <ChevronRight size={12} className="shrink-0 text-on-surface-low" aria-hidden />}
              <TagIcon size={14} className="shrink-0 text-on-surface-low" aria-hidden />
              {/* 🪤 `truncate` HIDES DATA WITH NO WAY BACK. Measured at 390px: this taxonomy's longest
                  tag needs 252px and gets 187, so `operational-runbooks-and-checklists` renders as
                  `operational-runbooks-and-che…` and a sighted user has no route to the rest — the DOM
                  text is complete, so assistive tech was the only reader getting the whole name. At
                  1440px nothing truncates (1041px available), which is why a desktop-only sweep sees
                  nothing here. `title` is what the six other truncating labels in this app use. */}
              <span className="min-w-0 flex-1 truncate text-on-surface text-[0.875rem]" style={fvs(500)} title={tag.name}>
                {tag.name}
              </span>
              <span className="shrink-0 text-on-surface-low text-[0.75rem]">
                {tag.usage_count === 0
                  ? 'unused'
                  : `${tag.usage_count} item${tag.usage_count === 1 ? '' : 's'}`}
              </span>
              {busy === tag.id
                ? <Loader2 size={13} className="shrink-0 animate-spin text-on-surface-low" />
                : (
                  <span className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <QuietButton onClick={() => rename(tag)} title={`Rename ${tag.name}`}>
                      <Pencil size={12} /> Rename
                    </QuietButton>
                  </span>
                )}
            </div>
          </ContextMenu>
        )
      })}
      <p className="pt-1 text-on-surface-low text-[0.75rem]">
        {/* This is the ONLY place the app tells anyone how to reach nest/merge/delete — six surfaces
            wrap rows in `ContextMenu` and this is the one with a visible hint. It named the pointer
            gesture alone, while `ui/motion/ContextMenu` has carried a keyboard route since the cycle
            that recorded "THE MENU WAS POINTER-ONLY" above its handler. Verified on this surface: Tab
            lands on a row's Rename button and Shift+F10 there opens the same menu, all items present.
            So a keyboard user could already do this and had been told they could not. */}
        Right-click a tag to nest, merge, or delete it, or Tab to one and press Shift+F10 for
        the same menu. An unused tag is kept — it stays part of your taxonomy even when nothing
        carries it right now.
      </p>
    </div>
  )
}
