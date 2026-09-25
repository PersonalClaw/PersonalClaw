# Design-System Pattern Gallery

**Plan:** DESIGN-SYSTEM-CONSISTENCY · **Contract:** C2 · **Authority:** `web/DESIGN.md` + `web/PRODUCT.md`

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

---

## In-session navigation

### Session Map — `web/src/pages/chat/sessionMap*` + `SessionMap*.tsx`

The canonical **index of a long scrollable surface**: a typed mark per indexable event, a narrow
rail of discrete ticks beside the content, a preview card that answers "what is this one?", one
jump handler, and one "back to newest". Chat is its only consumer today — it **replaced** the
Activity → **Index** tab rather than sitting beside it (`CHANGELOG.md` records the deletion of the
tab, its list body and the `ChatActivity.index` model) — so the parts live under `pages/chat/`
rather than `ui/`, the way `SlotEmptyState` lives under `pages/dashboard/widgets/`. It is
documented here because it is the app's one answer to "index this scroller": a second outline
panel or "jump to section" list is the thing not to build.

| Part | File | Owns |
|---|---|---|
| **mark contract + derivation** | `sessionMap.ts` | `SessionMark`, `SESSION_MARK_KINDS`, `sessionMapMarks()`, `SESSION_MAP_MIN_MARKS`, the density preference |
| **current region** | `sessionMapRegion.ts` | `useVisibleTurns()` (the `IntersectionObserver`) + `currentMarkRange()` (pure) |
| **the rail** | `SessionMapRail.tsx` | ticks, the roving cursor, hover/reveal, the halo |
| **the preview card** | `SessionMapCard.tsx` | `sessionMapCardContent()`, `sessionMapMarkName()`, the card body |
| **coarse-pointer form** | `SessionMapDrawer.tsx` | one 44px row per mark, kind in **words** |
| **return-to-newest** | `SessionMapReturnLatest.tsx` | the app's **one** "back to the newest turn" pill |

#### Marks — the closed contract every consumer reads

`SessionMarkKind` is a **closed** seven-value union — `user · assistant · tool · approval · error ·
subagent · activity` — exported both as the type and as the runtime `SESSION_MARK_KINDS` array so a
consumer or test validates against the set instead of re-listing it. Adding a kind is a contract
change: every consumer switches on the union.

`sessionMapMarks(turns, subagents)` is the one pure derivation. It emits **one mark per turn**
(kind = its role), then one typed sub-event mark per `tool` / `approval` / `error` /
meaningful-`activity` segment **in segment order**, each inheriting the owning turn's role, jump
coordinate and timestamp; subagent marks are appended afterwards carrying the **last** turn's
coordinate. `text` / `thinking` segments emit nothing (the turn mark already stands for the body),
and an `activity` segment marks only when it carries an `activityKind` — a bare "Thinking…" line is
not index-worthy.

| Field | Meaning |
|---|---|
| `markIndex` | 0-based position in the returned array — stable identity and render key |
| `kind` | the closed vocabulary above |
| `role` | the **owning turn's** role; a sub-event inherits it, so tone/ARIA treat user vs. agent uniformly |
| `visibleIndex` | the **jump coordinate** (the turn's `visibleIndex`) — **not** `markIndex`: a turn emits up to seven marks that all share one |
| `ts` | the owning turn's timestamp, `''` when it carries none — never null |
| `preview` | one-line **plain text** (markdown stripped via `previewText`, capped at 140) for the row, the card and the accessible name |
| `ok?` | tool marks only: present and `false` on a failed call, `undefined` otherwise — carried because a tool's outcome cannot be recovered once the raw segment is gone |

> **As-built: `ok` has no consumer yet.** The derivation sets it, and nothing reads it — the rail
> paints exactly two tones (current / history), so a failed tool tick is today indistinguishable from
> a successful one. The field is the contract half of a danger paint that has not shipped; if you add
> it, read `ok` here rather than re-deriving failure from the segment.

**Self-suppression:** `SESSION_MAP_MIN_MARKS = 2`. One threshold, two behaviours — the rail returns
`null` before its first hook; the drawer, which the user deliberately opened, renders the canonical
`EmptyState` instead of a blank panel.

> **🔴 THE MARK VOCABULARY HAS A SECOND PRODUCER IN CORE — adding a kind is a contract change on
> BOTH sides.** `dashboard/chat_session_map.py` (`GET /api/chat/sessions/{session}/map`) is the
> deliberate **server-side mirror** of this derivation: same closed vocabulary, same field names,
> same jump coordinate, so its JSON array is directly assignable to `SessionMark[]`. Two *sources*
> for one shape, never two shapes — it exists because `visibleIndex` **is** the backend's
> `at_message_index` (what fork and edit-resend speak), so the durable endpoint hands a map row a
> preview and telemetry back without waiting on `hydrateTurns`. Two consequences a frontend reader
> needs:
>
> - **The durable half can only witness FIVE of the seven kinds** (`PERSISTED_MARK_KINDS` — `user ·
>   assistant · tool · approval · error`). `subagent` rides a WS stream never written to the
>   conversation log, `activity` segments are live-only, and `approval` survives a restart once
>   it is decided (a pending one is held back from the save). Run the frontend's own
>   `sessionMapMarks()` (`web/src/pages/chat/sessionMap.ts:156`) over the hydrated turns when you
>   need the live kinds; read the endpoint when you need durability.
> - **The endpoint's `preview` is not always the first 140 characters.** It prefers a persisted
>   per-turn *summary label* when one is present, because an assistant mark's raw opening is usually
>   a preamble; the client derivation has no such label and always previews the text.
>
> The rail today renders the **client** derivation — nothing in `web/src/lib/api.ts` fetches the map
> endpoint — so the two producers do not yet meet in this surface. That is what makes the mirror easy
> to break silently: change a kind here and only the Python side's own tests notice.

**Density** is a persisted **view** preference, not config: `detailed` (default, every mark) or
`turns` (turn marks only, for a tool-heavy session whose sub-event marks are the majority). It is
registered as a `select` token under `--session-map-density` in `design/tokenRegistry.ts`, so the
appearance store persists it and `DesignPanel` renders the control for free — deliberately **not** a
`config.json` field, because nothing about it needs to sync across devices. `localStorage` is
untrusted input, so `asSessionMapDensity()` narrows a stored string to the vocabulary and falls back
to the **named** default (not to an array position). `sessionMapDensityMarks()` filters **and
re-indexes**: `markIndex` is documented as a position in the returned array and `sessionMapMarkName`
counts "Turn X of N" over the list it is handed, so a filtered list carrying pre-filter indices
would keep the field name and break its meaning.

> **Apply density once, in the host.** `ChatPage` filters before handing the array to either form
> (`ChatPage.tsx` — `sessionMarks`), because the rail and the drawer must index the **identical**
> array: a "Turn 3 of 7" that means a different 7 in each form is two maps, not one.

#### Current region — the accent means "what is on screen"

Two pieces, split so the arithmetic is testable without a DOM:

- **`useVisibleTurns(turnNodes, scrollRef, coords)`** — an `IntersectionObserver` over the host's
  live node registry, rooted on the **transcript scroll container**, not the window (the transcript
  is a pane with a header above and a composer below; "in the window" and "in the pane" are
  different rectangles). It accumulates across callbacks, because an observer reports only what
  *changed* — one entry is not a statement about the other turns.
- **`currentMarkRange(marks, visibleTurns)`** — pure; returns the **inclusive** `[lo, hi]` slice, and
  `[0, -1]` for empty so `i >= lo && i <= hi` is simply false for every index rather than needing a
  null check.

> **🔑 The binary search is a contract, not an optimisation.** `visibleIndex` is non-decreasing
> across the mark array (sub-events inherit their turn's coordinate; subagents carry the last
> turn's), which is what lets a *range of turns* become a *contiguous slice of marks* — and that is
> why the accent can be a range test instead of per-mark set membership. `sessionMapRegion.test.ts`
> asserts the ordering directly rather than trusting it.
>
> **🪤 The re-observe key is the coordinate LIST, and choosing it is the whole design.** The node
> registry is a mutated-in-place `useRef` Map, so its identity can never say "a new turn mounted";
> the marks array changes on **every streamed token** (a preview grows), so keying on it would tear
> down and rebuild the observer once per token. The coordinate list is the discriminator that
> separates the two events. Follow this if you index a streaming surface.
>
> **🪤 The empty visible set is not a placeholder branch.** Between mount and the observer's first
> callback — and in any environment with no layout — the honest answer is the newest turn, which is
> where an unscrolled surface *is*. One total function, not a second mechanism to keep in step.

Tone carries **position-in-session and nothing else**: the current region paints `--color-primary`
(One-Voice — coral means "the agent / live / current"), history paints `--color-on-surface-low`. The
track is a 1px `--color-rail` hairline, never a full-height coloured side-stripe (the Tone-Not-Line
rule, enforced by `sideStripeDoctrine.test.ts`).

#### Preview card — "what is this one?"

Each mark is the trigger for a per-mark `ui/Popover` (portalled, `placement="bottom"`, 300px — a
preview line wants ~45-55 characters; wider and the card occludes the very turn it describes). The
frosted material, corner, entrance spring, viewport clamp, z-index, single-layer Escape and
focus-restore all come from the shared primitive; the card owns only its body. Pointer intent is
two delays: **150ms to open** (so a pointer merely *crossing* the rail does not strobe a card per
tick) and **120ms to close** (the bridge that lets the pointer leave a 4px tick, cross the gap and
land on the card to read or select the excerpt).

`sessionMapCardContent(marks, index)` is pure and derives from **the mark list, not the
transcript** — which keeps the rail a pure function of `SessionMark[]` instead of re-hydrating
turns behind its back. It yields `roleLabel` ("You" / "Assistant", the words the transcript already
uses), `ts`, `request`, `response`, `turnPosition`, `turnTotal`. The **request** is the nearest
`user` mark at or before this one; the **response** is the mark's **own** preview *except* when the
mark is the request itself, in which case it is the next assistant mark.

> **🪤 That exception is what stops seven identical cards.** A turn emits up to seven marks sharing
> one coordinate and one timestamp, so a card built from the exchange alone would answer "what is
> this one?" with "the same as its neighbour". The response slot shows the mark's own line — the tool
> line for a tool tick, the error text for an error tick.
>
> **🔑 Exactly one card open is a property of focus, not coordination.** `Popover.openSignal` is
> one-way (nothing reaches `setOpen(false)`), so revealing card *j* while *i* was open would stack
> them. Instead the card the cursor opened belongs to the mark that has **focus**, which the platform
> already keeps a singleton, and a mark closes its own card on `blur`. `kbOpen` records *who opened
> it*, because a pointer-opened card must not be dismissed by the blur that a click **into** the card
> causes.

An unparseable timestamp renders **nothing** rather than a placeholder — `lib/epoch`'s contract — so
a turn with no `ts` shows a card with no clock instead of "Invalid Date".

#### Accessibility

- **Landmark, once.** The rail is `<nav aria-label="Session map">`. The drawer adds **no** nav
  landmark: it renders inside `ui/SidePanel`, already a `role="region"` named "Session map", and
  nesting two same-named regions for one surface is the defect.
- **One tab stop, roving `tabIndex`.** The slot starts on the current region's first mark; arrows
  move it, `Home`/`End` go to the ends, `PageUp`/`PageDown` step 5 **marks** (not turns — a turn can
  emit seven ticks, so a turn-sized page is a different distance every press), `Enter`/`Space` jump.
  `preventDefault()` fires only for handled keys: it stops the arrows scrolling the surface out from
  under the cursor **and** stops the browser's own button activation firing a second jump.
- **Passive focus must not open the card; an explicit cursor key must.** The rail sits between the
  content and the composer, so Tab-through would flash a card on the way past — but a sighted
  keyboard user arrowing down 4px ticks with no card is navigating blind. The discriminator is
  **intent**: a cursor key sets a one-shot `reveal` immediately before moving focus and the receiving
  `onFocus` consumes it. Tab never sets it.
- **Accessible name:** `sessionMapMarkName` → `Turn N of M: <subject>`, the subject bounded to 40
  chars through `lib/rowSubject`. Built from the **mark's own** preview, not the exchange's request —
  naming from the exchange would give one turn's seven ticks one name, the duplicate-name defect
  `computedNames.test.tsx` measured at ×83 elsewhere.
- **Live region:** `role="status" aria-live="polite"`, and it announces **only** the jump ("Jumped to
  turn N of M") and only while the rail holds focus. The name already states the cursor position on
  every move, so repeating it would be double-speak; what a screen-reader user cannot otherwise tell
  is whether the *content* moved, since focus stays on the rail. It clears when focus leaves the rail
  entirely, so a later hover cannot re-announce a jump nobody made.
- **Focus ring:** no `outline-none` anywhere — the mark takes the global `:focus-visible` ring
  (`tokens.css`, 2px opaque `--color-primary`, no alpha) rather than minting a local one.
- **Hit target:** `.hit-24-x` gives each 4px tick the 24px pressable band (WCAG 2.2 SC 2.5.8) that
  `NavRail`'s splitter uses. The band is **horizontal only, deliberately** — it pins to the element's
  own vertical extent, and on a rail whose ticks sit a few px apart a vertical 24px band would
  swallow its neighbours.
- **🔴 The rail's `ml-4` is load-bearing clearance, and it must not become a t-shirt token.** The
  rail mounts against the shell's content-column edge, where `NavRail`'s splitter wears the same
  `.hit-24-x` band plus `z-10`. Measured at 1280×420 on a 196px nav, `elementFromPoint` at every
  tick's own centre returned the **splitter**, 6 of 6 — every mark un-clickable by pointer at the
  default nav width. `ml-4` is a real 16px at every density; `--spacing-l` is `16px *
  var(--space-scale)` and `tokens.css` drops that scale to 0.8 and 0.68, so `ml-l` would be 12.8px
  and 10.9px against a fixed 14px requirement and the stolen target would return, invisibly, for
  exactly the users who chose tighter spacing.
- **Touch:** drawer rows are `min-h-11` (44px). `.hit-24-x` is deliberately **not** reused there —
  it grows a hairline to the 24px *pointer* minimum, which is a different and smaller target than a
  thumb. The drawer also carries kind as **words** (`KIND_LABEL`), not tone: it has no current-region
  observer, and per-kind colour would mint a third vocabulary for one closed set.
- **Reduced motion:** the halo runs on `physics.snappy` and the drawer rows and pill on `spring.*` —
  gated getters, so `prefers-reduced-motion` collapses them through `design/motion`'s single
  off-switch (`reducedMotionAppWide.test.ts`), never a hand-rolled spring.
- **Contrast:** a mark is a UI component, not text, so **SC 1.4.11 at 3:1** governs it — not 1.4.3's
  4.5, which a 4px tick is not subject to. `design/schemeContrast.test.ts` measures both mark tones
  on the rail's real ground across 12 schemes × 2 modes: worst **4.37** (light / coral / current),
  best 10.87. It parses the tones out of the component rather than restating them, so a repaint is
  followed rather than silently un-measured.

> **Two measured a11y limitations are recorded and deliberately NOT asserted** — each needs a design
> ruling the map does not own, and an invented assertion would either be red on arrival or bless the
> defect:
>
> 1. **The current region is encoded by hue alone.** `--color-primary` vs `--color-on-surface-low`
>    measures **1.004:1** (dark / coral) and never exceeds 1.944 in any scheme × mode. The accessible
>    name never says "current" and the halo paints only on hover/focus, so a reader with
>    achromatopsia cannot locate the current region. That is an **SC 1.4.1 (Use of Color)** question
>    about the *encoding*, and fixing it means a second visual channel (size / shape / ring). What
>    *is* asserted is the floor that keeps the question answerable: the two tones must remain two
>    different tokens, so collapsing them to one reds.
> 2. **The track is invisible in light mode.** `--color-rail` is `#f0f4f8` and so is
>    `--color-canvas` — byte-identical, **1.000:1** (1.163 in dark). No WCAG rule is broken: the
>    track is `aria-hidden` and documented decorative ("the marks are the targets"). But the tone is
>    shared with `NavRail`, which uses it as a *background* rather than a hairline, so a retint is a
>    cross-surface change with visual baselines attached, not a local fix.

Both forms are gated in a real browser: `web/e2e/a11y.spec.ts` runs axe with the desktop rail
**open** and its card open, and on the 390px drawer, each with a reachability floor so a clean axe
result cannot come from a surface it never visited; `web/e2e/sessionMap.spec.ts` walks the keyboard
path (Tab reaches the rail, arrows rove the single tab stop, `Enter` and `Space` each move the
transcript in opposite directions so neither can pass on the other's scroll), asserts scroll-fixity,
and reads the rail's real painted backdrop so re-parenting it reds loudly.

**Usage**

```tsx
import { sessionMapMarks, sessionMapDensityMarks, asSessionMapDensity } from './chat/sessionMap'
import { SessionMapRail } from './chat/SessionMapRail'
import { SessionMapDrawer } from './chat/SessionMapDrawer'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'

// derive once, filter once — both forms index the identical array
const all = useMemo(() => sessionMapMarks(turns, subagents), [turns, subagents])
const marks = useMemo(() => sessionMapDensityMarks(all, density), [all, density])

// 🔑 the rail is a SIBLING of the scroller, never a child: an element outside the scroll
// container has no scroll offset to inherit, so no `sticky`, no scroll listener and no
// re-positioning code exists to get wrong.
<div className="relative flex min-h-0 flex-1">
  {mapOpen && !isMobile && (
    <SessionMapRail marks={marks} turnNodes={turnNodes.current}
      scrollRef={scrollRef} onJumpTo={jumpToTurn} />
  )}
  <div ref={scrollRef} data-transcript-scroll className="relative min-w-0 flex-1 overflow-y-auto">
    {/* …content… */}
    <SessionMapReturnLatest scrolledUp={scrolledUp} onReturnToLatest={() => scrollToLatest(endRef.current)} />
  </div>
</div>

// coarse pointer: ONE named header control opens a drawer over the same marks + same jump
<SidePanel title="Session map" fillHeight onClose={() => setMapOpen(false)}>
  <SessionMapDrawer marks={marks} onJumpTo={jump} />
</SidePanel>
```

One header control (`ariaExpanded`, not `active` — it is a disclosure, not a setting) shows the rail
on a pointer device and opens the drawer on the mobile form, so there is never a second name for
"show me the map", and it is `priority="primary"` so it cannot shed into the overflow menu — on
mobile that control **is** the in-session navigation.

**Migrate to it when** you are about to index a long scrollable surface: a marks rail, an outline
panel, a "jump to section" list, or a second back-to-newest control. **Never add a second
return-to-newest** — `SessionMapReturnLatest.test.tsx` derives single-implementation over the whole
`web/src` tree, so a duplicate anywhere turns red rather than shipping two names for one intent.
Tests key off the stable `data-session-map-*` / `data-session-mark` hooks (plus `data-kind` and
`data-current`); keep them when you touch the markup.

> **NOTE: not yet a generic `ui/` primitive.** The derivation is typed to chat's
> `ChatTurn` / `Segment` / `SubagentCard`, so a second consumer means extracting `sessionMapMarks`
> behind an adapter (the rail, card, region and drawer are already pure over `SessionMark[]` and need
> no change) — **not** copying the rail. The mark vocabulary is closed for the same reason: a new kind
> is a contract change every consumer switches on.
