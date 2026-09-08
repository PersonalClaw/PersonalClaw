// The app-AUTHORED surface the a11y harness could not otherwise reach.
//
// WHY THIS EXISTS. `a11y.spec.ts` and `nonAxeA11yChecks` sweep every route in `e2e/routes.ts`, and
// `app/not-a-real-app` covers the app-hosting SHELL (its 404 branch). Neither reaches an app's OWN
// DOM: that needs an app installed WITH a built bundle, so `routeManifestParity` recorded it as the
// surviving half of an exemption. Three of ~51 first-party apps render their own surface, straight
// into the HOST document rather than an iframe — so an app-authored focus trap, a mouse-only click
// target or a clipped focus ring lands in the same accessibility tree as the shell's, and nothing
// was looking at it.
//
// This is deliberately NOT a copy of a real app. Depending on the apps repo would make the gate's
// coverage depend on a sibling checkout being present — the exact shape that made
// `test_apps_import_boundary` a no-op for its whole life (core issue 1777). A fixture inside this
// repo is always present, so the sweep cannot silently stop happening.
//
// 🪤 IT IS CLEAN ON PURPOSE, AND THAT IS WHY THE SPEC ASSERTS THE MOUNT. Both detectors return `[]`
// on a healthy tree, so "swept and clean" and "never swept" produce identical output. The route
// entry alone would therefore prove nothing. `appSurface.spec.ts` asserts this DOM is actually
// present and its control actually takes focus, which is what makes the green mean something —
// the same reasoning the shell's EmptyState mount assertion rests on.
//
// The imperative `mount(el, ctx)` shape is used rather than the React one so the fixture needs no
// bundler step: this file is served verbatim as ESM by `/apps/{name}/ui/{tail}`.

/** Marker the spec locates. Must stay in sync with `appSurface.spec.ts`. */
const MARKER = 'e2e-app-surface'

/**
 * @param {HTMLElement} el   host-provided mount point, inside the real document
 * @param {{name: string}} ctx  the app context (name, permissions, uiCapabilities)
 * @returns {() => void} cleanup, so the host can unmount without leaking listeners
 */
export function mount(el, ctx) {
  const root = document.createElement('section')
  root.dataset.testid = MARKER
  // A real accessible name, not a bare div: the detectors judge focus stops and click
  // targets, so the fixture has to offer both in their correct form.
  root.setAttribute('aria-labelledby', `${MARKER}-heading`)

  const heading = document.createElement('h2')
  heading.id = `${MARKER}-heading`
  heading.textContent = 'App-authored surface'
  root.appendChild(heading)

  const note = document.createElement('p')
  note.textContent =
    `Rendered by ${ctx?.name ?? 'an app'}'s own bundle, in the host document. ` +
    'The a11y sweep covers this DOM.'
  root.appendChild(note)

  // A genuine control: a real <button> with a visible focus ring that is NOT clipped by an
  // ancestor. If either detector regresses into a no-op, this is the shape that should have
  // been judged — which is why the spec proves the button can hold focus.
  const button = document.createElement('button')
  button.type = 'button'
  button.textContent = 'Acknowledge'
  button.dataset.testid = `${MARKER}-button`
  let count = 0
  const onClick = () => { count += 1; button.textContent = `Acknowledged ${count}` }
  button.addEventListener('click', onClick)
  root.appendChild(button)

  el.appendChild(root)
  return () => {
    button.removeEventListener('click', onClick)
    root.remove()
  }
}
