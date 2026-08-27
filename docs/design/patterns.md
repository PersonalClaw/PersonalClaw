# Design-System Pattern Gallery

**Plan:** [DESIGN-SYSTEM-CONSISTENCY](../roadmap/plans/DESIGN-SYSTEM-CONSISTENCY.md) · **Contract:** C2 · **Authority:** `web/DESIGN.md` + `web/PRODUCT.md`

The canonical usage of each shared primitive + each interaction pattern. Every page-touching plan cites this to "stay consistent." A new shared primitive lands here the moment it's added (that's how it stops being a one-off). This is a static doc (zero new dep) — the plan's default over a live Storybook route.

> **Rule:** if you're about to hand-roll chrome that appears elsewhere, it belongs here as a primitive first. Bring outliers to the system; never fork the system.

---

## Form fields

### `TextField` / `TextArea` — `web/src/ui/TextField.tsx`

The one shared single-line input (`TextField`) and multi-line input (`TextArea`). The S1 audit found **no** shared field primitive, so ~200 raw `<input>`s across the app re-rolled the same shape by hand — `TextField` is the canonical extraction of that exact shape (not a redesign).

**Canonical shape (what it renders):**
`rounded-md · bg-surface-{container|high|base} · h-{8|9|10} · px-3 · text-on-surface · placeholder:text-on-surface-low · outline-none · focus:ring-2 focus:ring-inset focus:ring-primary · disabled:opacity-50`

Keyboard focus = the global `:focus-visible` ring (`design/tokens.css`) **plus** the established inset primary focus ring. Fully token-routed — no hardcoded colors/px.

**Props**

| Prop | Values | Default | Notes |
|---|---|---|---|
| `size` | `sm` (h-8) · `md` (h-9) · `lg` (h-10) | `md` | mirrors the measured call-site heights |
| `surface` | `container` · `high` · `base` | `container` | which surface token the field sits on |
| `mono` | boolean | `false` | monospace (ids, tokens, code-ish values) |
| `leadingIcon` | ReactNode | — | adds `pl-9` and an icon slot (TextField only) |
| `resize` (TextArea) | `y` · `none` | `y` | vertical resize grip |
| …native | any `<input>`/`<textarea>` attr | — | `value`, `onChange`, `disabled`, `aria-*`, `placeholder`, `ref`, … forwarded |

**Usage**

```tsx
import { TextField, TextArea } from '../../ui/TextField'
import { Search } from 'lucide-react'

// search box
<TextField size="sm" surface="base" leadingIcon={<Search size={14} />} placeholder="Search…"
  value={q} onChange={(e) => setQ(e.target.value)} />

// a form value
<TextField value={name} onChange={(e) => setName(e.target.value)} aria-label="Name" />

// multi-line body
<TextArea mono value={body} onChange={(e) => setBody(e.target.value)} placeholder="Notes…" />
```

**Migrate to it when** you see a raw `<input>`/`<textarea>` whose className contains the canonical shape above. The class contract is pinned by `web/src/ui/TextField.test.ts…x` (`textFieldClass`/`textAreaClass`) so a migration is a provable drop-in — but any migration that changes rendered pixels must still be verified by the visual harness (`web/e2e/`).

> **NOTE:** `<select>` is not yet wrapped — a `Select` primitive is a follow-up (its native chevron + `[color-scheme]` handling differ enough to warrant its own entry). Until then, style `<select>` to match the `TextField` shape.

---

## Buttons (existing — `web/src/ui/Button.tsx`, `IconButton.tsx`)

`Button` (variant `primary|secondary|ghost|danger`, size `sm|md|lg`, `shape`, `loading`) and `IconButton` are the canonical clickable chrome. The S1 audit found **420 raw `<button>`** outside `ui/` — migrating those to `Button`/`IconButton` is the largest S2 primitive-adoption task (harness-gated, worst-first: CodeCockpit → ChatPage). Documented here as the target; full variant gallery to be expanded as that migration proceeds.

### `SquareIconButton` — `web/src/ui/SquareIconButton.tsx`

The **dense square** sibling of the round `IconButton`: a 28px (`size-7`) `rounded-md`
hit area with a small glyph, for tight action clusters in list rows, card headers,
and content toolbars where the 40px round pill is too big. Extracted from **five**
hand-rolled `IconBtn` copies (two byte-identical settings versions + three `ui/`
near-variants) that shared this exact shape — a genuine missing primitive, not a
redesign.

**Canonical shape (what it renders):**
`grid size-7 place-items-center rounded-md transition-colors` · idle
`text-on-surface-low` · hover `bg-surface-high + text-on-surface` · `on`
(selected) coral tint (`text-primary` + a `color-mix` 14% primary bg chip) ·
`disabled` `opacity-40 cursor-not-allowed` (onClick suppressed) · `tone="danger"`
hover tints the glyph red (`text-danger`, **no** fill) for destructive
delete/remove actions. Press springs in via framer `whileTap`
(expressiveness-scaled, reduced-motion safe) — matching the animated `IconButton`
doctrine. Fully token-routed.

**Props**

| Prop | Values | Default | Notes |
|---|---|---|---|
| `icon` | `LucideIcon` | — | icon-component form; sizes via `iconSize` |
| `children` | ReactNode | — | alt to `icon`, for glyphs that swap on state (spinner⇄wifi, rotating chevron) |
| `label` | string | — | required; accessible name **and** default tooltip |
| `title` | string | `label` | tooltip override — use when the hover hint should differ from the accessible name (e.g. a gated-reason hint) |
| `tone` | `neutral \| danger` | `neutral` | `danger` = destructive action: hover tints the glyph red, no fill. Ignored while `on` |
| `on` | boolean | `false` | **selected/toggled** — carries the coral tint |
| `disabled` | boolean | `false` | **busy/unavailable** — dim + inert; kept distinct from `on` |
| `iconSize` | number | `14` | glyph size for the `icon` form |
| `onClick` | handler | — | suppressed while `disabled` |

> **Naming note:** the old copies overloaded `active` to mean *busy → disabled*
> (settings) in some places and *selected → coral* (ContentSurface) in others. The
> primitive splits that into orthogonal **`on`** (selected) and **`disabled`**
> (busy) — pinned by `SquareIconButton.test.tsx`.

**Usage**

```tsx
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Pencil, Trash2, Wifi, Loader2 } from 'lucide-react'

<SquareIconButton label="Test" onClick={runTest} disabled={testing}>
  {testing ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
</SquareIconButton>
<SquareIconButton label="Edit" on={editing} onClick={() => setEditing(v => !v)}>
  {editing ? <X size={14} /> : <Pencil size={14} />}
</SquareIconButton>
<SquareIconButton icon={Trash2} label="Remove server" tone="danger" onClick={remove} />
```

An absolutely-positioned toggle (e.g. a password field's show/hide eye) must wrap
the button in the positioning element, **not** put `-translate-y-1/2` on the button
itself — `whileTap` composes its own `transform` and would clobber the centering:

```tsx
<span className="absolute right-1.5 top-1/2 -translate-y-1/2">
  <SquareIconButton label={show ? 'Hide' : 'Show'} onClick={() => setShow(s => !s)}>
    {show ? <EyeOff size={14} /> : <Eye size={14} />}
  </SquareIconButton>
</span>
```

**Migrate to it when** you see a hand-rolled `size-7 … rounded-md … place-items-center`
icon `<button>`. The round pill (`IconButton`) and this square dense form are the
two canonical icon-action shapes — pick by density, don't hand-roll a third.

## Dialogs (existing — `web/src/ui/Modal.tsx`)

`Modal` is already the sole canonical dialog (the audit found **0** bespoke `<dialog>`/`role="dialog"` outside `ui/`). Keep it that way — the primitive-adoption ratchet (C1/T3.4) guards against regression.

---

## Typography weight

### `fvs(weight)` / `.fw-<n>` — `web/src/design/fontWeight.ts` + `tokens.css`

The app's variable font is driven by `font-variation-settings: "wght" <n>`. The audit found this set **inline ~180 times** across pages (75× `500`, 48× `600`, 42× `550`, 11× `470`, …) with no shared home. Two canonical ways to apply a weight (both emit the identical `font-variation-settings`):

- **`fvs(n)`** (inline style) — `style={fvs(500)}`; `withWeight(existingStyle, 550)` merges onto an existing style object. Byte-identical drop-in for the hand-written `{ fontVariationSettings: '"wght" 500' }`.
- **`.fw-<n>`** (className) — `.fw-400/470/500/550/600/650` in `tokens.css`; use `className="… fw-500"` when a class fits better.

Prefer a **type-role** (`data-type="title-m"` etc.) when the element maps to one — it sets size + weight together. Use `fvs()`/`.fw-*` for the many spots that only nudge weight on already-sized text. Allowed weights: `400 · 470 · 500 · 550 · 600 · 650`. Pinned by `fontWeight.test.ts`.

**Migrate to it when** you see `style={{ fontVariationSettings: '"wght" <n>' }}` — swap to `fvs(<n>)` (a provable zero-pixel drop-in; combined styles use `withWeight(existing, n)` or `{ ...other, ...fvs(n) }`). Migration progress: complete in `NotificationBell`, `HeaderActions`, `Button`, `Segmented`, `NavRail`, `FilterMenu`, `BoardCollapse`, `SystemWidget` (+ `NotificationsPage` partial). **True total 168** (measured by the scanner across all `.ts`/`.tsx`; ~22 migrated). A few (`liveMarkdown.ts`'s CodeMirror theme object) are editor-internals, not JSX inline styles — those stay. The count is now **ratcheted** in CI (`primitiveAdoption.baseline.json` → `inlineFontWeight: 168`): it may only shrink, and a new inline weight turns CI red.

---

## Interaction patterns (S3 — in progress)

The S3 pass standardizes and documents these here, one implementation each:

### Empty state — TWO distinct canonical patterns (don't conflate them)

The S1 audit found the name `EmptyState` was used for two genuinely different things. They are **distinct on purpose** — pick by context:

| Pattern | Component | Shape | Use when |
|---|---|---|---|
| **Page-empty** | `EmptyState` — `web/src/ui/ListScaffold.tsx` | Full-height **centered** column: tinted icon chip, headline, hint, optional `Button` action | A whole page/list/panel is empty (Tasks page with no tasks, empty Knowledge, etc.) — the empty state IS the content |
| **Slot-empty** | `SlotEmptyState` — `web/src/pages/dashboard/widgets/kit.tsx` | Compact **top-aligned strip**: small icon + one line + optional inline action, dashed hairline | A dashboard widget/slot sits next to full siblings in a grid — a stretched centered empty would read as a conspicuous void |

> **Cycle 6→7 note:** `kit.tsx`'s slot variant was renamed `EmptyState` → **`SlotEmptyState`** so the name no longer collides with the canonical page-empty primitive (the collision made two intentional patterns look like an accidental duplicate). Pure rename — zero visual change.
>
> **cy17 convergence:** the last two hand-rolled page-empties adopted `EmptyState` — `LoopsListPage`'s "No loops yet" (a Spark-branch drop-in) and `CodeSection`'s "No code projects yet" (its bare dim glyph normalized to the canonical tinted chip; its `size="sm"` CTA to the default md Button). The primitive's markup is now locked by `ListScaffold.test.tsx`. Remaining hand-rolled centered blocks are **not** page-empties and stay distinct: load-error / not-found variants (they carry a retry/back CTA and a danger/warn icon), filtered-empties (a list exists; nothing matches — a `TextLink` reset, not a `Button`), and true slot strips (`ChatActivityPanel`'s side-question panel). Don't force those onto `EmptyState`.

**Usage**
```tsx
// page/list empty (centered):
import { EmptyState } from '../../ui/ListScaffold'
<EmptyState icon={Inbox} title="No tasks yet" hint="Create one to get started."
  action={{ label: 'New task', icon: Plus, onClick: () => navigate('tasks/new') }} />

// dashboard slot empty (compact strip):
import { SlotEmptyState } from './kit'
<SlotEmptyState icon={CheckCheck}>All clear — nothing waiting on you.</SlotEmptyState>
```

Still to standardize + document:
- [x] **Empty state** — the two patterns above (page-empty vs slot-empty)
- [x] **Confirm dialog** — see below
- [x] **Loading / skeleton** — see below
- [x] **Selection** — see below
- [ ] **Error state** — inline (`alertDialog({ tone: 'danger' })` for imperative failures; a shared inline error banner primitive is a follow-up if the audit finds enough ad-hoc ones)

### Confirm / prompt / alert — `web/src/ui/dialog/`

The app-wide replacement for `window.confirm/prompt/alert` — imperative, styled, callable from anywhere (event handlers, catch blocks, plain modules). A single `<DialogHost>` in the shell renders them; all use the canonical `Modal`.

```tsx
import { confirm, confirmDelete, promptInput, alertDialog } from '../../ui/dialog'

if (!(await confirm({ title: 'Apply update?', body: '…' }))) return
if (!(await confirmDelete('schedule', job.name))) return          // danger-tinted delete
const name = await promptInput({ title: 'New file', label: 'Name' })
await alertDialog({ title: 'Could not save', body: err.message, tone: 'danger' })
```

- **`confirm(opts | string)`** → `Promise<boolean>`; `danger: true` for destructive.
- **`confirmDelete(entity, name?, opts?)`** — the dominant destructive pattern (danger tint, "Delete" label, "This cannot be undone.").
- **`promptInput` / `promptForm`** — single value / multi-field.
- **`alertDialog({ tone })`** — the canonical **error state** for imperative failures.

**Migrate to it when** you see raw `window.confirm`/`window.prompt` or an ad-hoc confirm modal.

### Loading / skeleton — `web/src/ui/ListScaffold.tsx`

One skeleton family, shaped like the real chrome so the first paint doesn't jump:

- **`Skeleton({ className })`** — the atom (a `.skeleton` shimmer block).
- **`ListSkeleton({ rows })`** — N placeholder rows shaped like `ListRow` (default list first-load).
- **`FormSkeleton({ sections, rows, title })`** — settings-form panels.
- **`CardGridSkeleton({ cards, cols, title })`** — card grids.
- **`Loading()`** — a plain "Loading…" line for tiny inline spots.

All carry `aria-busy`/`aria-label`. **Migrate to it when** you see a bespoke `animate-pulse` block or an ad-hoc spinner as a page's first-load state.

### Selection / list rows — `ListRow` (`web/src/ui/ListScaffold.tsx`)

`ListRow({ index, onClick, children, accent })` — the canonical list row: staggered rise+fade in, physical hover-lift/press when clickable, optional left `accent` rail. Consistent across every list page; use it rather than hand-rolling a `<div className="rounded-lg bg-surface-container …">` row.
