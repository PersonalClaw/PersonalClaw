import { sanitizeCssValue } from './cssSanitize'

/** The CSP every widget document carries. Nothing is fetched: no `https:` source anywhere, so a
 *  widget — or a script an agent put in one — reaches no third party. Tailwind rides in the
 *  document as CSS this app compiled (`widgetStyles`), and a react artifact's React and compiled
 *  JSX as inline scripts (`reactPreview`). `connect-src 'none'` keeps it off the network entirely.
 *  Pinned by `widgetFetchesNothing.test.ts`. */
const WIDGET_CSP =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; "
  + "img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none';"

/** CSS that cannot end the `<style>` element it is placed in. */
const styleSafe = (css: string) => css.replace(/<\/style/gi, '<\\/style')

// NE design tokens (--color-*) exposed to widgets, each aliased to a short,
// documented widget-facing name (--bg, --text, --accent, …) so agent widgets
// written against the documented contract inherit the live theme. Both the
// alias and the raw NE token are injected.
const TOKEN_ALIASES: Record<string, string> = {
  '--bg': '--color-canvas',
  '--bg-elevated': '--color-surface-high',
  '--bg-hover': '--color-surface-highest',
  '--card': '--color-surface-container',
  '--card-fg': '--color-on-surface',
  '--text': '--color-on-surface',
  '--text-strong': '--color-on-surface',
  '--muted': '--color-on-surface-low',
  '--muted-strong': '--color-on-surface-var',
  '--border': '--color-outline-variant',
  '--border-strong': '--color-outline',
  '--accent': '--color-primary',
  '--accent-hover': '--color-primary-emphasis',
  '--accent-subtle': '--color-primary-container',
  '--ok': '--color-ok',
  '--warn': '--color-warn',
  '--danger': '--color-danger',
  '--info': '--color-info',
}

/** Read the live theme into a {widget-var: value} map by resolving each NE token
 *  off document.documentElement and aliasing to the widget-facing name. */
export function readThemeVars(): Record<string, string> {
  if (typeof window === 'undefined' || typeof document === 'undefined') return {}
  const computed = getComputedStyle(document.documentElement)
  const out: Record<string, string> = {}
  for (const [alias, token] of Object.entries(TOKEN_ALIASES)) {
    const v = sanitizeCssValue(computed.getPropertyValue(token))
    if (v) { out[alias] = v; out[token] = v }
  }
  return out
}

function themeStyleBlock(vars: Record<string, string>, mode: 'dark' | 'light', transparentBody = false): string {
  const rootBody = Object.entries(vars).map(([k, v]) => `${k}:${v}`).join(';')
  // transparentBody: the inline chat host renders the widget frameless, directly
  // against the app background — the iframe body must not paint its own canvas.
  // Standalone contexts (download, open-in-new-tab) keep the solid theme bg so
  // the document is readable outside the app.
  return rootBody
    ? `:root{${rootBody};color-scheme:${mode}}body{background:${transparentBody ? 'transparent' : 'var(--bg)'};color:var(--text)}`
    : ''
}

/** Reports content height + forwards `[data-action]` clicks to the parent, as
 *  `widget-height` / `widget-action` (see useWidgetActionBridge.ts for the wire
 *  contract). Exported as SOURCE so the human-gesture gate below can be executed —
 *  and not merely grepped for — by widgetHostScript.test.ts. */
export const HOST_SCRIPT_SOURCE = `(function(){
  function report(){
    var h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    // Natural content width: the widest top-level element's rendered box. A
    // fixed/max-width card reports its own width (< iframe width) so the host
    // can shrink-wrap + let prose flow beside it; fluid content fills the
    // iframe and reports full width -> the host keeps it block.
    var w = 0;
    var kids = document.body.children;
    for (var i = 0; i < kids.length; i++) {
      var t = kids[i].tagName;
      if (t === 'SCRIPT' || t === 'STYLE') continue;
      var r = kids[i].getBoundingClientRect();
      if (r.width > w) w = r.width;
    }
    parent.postMessage({type:'widget-height', height:h, width:Math.ceil(w)}, '*');
  }
  new ResizeObserver(report).observe(document.body);
  window.addEventListener('load', function(){ setTimeout(report, 100); });
  report();
  // ONE payload rule, called from the click path and the form-submit path below. It used to
  // live inline in the click handler; a second copy for submit is the thing most likely to
  // disagree about what a widget sent.
  function send(el){
    var action = el.dataset.action;
    var payload = {};
    try { payload = JSON.parse(el.dataset.payload || '{}'); } catch(x){}
    var inputs = document.querySelectorAll('input,select,textarea');
    var formData = {};
    inputs.forEach(function(inp){
      var n = inp.name || inp.id || inp.getAttribute('data-field');
      if (!n) return;
      if (inp.type === 'checkbox') formData[n] = inp.checked;
      else if (inp.type === 'radio') { if (inp.checked) formData[n] = inp.value; }
      else formData[n] = inp.value;
    });
    if (Object.keys(formData).length) payload.formData = formData;
    parent.postMessage({type:'widget-action', action:action, payload:payload}, '*');
  }
  document.addEventListener('click', function(e){
    if (!e.isTrusted) return;
    var el = e.target.closest('[data-action]');
    if (!el) return;
    e.preventDefault();
    send(el);
  });
  // 🔴 A FORM SUBMIT USED TO VANISH. The frame is sandboxed \`allow-scripts\` with no
  // \`allow-forms\`, so the browser blocks a real submit and reports it only to the frame's own
  // console — the user sees a Submit button that does nothing at all, and the agent that wrote
  // the widget never learns why. Measured in the field twice, months apart (#2263): the agent
  // ended up telling the user to copy 200 lines of text out of the widget and paste them into
  // chat.
  //
  // Submit is unambiguous intent to send data, so it is claimed rather than left to the
  // browser:
  //   · a form carrying a \`[data-action]\` (its submitter, or any inside it) SENDS through the
  //     documented channel — which also makes Enter-in-a-text-field work, where before only a
  //     mouse click on that same button did;
  //   · a form with no action anywhere is a widget that cannot possibly deliver, so it says so
  //     through \`widget-error\` instead of failing mute.
  document.addEventListener('submit', function(e){
    if (!e.isTrusted) return;
    e.preventDefault();
    var form = e.target;
    var el = (e.submitter && e.submitter.closest && e.submitter.closest('[data-action]'))
      || (form.matches && form.matches('[data-action]') ? form : null)
      || (form.querySelector && form.querySelector('[data-action]'));
    if (el) { send(el); return; }
    parent.postMessage({type:'widget-error', message:'This widget cannot submit: a widget sends data with data-action on the button (form submission is blocked in the widget sandbox).'}, '*');
  });
})();`

const HOST_SCRIPT = `<script>\n${HOST_SCRIPT_SOURCE}\n<\/script>`

/** The CHILD half of artifact iteration: applies live
 *  EDITMODE values to `:root`, reads them back on demand, and derives an anchor for
 *  a click-annotated element. Injected only for a host that actually offers
 *  iteration, so every other srcdoc is byte-identical to before.
 *
 *  Three rules this script must not lose:
 *   · **Only the parent may drive it.** `e.source === window.parent` mirrors, inside
 *     the frame, the provenance check the host performs on the way back. A sibling
 *     frame cannot forge a `__edit_mode_*` message.
 *   · **A key is a name, not an expression.** Keys are re-validated here even though
 *     the parent validated them, because this is where they become CSS.
 *   · **An annotation is a human gesture.** `e.isTrusted` gates it exactly as it
 *     gates an action — a widget's own script must not be able to mint a correction
 *     directive about itself. While annotating, the click is consumed in the CAPTURE
 *     phase so the action forwarder below never also sees it.
 *
 *  Exported as SOURCE so those rules are executed by editModeChildScript.test.ts
 *  rather than grepped for. */
export const EDIT_MODE_SCRIPT_SOURCE = `(function(){
  var KEY_RE = /^[a-zA-Z][a-zA-Z0-9-]*$/;
  var UTIL_RE = /^(p|m|px|py|pt|pb|pl|pr|mx|my|mt|mb|ml|mr|w|h|min|max|text|bg|border|rounded|flex|grid|gap|items|justify|self|col|row|space|font|leading|tracking|shadow|opacity|z|top|left|right|bottom|inset|overflow|absolute|relative|fixed|sticky|block|inline|hidden|truncate|uppercase|lowercase|capitalize|cursor|transition|duration|ease|animate|ring|outline|divide|order|basis|grow|shrink|aspect|object|place|content|whitespace|break|list|underline|antialiased|sr|tabular)(-|$)/;
  var annotating = false;

  function keep(c){
    // Utility-class noise makes a selector that matches forty elements. Drop
    // variants (hover:), arbitrary values (w-[3px]), and the utility prefixes.
    if (!c || c.indexOf(':') >= 0 || c.indexOf('[') >= 0 || c.indexOf('/') >= 0) return false;
    return !UTIL_RE.test(c);
  }
  function classesOf(el){
    var out = [];
    var cl = el.classList ? el.classList : [];
    for (var i = 0; i < cl.length && out.length < 2; i++) if (keep(cl[i])) out.push(cl[i]);
    return out;
  }
  function attrSel(el){
    var t = el.getAttribute && el.getAttribute('data-testid');
    if (t) return '[data-testid="' + t.replace(/["\\\\]/g, '') + '"]';
    if (el.id) return '[id="' + String(el.id).replace(/["\\\\]/g, '') + '"]';
    return '';
  }
  function nthPath(el){
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.body && parts.length < 6) {
      var parent = node.parentNode;
      var idx = 1;
      if (parent) {
        var kids = parent.children;
        for (var i = 0; i < kids.length; i++) { if (kids[i] === node) break; idx++; }
      }
      parts.unshift(node.tagName.toLowerCase() + ':nth-child(' + idx + ')');
      node = parent;
    }
    return (parts.length ? 'body > ' : 'body') + parts.join(' > ');
  }
  // Priority: data-testid -> id -> class chain -> nth-child. The class chain is
  // only accepted when it actually identifies ONE element; otherwise a selector
  // that "works" would point the agent at the wrong card.
  function selectorFor(el){
    var a = attrSel(el);
    if (a) return a;
    var cls = classesOf(el);
    if (cls.length) {
      var sel = el.tagName.toLowerCase() + '.' + cls.join('.');
      try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (x) {}
    }
    return nthPath(el);
  }
  function contextFor(el){
    var p = el.parentElement;
    if (!p || p === document.body) return 'body';
    var a = attrSel(p);
    var cls = classesOf(p);
    return p.tagName.toLowerCase() + (a || (cls.length ? '.' + cls.join('.') : ''));
  }

  window.addEventListener('message', function(e){
    if (e.source !== window.parent) return;
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    var t = d.type;
    if (typeof t !== 'string' || t.indexOf('__edit_mode_') !== 0) return;
    if (t === '__edit_mode_set_keys') {
      var edits = d.edits;
      // Array.isArray, not a .length duck-check: a STRING has a length, and
      // iterating one would apply its characters as keys.
      if (!Array.isArray(edits)) return;
      for (var i = 0; i < edits.length; i++) {
        var ed = edits[i];
        if (!ed || typeof ed.key !== 'string' || typeof ed.value !== 'string') continue;
        if (!KEY_RE.test(ed.key)) continue;
        document.documentElement.style.setProperty('--' + ed.key, ed.value);
      }
      return;
    }
    if (t === '__edit_mode_read_keys') {
      var keys = d.keys;
      if (!Array.isArray(keys)) return;
      var cs = getComputedStyle(document.documentElement);
      var values = {};
      for (var j = 0; j < keys.length; j++) {
        var k = keys[j];
        if (typeof k !== 'string' || !KEY_RE.test(k)) continue;
        values[k] = String(cs.getPropertyValue('--' + k) || '').trim();
      }
      parent.postMessage({type:'widget-edit-values', values: values}, '*');
      return;
    }
    if (t === '__edit_mode_annotate') {
      annotating = !!d.on;
      document.body.style.cursor = annotating ? 'crosshair' : '';
      return;
    }
  });

  // Ask the host for the artifact's declared values as soon as this document can
  // receive them. The EDITMODE block is the DECLARATION — the renderer owns
  // applying it, so an author writes each value once and a saved tweak survives a
  // reload without the stylesheet having to be rewritten too. Seeding on a timer or
  // at parent mount would race the blob load and silently drop the values.
  parent.postMessage({type:'widget-edit-ready'}, '*');

  document.addEventListener('click', function(e){
    if (!annotating) return;
    if (!e.isTrusted) return;
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    // Consume it: while annotating, a click marks an element and does NOT also
    // fire the widget's own [data-action] (this runs in the capture phase, so the
    // action forwarder's document listener is never reached).
    e.preventDefault();
    e.stopPropagation();
    parent.postMessage({
      type: 'widget-annotation',
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      outerHTML: String(el.outerHTML || '').slice(0, 400),
      parentContext: contextFor(el)
    }, '*');
  }, true);
})();`

const EDIT_MODE_SCRIPT = `<script>\n${EDIT_MODE_SCRIPT_SOURCE}\n<\/script>`

export interface BuildSrcdocOpts {
  html: string
  /** The Tailwind CSS this document's classes need (`widgetStyles`) — `''` for none. */
  css: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
  /** Include the height-reporter + action-forwarder (the inline host needs it; a
   *  full-page viewer that sizes its own iframe does not). Default true. */
  includeHost?: boolean
  /** Transparent iframe body — for the frameless inline-chat host where the
   *  widget renders directly against the app canvas. Default false (solid theme
   *  bg for standalone contexts: download, open-in-new-tab). */
  transparentBody?: boolean
  /** Include the artifact-iteration script (EDITMODE live values + click
   *  annotation). Opt-in per host: a download/open-in-tab document and any host
   *  that offers no iteration UI gets the byte-identical document it got before. */
  editMode?: boolean
}

/** Build the sandboxed iframe document for an agent-generated widget.
 *
 *  SECURITY: rendered in an iframe with sandbox="allow-scripts" off a blob/null
 *  origin, so widget content can't reach parent DOM, cookies, or storage (the
 *  Claude-artifacts model). Theme values pass sanitizeCssValue (char allowlist +
 *  dangerous-fn denylist + length cap); a strict CSP (connect-src 'none', img-src
 *  data: blob:, no third-party source) contains the content. DOMPurify intentionally
 *  NOT applied — widgets need <script> for their own drawing and interaction; output
 *  is redacted upstream.
 *
 *  The document's defaults sit in Tailwind's `base` layer, so a utility on an element
 *  (`mb-4` on an `h1`) wins over the default for that element, as it did when the
 *  CDN's unlayered utilities came last. */
export function buildSrcdoc({ html, css, themeVars, mode, includeHost = true, transparentBody = false, editMode = false }: BuildSrcdocOpts): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">
<style>${styleSafe(css)}</style>
<style>
@layer base {
  *, *::before, *::after { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  /* Match the parent app's hidden-scrollbar tenet — the iframe is its own
     document, so the app's global rule can't reach in here. Content still
     scrolls if it ever overflows; only the bar chrome is hidden. */
  * { scrollbar-width: none; -ms-overflow-style: none; }
  *::-webkit-scrollbar { display: none; }
  body {
    margin: 0; padding: 16px;
    /* iframe CSP blocks external fonts; use the platform UI stack (matches the
       app's system fallback closely) + antialiasing for a clean baseline. */
    font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px; line-height: 1.5;
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
  }
  /* sensible defaults so minimal widgets still read well */
  h1,h2,h3,h4 { line-height: 1.25; margin: 0 0 0.4em; }
  p { margin: 0 0 0.75em; }
  img, svg, canvas, video { max-width: 100%; height: auto; }
  table { border-collapse: collapse; }
  a { color: var(--accent); }
  ${themeStyleBlock(themeVars, mode, transparentBody)}
}
</style>
</head>
<body class="${mode}">
${html}
${[editMode ? EDIT_MODE_SCRIPT : '', includeHost ? HOST_SCRIPT : ''].filter(Boolean).join('\n')}
</body>
</html>`
}

// Registered BEFORE the component's own script, so an error that script throws while it loads
// (a syntax error the compiler let through, an import this preview does not offer) is reported
// the same way a render error is: a `widget-error` to the parent and the message in the frame.
const REACT_TRAP = `(function(){
  window.__previewError = function(err){
    if (window.__previewFailed) return;
    window.__previewFailed = true;
    var message = String(err && err.message || err);
    parent.postMessage({type:'widget-error', message: message}, '*');
    var r = document.getElementById('root');
    if (r) r.innerHTML = '<pre style="color:var(--danger);white-space:pre-wrap;font-family:monospace;font-size:13px">' + message.replace(/[&<>]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}) + '</pre>';
  };
  window.addEventListener('error', function(e){ window.__previewError(e.error || e.message); });
})();`

// Renders the agent's component — a top-level `App`, or the module's default export — inside an
// error boundary. Plain JavaScript: it runs after the component's compiled script and sees its
// globals. React is read off `window` because the component's script can declare its own
// top-level `React` or `ReactDOM` (`import * as ReactDOM from 'react-dom'` compiles to one), and a
// bare name here would find that binding instead of the frame's.
const REACT_HARNESS = `(function(){
  if (window.__previewFailed) return;
  var React = window.React, ReactDOM = window.ReactDOM;
  function report(){
    var h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    parent.postMessage({type:'widget-height', height:h}, '*');
  }
  try {
    var exported = window.module && window.module.exports;
    var Comp = (typeof App !== 'undefined' && App) ||
               (typeof window.App !== 'undefined' && window.App) ||
               (exported && (exported.default || exported.App)) ||
               (typeof exported === 'function' ? exported : null);
    if (!Comp) { throw new Error('No component found. Define a top-level function named App.'); }
    class ErrorBoundary extends React.Component {
      constructor(p){ super(p); this.state = {err:null}; }
      static getDerivedStateFromError(err){ return {err: err}; }
      componentDidCatch(err){ parent.postMessage({type:'widget-error', message:String(err && err.message || err)}, '*'); }
      render(){
        if (this.state.err) {
          return React.createElement('pre', {style:{color:'var(--danger)',whiteSpace:'pre-wrap',fontFamily:'monospace',fontSize:'13px'}}, String(this.state.err.message || this.state.err));
        }
        return this.props.children;
      }
    }
    var root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(React.createElement(ErrorBoundary, null, React.createElement(Comp)));
    new ResizeObserver(report).observe(document.body);
    setTimeout(report, 100);
  } catch (e) {
    window.__previewError(e);
  }
})();`

/** Script text that cannot end the `<script>` element it is placed in: `<\/script` is the same
 *  text to JavaScript. */
const scriptSafe = (js: string) => js.replace(/<\/script/gi, '<\\/script')

export interface BuildReactSrcdocOpts {
  /** The component, compiled to plain JavaScript (`reactPreview`): JSX authored against the
   *  React / ReactDOM globals, defining a top-level `App` or exporting one. */
  code: string
  /** React for the frame (`reactFrameRuntime`), inlined: the frame fetches nothing. */
  runtime: string
  /** The Tailwind CSS the component's classes need (`widgetStyles`). */
  css: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
}

/** Build the sandboxed iframe document for a dynamic React (kind:'react')
 *  artifact. Same security model as :func:`buildSrcdoc` (sandbox="allow-scripts"
 *  off a blob/null origin + the same strict CSP + sanitized theme vars). Everything it
 *  runs is inline — React, the compiled component, the harness — so it fetches nothing,
 *  and the component executes only inside the sandboxed frame, never in the parent. */
export function buildReactSrcdoc({ code, runtime, css, themeVars, mode }: BuildReactSrcdocOpts): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">
<style>${styleSafe(css)}</style>
<style>
@layer base {
  *, *::before, *::after { box-sizing: border-box; }
  * { scrollbar-width: none; -ms-overflow-style: none; }
  *::-webkit-scrollbar { display: none; }
  body {
    margin: 0; padding: 16px;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px; line-height: 1.5;
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  }
  img, svg, canvas, video { max-width: 100%; height: auto; }
  a { color: var(--accent); }
  ${themeStyleBlock(themeVars, mode)}
}
</style>
</head>
<body class="${mode}">
<div id="root"></div>
<script>
${scriptSafe(runtime)}
<\/script>
<script>
${REACT_TRAP}
<\/script>
<script>
${scriptSafe(code)}
<\/script>
<script>
${REACT_HARNESS}
<\/script>
</body>
</html>`
}
