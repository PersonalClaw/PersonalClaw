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
| `widget-action` | `action: string`, `payload?: unknown` | the child's host script reports a click on a `[data-action]` element; the parent can't verify it (see [What stops a widget driving the host](#what-stops-a-widget-driving-the-host)). `payload` is the element's `data-payload` JSON, plus a `formData` map of every named `input`/`select`/`textarea` in the document when there is one. |
| `widget-error` | `message: string` | the child failed to render (used by the React harness's error boundary) — surfaced inline instead of a blank frame. |
| `widget-edit-values` | `values: Record<string,string>` | the child's answer to `__edit_mode_read_keys`: the CURRENT computed value of each requested custom property. This is what an EDITMODE Save writes — never the rail's own state. Keys must be custom-property names; at most 32, each clipped to 200 chars. |
| `widget-annotation` | `selector`, `tag`, `outerHTML`, `parentContext` | the child's host script reports a click on an element while annotate mode was on; the parent can't verify it, so it takes one only in annotate mode. The selector is derived in the child (`data-testid` → `id` → unique non-utility class chain → `nth-child` path); whitespace is collapsed and every field capped, so a smuggled newline cannot forge a second anchor line. An annotation with no selector is dropped. |

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
- **An annotation's click is gated as an action's is**: the click handler forwards only an
  `e.isTrusted` click, as the action forwarder does. That stops the listener forwarding an
  untrusted click, not a widget's own script posting an annotation itself, so the host takes
  one only in annotate mode, and the correction goes out only on the owner's Send. While
  annotating, the click is consumed in the
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

## What stops a widget driving the host

A widget action becomes a **user turn in a conversation**, so "a widget can raise one"
has to mean "the owner sent one".

1. **The parent never sends a frame's action by itself.** The child's host script
   forwards a `[data-action]` click only when `event.isTrusted`, so a widget's own script
   calling `el.click()` or dispatching a synthetic `MouseEvent` produces nothing through
   that listener. But the listener lives in the same document as the widget's own script,
   which can post the same `widget-action` to the parent directly, and the parent cannot
   tell the two apart: both come from the frame's own
   `contentWindow` with the same shape. So the decision to send moves to the parent's
   DOM, where the frame cannot reach:
   - **in a chat**, the action fills the composer and waits for the owner's own Send, and a
     chip says which widget put it there, when the owner gave its frame focus (fence 3);
     from any other frame it waits as a chip. A composer the owner is using (it holds
     anything of a draft, a file still uploading or a capture waiting for its crop, or they
     typed, attached or sent in the last 2 s) is never filled: the message waits as a
     "suggests a message · Add / Dismiss" chip. Add puts it after the owner's own words and
     keeps them apart: the frame's next post never replaces them, and the chip's × takes out
     only the frame's text. An Optimize's rewrite of a widget's words stays marked, with no ×,
     until Revert, which goes back one Optimize. For 1 s after a fill, Send, Steer and Queue
     are off and say why ("Read the message the widget put here first"), and an Enter is
     refused and the notice line says why; only the hold's timer, the owner's edit, or a
     press in the editor ends it, never a press on one of those buttons, so a reflexive
     press does not send it. Only the
     draft's own send is marked: a follow-up, Check this work or `/compact` sent while it
     sits there is the owner's;
   - **anywhere else** (an artifact-library preview, a dashboard tile), the app shell asks
     first, in its own dialog, naming the frame and showing the exact text, all of it, with
     its size and a cue when there is more below. The ask is `deliberate` (the dialog
     store): focus starts on "Don't send", no Enter from the window answers it, and "Send in
     a new chat" answers only a press that begins after the dialog has been quiet for 1 s
     since the sheet came into full view. Every key and every press anywhere starts that wait
     again, so do focus reaching "Send in a new chat" and the window losing or regaining focus
     or visibility; a press begun before it armed does nothing; and a keyboard press is read
     at its click, which counts only when the button has held focus that long with no other
     key. Typing that runs on into the ask,
     a held key, a Tab then Enter, or a run of clicks on its spot is never its yes. Only the
     owner's yes stages it and opens a chat; while one ask is open, further actions are
     dropped;
   - **a no is kept**: "Don't send", Dismiss, or emptying or clearing an offered draft drops
     that frame's later actions while its chat is open, or elsewhere while the frame is shown
     (until its host remounts, by a page-assigned id, never the frame's title), and after any
     answer (a yes, an Add, a Send of the draft it filled) the frame's next action waits 5 s.
2. **Provenance, checked in the host.** A host accepts a message only when
   `event.source === thatFrame.contentWindow`. A sibling iframe, a browser extension, or
   the page itself posting a look-alike `widget-action` is refused before its shape is
   read.
3. **Its frame holds focus.** A `widget-action` counts only while `document.activeElement`
   is that frame's own iframe when it arrives: a click or a tap in a frame gives it focus, and
   the host script focuses the frame on a real click itself, so a widget that cancels its own
   mousedown still gets its press counted. A post made while focus is elsewhere in the page
   is dropped, with a debug line. Whether the owner gave the frame that focus (the page saw
   them click, tap or Tab into it, as best it can tell) decides only where a chat puts the
   action: into an idle composer, or offered as a chip. Nothing a frame does sends anything.
   The owner's own typing elsewhere cannot satisfy this, as it can the next fence.
4. **Transient user activation, as a second fence.** A `widget-action` that arrives while
   the parent holds no transient user activation (`navigator.userActivation.isActive`: no
   click a moment ago, which a click inside the frame also gives its parent) is dropped. It
   is a backstop only: activation is a timing window, and an unrelated click can coincide
   with a post, so it never stands in for (1). Where a browser has no such reading, (1)
   still holds.
5. **A widget's words grant nothing.** Every turn that came from a widget carries
   `widget_action` in its meta (the genui path also carries its `ui_label`), and the gateway
   takes none of its links as the owner's own for `web_fetch`, whether the owner edited the
   text first or not. One rule says so (`own_words.came_from_a_widget`). The marker holds on
   every way the draft travels (an upload's hold, a missing chat's "Start a new chat", a
   queued message's Edit, `/optimize` and Revert, the turn a slash command writes from it, a
   quote of it or of a selection that takes in any of it (a quote from within a widget's
   message says "A widget said"), and the chip's × when the draft held a widget's words
   before it), when it is sent mid-turn (a steer's row, a queue item and the row it runs as,
   the bubble the page shows for either, a steer the turn ends without taking, requeued), on
   Edit and resend and Rewind, whose new row keeps it, and on a branch of the chat and a
   rewind tail restored as one, whose copy of it keeps it. ↑ recall skips it, live and after
   a reload: the owner did not type it. A goal loop takes its task as the owner's, so it
   does not start from a draft that holds a widget's words, and says why.

A host whose child carries no `isTrusted` gate does not forward actions at all. The React
harness (`kind: react`) transforms JSX in-frame and has no click gate, so its frame is
wired for height and errors only.

An annotation (`widget-annotation`) counts only while annotate mode is on: one a frame
posts with the mode off never reaches the correction rail.

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

- **A chat host claims the bridge while it is mounted.** A frame's action lands in *that*
  conversation's composer, unsent; an action the owner pressed on a control the page
  renders itself (a genui button or form, the correction rail's Send) is sent as the next
  turn at once, since the press was theirs. The event says which (`pressed`); only the
  page's own code sets it, and nothing a frame posts can.
- **Otherwise the app shell's fallback handles it.** An artifact-library preview, a
  dashboard tile, or any future widget host asks the owner first (a pressed action needs
  no ask), and on their yes opens a chat through the one `ne:launch-chat` path, where the
  `[UI]` text lands as that session's first turn.

Never both: two consumers would send the turn twice. Registering the fallback at the
shell is what makes a *new* widget host inherit routing instead of silently dropping
clicks, which is what every non-chat host did before this contract existed.

The text handed to the new session waits in memory, not in the URL. A `?send=` query
parameter would turn "auto-send this prompt" into a link anyone could hand the user;
keeping the authority in-process means only the owner's press can arm it. The launch carries
a nonce (80 random bits from `crypto.getRandomValues`, which every context has: `randomUUID`
exists only in a secure one, and the remote link may be plain http), and only the chat its
navigation opens drains the stage. The shell names the nonce in that chat's address only for
its own launch, so an app's `ne:launch-chat` that names one is ignored. A navigation anywhere
else drops the stage, and it expires so a navigation that never reached chat cannot fire a
stale turn later.

The ask shows the text as it is sent: `composeWidgetActionText` drops hidden characters from the
action's name (format characters, controls, and every other default-ignorable one: variation
selectors, the combining grapheme joiner, the Hangul fillers; an emoji's own presentation
selector is kept) and makes each run of whitespace one space, before and after. In the payload
it writes each of those, DEL, every C1 control and line separator, every space but U+0020, and
any run of three or more U+0020, as its `\uXXXX` escape: the same value to the model, and
nothing of it out of the owner's view. The composer's editor marks the same characters. A
frame is named by its title, clipped to 80 code points, with hidden characters and quote marks
escaped, so a title cannot close the quotes it is shown in; "Widget" when it has none. A frame's
`widget-height` is applied at most once in 300 ms, and its `widget-error` is clipped to 200
characters, named by the frame, and a repeat of the same one shown at most once in 5 s.

## Genui actions: dual payloads and the producer

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

## The layered surface overlay (L0/L1/L2) + safe mode

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
| the surface-layer ceiling + safe mode | `web/src/ui/surfaces/layers.ts`, `personalclaw/surface_layers.py` |
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
  `[UI]`-prefixed turn a widget action produces, carrying the living-view `refresh artifact
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
