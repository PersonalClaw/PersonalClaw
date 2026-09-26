import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { EditorView } from '@codemirror/view'
import { Search } from 'lucide-react'

// ── Typing fast must never trip React's nested-update limit (#185) ───────────────────────────
//
// Seen on a dev gateway as "Minified React error #185" while driving a chat. Reproduced on `#/chat`
// with the development build by typing at 2 ms a key: every message longer than ~50 characters
// logged, from CodeMirror's update listener,
//
//     update listener: Error: Maximum update depth exceeded …
//       at dispatchSetState  ←  ChatPage onChange (setInput)  ←  MarkdownInput updateListener
//       ←  EditorView.dispatch  ←  applyDOMChange  ←  DOMObserver.flush  ←  MutationObserver
//
// The throwing call is only where the overflow was DETECTED. React 19 counts a commit as a nested
// update when it ends with Sync/Default work still pending, and resets the count only after a
// commit that ends clean. A keystroke is a discrete event, so its render is SyncLane and React
// flushes that commit's passive effects inside the same commit. Any effect there that calls a
// setter — even one whose updater returns `prev` — leaves a render pending, because React can only
// drop a same-value update EAGERLY while neither copy of the fiber has pending lanes, and a
// component that just re-rendered still carries them on its alternate. The pending render would
// run as its own clean commit — but the next keystroke arrives first (the browser runs input ahead
// of React's scheduler task), so its commit ends with work pending too, and the 51st throws.
// Measured per surface with the dev bundle instrumented (setters scheduled NON-eagerly during a
// passive flush, and the highest count any setter saw), 110 keys at 2 ms:
//
//     surface                        scheduler found                           BEFORE      AFTER
//     chat composer                  MentionMenu+SlashMenu → reportCursor      102+102/51   0/0
//                                    ChatSession paste-prune effect            102/51       0/0
//     home launcher composer         SlashMenu → reportCursor                  65/2         0/0
//     command palette                setActive(0) keyed on the query           102/51       0/0
//     chat → Add knowledge           setLoading(true) keyed on the query       102/51       0/0
//     settings → model library       setSearching(true) keyed on the query     103/5        0/0
//     tasks search                   setSearchErr(null) keyed on the query      43/2        0/0
//     loop composer                  (mounts neither menu)                        0/0        0/0
//
// The rows that stopped short of 51 scheduled a render per key as well and got luckier in that
// browser run: the home launcher and the model library render fast enough that React's own task
// slipped in between some keys, and the tasks effect keys on the TRIMMED query, so every space left
// it unchanged and reset the count. None of that is a guard — with the keys arriving faster than
// the renders (this harness), the first two throw, and the tasks search throws on any long run
// without a space: a URL, a path, an identifier.
// The one remaining member, `CodeCockpitPage`'s file finder, has the model library's exact shape;
// it is fixed the same way and sits behind a code project this harness does not stand up.
//
// 🔑 HOW THIS HARNESS TYPES, and why it cannot use `fireEvent` / `userEvent` / `act()`: each of
// those drains every lane before it returns, so the pending render the bug depends on is always
// flushed between keys and the count never climbs — a green that could not have failed. Instead a
// key here is what the browser delivers: a real DOM event (discrete, so the update is SyncLane),
// followed by MICROTASKS ONLY before the next key. React's sync flush is a microtask; its scheduler
// task is not, so it never gets a turn between keys — the state Chrome is in when keys outrun it.
// The positive control below proves this harness trips the limit on the old shape.

// jsdom has no layout: scrollIntoView (menu cursors, the transcript) and IntersectionObserver (the
// session map) are stood in for, as the other ChatPage harnesses do.
Element.prototype.scrollIntoView ??= () => {}

const h = vi.hoisted(() => ({
  searchTasks: vi.fn((..._a: unknown[]) => Promise.resolve({ tasks: [] as unknown[] })),
  // A finished code loop bound to a workspace — the cockpit shows its file finder for any bound one.
  uLoop: vi.fn((..._a: unknown[]) => Promise.resolve({
    id: 'c1', name: 'ingestion speed-up', kind: 'code', status: 'complete', workspace_dir: '/tmp/ingest-ws',
    kind_config: {}, plan: [], phase_status: {}, stages: [], total_cycles: 3, max_cycles: 10, elapsed_seconds: 60,
  })),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  // Every endpoint these surfaces read settles to a benign, shape-agnostic value; the few whose
  // shape a render dereferences are spelled out. No search ever reaches the network here: each is
  // debounced behind a timer, and no timer fires while keys are arriving — which is the point.
  const base: Record<string, unknown> = {
    dashboardConfig: () => Promise.resolve({ send_on_enter: true }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
    allTasks: () => Promise.resolve({ tasks: [] }),
    fileList: () => Promise.resolve({ entries: [] }),
    searchTasks: (...a: unknown[]) => h.searchTasks(...a),
    uLoop: (...a: unknown[]) => h.uLoop(...a),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
})

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('EventSource', class {
    onopen = null; onmessage = null; onerror = null; readyState = 0
    addEventListener(): void {}
    removeEventListener(): void {}
    close(): void {}
  })
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { vi.unstubAllGlobals() })

const TEXT = 'Question: how should I speed up the ingestion pipeline? It takes forty minutes to re-index every night.'
const RUN = '-github.com/personalclaw/personalclaw/pull/3620#discussion_r1790409513-ingestion-pipeline-reindex'

/** Let React's microtask-scheduled sync work run — and nothing else: no timer, no scheduler task. */
async function microtasks() {
  for (let i = 0; i < 4; i++) await new Promise<void>((r) => queueMicrotask(r))
}

/** Outside `act()` on purpose (see the header), so React must not warn that it is. */
async function outsideAct(fn: () => Promise<void>) {
  const g = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
  const was = g.IS_REACT_ACT_ENVIRONMENT
  g.IS_REACT_ACT_ENVIRONMENT = false
  try { await fn() } finally { g.IS_REACT_ACT_ENVIRONMENT = was }
  // Drain whatever the last key left scheduled, inside act, before the test asserts or unmounts.
  await act(async () => {})
}

/** Keystrokes into the composer's CodeMirror editor, through the same dispatch → update listener →
 *  `onChange` path a real key takes (jsdom cannot produce the DOM mutation CodeMirror reads, so the
 *  change is dispatched from inside the discrete event instead). */
async function typeIntoEditor(text: string) {
  const content = document.querySelector<HTMLElement>('.cm-content')
  expect(content, 'the composer editor mounted').toBeTruthy()
  const view = EditorView.findFromDOM(content!)
  expect(view, 'CodeMirror owns the editor').toBeTruthy()
  await outsideAct(async () => {
    for (const ch of text) {
      content!.addEventListener('input', () => view!.dispatch(view!.state.replaceSelection(ch)), { once: true })
      content!.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }))
      await microtasks()
    }
  })
}

/** Keystrokes into a React-controlled `<input>`: the native value setter (so React's value tracker
 *  sees a change), then a real `input` event, which React routes to `onChange` as a discrete event. */
async function typeIntoInput(input: HTMLInputElement, text: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await outsideAct(async () => {
    let v = input.value
    for (const ch of text) {
      v += ch
      setValue.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await microtasks()
    }
  })
}

/** Every "Maximum update depth" React reports while `fn` runs — the thrown #185 (CodeMirror logs
 *  the one it catches; React reports one thrown from an event handler as a window error) and the
 *  dev-only effect-loop warning alike. */
async function updateLoopsDuring(fn: () => Promise<void>): Promise<string[]> {
  const seen: string[] = []
  const spy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { seen.push(args.map(String).join(' ')) })
  const onError = (e: ErrorEvent) => { seen.push(String(e.error ?? e.message)); e.preventDefault() }
  window.addEventListener('error', onError)
  try { await fn() } finally { spy.mockRestore(); window.removeEventListener('error', onError) }
  return seen.filter((s) => /Maximum update depth/.test(s)).map((s) => s.split('\n')[0])
}

describe('the harness can see the defect (positive control)', () => {
  it('an effect that resets state on every keystroke trips the limit when typed at speed', async () => {
    // The exact shape the command palette shipped: `useEffect(() => { setActive(0) }, [q])`.
    function ResetsFromAnEffect() {
      const [q, setQ] = useState('')
      const [active, setActive] = useState(0)
      useEffect(() => { setActive(0) }, [q])
      return <input aria-label="control" value={q} data-active={active} onChange={(e) => setQ(e.target.value)} />
    }
    render(<ResetsFromAnEffect />)
    const loops = await updateLoopsDuring(() => typeIntoInput(screen.getByLabelText('control') as HTMLInputElement, TEXT))
    expect(loops.length, 'without this red, a green below proves nothing').toBeGreaterThan(0)
  })
})

describe('typing fast into', () => {
  // A host that owns the draft, with the menus closed — the state every ordinary keystroke is typed
  // in. The chat mounts both menus; the home launcher mounts only the slash menu (it has no @-mention
  // handler); the loop composer mounts neither, which is why it never scheduled anything.
  it.each([
    ['both menus, as the chat mounts them', true],
    ['the slash menu alone, as the home launcher mounts it', false],
  ])('the composer — its typeahead menus report their cursor only when it moves (%s)', async (_label, mention) => {
    const { Composer } = await import('../Composer')
    const draft = { current: '' }
    function DraftHost() {
      const [value, setValue] = useState('')
      draft.current = value
      return (
        <Composer value={value} onChange={setValue} onSend={() => {}} onMentionFile={mention ? () => {} : undefined}
          controls={{ agent: false, model: false, approval: false, reasoning: false, attach: false, mic: false, optimize: false, slash: true }} />
      )
    }
    render(<DraftHost />)
    await waitFor(() => expect(document.querySelector('.cm-content')).toBeTruthy())
    const loops = await updateLoopsDuring(() => typeIntoEditor(TEXT))
    expect(loops).toEqual([])
    expect(draft.current, 'every keystroke reached the host').toBe(TEXT)
  })

  it("the chat's composer — the paste cards are derived, not re-synced on every keystroke", async () => {
    const { ChatPage } = await import('../../pages/ChatPage')
    const { AppearanceProvider } = await import('../../app/appearance')
    render(<AppearanceProvider><ChatPage sub="" navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
    await waitFor(() => expect(document.querySelector('.cm-content')).toBeTruthy())
    const loops = await updateLoopsDuring(() => typeIntoEditor(TEXT))
    expect(loops).toEqual([])
  })

  it('the command palette — a new query resets the cursor in its own handler', async () => {
    const { CommandPalette } = await import('../../app/CommandPalette')
    render(<CommandPalette commands={[
      { id: 'a', label: 'Chat', icon: Search, run: () => {} },
      { id: 'b', label: 'Settings', icon: Search, run: () => {} },
    ]} />)
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
    const input = await screen.findByRole('searchbox', { name: 'Search pages and actions' }) as HTMLInputElement
    const loops = await updateLoopsDuring(() => typeIntoInput(input, TEXT))
    expect(loops).toEqual([])
  })

  it("the chat's knowledge picker — the search's loading state is set where the query changes", async () => {
    const { ChatPage } = await import('../../pages/ChatPage')
    const { AppearanceProvider } = await import('../../app/appearance')
    render(<AppearanceProvider><ChatPage sub="" navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
    act(() => { screen.getByRole('button', { name: 'Add to message' }).click() })
    act(() => { screen.getByRole('button', { name: /^Add knowledge/ }).click() })
    const input = await screen.findByRole('searchbox', { name: 'Search your knowledge library' }) as HTMLInputElement
    const loops = await updateLoopsDuring(() => typeIntoInput(input, TEXT))
    expect(loops).toEqual([])
  })

  it("the model library's search — the same, for a local model provider", async () => {
    const { LocalModelManager } = await import('../../pages/settings/LocalModelManager')
    render(<LocalModelManager provider="ollama" models={[]} searchable onChanged={() => {}} />)
    const input = await screen.findByRole('searchbox', { name: 'Search the model library' }) as HTMLInputElement
    const loops = await updateLoopsDuring(() => typeIntoInput(input, TEXT))
    expect(loops).toEqual([])
  })

  it("the code cockpit's file finder — the same, for a workspace's files", async () => {
    const { CodeCockpitPage } = await import('../../pages/code/CodeCockpitPage')
    const noop = () => {}
    render(<CodeCockpitPage id="c1" onBack={noop} onDeleted={noop} query={{}} setQuery={noop} />)
    const input = await screen.findByRole('searchbox', { name: 'Find file by name' }) as HTMLInputElement
    const loops = await updateLoopsDuring(() => typeIntoInput(input, TEXT))
    expect(loops).toEqual([])
  })

  it('the tasks search — typing over a failed search hides its error by key, not by a clearing set', async () => {
    // Typed over a search that FAILED, which is where a clearing set in the effect is a real update
    // on the first key and a same-value one on every key after it — the self-sustaining shape.
    h.searchTasks.mockRejectedValueOnce(new Error('the search index is not responding'))
    const { TasksListPage } = await import('../../pages/tasks/TasksListPage')
    function UrlBackedQuery() {
      const [q, setQ] = useState('')
      const noop = () => {}
      return (
        <TasksListPage onCreate={noop} view="list" filter="all" openId={null} setView={noop} setFilter={noop}
          setOpenId={noop} editing={false} setEditing={noop} q={q} sort="" scope="" list="" tag=""
          setQ={setQ} setSort={noop} setScope={noop} setList={noop} setTag={noop} />
      )
    }
    render(<UrlBackedQuery />)
    const input = await screen.findByRole('searchbox', { name: 'Search tasks' }) as HTMLInputElement
    await typeIntoInput(input, 'ingest')
    // The debounced search runs and fails: its error is what the user is looking at.
    expect(await screen.findByRole('heading', { name: "Couldn't load your search results" })).toBeInTheDocument()
    // A long run with no spaces — a pasted-then-edited URL, a path, an identifier. The effect keys on
    // the TRIMMED query, so a space leaves it unchanged and that key's commit ends clean, resetting
    // React's count; only an unbroken run can take it to the limit.
    const loops = await updateLoopsDuring(() => typeIntoInput(input, RUN))
    expect(loops).toEqual([])
    expect(screen.queryByRole('heading', { name: "Couldn't load your search results" }),
      "a new query is not the one that failed, so its error must not stand over it").toBeNull()
  })
})
