import { useState } from 'react'
import { Sun, Moon, Monitor, Check, Plus, Trash2, ChevronDown, RotateCcw, Sliders, Boxes, Layout as LayoutIcon, Type, Save } from 'lucide-react'
import { Surface } from '../../ui/Surface'
import { Button } from '../../ui/Button'
import { Field, TextInput } from '../tasks/formControls'
import { ColorControl, ScalarControl, SelectControl } from '../../ui/TokenControls'
import { TOKENS, type ColorToken, type ScalarToken, type SelectToken } from '../../design/tokenRegistry'
import { useAppearance } from '../../app/appearance'
import { useMode, type Preference } from '../../app/theme'
import { COLOR_GROUPS, BACKDROP_GROUPS, TYPOGRAPHY_GROUPS, LAYOUT_GROUPS, type Scheme } from '../../design/schemes'

/** Design subpage. Cleanly separated concerns:
 *   1. COLOR SCHEME — pick a curated scheme (swatches) or fork your own. Colors only.
 *   2. BACKDROP & MOTION — the 3D dot-wave surface + animation, as their own controls.
 *   3. LAYOUT & SHAPE — content width + corner roundness.
 *  A scheme encapsulates ONLY colors; backdrop/motion/layout live outside it. */
export function DesignPanel() {
  const { activeScheme, allSchemes, applyScheme, saveCustomScheme, updateCustomScheme, deleteCustomScheme, themesLoading, resetAll } = useAppearance()
  const { mode, preference, setPreference } = useMode()
  const [editingColors, setEditingColors] = useState(false)
  const dark = mode === 'dark'
  const isCustom = (id: string) => id.startsWith('custom:') && id !== 'custom:unsaved'
  // The active scheme, if it is a SAVED custom theme (enables in-place "Update").
  const activeSaved = isCustom(activeScheme) ? allSchemes.find((s) => s.id === activeScheme) : undefined
  const MODES: { key: Preference; label: string; icon: typeof Sun }[] = [
    { key: 'dark', label: 'Dark', icon: Moon }, { key: 'light', label: 'Light', icon: Sun }, { key: 'auto', label: 'Auto', icon: Monitor },
  ]

  return (
    <div className="flex flex-col gap-2xl">
      {/* ── 1. COLOR SCHEME ── */}
      <section>
        <div className="flex items-center justify-between mb-m">
          <div>
            <h2 className="text-on-surface text-[1rem]" style={{ fontVariationSettings: '"wght" 600' }}>Color scheme</h2>
            <p className="text-on-surface-low text-[0.8125rem] mt-0.5">A scheme is the system's color identity. Tuning the <strong className="text-on-surface-var">{mode}</strong> theme.</p>
          </div>
          <div className="inline-flex rounded-pill bg-surface-container p-1">
            {MODES.map((m) => {
              const on = preference === m.key
              return (
                <button key={m.key} onClick={() => setPreference(m.key)} title={m.key === 'auto' ? 'Follow the system theme' : undefined}
                  className="inline-flex items-center gap-1.5 rounded-pill px-m h-8 text-[0.8125rem] transition-colors"
                  style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                  <m.icon size={14} /> {m.label}
                </button>
              )
            })}
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-m">
          {allSchemes.map((s) => (
            <SchemeTile key={s.id} scheme={s} dark={dark} active={activeScheme === s.id} custom={isCustom(s.id)}
              onPick={() => applyScheme(s.id)}
              onDelete={isCustom(s.id) ? () => deleteCustomScheme(s.id).catch(() => {}) : undefined} />
          ))}
        </div>
        {themesLoading && <p className="mt-s text-on-surface-low text-[0.75rem]">Loading saved themes…</p>}

        {/* fork → custom (colors only) */}
        <div className="mt-l">
          <button onClick={() => setEditingColors((v) => !v)} className="flex items-center gap-s text-on-surface-var text-[0.875rem]">
            <ChevronDown size={16} className={`transition-transform ${editingColors ? 'rotate-180' : ''}`} />
            <Sliders size={15} /> Edit colors &amp; save a custom theme
          </button>
          {activeScheme === 'custom:unsaved' && !editingColors && (
            <p className="mt-1.5 text-on-surface-low text-[0.8125rem]">You've edited colors — open this to save them as a shareable theme.</p>
          )}
          {editingColors && <ColorEditor onSave={saveCustomScheme} onUpdate={updateCustomScheme} activeTheme={activeSaved} />}
        </div>
      </section>

      {/* ── live preview ── */}
      <section>
        <h2 className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-s">Preview</h2>
        <Preview />
      </section>

      {/* ── 2. TYPOGRAPHY (zoom / font size / typeface) ── */}
      <ControlSection title="Typography & scale" icon={Type} subtitle="Whole-UI zoom, text size, and the interface typeface." groups={TYPOGRAPHY_GROUPS} />

      {/* ── 3. BACKDROP & MOTION (separate from the scheme) ── */}
      <ControlSection title="Backdrop & motion" icon={Boxes} subtitle="The 3D dot-wave surface and animation — independent of the color scheme." groups={BACKDROP_GROUPS} />

      {/* ── 4. SHAPE ── (content width is a preset in Account → Layout) */}
      <ControlSection title="Layout & shape" icon={LayoutIcon} subtitle="Interface density (comfortable / dense / CLI) and corner roundness." groups={LAYOUT_GROUPS} />

      <div>
        <Button variant="ghost" size="sm" onClick={resetAll}><RotateCcw size={15} /> Reset everything to defaults</Button>
      </div>
    </div>
  )
}

function SchemeTile({ scheme, dark, active, custom, onPick, onDelete }: { scheme: Scheme; dark: boolean; active: boolean; custom: boolean; onPick: () => void; onDelete?: () => void }) {
  const sw = dark ? scheme.swatch.dark : scheme.swatch.light
  const swAlt = dark ? scheme.swatch.light : scheme.swatch.dark
  const emoji = custom && scheme.emoji && !scheme.emoji.startsWith('icon:') ? scheme.emoji : null
  return (
    <div className="group relative">
      <button type="button" onClick={onPick}
        className="w-full flex flex-col gap-2 rounded-xl p-2.5 transition-all text-left"
        style={{ background: 'var(--color-surface-container)', outline: active ? '2px solid var(--color-primary)' : '1px solid var(--color-outline-variant)', outlineOffset: active ? '0' : '-1px' }}>
        <div className="h-12 w-full rounded-lg grid place-items-center text-[1.5rem]" style={{ background: `linear-gradient(135deg, ${sw} 55%, ${swAlt} 55%)` }}>
          {emoji && <span aria-hidden>{emoji}</span>}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-on-surface text-[0.8125rem] truncate" style={{ fontVariationSettings: '"wght" 500' }}>{scheme.label}</span>
          {active && <Check size={13} className="text-primary shrink-0" />}
          {custom && <span className="ml-auto text-on-surface-low text-[0.6rem] uppercase tracking-wide rounded-pill bg-surface-high px-1.5 shrink-0">saved</span>}
        </div>
      </button>
      {onDelete && (
        <button type="button" onClick={onDelete} title="Delete saved theme"
          className="absolute top-1 right-1 size-6 grid place-items-center rounded-pill bg-surface-high text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity hover:text-danger"><Trash2 size={12} /></button>
      )}
    </div>
  )
}

function Preview() {
  return (
    <Surface tone="container" radius="lg" className="p-l">
      <div className="flex gap-m" style={{ minHeight: 150 }}>
        <div className="w-32 shrink-0 rounded-lg p-2 flex flex-col gap-1" style={{ background: 'var(--color-rail)' }}>
          {['Chat', 'Tasks', 'Triggers', 'Knowledge'].map((it, i) => (
            <div key={it} className="flex items-center gap-1.5 rounded-md px-2 h-7 text-[0.7rem]" style={i === 0 ? { background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)', color: 'var(--color-primary)' } : { color: 'var(--color-on-surface-low)' }}>
              <span className="size-1.5 rounded-pill" style={{ background: i === 0 ? 'var(--color-primary)' : 'var(--color-outline)' }} />{it}
            </div>
          ))}
        </div>
        <div className="flex-1 flex flex-col gap-2">
          <div className="rounded-lg p-3" style={{ background: 'var(--color-surface)' }}>
            <div className="h-2.5 w-24 rounded-pill mb-2" style={{ background: 'var(--color-on-surface)' }} />
            <div className="h-2 w-40 rounded-pill mb-1" style={{ background: 'var(--color-on-surface-low)' }} />
            <div className="h-2 w-32 rounded-pill" style={{ background: 'var(--color-on-surface-low)' }} />
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-pill px-m h-8 text-[0.75rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>Primary action</span>
            <span className="inline-flex items-center rounded-pill px-m h-8 text-[0.75rem]" style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>Secondary</span>
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-ok)' }} />
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-warn)' }} />
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-danger)' }} />
          </div>
          <div className="h-6 rounded-lg" style={{ background: 'linear-gradient(90deg, var(--grad-1), var(--grad-2), var(--grad-3), var(--grad-4))' }} />
        </div>
      </div>
    </Surface>
  )
}

/** A group of non-color token controls (scalars + selects), always visible. */
function ControlSection({ title, icon: Icon, subtitle, groups }: { title: string; icon: typeof Boxes; subtitle: string; groups: string[] }) {
  const tokens = TOKENS.filter((t) => groups.includes(t.group))
  if (!tokens.length) return null
  return (
    <section>
      <div className="mb-m flex items-center gap-s">
        <Icon size={16} className="text-primary" />
        <div>
          <h2 className="text-on-surface text-[1rem]" style={{ fontVariationSettings: '"wght" 600' }}>{title}</h2>
          <p className="text-on-surface-low text-[0.8125rem]">{subtitle}</p>
        </div>
      </div>
      <Surface tone="container" radius="lg" className="px-l py-m">
        <div className="divide-y divide-outline-variant/30">
          {tokens.map((t) =>
            t.kind === 'select' ? <SelectControl key={t.varName} token={t as SelectToken} />
              : <ScalarControl key={t.varName} token={t as ScalarToken} />,
          )}
        </div>
      </Surface>
    </section>
  )
}

const THEME_EMOJI_CHOICES = ['🎨', '🌊', '🌇', '🌿', '🔥', '🌙', '⭐', '🍑', '💜', '🩵', '🌸', '🖤']

/** Color-only editor (the scheme's tokens), with save-as-new + update-in-place
 *  (both server-persisted). `activeTheme` is the currently-active SAVED theme, if
 *  any — its presence enables overwriting it in place with the current colors. */
function ColorEditor({ onSave, onUpdate, activeTheme }: {
  onSave: (label: string, emoji?: string) => Promise<string>
  onUpdate: (id: string, label: string, emoji?: string) => Promise<void>
  activeTheme?: Scheme
}) {
  const [name, setName] = useState('')
  const [emoji, setEmoji] = useState('🎨')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const save = async () => {
    if (!name.trim() || busy) return
    setBusy('save'); setErr('')
    try { await onSave(name.trim(), emoji); setName('') }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save theme') }
    finally { setBusy('') }
  }
  const update = async () => {
    if (!activeTheme || busy) return
    setBusy('update'); setErr('')
    try { await onUpdate(activeTheme.id, activeTheme.label, activeTheme.emoji) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to update theme') }
    finally { setBusy('') }
  }
  return (
    <div className="mt-l flex flex-col gap-l">
      {activeTheme && (
        <Surface tone="container" radius="lg" className="px-l py-m">
          <div className="flex items-center justify-between gap-s">
            <p className="text-on-surface-var text-[0.8125rem]">
              Editing the saved theme <strong className="text-on-surface">{activeTheme.emoji && !activeTheme.emoji.startsWith('icon:') ? `${activeTheme.emoji} ` : ''}{activeTheme.label}</strong> — save your changes back to it.
            </p>
            <Button size="sm" variant="ghost" onClick={update} disabled={!!busy}><Save size={15} /> {busy === 'update' ? 'Updating…' : 'Update theme'}</Button>
          </div>
        </Surface>
      )}
      <Surface tone="container" radius="lg" className="px-l py-m">
        <Field label={activeTheme ? 'Or save as a new theme' : 'Save these colors as a shareable theme'}>
          <div className="flex flex-wrap items-center gap-1.5 mb-s">
            {THEME_EMOJI_CHOICES.map((e) => (
              <button key={e} type="button" onClick={() => setEmoji(e)}
                className="size-8 grid place-items-center rounded-lg text-[1.05rem] transition-colors"
                style={emoji === e ? { background: 'color-mix(in srgb, var(--color-primary) 20%, transparent)', outline: '1.5px solid var(--color-primary)' } : { background: 'var(--color-surface-high)' }}>{e}</button>
            ))}
          </div>
          <div className="flex items-end gap-s">
            <div className="flex-1"><TextInput value={name} onChange={setName} placeholder="My theme" /></div>
            <Button size="sm" onClick={save} disabled={!name.trim() || !!busy}><Plus size={15} /> {busy === 'save' ? 'Saving…' : 'Save theme'}</Button>
          </div>
        </Field>
        {err && <p className="mt-s text-danger text-[0.8125rem]">{err}</p>}
      </Surface>
      {COLOR_GROUPS.map((group) => {
        const tokens = TOKENS.filter((t) => t.group === group)
        if (!tokens.length) return null
        return (
          <Surface key={group} tone="container" radius="lg" className="px-l py-m">
            <h3 className="text-on-surface-var mb-1 uppercase tracking-wide text-[0.7rem]">{group}</h3>
            <div className="divide-y divide-outline-variant/30">
              {tokens.map((t) => <ColorControl key={t.varName} token={t as ColorToken} />)}
            </div>
          </Surface>
        )
      })}
    </div>
  )
}
