/** The CHILD half of artifact iteration, executed.
 *
 *  `EDIT_MODE_SCRIPT_SOURCE` runs inside the widget's sandboxed frame. It carries
 *  three rules that only mean something if they actually run, so this file runs the
 *  shipped source rather than grepping it:
 *
 *   1. **Only the parent may drive it.** A `__edit_mode_*` message from anything that
 *      is not `window.parent` is refused — the mirror image of the provenance check
 *      the host performs on the way back.
 *   2. **An annotation is a human gesture.** `e.isTrusted` gates a click exactly as it
 *      gates a `[data-action]`; a widget's own `el.click()` mints nothing.
 *   3. **While annotating, a click is CONSUMED.** It marks the element and does not
 *      also fire the widget's own action, which would send the agent a form submission
 *      the user never made.
 *
 *  jsdom cannot mint a trusted event and `isTrusted` is not redefinable on an
 *  instance, so — following widgetHostScript.test.ts — the fixture captures the
 *  handlers the script installs and invokes them with both flag values. The untrusted
 *  leg is additionally driven through the real dispatch path (`el.click()`), which is
 *  exactly what a widget's script has. */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { EDIT_MODE_SCRIPT_SOURCE } from './widgetSrcdoc'

const posted = vi.fn()
let onMessage: (e: { source: unknown; data: unknown }) => void
let onClick: (e: { isTrusted: boolean; target: Element; preventDefault: () => void; stopPropagation: () => void }) => void

beforeAll(() => {
  vi.spyOn(window, 'postMessage').mockImplementation(((...args: unknown[]) => { posted(...args) }) as never)
  document.body.innerHTML = `
    <section class="hero" id="hero">
      <button data-testid="cta" data-action="buy" class="px-4 rounded-md">Buy</button>
      <p class="price px-2">$9</p>
      <ul><li class="px-1">a</li><li class="px-1">b</li></ul>
    </section>`
  const realAddWin = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation(((type: string, fn: never, ...rest: never[]) => {
    if (type === 'message') onMessage = fn as unknown as typeof onMessage
    realAddWin(type, fn, ...rest)
  }) as never)
  const realAddDoc = document.addEventListener.bind(document)
  vi.spyOn(document, 'addEventListener').mockImplementation(((type: string, fn: never, ...rest: never[]) => {
    if (type === 'click') onClick = fn as unknown as typeof onClick
    realAddDoc(type, fn, ...rest)
  }) as never)
  new Function(EDIT_MODE_SCRIPT_SOURCE)()
  // The install-time ready ping is asserted in its own test below; clear it so it
  // does not leak into every other assertion about what the script posts.
  expect(posted.mock.calls.map((c) => c[0])).toEqual([{ type: 'widget-edit-ready' }])
  posted.mockClear()
})

beforeEach(() => {
  posted.mockClear()
  document.documentElement.removeAttribute('style')
  // Every test starts with annotate OFF.
  onMessage({ source: window.parent, data: { type: '__edit_mode_annotate', on: false } })
})

const el = (sel: string) => document.querySelector(sel) as Element

/** Deliver a parent→child message as if it came from `source`. */
function fromParent(data: unknown, source: unknown = window.parent) {
  onMessage({ source, data })
}

function click(sel: string, isTrusted: boolean) {
  const preventDefault = vi.fn()
  const stopPropagation = vi.fn()
  onClick({ isTrusted, target: el(sel), preventDefault, stopPropagation })
  return { preventDefault, stopPropagation }
}

describe('edit-mode child script — live value application', () => {
  it('announces readiness on install, so the host can seed the declared values', () => {
    // The document loads from a blob asynchronously; a host that seeded at its own
    // mount would post into an about:blank window and lose every value. The child
    // asking is what makes the timing correct. (Asserted in beforeAll, restated here
    // so the reason is discoverable at the assertion.)
    posted.mockClear()
    new Function(EDIT_MODE_SCRIPT_SOURCE)()
    expect(posted.mock.calls.map((c) => c[0])).toContainEqual({ type: 'widget-edit-ready' })
  })

  it('applies a batch of keys to :root custom properties', () => {
    fromParent({ type: '__edit_mode_set_keys', edits: [
      { key: 'accent', value: '#ff0000' },
      { key: 'radius', value: '20px' },
    ] })
    expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#ff0000')
    expect(document.documentElement.style.getPropertyValue('--radius')).toBe('20px')
  })

  it('refuses a key that is not a custom-property name, and a non-string value', () => {
    fromParent({ type: '__edit_mode_set_keys', edits: [
      { key: 'a;color:red', value: 'blue' },
      { key: '--nested', value: 'blue' },
      { key: 'numeric', value: 4 },
    ] })
    expect(document.documentElement.getAttribute('style')).toBeNull()
  })

  it('answers a read-back with the CURRENT computed values of the keys asked for', () => {
    fromParent({ type: '__edit_mode_set_keys', edits: [{ key: 'accent', value: '#00ff00' }] })
    posted.mockClear()
    fromParent({ type: '__edit_mode_read_keys', keys: ['accent', 'unset', 'bad;key'] })
    expect(posted).toHaveBeenCalledTimes(1)
    const msg = posted.mock.calls[0][0] as { type: string; values: Record<string, string> }
    expect(msg.type).toBe('widget-edit-values')
    expect(msg.values.accent).toBe('#00ff00')
    expect(msg.values.unset).toBe('')
    expect(msg.values).not.toHaveProperty('bad;key')
  })

  it('ignores a malformed set/read message instead of throwing', () => {
    expect(() => {
      fromParent({ type: '__edit_mode_set_keys' })
      fromParent({ type: '__edit_mode_read_keys', keys: 'accent' })
      fromParent({ type: '__edit_mode_set_keys', edits: [null, 3, {}] })
      fromParent(null)
      fromParent('a string')
    }).not.toThrow()
    expect(posted).not.toHaveBeenCalled()
  })
})

describe('edit-mode child script — provenance (rule 1)', () => {
  it('refuses a __edit_mode_* message from anything that is not the parent', () => {
    const sibling = { postMessage() {} }
    fromParent({ type: '__edit_mode_set_keys', edits: [{ key: 'accent', value: '#ff0000' }] }, sibling)
    expect(document.documentElement.getAttribute('style')).toBeNull()
    fromParent({ type: '__edit_mode_read_keys', keys: ['accent'] }, sibling)
    expect(posted).not.toHaveBeenCalled()
  })

  it('ignores a message outside the reserved namespace even from the parent', () => {
    fromParent({ type: 'set_keys', edits: [{ key: 'accent', value: '#ff0000' }] })
    fromParent({ type: 'widget-action', action: 'x' })
    expect(document.documentElement.getAttribute('style')).toBeNull()
    expect(posted).not.toHaveBeenCalled()
  })
})

describe('edit-mode child script — annotation capture', () => {
  beforeEach(() => fromParent({ type: '__edit_mode_annotate', on: true }))

  it('reports nothing at all while annotate is off', () => {
    fromParent({ type: '__edit_mode_annotate', on: false })
    click('[data-testid="cta"]', true)
    expect(posted).not.toHaveBeenCalled()
  })

  it('refuses an UNTRUSTED click — a widget cannot annotate itself (rule 2)', () => {
    const { preventDefault } = click('[data-testid="cta"]', false)
    expect(posted).not.toHaveBeenCalled()
    expect(preventDefault).not.toHaveBeenCalled()
    // …and through the real dispatch path, which is what a widget's script has.
    ;(el('[data-testid="cta"]') as HTMLElement).click()
    expect(posted.mock.calls.filter((c) => (c[0] as { type: string }).type === 'widget-annotation')).toHaveLength(0)
  })

  it('CONSUMES a trusted click so it is not also a widget action (rule 3)', () => {
    const { preventDefault, stopPropagation } = click('[data-testid="cta"]', true)
    expect(preventDefault).toHaveBeenCalled()
    expect(stopPropagation).toHaveBeenCalled()
  })

  it('prefers data-testid over every other anchor', () => {
    click('[data-testid="cta"]', true)
    expect((posted.mock.calls[0][0] as { selector: string }).selector).toBe('[data-testid="cta"]')
  })

  it('falls back to id when there is no testid', () => {
    click('#hero', true)
    expect((posted.mock.calls[0][0] as { selector: string }).selector).toBe('[id="hero"]')
  })

  it('uses a UNIQUE non-utility class chain, dropping utility noise', () => {
    click('.price', true)
    // `px-2` is utility noise and must not appear; `price` identifies one element.
    expect((posted.mock.calls[0][0] as { selector: string }).selector).toBe('p.price')
  })

  it('falls through to an nth-child path when no class identifies ONE element', () => {
    // Both <li>s carry only `px-1` (utility, dropped) → no class chain at all.
    click('li:nth-child(2)', true)
    const sel = (posted.mock.calls[0][0] as { selector: string }).selector
    expect(sel).toMatch(/^body > /)
    expect(sel).toContain('li:nth-child(2)')
    // The derived selector must actually resolve to the element that was clicked.
    expect(document.querySelector(sel)).toBe(el('li:nth-child(2)'))
  })

  it('carries the element, its tag and its parent context', () => {
    click('.price', true)
    const msg = posted.mock.calls[0][0] as { tag: string; outerHTML: string; parentContext: string }
    expect(msg.tag).toBe('p')
    expect(msg.outerHTML).toContain('$9')
    expect(msg.parentContext).toBe('section[id="hero"]')
  })

  it('caps the reported markup so one element cannot spend the whole turn budget', () => {
    const big = document.createElement('div')
    big.className = 'huge'
    big.textContent = 'x'.repeat(5000)
    document.body.appendChild(big)
    click('.huge', true)
    expect((posted.mock.calls[0][0] as { outerHTML: string }).outerHTML.length).toBe(400)
    big.remove()
  })
})
