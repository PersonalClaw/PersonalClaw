/** The deck editor — a slide list plus per-slide fields over a `DeckModel`.
 *
 *  Mounted by `<ContentSurface>` for a `.pptx` artifact, in place of Monaco, through the
 *  same non-Monaco renderer slot the document editor and the sheet grid use
 *  (DOCUMENT-FIDELITY-EDITOR §C4).
 *
 *  **Structural, not WYSIWYG.** This follows DFE-5's ratified decision (owner task 2,
 *  option (c)) for the third time and for the same reason: a slide canvas's whole value is
 *  owning its own geometry model, so adopting one would mean a second representation of a
 *  deck plus a lossy mapping to ours — exactly the second fidelity story the plan refuses.
 *  So: controlled inputs over the model, no embedded widget, no new frontend dependency.
 *
 *  **A bullet's depth is a FIELD, not a typing behaviour.** No tab-to-indent, no leading
 *  dashes parsed out of prose: the depth is chosen from a select beside the bullet, which
 *  is the whole point of the atom — the model carries `level`, the writer writes it, the
 *  parser reads it back, and the editor lets a person see and set it. Guessing depth from
 *  characters is what a structural editor exists to avoid.
 *
 *  **Geometry is preserved and releasable, not authored.** A deck whose title was dragged
 *  somewhere in PowerPoint keeps that position through a save; the editor says so and
 *  offers to let go of it. It does not offer eight number fields to nudge it — that is a
 *  canvas, and this is not one.
 *
 *  **The lossy-edit contract is a MECHANISM, not a notice** — same posture as
 *  `DocumentEditor` and `SheetGrid`: while the parse's loss report is non-empty and
 *  unacknowledged, every control is disabled and carries a reason. A warning a user can
 *  type past has already failed. (Its own copy rather than a shared component, per the
 *  owner ruling recorded in DFE-7's execution-log entry; if the three loss stories really
 *  diverge once every branch lands, that is a follow-up coherence pass.)
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, FileWarning, Loader2, Plus, Presentation, Save, Trash2 } from 'lucide-react'
import { api, ApiError, type DeckModelJson, type DocumentLossReport } from '../../lib/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { MoreRow } from '../MoreRow'
import { EmptyState } from '../ListScaffold'
import { Segmented } from '../Segmented'
import { Field, Select, TextArea, TextInput } from '../forms'
import { confirm } from '../dialog'
import {
  boxSummary,
  isPlaced,
  layoutOptions,
  levelOptions,
  slideLabel,
  slideSizeKey,
  slideSizeOptions,
  withAppendedBullet,
  withAppendedSlide,
  withBullet,
  withInheritedBoxes,
  withSlide,
  withSlideSize,
  withoutBullet,
  withoutSlide,
} from './deckModelEdit'
import type { DocumentEditorProps } from './contentTypes'

/** The parse's losses as prose a person can act on. Rendered in the pre-edit gate AND in
 *  the save confirmation from one place here, so this surface's two copies cannot drift. */
function DeckLossList({ loss }: { loss: DocumentLossReport }) {
  return (
    <div className="text-[0.8125rem]">
      <p className="text-on-surface">{loss.summary}</p>
      <ul className="mt-2 space-y-1 text-on-surface-var">
        {loss.items.slice(0, 12).map((item, i) => (
          <li key={`${item.kind}-${item.where}-${i}`}>
            <span className="text-on-surface">{item.kind}</span>
            {' · '}{item.where}{' — '}{item.detail}
          </li>
        ))}
      </ul>
      {/* The summary above states the FULL count, so a truncated list owes this line. */}
      <MoreRow total={loss.items.length} shown={12} noun="losses" />
    </div>
  )
}

export function SlideDeck({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const [loaded, setLoaded] = useState<{ model: DeckModelJson; loss: DocumentLossReport; version: number } | null>(null)
  const [model, setModel] = useState<DeckModelJson | null>(null)
  const [loadError, setLoadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [current, setCurrent] = useState(0)
  // Identity-stable across renders so the effect below cannot become an unbounded fetch.
  const dirtyRef = useRef(onDirty)
  dirtyRef.current = onDirty

  useEffect(() => {
    let alive = true
    setLoadError('')
    api.artifactDeckModel(slug)
      .then((r) => {
        if (!alive) return
        setLoaded({ model: r.model, loss: r.loss, version: r.version })
        setModel(r.model)
        // A lossless deck needs no acknowledgement — a ceremonial gate in front of a safe
        // edit only teaches people to click past the one that matters.
        setAcknowledged(r.loss.lossless)
      })
      .catch((e) => { if (alive) setLoadError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [slug])

  const dirty = !!model && !!loaded && model !== loaded.model
  useEffect(() => { dirtyRef.current?.(dirty) }, [dirty])

  const editing = !readOnly && acknowledged
  // ONE sentence for why nothing can be typed, reused by every control that goes off — a
  // keyboard user who tabs onto a dead control has to be able to learn what is missing.
  const blockedReason = readOnly
    ? 'This version is read-only — open the current version to edit it.'
    : !acknowledged
      ? 'Read the formatting notice above, then choose “edit anyway”.'
      : ''

  const slide = model?.slides[current] ?? null
  const patch = useCallback((next: Partial<NonNullable<typeof slide>>) => {
    if (!model || !slide) return
    setModel(withSlide(model, current, { ...slide, ...next }))
  }, [model, slide, current])

  const save = async () => {
    if (!model || !loaded || saving) return
    if (!loaded.loss.lossless) {
      const ok = await confirm({
        title: `Save and re-render “${title}”?`,
        body: (
          <div className="space-y-2">
            <p className="text-[0.8125rem] text-on-surface">
              Saving re-creates the deck from the slides below, so the things PersonalClaw
              cannot represent will not be in the saved copy:
            </p>
            <DeckLossList loss={loaded.loss} />
            <p className="text-[0.8125rem] text-on-surface-var">
              Version {loaded.version} is kept — you can restore it from Details › Versions
              at any time.
            </p>
          </div>
        ),
        danger: true,
        confirmLabel: 'Save and re-render',
        icon: FileWarning,
      })
      if (!ok) return
    }
    setSaving(true)
    setSaveError('')
    try {
      const res = await api.saveArtifactDeckModel(slug, loaded.version, model)
      // Re-baseline on what the server accepted: the next save must carry the NEW version
      // or it would fail its own If-Match.
      setLoaded({ ...loaded, model, version: res.version })
    } catch (e) {
      const stale = e instanceof ApiError && e.status === 409
      setSaveError(
        stale
          ? 'This deck changed somewhere else (another tab, or the agent) since you opened it. Your edits are still here — reopen it to get the current version, then re-apply them.'
          : e instanceof Error ? e.message : String(e),
      )
    } finally {
      setSaving(false)
    }
  }

  if (loadError) {
    return (
      <div className="p-l">
        <InlineError icon multiline>Couldn’t read {title}: {loadError}</InlineError>
      </div>
    )
  }
  if (!model || !loaded) {
    return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  }

  const placed = !!slide && (isPlaced(slide.title_box) || isPlaced(slide.body_box))

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── the pre-edit gate ── */}
      {!loaded.loss.lossless && !acknowledged && (
        <div role="alert" className="border-b border-outline/40 p-l" style={{ background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)' }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden="true" />
            <div className="min-w-0">
              <p className="text-[0.875rem] text-on-surface">Editing this deck loses formatting</p>
              <p className="mt-1 text-[0.8125rem] text-on-surface-var">
                It contains things this editor’s deck model cannot hold. Saving re-creates
                the file, so they will not be in the saved copy. The version you have now is
                kept and can be restored from Details › Versions.
              </p>
              <div className="mt-2"><DeckLossList loss={loaded.loss} /></div>
              <Button size="xs" variant="tonal" className="mt-3" onClick={() => setAcknowledged(true)}>
                I understand — edit anyway
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── the deck: its cover title, its slide size, and Save ── */}
      <div className="flex flex-wrap items-end gap-3 border-b border-outline/40 px-m py-2">
        <div className="min-w-[14rem] flex-1">
          <Field label="Deck title" hint="Saved as the deck’s cover slide.">
            <TextInput size="sm" value={model.title}
              placeholder="No cover slide"
              ariaLabel="Deck title"
              disabled={!editing}
              disabledReason={blockedReason || undefined}
              onChange={(v) => setModel({ ...model, title: v })} />
          </Field>
        </div>
        <div className="min-w-[13rem]">
          <Field label="Slide size">
            <Select value={slideSizeKey(model)}
              options={slideSizeOptions(model)}
              disabled={!editing}
              disabledReason={blockedReason || undefined}
              ariaLabel="Slide size"
              onChange={(key) => setModel(withSlideSize(model, key))} />
          </Field>
        </div>
        <div className="ml-auto flex items-center gap-2 pb-1">
          {dirty && <span className="text-[0.75rem] text-on-surface-low">Unsaved changes</span>}
          <Button size="xs" variant="primary" shape="squircle" loading={saving}
            disabled={!editing || !dirty}
            disabledReason={blockedReason || 'No changes to save yet.'}
            onClick={() => void save()}>
            <Save size={13} aria-hidden="true" /> Save
          </Button>
        </div>
      </div>

      {saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={() => setSaveError('')}>{saveError}</InlineError>}

      {/* ── the slide list ──
           Hidden entirely while the deck has no slides: an empty list's controls would be
           a second "Add slide" beside the empty state's own, and two controls with one
           accessible name doing one thing is an ambiguity a screen-reader user cannot
           resolve (the a11y suite caught exactly that). One action, one control. */}
      {model.slides.length > 0 && (
      <div className="flex flex-wrap items-center gap-2 border-b border-outline/40 px-m py-1.5">
        <Segmented
          size="sm"
          collapse="scroll"
          ariaLabel="Slides"
          value={String(current)}
          options={model.slides.map((s, i) => ({ key: String(i), label: `${i + 1}. ${slideLabel(s, i)}` }))}
          onChange={(key) => setCurrent(Number(key))} />
        <Button size="xs" variant="ghost" shape="squircle"
          disabled={!editing}
          disabledReason={blockedReason || undefined}
          ariaLabel="Add slide after this one"
          title="Add a slide after this one"
          onClick={() => { setModel(withAppendedSlide(model, current)); setCurrent(Math.min(current + 1, model.slides.length)) }}>
          <Plus size={13} aria-hidden="true" />
        </Button>
        <Button size="xs" variant="ghost" shape="squircle"
          disabled={!editing || !slide}
          disabledReason={blockedReason || 'There is no slide to delete.'}
          ariaLabel="Delete slide"
          title="Delete this slide"
          onClick={() => { setModel(withoutSlide(model, current)); setCurrent(Math.max(0, current - 1)) }}>
          <Trash2 size={13} aria-hidden="true" />
        </Button>
      </div>
      )}

      {/* ── the slide itself ── */}
      {!slide ? (
        <EmptyState
          icon={Presentation}
          title="This deck has no slides"
          hint="Add one to start building the deck, or ask the agent to generate an outline."
          action={editing ? { label: 'Add slide', onClick: () => { setModel(withAppendedSlide(model, 0)); setCurrent(0) } } : undefined}
        />
      ) : (
        <div className="min-h-0 flex-1 space-y-4 overflow-auto p-l">
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[16rem] flex-1">
              <Field label={`Slide ${current + 1} title`}>
                <TextInput size="sm" value={slide.title}
                  ariaLabel={`Title of slide ${current + 1}`}
                  disabled={!editing}
                  disabledReason={blockedReason || undefined}
                  placeholder="Untitled slide"
                  onChange={(v) => patch({ title: v })} />
              </Field>
            </div>
            <div className="min-w-[14rem]">
              <Field label="Layout">
                <Select value={slide.layout}
                  options={layoutOptions(slide.layout)}
                  disabled={!editing}
                  disabledReason={blockedReason || undefined}
                  ariaLabel={`Layout of slide ${current + 1}`}
                  onChange={(v) => patch({ layout: v })} />
              </Field>
            </div>
          </div>

          {/* the outline — text and DEPTH, which is what this atom is about */}
          <div>
            <p className="text-[0.8125rem] font-medium text-on-surface">Bullets</p>
            <p className="mt-0.5 text-[0.75rem] text-on-surface-low">
              A bullet’s level is its indent depth in the saved deck — choose it here rather
              than typing dashes.
            </p>
            <ul className="mt-2 space-y-1.5">
              {slide.bullets.map((bullet, index) => (
                <li key={index} className="flex items-center gap-2">
                  <div className="w-[8.5rem] shrink-0">
                    <Select value={String(bullet.level)}
                      options={levelOptions()}
                      disabled={!editing}
                      disabledReason={blockedReason || undefined}
                      ariaLabel={`Indent level of bullet ${index + 1} on slide ${current + 1}`}
                      onChange={(v) => setModel(withBullet(model, current, index, { ...bullet, level: Number(v) }))} />
                  </div>
                  <div className="min-w-0 flex-1" style={{ paddingLeft: `${bullet.level * 0.75}rem` }}>
                    <TextInput size="sm" value={bullet.text}
                      ariaLabel={`Bullet ${index + 1} on slide ${current + 1}`}
                      disabled={!editing}
                      disabledReason={blockedReason || undefined}
                      placeholder="Empty bullet"
                      onChange={(v) => setModel(withBullet(model, current, index, { ...bullet, text: v }))} />
                  </div>
                  <Button size="xs" variant="ghost" shape="squircle"
                    disabled={!editing}
                    disabledReason={blockedReason || undefined}
                    ariaLabel={`Remove bullet ${index + 1} on slide ${current + 1}`}
                    onClick={() => setModel(withoutBullet(model, current, index))}>
                    <Trash2 size={13} aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
            <Button size="xs" variant="tonal" className="mt-2"
              disabled={!editing}
              disabledReason={blockedReason || undefined}
              onClick={() => setModel(withAppendedBullet(model, current))}>
              <Plus size={13} aria-hidden="true" /> Add bullet
            </Button>
          </div>

          <Field label="Speaker notes" hint="Not shown on the slide; saved in the deck’s notes pane.">
            <TextArea size="sm" rows={3} value={slide.notes}
              ariaLabel={`Speaker notes for slide ${current + 1}`}
              disabled={!editing}
              disabledReason={blockedReason || undefined}
              onChange={(v) => patch({ notes: v })} />
          </Field>

          {/* Geometry: preserved, explained, releasable — never authored here. */}
          <div className="rounded-md border border-outline/40 p-m text-[0.75rem] text-on-surface-var">
            {placed ? (
              <>
                <p className="text-on-surface">This slide’s shapes were moved out of their layout’s positions.</p>
                <ul className="mt-1 space-y-0.5">
                  {isPlaced(slide.title_box) && <li>Title: {boxSummary(slide.title_box)}</li>}
                  {isPlaced(slide.body_box) && <li>Body: {boxSummary(slide.body_box)}</li>}
                </ul>
                <p className="mt-1">A save keeps those positions.</p>
                <Button size="xs" variant="ghost" className="mt-2"
                  disabled={!editing}
                  disabledReason={blockedReason || undefined}
                  onClick={() => setModel(withInheritedBoxes(model, current))}>
                  Use the layout’s positions
                </Button>
              </>
            ) : (
              <p>This slide’s title and body sit where its layout puts them.</p>
            )}
          </div>

          <p className="text-[0.75rem] text-on-surface-low">
            Pictures, tables, charts and per-character formatting are listed above if this
            deck has any; they are not carried through a save.
          </p>
        </div>
      )}
    </div>
  )
}
