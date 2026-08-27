import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applySafeAreaInsets,
  readSafeAreaInsets,
  SAFE_AREA_VARS,
  watchSafeAreaInsets,
} from '../www/shell/safeArea.mjs'

/** A document whose root element resolves the four custom properties to `values`. */
function fakeEnvironment(values = {}) {
  const doc = { documentElement: { tagName: 'HTML' } }
  const listeners = []
  const view = {
    getComputedStyle(element) {
      assert.equal(element, doc.documentElement, 'insets must be read off the ROOT element')
      return { getPropertyValue: (name) => values[name] ?? '' }
    },
    addEventListener(event, handler) {
      listeners.push([event, handler])
    },
  }
  return { doc, view, listeners, target: { style: {} } }
}

/** A notched phone in portrait. */
const NOTCHED = {
  [SAFE_AREA_VARS.top]: '47px',
  [SAFE_AREA_VARS.right]: '0px',
  [SAFE_AREA_VARS.bottom]: '34px',
  [SAFE_AREA_VARS.left]: '0px',
}

test('the insets are read out of the custom properties shell.css resolves env() into', () => {
  const { doc, view } = fakeEnvironment(NOTCHED)
  assert.deepEqual(readSafeAreaInsets(doc, view), {
    top: '47px',
    right: '0px',
    bottom: '34px',
    left: '0px',
  })
})

test('applied, not merely declared — the target element carries the padding afterwards', () => {
  const { doc, view, target } = fakeEnvironment(NOTCHED)
  // Before: nothing. This is the half that makes the assertion below non-vacuous — a CSS-only
  // shell would leave the element exactly like this and still "declare" safe areas.
  assert.deepEqual(target.style, {})

  const applied = applySafeAreaInsets(doc, view, target)

  assert.equal(target.style.paddingTop, '47px')
  assert.equal(target.style.paddingBottom, '34px')
  assert.equal(target.style.paddingLeft, '0px')
  assert.equal(target.style.paddingRight, '0px')
  assert.deepEqual(applied, { top: '47px', right: '0px', bottom: '34px', left: '0px' })
})

test('each inset lands on its OWN edge', () => {
  // A transposed mapping (top->bottom, left->right) is the defect a symmetric fixture cannot
  // see, so every value here is distinct.
  const { doc, view, target } = fakeEnvironment({
    [SAFE_AREA_VARS.top]: '1px',
    [SAFE_AREA_VARS.right]: '2px',
    [SAFE_AREA_VARS.bottom]: '3px',
    [SAFE_AREA_VARS.left]: '4px',
  })
  applySafeAreaInsets(doc, view, target)
  assert.deepEqual(target.style, {
    paddingTop: '1px',
    paddingRight: '2px',
    paddingBottom: '3px',
    paddingLeft: '4px',
  })
})

test('a platform without insets reports 0px rather than an empty string', () => {
  // An empty `padding-top: ` is invalid CSS and would be dropped, so the fallback has to be a
  // real length. A browser preview with no `viewport-fit=cover` lands here.
  const { doc, view, target } = fakeEnvironment({})
  applySafeAreaInsets(doc, view, target)
  assert.deepEqual(target.style, {
    paddingTop: '0px',
    paddingRight: '0px',
    paddingBottom: '0px',
    paddingLeft: '0px',
  })
})

test('rotation reapplies — insets read once are correct in one orientation only', () => {
  const { doc, view, listeners, target } = fakeEnvironment(NOTCHED)
  watchSafeAreaInsets(doc, view, target)
  assert.equal(target.style.paddingTop, '47px')
  assert.deepEqual(
    listeners.map(([event]) => event).sort(),
    ['orientationchange', 'resize'],
  )

  // Landscape on the same device: the notch moves to the left edge.
  Object.assign(NOTCHED, {
    [SAFE_AREA_VARS.top]: '0px',
    [SAFE_AREA_VARS.left]: '47px',
  })
  for (const [, handler] of listeners) handler()
  assert.equal(target.style.paddingTop, '0px')
  assert.equal(target.style.paddingLeft, '47px')
})

test('a missing document degrades to zero rather than throwing in the shell boot path', () => {
  assert.deepEqual(readSafeAreaInsets(undefined, undefined), {
    top: '0px',
    right: '0px',
    bottom: '0px',
    left: '0px',
  })
})
