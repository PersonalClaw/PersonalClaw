/** Artifact iteration, driven through a real host (AS-3).
 *
 *  Four claims live here, and each is asserted as the property it actually is:
 *
 *   · **Zero network requests during a drag.** Asserted as an ABSENCE, at the sink:
 *     `fetch` is the ONE transport the api client uses (`lib/api.ts`), so it is spied
 *     and must stay at zero calls while a control moves. This is the defect the clause
 *     exists to prevent — a tweak that quietly round-trips to the server.
 *   · **The postMessage is BATCHED.** N slider ticks inside one frame produce ONE
 *     message, not N.
 *   · **Save reads the LIVE values back.** The child answers with values that DIFFER
 *     from what the rail sent, and what gets persisted is the child's answer. A Save
 *     that wrote the rail's own state would pass a weaker test and be wrong.
 *   · **Two marked elements produce ONE correction directive**, routed either to the
 *     host's own target (a design loop's guidance channel) or, by default, through the
 *     widget bridge's C32 refresh-injection path.
 *
 *  jsdom caveat inherited from widgetWire.test.tsx: jsdom's postMessage does not
 *  populate `event.source`, so child→parent messages are constructed directly WITH
 *  `source` — which is also exactly how a same-page attacker would forge one. */
import { render, act, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { IframeHtmlPreview } from '../content/renderers'
import { EDIT_MODE_ANNOTATE, EDIT_MODE_READ_KEYS, EDIT_MODE_SET_KEYS, parseEditModeBlock } from './editMode'
import { buildSrcdoc, EDIT_MODE_SCRIPT_SOURCE } from './widgetSrcdoc'

const BEGIN = '/*EDITMODE-BEGIN*/'
const END = '/*EDITMODE-END*/'

const BLOCK = JSON.stringify({
  accent: { label: 'Accent', type: 'color', value: '#3b82f6' },
  radius: { label: 'Corners', type: 'range', value: '4px', min: 0, max: 32, step: 1, unit: 'px' },
})
const SOURCE = [
  '<section id="hero"><button data-testid="cta">Buy</button><p class="price">$9</p></section>',
  `<script>${BEGIN}${BLOCK}${END}</script>`,
].join('\n')

let fetchSpy: ReturnType<typeof vi.fn>

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:artifact-iteration-test'
    URL.revokeObjectURL = () => {}
  }
})

beforeEach(() => {
  // The SINK, not the code path: any request the drag makes lands here.
  fetchSpy = vi.fn(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchSpy)
})
afterEach(() => vi.unstubAllGlobals())

/** Mount the artifact-library preview host with an iteration target. */
async function mount(target: Parameters<typeof IframeHtmlPreview>[0]['iterate']) {
  const view = render(<IframeHtmlPreview content={SOURCE} mode="dark" title="Card" iterate={target} />)
  const iframe = view.container.querySelector('iframe')
  if (!iframe) throw new Error('the host rendered no iframe — the fixture never reached the wire')
  const child = iframe.contentWindow
  if (!child) throw new Error('the iframe has no contentWindow — nothing to talk to')
  const posted = vi.spyOn(child, 'postMessage').mockImplementation(() => {})
  // Open the rail (it is a fold-out, closed by default).
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Iterate on this artifact/i }))
  })
  return { view, iframe, child, posted }
}

/** Let the batched apply flush (one animation frame). */
async function nextFrame() {
  await act(async () => { await new Promise<void>((r) => requestAnimationFrame(() => r())) })
}

/** Deliver a child→parent message with the provenance the host demands. */
function fromChild(child: Window, data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source: child as MessageEventSource }))
  })
}

function setKeyMessages(posted: ReturnType<typeof vi.spyOn>) {
  return posted.mock.calls
    .map((c) => c[0] as { type: string; edits?: { key: string; value: string }[] })
    .filter((m) => m.type === EDIT_MODE_SET_KEYS)
}

describe('EDITMODE seeding — the block IS the declaration', () => {
  it('answers the child\'s ready ping by applying every declared value', async () => {
    // Without this the author would have to write each value twice (block + a :root
    // rule), and a SAVED tweak would not survive a reload — saving rewrites the
    // block, never the stylesheet. Measured while driving the real UI.
    const { child, posted } = await mount({ slug: 'card' })
    expect(setKeyMessages(posted)).toHaveLength(0)   // nothing before the child asks
    fromChild(child, { type: 'widget-edit-ready' })
    const msgs = setKeyMessages(posted)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].edits).toEqual([
      { key: 'accent', value: '#3b82f6' },
      { key: 'radius', value: '4px' },
    ])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('ignores a ready ping that did not come from THIS frame', async () => {
    const { posted } = await mount({ slug: 'card' })
    const foreign = document.createElement('iframe')
    document.body.appendChild(foreign)
    fromChild(foreign.contentWindow as Window, { type: 'widget-edit-ready' })
    expect(setKeyMessages(posted)).toHaveLength(0)
    foreign.remove()
  })
})

describe('EDITMODE — a drag restyles live, with zero network requests', () => {
  it('sends the edit into the frame and makes NO request', async () => {
    const { child, posted } = await mount({ slug: 'card' })
    const slider = screen.getByRole('slider', { name: 'Corners' })
    await act(async () => { fireEvent.change(slider, { target: { value: '18' } }) })
    await nextFrame()

    const msgs = setKeyMessages(posted)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].edits).toEqual([{ key: 'radius', value: '18px' }])
    // The claim, asserted as an absence.
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(child).toBeTruthy()
  })

  it('BATCHES a drag — six ticks inside one frame are ONE message', async () => {
    const { posted } = await mount({ slug: 'card' })
    const slider = screen.getByRole('slider', { name: 'Corners' })
    await act(async () => {
      for (const v of [5, 8, 11, 14, 17, 20]) fireEvent.change(slider, { target: { value: String(v) } })
    })
    await nextFrame()

    const msgs = setKeyMessages(posted)
    expect(msgs).toHaveLength(1)
    // Last value wins; the five intermediate positions cost nothing.
    expect(msgs[0].edits).toEqual([{ key: 'radius', value: '20px' }])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('coalesces edits to SEVERAL params into one message', async () => {
    const { posted } = await mount({ slug: 'card' })
    await act(async () => {
      fireEvent.change(screen.getByRole('slider', { name: 'Corners' }), { target: { value: '9' } })
      fireEvent.change(screen.getByLabelText('Accent colour'), { target: { value: '#ff0000' } })
    })
    await nextFrame()

    const msgs = setKeyMessages(posted)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].edits).toEqual([
      { key: 'radius', value: '9px' },
      { key: 'accent', value: '#ff0000' },
    ])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('refuses a value the CSS allowlist rejects rather than posting it', async () => {
    // A native colour input can only produce hex, so the guard is reached through the
    // TEXT control that an unparseable authored colour (oklch) renders instead.
    const view = render(
      <IframeHtmlPreview content={SOURCE.replace('#3b82f6', 'oklch(0.7 0.1 250)')} mode="dark" title="Card" iterate={{ slug: 'card' }} />,
    )
    const child = view.container.querySelector('iframe')!.contentWindow!
    const posted = vi.spyOn(child, 'postMessage').mockImplementation(() => {})
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Iterate on this artifact/i })) })
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Accent value'), { target: { value: 'red;}html{display:none' } })
    })
    await nextFrame()
    expect(setKeyMessages(posted)).toHaveLength(0)
  })
})

describe('EDITMODE Save — the LIVE values, not the ones the rail thinks it sent', () => {
  it('reads back, rewrites the fenced block, and persists it as a new version', async () => {
    const persistVersion = vi.fn(async (_next: string): Promise<void> => {})
    const { child, posted } = await mount({ slug: 'card', persistVersion })

    await act(async () => { fireEvent.change(screen.getByRole('slider', { name: 'Corners' }), { target: { value: '18' } }) })
    await nextFrame()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save as a new version/i })) })
    const read = posted.mock.calls.map((c) => c[0] as { type: string; keys?: string[] }).filter((m) => m.type === EDIT_MODE_READ_KEYS)
    expect(read).toHaveLength(1)
    expect(read[0].keys).toEqual(['accent', 'radius'])

    // The document reports something DIFFERENT from what the rail sent — a stylesheet
    // clamped it, say. What is persisted must be the document's answer.
    fromChild(child, { type: 'widget-edit-values', values: { accent: '#00ff00', radius: '16px' } })
    await waitFor(() => expect(persistVersion).toHaveBeenCalledTimes(1))

    const next = persistVersion.mock.calls[0][0]
    const params = parseEditModeBlock(next)!.params
    expect(params.find((p) => p.key === 'radius')!.value).toBe('16px')   // live, not 18px
    expect(params.find((p) => p.key === 'accent')!.value).toBe('#00ff00')
    expect(next).not.toContain('18px')
    // Everything outside the fence survived untouched.
    expect(next.slice(0, next.indexOf(BEGIN))).toBe(SOURCE.slice(0, SOURCE.indexOf(BEGIN)))
    expect(next.slice(next.indexOf(END))).toBe(SOURCE.slice(SOURCE.indexOf(END)))
  })

  it('ignores keys the artifact never declared, however the child answers', async () => {
    const persistVersion = vi.fn(async (_next: string): Promise<void> => {})
    const { child } = await mount({ slug: 'card', persistVersion })
    await act(async () => { fireEvent.change(screen.getByRole('slider', { name: 'Corners' }), { target: { value: '7' } }) })
    await nextFrame()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save as a new version/i })) })
    fromChild(child, { type: 'widget-edit-values', values: { radius: '7px', smuggled: 'red' } })
    await waitFor(() => expect(persistVersion).toHaveBeenCalledTimes(1))
    expect(persistVersion.mock.calls[0][0]).not.toContain('smuggled')
  })

  it('REFUSES to save when the frame never answers — it does not write a guess', async () => {
    vi.useFakeTimers()
    try {
      const persistVersion = vi.fn(async (_next: string): Promise<void> => {})
      const view = render(<IframeHtmlPreview content={SOURCE} mode="dark" title="Card" iterate={{ slug: 'card', persistVersion }} />)
      const child = view.container.querySelector('iframe')!.contentWindow!
      vi.spyOn(child, 'postMessage').mockImplementation(() => {})
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Iterate on this artifact/i })) })
      await act(async () => { fireEvent.change(screen.getByRole('slider', { name: 'Corners' }), { target: { value: '18' } }) })
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save as a new version/i })) })
      await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
      expect(persistVersion).not.toHaveBeenCalled()
      expect(screen.getByRole('alert').textContent).toContain('did not report its live values')
      view.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('offers no save at all when the host supplies no persist path', async () => {
    await mount({ slug: 'card' })
    expect(screen.queryByRole('button', { name: /Save as a new version/i })).toBeNull()
  })
})

describe('annotate mode — two marked elements, ONE correction', () => {
  const anchorA = { type: 'widget-annotation', selector: '[data-testid="cta"]', tag: 'button', outerHTML: '<button>Buy</button>', parentContext: 'section[id="hero"]' }
  const anchorB = { type: 'widget-annotation', selector: 'p.price', tag: 'p', outerHTML: '<p class="price">$9</p>', parentContext: 'section[id="hero"]' }

  it('toggling annotate tells the frame, and the anchors accumulate in the rail', async () => {
    const { child, posted } = await mount({ slug: 'card' })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Mark elements/i })) })
    const toggles = posted.mock.calls.map((c) => c[0] as { type: string; on?: boolean }).filter((m) => m.type === EDIT_MODE_ANNOTATE)
    expect(toggles).toEqual([{ type: EDIT_MODE_ANNOTATE, on: true }])

    fromChild(child, anchorA)
    fromChild(child, anchorB)
    expect(await screen.findByLabelText('Correction for [data-testid="cta"]')).toBeTruthy()
    expect(screen.getByLabelText('Correction for p.price')).toBeTruthy()
    // An anchor with no selector is refused at the wire and never becomes a row.
    fromChild(child, { type: 'widget-annotation', selector: '' })
    expect(screen.getAllByRole('textbox').filter((el) => el.getAttribute('aria-label')?.startsWith('Correction for'))).toHaveLength(2)
  })

  it('dispatches ONE directive carrying BOTH anchors to the host target (a design loop)', async () => {
    const correction = vi.fn(async (_next: string): Promise<void> => {})
    const { child } = await mount({ slug: 'card', correction })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Mark elements/i })) })
    fromChild(child, anchorA)
    fromChild(child, anchorB)
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Correction for [data-testid="cta"]'), { target: { value: 'make it green' } })
      fireEvent.change(screen.getByLabelText('Correction for p.price'), { target: { value: 'bigger' } })
    })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Send one correction/i })) })

    await waitFor(() => expect(correction).toHaveBeenCalledTimes(1))
    const directive = correction.mock.calls[0][0]
    expect(directive).toContain('[data-testid="cta"]')
    expect(directive).toContain('make it green')
    expect(directive).toContain('p.price')
    expect(directive).toContain('bigger')
    // Sending clears the rail and leaves annotate off.
    expect(screen.queryByLabelText('Correction for p.price')).toBeNull()
    expect(screen.getByRole('button', { name: /Mark elements/i })).toBeTruthy()
  })

  it('with no host target, routes through the widget bridge with the C32 refresh suffix', async () => {
    const published: string[] = []
    const record = (e: Event) => { published.push(String((e as CustomEvent).detail?.text)) }
    window.addEventListener('ne:widget-action', record as EventListener)
    try {
      const { child } = await mount({ slug: 'sales-snapshot' })
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Mark elements/i })) })
      fromChild(child, anchorA)
      fromChild(child, anchorB)
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Send one correction/i })) })

      expect(published).toHaveLength(1)
      expect(published[0].startsWith('[UI] correction: 2 elements marked')).toBe(true)
      expect(published[0]).toContain('[data-testid="cta"]')
      expect(published[0]).toContain('p.price')
      expect(published[0].endsWith('(refresh artifact "sales-snapshot" in place)')).toBe(true)
    } finally {
      window.removeEventListener('ne:widget-action', record as EventListener)
    }
  })
})

describe('a host that offers no iteration is unchanged', () => {
  it('renders no rail affordance and ships no iteration script', async () => {
    const view = render(<IframeHtmlPreview content={SOURCE} mode="dark" title="Card" />)
    expect(screen.queryByRole('button', { name: /Iterate on this artifact/i })).toBeNull()
    expect(view.container.querySelector('iframe')).toBeTruthy()
  })

  it('the child document differs by EXACTLY the inserted script — nothing else moved', () => {
    // Every existing caller of buildSrcdoc omits `editMode`, so its document must be
    // byte-for-byte what it was. Asserting it here means a later edit to the assembly
    // (a stray newline from a new interpolation, say) reddens instead of silently
    // changing thousands of already-rendered widgets.
    const opts = { html: SOURCE, themeVars: { '--bg': 'black' }, mode: 'dark' as const }
    const off = buildSrcdoc(opts)
    const on = buildSrcdoc({ ...opts, editMode: true })
    expect(off).not.toContain('__edit_mode_')
    expect(on).toContain('__edit_mode_set_keys')
    const inserted = `<script>\n${EDIT_MODE_SCRIPT_SOURCE}\n<\/script>\n`
    expect(on.replace(inserted, '')).toBe(off)
    // And with no host script either — the download / open-in-tab document.
    const bare = buildSrcdoc({ ...opts, includeHost: false })
    expect(bare).not.toContain('__edit_mode_')
    expect(buildSrcdoc({ ...opts, includeHost: false, editMode: true }).replace(`<script>\n${EDIT_MODE_SCRIPT_SOURCE}\n<\/script>`, '')).toBe(bare)
  })
})
