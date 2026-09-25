/* The owner-token link, handled in the browser.
 *
 * ONE source, inlined by the gateway (owner_token_url.py) into the three documents that stand
 * between a person and the dashboard: the SPA document a `?token=` link opens, the paste-token
 * Connect gate, and the password sign-in page.
 *
 *   scrub()          takes `token` out of the address bar and the CURRENT history entry, keeping
 *                    the path, every other query parameter and the hash route. By the time a
 *                    document runs, the gateway has already exchanged the token for the HttpOnly
 *                    session cookie, so the URL copy is only something to leak: into a copied
 *                    link, a screenshot, the Back button.
 *   route(hash)      the hash when it is a dashboard route ("#/..."), else "".
 *   connectUrl(raw)  where the Connect gate sends a pasted token: THIS origin's root, the token,
 *                    and the route the gate was opened at. A pasted URL contributes only its
 *                    token, so pasting cannot move anyone to another origin or path.
 *   home()           "/" plus the route — where a successful sign-in lands, instead of "/".
 */
(function (root) {
  'use strict'

  var TOKEN = 'token'
  // "#/" and then only characters a dashboard route uses. The result is only ever the FRAGMENT
  // of a URL built on this origin, so it cannot change where the browser goes; the check keeps
  // it to the shape the hash router reads. Refused: anything that is not "#/...", "#//..."
  // (it reads as a scheme-relative URL), whitespace, control characters, backslashes, and
  // anything longer than a route plausibly is.
  var ROUTE = /^#\/(?!\/)[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*$/

  function route(hash) {
    var h = String(hash || '')
    return h.length <= 2048 && ROUTE.test(h) ? h : ''
  }

  function scrub() {
    var url = new URL(root.location.href)
    if (!url.searchParams.has(TOKEN)) return false
    url.searchParams.delete(TOKEN)
    var clean = url.pathname + url.search + url.hash
    try {
      root.history.replaceState(root.history.state, '', clean)
    } catch (_) {
      // A webview that refuses History API writes: a replacing navigation has the same
      // property (no history entry keeps the token), and the cookie is already set.
      root.location.replace(clean)
    }
    return true
  }

  function tokenFrom(raw) {
    var value = String(raw || '').trim()
    if (!value) return ''
    try {
      return new URL(value).searchParams.get(TOKEN) || ''
    } catch (_) {
      return value
    }
  }

  function connectUrl(raw) {
    var token = tokenFrom(raw)
    if (!token) return ''
    var target = new URL('/', root.location.origin)
    target.searchParams.set(TOKEN, token)
    target.hash = route(root.location.hash)
    return target.href
  }

  function home() {
    return '/' + route(root.location.hash)
  }

  root.PersonalClawOwnerToken = Object.freeze({
    route: route,
    scrub: scrub,
    connectUrl: connectUrl,
    home: home,
  })

  var self = root.document && root.document.currentScript
  if (self && self.hasAttribute('data-owner-token-scrub')) scrub()
})(window)
