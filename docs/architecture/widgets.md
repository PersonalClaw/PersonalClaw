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

Anything else — an unknown `type`, a missing or wrongly typed required field, a
non-finite height, an empty `action` — is **dropped**. `payload` is deliberately not
shape-checked: it becomes text in a chat turn, never a command.

## parent → child

The `__edit_mode_*` namespace is **reserved** for annotate mode's live token edits
(batched `postMessage({type:'__edit_mode_set_keys', edits})`, applied by the child to CSS
custom properties). It is one-way: a child that posts a `__edit_mode_*` message is out of
contract and the host refuses it, so the reservation is enforced rather than merely
documented.

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

## Where this lives

| concern | file |
| --- | --- |
| the wire: validator, `[UI]` composer, consumers | `web/src/ui/widget/useWidgetActionBridge.ts` |
| the child document + its `isTrusted` gate | `web/src/ui/widget/widgetSrcdoc.ts` |
| HTML widget host (chat, tile band) | `web/src/ui/widget/WidgetFrame.tsx` |
| React widget host (height + errors only) | `web/src/ui/widget/ReactWidgetFrame.tsx` |
| artifact-library preview host | `web/src/ui/content/renderers.tsx` |

The authoring contract a widget is written against — which `data-action`/`data-payload`
attributes to emit, and the theme variables available — is documented for agents in
`skills/bundled/visual-output/SKILL.md`.
