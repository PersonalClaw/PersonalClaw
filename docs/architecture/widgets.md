# Widgets: the child→parent wire contract

An agent-authored `<widget>` is arbitrary HTML with arbitrary scripts. It renders in an
iframe with `sandbox="allow-scripts"` off a **blob (null) origin** under a strict CSP
(`connect-src 'none'`, `form-action 'none'`, `base-uri 'none'`), so it cannot reach the
host's DOM, cookies, storage, or network. One channel crosses that boundary in each
direction, and this document is its contract.

Everything a widget sends is **untrusted input**. The host validates provenance before
shape and refuses anything that is not spelled out below.

## child → parent

Sent by the child with `parent.postMessage(msg, '*')`. `'*'` is correct here: the child
is on an opaque origin and has no way to name the parent.

| `type` | fields | meaning |
| --- | --- | --- |
| `widget-height` | `height: number`, `width?: number` | the content's natural size, reported on load and on every resize. `width` is the widest top-level element, which lets the host shrink-wrap a narrow card and flow prose beside it. |
| `widget-action` | `action: string`, `payload?: unknown` | a human clicked a `[data-action]` element. `payload` is the element's `data-payload` JSON, plus a `formData` map of every named `input`/`select`/`textarea` in the document when there is one. |
| `widget-error` | `message: string` | the child failed to render (used by the React harness's error boundary) — surfaced inline instead of a blank frame. |
| `widget-edit-values` | `values: Record<string,string>` | the child's answer to `__edit_mode_read_keys`: the CURRENT computed value of each requested custom property. This is what an EDITMODE Save writes — never the rail's own state. Keys must be custom-property names; at most 32, each clipped to 200 chars. |
| `widget-annotation` | `selector`, `tag`, `outerHTML`, `parentContext` | a human clicked an element while annotate mode was on. The selector is derived in the child (`data-testid` → `id` → unique non-utility class chain → `nth-child` path); whitespace is collapsed and every field capped, so a smuggled newline cannot forge a second anchor line. An annotation with no selector is dropped. |

Anything else — an unknown `type`, a missing or wrongly typed required field, a
non-finite height, an empty `action` — is **dropped**. `payload` is deliberately not
shape-checked: it becomes text in a chat turn, never a command.

## parent → child

The `__edit_mode_*` namespace is **reserved** for artifact iteration. It is one-way: a
child that posts a `__edit_mode_*` message is out of contract and the host refuses it,
so the reservation is enforced rather than merely documented.

| `type` | fields | meaning |
| --- | --- | --- |
| `__edit_mode_set_keys` | `edits: {key, value}[]` | apply live EDITMODE values to `:root` custom properties. **Batched** — a drag coalesces to one message per animation frame, so moving a slider across its range costs one message, not sixty. Nothing in this path touches the network. |
| `__edit_mode_read_keys` | `keys: string[]` | ask for the current computed value of each key. Answered with `widget-edit-values`. |
| `__edit_mode_annotate` | `on: boolean` | turn click-annotation capture on/off. |

The child applies the same discipline to these that the host applies to the reverse
direction, and for the same reason — a sandboxed frame has no way to know who is
talking to it otherwise:

- **`event.source === window.parent`**, checked before anything is read. A sibling
  frame or an extension cannot drive a widget's styling or read its state.
- **A key is re-validated as a custom-property name in the child**, even though the
  parent validated it, because the child is where it becomes CSS.
- **`Array.isArray`, not a `.length` duck-check** — a string has a length, and
  iterating one would apply its characters as keys.
- **An annotation is a human gesture**: the click handler gates on `e.isTrusted`
  exactly as the action forwarder does, so a widget's own script cannot mint a
  correction directive about itself. While annotating, the click is consumed in the
  capture phase, so it marks the element and does *not* also fire the widget's
  `[data-action]` — otherwise a single click would send the agent a form submission
  the user never made.

The iteration script ships **only** into a document whose host actually offers
iteration (`buildSrcdoc({editMode: true})`). Every other document — chat widgets while
streaming, the download and open-in-new-tab standalone bodies, and any host that
renders a widget read-only — is byte-identical to what it was before iteration
existed, which is asserted rather than assumed.

## Evolution rule

**Additive only.** A new message needs a new `type`. A shipped field is never re-meant
and never re-typed — a widget authored last month must keep working, and an agent that
learned the vocabulary must not have to unlearn it.

## The two invariants that stop a widget driving the host

A widget action becomes a **user turn in a conversation**, so "a widget can raise one"
has to mean "a human asked for one".

1. **Human gesture, checked in the child.** The child's host script forwards a
   `[data-action]` click only when `event.isTrusted`. A widget's own script calling
   `el.click()` or dispatching a synthetic `MouseEvent` produces nothing. This gate can
   only live in the frame, because that is where the click happens.
2. **Provenance, checked in the host.** A host accepts a message only when
   `event.source === thatFrame.contentWindow`. A sibling iframe, a browser extension, or
   the page itself posting a look-alike `widget-action` is refused before its shape is
   read.

Because invariant 1 lives in the child document, **a host whose child carries no such
gate does not forward actions at all.** The React harness (`kind: react`) transforms JSX
in-frame and has no click gate, so its frame is wired for height and errors only —
otherwise a react widget's own script could mint a turn with no human involved.

Two further limits apply to what an action can carry:

- **16 KiB cap.** The `[UI]` turn text clips at 16 KiB of UTF-8, appending `…truncated`.
  An adversarial widget cannot stuff a conversation turn, and the truncation is visible
  rather than silent — a quietly shortened payload would hand the agent a lie about what
  the user submitted.
- **Serializable payloads only.** `postMessage`'s structured clone carries object cycles
  that `JSON.stringify` cannot. Such a payload is refused rather than thrown: an
  unhandled throw inside a window listener is a widget crashing its host.

## Where the action goes

A validated action is republished once, as the `ne:widget-action` window event, and
exactly one consumer handles it:

- **A chat host claims the bridge while it is mounted** — the action becomes the next
  turn in *that* conversation, so a widget rendered mid-thread answers in place.
- **Otherwise the app shell's fallback handles it** — an artifact-library preview, a
  dashboard tile, or any future widget host opens a chat through the one
  `ne:launch-chat` path and lands the `[UI]` text as that session's first turn.

Never both: two consumers would send the turn twice. Registering the fallback at the
shell is what makes a *new* widget host inherit routing instead of silently dropping
clicks, which is what every non-chat host did before this contract existed.

The text handed to the new session waits in memory, not in the URL. A `?send=` query
parameter would turn "auto-send this prompt" into a link anyone could hand the user;
keeping the authority in-process means only a real widget action can arm it, and the
handoff expires so a navigation that never reached chat cannot fire a stale turn later.

## Genui actions: dual payloads and the producer (AS-6)

A `<widget kind="genui">` block renders in the HOST React tree, so its controls do not cross
the postMessage boundary at all. What they share with the iframe path is the TURN DIALECT —
the `[UI] ` prefix and the 16 KiB clip (`ui/widget/actionTurn.ts`, a leaf module both sides
import) — and nothing else.

Activating a genui control emits **dual payloads**:

| field | who reads it | content |
| --- | --- | --- |
| `llmFriendlyMessage` | the model / the run | `[UI] <action>: {full state}` — the collected form values, JSON |
| `humanFriendlyMessage` | the TRANSCRIPT | the control's own label ("Log expense") |

The pair exists so a form submit is not shown to the user as raw JSON. On the chat path the
machine half becomes the message content and the human half rides as `meta.ui_label`, which
is what the bubble renders — live and after a reload, since the label is persisted with the
turn.

**Routing is by PRODUCER, and the producer comes from the HOST, never from the widget's own
text** (`GenUiHostCtx` in `ui/genui/actions.ts`):

| producer | host that declares it | where the action goes |
| --- | --- | --- |
| `chat` (default) | none needed | the next user turn, via `ne:widget-action` |
| `workflow-gate` | `WorkflowAsk` (run view + inbox) | `POST /api/workflows/runs/{id}/resume` with that gate's token — the run advances |
| `tile` | `PinnedTiles` | `POST /api/dashboard/views/{view}/tiles/action` — a fenced re-fire |

A producer stamped into the block (`gate="run:token"`) was considered and rejected: block
attributes are model-authored, so a transcript could name a run and turn a click into a gate
answer. Only a host that already holds the run or tile identity can widen a widget's reach,
and the default — a plain chat turn — is the harmless one.

**The tile fence.** A tile's body is generated, so the action name arriving at the endpoint is
untrusted text. `dashboard/tile_actions.py` checks it against the capability set frozen on the
tile's SAVED binding, through the same helper the trigger fence uses
(`triggers.screen.unfenced_actions`), and only then re-fires — via `refresh_tile(force=True)`,
the same path the tile's own refresh button uses, so a UI-originated fire cannot skip the
unattended gates. The trigger fence's read-only default is deliberately NOT applied here: a
tile's set is derived from its own nodes, so it is never empty-by-omission, and the default
would let a button reach a read-only provider the tile never declared.

## The layered surface overlay (L0/L1/L2) + safe mode (AS-6 §6)

| layer | what | removable by |
| --- | --- | --- |
| L0 core | the shipped `web/dist` bundle | nothing — the build owns it |
| L1 app | app pages + app-registered genui components | disabling the app |
| L2 user/agent | user/agent surface overrides | deleting the override |

Component registrations **compose**: a higher layer may ADD a name, never SHADOW a lower
layer's — refused at register time (`registerLayerComponent`), because the reason a genui tree
may render in the host tree at all is that only registered, schema-validated components can
appear in it, and model-authored `StatTile(…)` must reach the CORE StatTile.

Every load above L0 is error-boundaried (`ui/surfaces/LayerBoundary.tsx`): a broken app
component renders a named notice and the surface around it survives.

Safe mode forces `maxLayer = 0` — two ways in, deliberately: `#/dashboard?safe=1` (the URL a
user can be read over the phone) and the `--safe-surfaces` gateway flag, which latches the
process and stamps a `<meta name="personalclaw-safe-surfaces">` into `index.html` so the
ceiling is known BEFORE the first app module loads. Because L0 is immutable and the safe route
is part of L0, agent-written UI cannot brick the app.

## Where this lives

| concern | file |
| --- | --- |
| the wire: validator, consumers | `web/src/ui/widget/useWidgetActionBridge.ts` |
| the `[UI]` turn dialect (prefix, clip, publisher) — a LEAF both paths import | `web/src/ui/widget/actionTurn.ts` |
| genui dual payloads + producer routing | `web/src/ui/genui/actions.ts` |
| the surface-layer ceiling + safe mode | `web/src/ui/surfaces/layers.ts`, `personalclaw/dashboard/surface_layers.py` |
| the tile re-fire fence | `personalclaw/dashboard/tile_actions.py` |
| the child document + its `isTrusted` gates | `web/src/ui/widget/widgetSrcdoc.ts` |
| HTML widget host (chat, tile band) | `web/src/ui/widget/WidgetFrame.tsx` |
| React widget host (height + errors only) | `web/src/ui/widget/ReactWidgetFrame.tsx` |
| artifact-library preview host | `web/src/ui/content/renderers.tsx` |
| the EDITMODE block: parse + in-place rewrite | `web/src/ui/widget/editMode.ts` |
| correction directives: validate + compose one | `web/src/ui/widget/annotate.ts` |
| the parent half of iteration (batching, save, dispatch) | `web/src/ui/widget/useArtifactIteration.ts` |
| the tweak/annotate rail | `web/src/ui/widget/ArtifactIterationRail.tsx` |

## Where a correction goes

A correction directive is **data with provenance, not executed UI** — nothing in the
iteration path mutates the artifact; the receiving agent regenerates it. The host that
shows the artifact decides who receives it:

- **the artifact library / a chat widget** → the widget bridge, i.e. the same
  `[UI]`-prefixed turn a widget action produces, carrying the C32 `refresh artifact
  "<slug>" in place` suffix so the agent rewrites THAT artifact rather than spawning a
  new one;
- **a loop's output tab** → the loop's own steer channel (`guidance.txt`, via
  `POST /api/loops/{id}/nudge`), because the worker that produced the deliverable is
  the thing that must regenerate it.

A correction inherits the action wire's 16 KiB clip and its living-view suffix from the
same composer, so there is one rule for both rather than two dialects of it.

The authoring contract a widget is written against — which `data-action`/`data-payload`
attributes to emit, and the theme variables available — is documented for agents in
`skills/bundled/visual-output/SKILL.md`.
