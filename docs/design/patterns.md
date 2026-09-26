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

The canonical **index of a long scrollable surface**: one marker per thing a reader will want to go
back to, a narrow rail of uniform markers beside the content, a preview card that answers "what is
this one?", one jump handler, and one "back to newest". Chat is its only consumer today — it
**replaced** the Activity → **Index** tab rather than sitting beside it (`CHANGELOG.md` records the
deletion of the tab, its list body and the `ChatActivity.index` model) — so the parts live under
`pages/chat/` rather than `ui/`, the way `SlotEmptyState` lives under `pages/dashboard/widgets/`. It
is documented here because it is the app's one answer to "index this scroller": a second outline
panel or "jump to section" list is the thing not to build.

| Part | File | Owns |
|---|---|---|
| **typed index + map entries** | `sessionMap.ts` | `SessionMark`, `SESSION_MARK_KINDS`, `sessionMapMarks()`; `SessionMapEntry`, `sessionMapEntries()`; `SESSION_MAP_MIN_MARKS` |
| **current region** | `sessionMapRegion.ts` | `useVisibleTurns()` (the `IntersectionObserver`) + `currentMarkRange()` (pure) |
| **the rail** | `SessionMapRail.tsx` | the markers, the roving cursor, the one expanded marker, the reveal |
| **the preview card** | `SessionMapCard.tsx` | `sessionMapMarkName()`, the card body |
| **coarse-pointer form** | `SessionMapDrawer.tsx` | one 44px row per user message, the card's content inlined |
| **return-to-newest** | `SessionMapReturnLatest.tsx` | the app's **one** "back to the newest message" control |

#### The owner's rules (2026-09-25)

The owner ruled on the form property by property, and each rule replaced something an earlier form
did. They are the spec; the rail's header restates them beside the code.

1. **A marker is a user message.** Replies and their tool calls are not markers: a reply is too long
   to show, and its opening is already on the question's card.
2. **Every marker rests at one length.** At rest, length encodes nothing — not structure, not
   position in the session.
3. **What is on screen is said by colour alone**, never by size, so scrolling the transcript never
   moves the rail's geometry.
4. **Only the marker under the pointer or the keyboard cursor expands**, beyond the resting length and
   in a brighter tone, with its card beside it. Its neighbours do not move, and it returns to the
   resting length when the pointer or focus leaves.
5. **Skimming moves nothing but the card.** Pointing and arrowing preview; only a click, `Enter` or
   `Space` jumps the transcript.

The rail stays put while the transcript scrolls (it is a sibling of the scroller — see **Usage**),
the return-to-newest control is a circular button, and the phone drawer follows the same rules.

#### The typed index — the contract the durable endpoint mirrors

`SessionMarkKind` is a **closed** seven-value union — `user · assistant · tool · approval · error ·
subagent · activity` — exported both as the type and as the runtime `SESSION_MARK_KINDS` array so a
consumer or test validates against the set instead of re-listing it. The rail no longer draws every
kind (rule 1), and the vocabulary stays anyway: it is the shape the durable endpoint mirrors, and the
map's entries are grouped from it.

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
| `role` | the **owning turn's** role; a sub-event inherits it |
| `visibleIndex` | the **jump coordinate** (the turn's `visibleIndex`) — **not** `markIndex`: a turn emits up to seven marks that all share one |
| `ts` | the owning turn's timestamp, `''` when it carries none — never null |
| `preview` | one-line **plain text** (markdown stripped via `previewText`, capped at 140) |
| `ok?` | tool marks only: present and `false` on a failed call, `undefined` otherwise — carried because a tool's outcome cannot be recovered once the raw segment is gone |

> **As-built: `ok` has no consumer.** The derivation sets it and nothing reads it — the map draws
> user messages only, so there is no tool marker to paint. If a failure paint ever returns, read `ok`
> here rather than re-deriving failure from the segment.

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
>   `sessionMapMarks()` over the hydrated turns when you need the live kinds; read the endpoint when
>   you need durability.
> - **The endpoint's `preview` is not always the first 140 characters.** It prefers a persisted
>   per-turn *summary label* when one is present, because an assistant mark's raw opening is usually
>   a preamble; the client derivation has no such label and always previews the text.
>
> The rail today renders entries grouped from the **client** derivation — nothing in
> `web/src/lib/api.ts` fetches the map endpoint — so the two producers do not yet meet in this
> surface. That is what makes the mirror easy to break silently: change a kind here and only the
> Python side's own tests notice (`tests/test_session_map_endpoint.py` parses `SESSION_MARK_KINDS`
> out of `sessionMap.ts`).

#### Entries — what the map shows

`sessionMapEntries(turns)` returns **one entry per user message**. It is built by **grouping** the
typed index by exchange rather than by re-walking the turns, so an entry and the endpoint's marks
agree about coordinates and previews by construction.

| Field | Meaning |
|---|---|
| `markIndex` | 0-based position among the entries — the render key, and the N of "Message N of M" |
| `visibleIndex` | the user message's jump coordinate (`markCoordOf`) — what `onJumpTo` receives |
| `coords` | **every** turn coordinate in the exchange, in order: the message, then each reply turn up to the next user message |
| `ts` | the user message's timestamp, `''` when it carries none |
| `preview` | the user message, one line of plain text |
| `response` | the opening of the reply, one line of plain text — `''` until reply text has arrived |

Agent output that precedes the first user message (an agent-initiated or resumed session) opens no
entry: there is no question to file it under.

**Self-suppression:** `SESSION_MAP_MIN_MARKS = 2` entries. One threshold, two behaviours — the rail
returns `null` before its first hook; the drawer, which the user deliberately opened, renders the
canonical `EmptyState` instead of a blank panel.

> **Derive once, in the host.** `ChatPage` derives `sessionEntries` once and hands the same array to
> both forms, because the rail and the drawer must index the **identical** array: a "Message 3 of 7"
> that means a different 7 in each form is two maps, not one. There is no density preference to
> filter by — the `detailed`/`turns` setting was deleted along with the per-event marks it filtered.

#### Current region — the colour means "what is on screen"

Two pieces, split so the arithmetic is testable without a DOM:

- **`useVisibleTurns(turnNodes, scrollRef, coords)`** — an `IntersectionObserver` over the host's
  live node registry, rooted on the **transcript scroll container**, not the window (the transcript
  is a pane with a header above and a composer below; "in the window" and "in the pane" are
  different rectangles). It observes **every coordinate an entry owns** — the question and each turn
  of its answer — and accumulates across callbacks, because an observer reports only what *changed*.
- **`currentMarkRange(entries, visibleTurns)`** — pure; returns the **inclusive** `[lo, hi]` slice,
  and `[0, -1]` for empty so `i >= lo && i <= hi` is simply false for every index.

> **🔑 A coordinate belongs to the last entry that starts at or before it.** An entry owns every turn
> from its own message up to the next entry's, so an answer on screen lights its question. That is the
> whole of "reading a long answer keeps its question lit" — the state a reader spends the most time
> in, and the one the earlier rule (an entry is current only while its *own* turn is on screen) left
> dark.
>
> **🔑 The binary search is a contract, not an optimisation.** `visibleIndex` is strictly increasing
> across the entries, which is what lets a *range of turns* become a *contiguous slice of entries* —
> and that is why the colour can be a range test instead of per-entry set membership.
> `sessionMapRegion.test.ts` checks it against a linear reference over every window.
>
> **🪤 The re-observe key is the coordinate LIST, and choosing it is the whole design.** The node
> registry is a mutated-in-place `useRef` Map, so its identity can never say "a new turn mounted";
> the entries change on **every streamed token** (an entry's `response` grows), so keying on them
> would tear down and rebuild the observer once per token. The coordinate list is the discriminator
> that separates the two events. Follow this if you index a streaming surface.
>
> **🪤 The empty visible set is not a placeholder branch.** Between mount and the observer's first
> callback — and in any environment with no layout — the honest answer is the newest entry, which is
> where an unscrolled surface *is*. One total function, not a second mechanism to keep in step.

#### Markers — one length at rest, colour for what is on screen

Each marker is a 2px line inside a 32×24 button. At rest every marker is 12px long
(`MARK_REST_SCALE = 0.5` of the 24px line), whatever it indexes and whether or not it is on screen.
The on-screen region paints `--color-primary` (One-Voice — coral means "the agent / live / current")
and the rest paints `--color-map-rest`, a neutral minted for this rail (see **Contrast**). Only the
pointed-at marker changes size: it grows to the full 24px (`MARK_EXPANDED_SCALE`) and brightens, to
`--color-primary-emphasis` on screen and to `--color-on-surface` off it. Which marker is pointed at
is rail state rather than per-marker state, because it is a singleton — a pointer moving from one
marker to the next hands the expansion over and never leaves two expanded.

There is **no track**. The earlier 1px `--color-rail` hairline measured 1.000:1 on the light canvas
(it painted nothing), and a column of markers on a constant pitch already reads as a rail;
`SessionMapRail.test.tsx` asserts its absence. Nothing here is a coloured side-stripe either (the
Tone-Not-Line rule, `sideStripeDoctrine.test.ts`): the tone lives in discrete markers that are the
nav targets.

#### Preview card — "what is this one?"

Each marker is the trigger for a per-marker `ui/Popover` (portalled, `placement="right"` so it sits
beside the rail instead of over the markers below it, 300px — a preview line wants ~45-55
characters). The frosted material, corner, entrance spring, viewport clamp, z-index, single-layer
Escape and focus-restore all come from the shared primitive; the card owns only its body:

- a small **timestamp** (caption, `--color-on-surface-low`); an unparseable one renders **nothing**
  rather than "Invalid Date" (`lib/epoch`'s contract);
- the **request**, clamped to two lines, in `--color-on-surface` — not the low-contrast ramp, which
  is for chrome, never for the sentence you came to read;
- under a hairline divider, a **muted excerpt of the reply's opening**, clamped to three lines, in
  `--color-on-surface-var`.

There is no role label: every entry is a user message and its excerpt is the reply, so a "You" on
every card would label the one thing the map never varies.

Pointer intent is two delays: **150ms to open** (so a pointer merely *crossing* the rail does not
strobe a card per marker) and **120ms to close** (the bridge that lets the pointer leave the marker,
cross the gap and land on the card to read or select the excerpt).

> **🔑 Exactly one card open is a property of focus, not coordination.** `Popover.openSignal` is
> one-way (nothing reaches `setOpen(false)`), so revealing card *j* while *i* was open would stack
> them. Instead the card the cursor opened belongs to the marker that has **focus**, which the
> platform already keeps a singleton, and a marker closes its own card on `blur`. `kbOpen` records
> *who opened it*, because a pointer-opened card must not be dismissed by the blur that a click
> **into** the card causes.

#### Accessibility

- **Landmark, once.** The rail is `<nav aria-label="Session map">`, described by a visually hidden
  hint ("One mark per message you sent. Point at or arrow to a mark to preview it; click it or press
  Enter to jump to it.") so what the markers are is said once rather than on every marker. The drawer
  adds **no** nav landmark: it renders inside `ui/SidePanel`, already a `role="region"` named
  "Session map", and nesting two same-named regions for one surface is the defect.
- **One tab stop, roving `tabIndex`.** The slot starts on the current region's first marker; arrows
  move it, `Home`/`End` go to the ends, `PageUp`/`PageDown` step 5 markers, `Enter`/`Space` jump.
  `preventDefault()` fires only for handled keys: it stops the arrows scrolling the surface out from
  under the cursor **and** stops the browser's own button activation firing a second jump.
- **Passive focus must not open the card; an explicit cursor key must.** The rail sits between the
  content and the composer, so Tab-through would flash a card on the way past — but a sighted
  keyboard user arrowing down markers with no card is navigating blind. The discriminator is
  **intent**: a cursor key sets a one-shot `reveal` immediately before moving focus and the receiving
  `onFocus` consumes it. Tab never sets it.
- **The cursor is visible twice.** The focused marker keeps the global `:focus-visible` ring
  (`tokens.css`, 2px opaque `--color-primary`; no `outline-none` anywhere in the rail) **and** expands
  like a hovered one, so a keyboard user sees where the cursor is through the platform's channel and
  the design's.
- **Accessible name:** `sessionMapMarkName` → `Message N of M: <subject>`, the subject bounded to 40
  chars through `lib/rowSubject`. The drawer's rows and the live region read the same position, so
  the two forms cannot number a message differently.
- **Live region:** `role="status" aria-live="polite"`, and it announces **only** the jump ("Jumped to
  message N of M") and only while the rail holds focus. The name already states the cursor position
  on every move, so repeating it would be double-speak; what a screen-reader user cannot otherwise
  tell is whether the *content* moved, since focus stays on the rail. It clears when focus leaves the
  rail entirely, so a later hover cannot re-announce a jump nobody made.
- **Hit target:** the pressable area is the **whole row**, 32×24, and the rows are flush — every
  pixel of the rail belongs to some marker, and each clears WCAG 2.2 SC 2.5.8's 24×24. The `.hit-24-x`
  band the tick form used is gone: it widens only the horizontal axis, and the failing axis was the
  tick's 4px height.
- **🔴 The rail's `ml-4` is load-bearing clearance, and it must not become a t-shirt token.** The
  rail mounts against the shell's content-column edge, where `NavRail`'s splitter wears a `.hit-24-x`
  band plus `z-10`. Measured at 1280×420 on a 196px nav, `elementFromPoint` at every mark's own
  centre returned the **splitter**, 6 of 6 — every mark un-clickable by pointer at the default nav
  width. `ml-4` is a real 16px at every density; `--spacing-l` is `16px * var(--space-scale)` and
  `tokens.css` drops that scale to 0.8 and 0.68, so `ml-l` would be 12.8px and 10.9px against a fixed
  14px requirement and the stolen target would return, invisibly, for exactly the users who chose
  tighter spacing.
- **The edge fade must not dim a resting marker.** The list fades its own top and bottom by
  `--spacing-xl` so an overflowing rail ends in a gradient, and it is **padded by the same token**, so
  a list that fits shows every marker at full strength. Unpadded, the first and last markers sat 12px
  into the fade at 60% alpha, which composites the rest tone to about 1.9:1 on the canvas. The roving
  cursor scrolls the rail until its marker clears the fade, not just the box edge.
- **Touch:** drawer rows are `min-h-11` (44px) and carry the card's content inline — the request (two
  lines), the reply's opening (one line) and the clock — because a touch device has no hover to reveal
  a card with.
- **Reduced motion:** the marker's length runs on `physics.snappy` and the drawer rows and the return
  control on `spring.*` — gated getters, so `prefers-reduced-motion` collapses them through
  `design/motion`'s single off-switch (`reducedMotionAppWide.test.ts`), never a hand-rolled spring.
  With motion off a marker still expands; it just does not travel there. Colour switches and never
  animates.
- **Contrast:** a marker is a UI component, not text, so **SC 1.4.11 at 3:1** governs it — not
  1.4.3's 4.5. `design/schemeContrast.test.ts` parses both resting tones and both lit tones out of the
  component and measures them on the rail's real ground, the canvas, across 12 schemes × 2 modes: the
  on-screen accent 5.60-12.93, `--color-map-rest` 3.21 (dark) / 3.23 (light). It also asserts that
  the on-screen and off-screen tones differ in **lightness** by at least 1.7:1 in all 24 combinations
  (worst 1.733, light / slate; best 4.035), and that each lit tone out-contrasts its resting tone.

> **Decided (2026-09-25): the two tones do not need 3:1 between them, and the rail keeps the accent
> for on-screen messages and the grey for the rest.** Three reasons:
>
> 1. **Which messages are on screen is supplementary information.** The transcript itself shows what
>    is on screen, so SC 1.4.1 does not require the rail's colour to carry that on its own, and the
>    step between the two states does not need 3:1. It measures 1.73-4.03:1 (worst light / slate). A
>    3:1 step is out of reach with the accent in any case: with both tones at 3:1 on the canvas, it
>    would need an accent about 9:1 from the canvas, and the accent is 5.6-12.9:1. `aria-current`
>    still gives the state to assistive tech, and `schemeContrast.test.ts` keeps the 1.7:1 floor so
>    the step cannot quietly shrink.
> 2. **Each marker meets SC 1.4.11 on its own.** Both tones clear 3:1 against the rail's ground, the
>    canvas, in all 12 schemes × 2 modes: the accent at 5.60-12.93, `--color-map-rest` at 3.21 (dark)
>    and 3.23 (light). No scheme overrides either the grey or the canvas, and
>    `schemeContrast.test.ts` asserts every scheme × mode.
> 3. **The essential interaction states are not colour.** Hover and focus are carried by size (the
>    marker grows to 24px), by the card, and for focus by the ring. The brighter tone only adds to
>    them.

Both forms are gated in a real browser: `web/e2e/a11y.spec.ts` runs axe with the desktop rail
**open** and its card open, and on the 390px drawer, each with a reachability floor so a clean axe
result cannot come from a surface it never visited. `web/e2e/sessionMap.spec.ts` walks the keyboard
path (Tab reaches the rail, arrows rove the single tab stop, `Enter` and `Space` each move the
transcript in opposite directions so neither can pass on the other's scroll), asserts scroll-fixity,
reads the rail's real painted backdrop so re-parenting it reds loudly, and pins the owner's rules:
the 32×24 row, one resting length with no marker inside the fade, colour following the scroll while
no geometry moves, only the pointed-at marker expanding and letting go, the card beside the rail,
expansion without travel under reduced motion, the visible roving cursor, and an answer on screen
keeping its question lit.

> **🪤 Read a marker's length after it has SETTLED, not when it crosses a threshold.** The length is a
> framer-motion spring driven from framer's own frame loop — `document.getAnimations()`, and so
> `settleEntranceAnimations`, cannot see it — and it takes about half a second to land. The first two
> browser tests of this rail awaited only the first sample past a threshold and then compared the
> shape, i.e. they compared it mid-flight: measured frame by frame, the hovered mark crossed the
> threshold at ~110ms while its neighbour was still at 17.2-18.1px on its way to 18.6, and the cursor
> test read a mark at 12.15px, one frame above its 12.12 rest. They were red in 4 of 6 runs at the
> commit that added them. `sessionMap.spec.ts`'s `restingLengths` waits for movement first and then
> for two equal readings at least 100ms apart; reuse it.

**Usage**

```tsx
import { sessionMapEntries } from './chat/sessionMap'
import { SessionMapRail } from './chat/SessionMapRail'
import { SessionMapDrawer } from './chat/SessionMapDrawer'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'

// derive once — both forms index the identical array
const entries = useMemo(() => sessionMapEntries(turns), [turns])

// 🔑 the rail is a SIBLING of the scroller, never a child: an element outside the scroll
// container has no scroll offset to inherit, so no `sticky`, no scroll listener and no
// re-positioning code exists to get wrong.
<div className="relative flex min-h-0 flex-1">
  {mapOpen && !isMobile && (
    <SessionMapRail entries={entries} turnNodes={turnNodes.current}
      scrollRef={scrollRef} onJumpTo={jumpToTurn} />
  )}
  <div ref={scrollRef} data-transcript-scroll className="relative min-w-0 flex-1 overflow-y-auto">
    {/* …content… */}
    <SessionMapReturnLatest scrolledUp={scrolledUp} onReturnToLatest={() => scrollToLatest(endRef.current)} />
  </div>
</div>

// coarse pointer: ONE named header control opens a drawer over the same entries + same jump
<SidePanel title="Session map" fillHeight onClose={() => setMapOpen(false)}>
  <SessionMapDrawer entries={entries} onJumpTo={jump} />
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
Tests key off the stable `data-session-map-*` / `data-session-mark` hooks (plus `data-current` and
`data-expanded`); keep them when you touch the markup.

> **NOTE: not yet a generic `ui/` primitive.** The derivation is typed to chat's
> `ChatTurn` / `Segment` / `SubagentCard`, so a second consumer means extracting `sessionMapEntries`
> behind an adapter (the rail, card, region and drawer are already pure over `SessionMapEntry[]` and
> need no change) — **not** copying the rail. The mark vocabulary is closed for the same reason: a
> new kind is a contract change every consumer switches on.
