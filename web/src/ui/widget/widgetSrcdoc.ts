import { sanitizeCssValue } from './cssSanitize'

const TAILWIND_CDN = '<script src="https://cdn.tailwindcss.com"><\/script>'
// Drive Tailwind's `dark:` variant off `class="dark"` on <body> rather than the
// prefers-color-scheme media query — the iframe can't know the parent's theme.
const TAILWIND_CONFIG = '<script>tailwind.config={darkMode:\'class\'}<\/script>'

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

// Reports content height + forwards [data-action] clicks to the parent. The
// message types are `widget-height` / `widget-action`.
const HOST_SCRIPT = `<script>
(function(){
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
  document.addEventListener('click', function(e){
    if (!e.isTrusted) return;
    var el = e.target.closest('[data-action]');
    if (!el) return;
    e.preventDefault();
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
  });
})();
<\/script>`

export interface BuildSrcdocOpts {
  html: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
  /** Include the height-reporter + action-forwarder (the inline host needs it; a
   *  full-page viewer that sizes its own iframe does not). Default true. */
  includeHost?: boolean
  /** Transparent iframe body — for the frameless inline-chat host where the
   *  widget renders directly against the app canvas. Default false (solid theme
   *  bg for standalone contexts: download, open-in-new-tab). */
  transparentBody?: boolean
}

/** Build the sandboxed iframe document for an agent-generated widget.
 *
 *  SECURITY: rendered in an iframe with sandbox="allow-scripts" off a blob/null
 *  origin, so widget content can't reach parent DOM, cookies, or storage (the
 *  Claude-artifacts model). Theme values pass sanitizeCssValue (char allowlist +
 *  dangerous-fn denylist + length cap); a strict CSP (connect-src 'none', img-src
 *  data: blob:) contains the content. DOMPurify intentionally NOT applied —
 *  widgets need <script> for Chart.js/D3; output is redacted upstream. */
export function buildSrcdoc({ html, themeVars, mode, includeHost = true, transparentBody = false }: BuildSrcdocOpts): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'unsafe-inline' https://cdn.tailwindcss.com; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none';">
${TAILWIND_CDN}
${TAILWIND_CONFIG}
<style>
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
</style>
</head>
<body class="${mode}">
${html}
${includeHost ? HOST_SCRIPT : ''}
</body>
</html>`
}

// React-artifact CDNs (pinned majors). React/ReactDOM UMD globals + Babel
// standalone for in-iframe JSX transform — all from the CDNs already allowed by
// the shared CSP (jsdelivr/cdnjs). Babel (~3MB) loads only inside a react
// iframe, which is created only when a kind:'react' widget actually renders.
const REACT_CDN = [
  '<script crossorigin src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"><\/script>',
  '<script crossorigin src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js"><\/script>',
  '<script src="https://cdn.jsdelivr.net/npm/@babel/standalone@7/babel.min.js"><\/script>',
].join('\n')

// Renders the agent's component (a top-level `App`, or a default-exported one)
// inside an error boundary. A render/transform failure posts `widget-error` to
// the parent (surfaced as an inline error) instead of a blank frame. Babel
// transforms `type="text/babel"` scripts in document order, so this harness —
// itself text/babel — runs AFTER the user code script and sees its globals.
const REACT_HARNESS = `<script type="text/babel" data-presets="react">
(function(){
  function report(){
    var h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    parent.postMessage({type:'widget-height', height:h}, '*');
  }
  try {
    var Comp = (typeof App !== 'undefined' && App) ||
               (typeof window.App !== 'undefined' && window.App) || null;
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
    parent.postMessage({type:'widget-error', message:String(e && e.message || e)}, '*');
    var r = document.getElementById('root');
    if (r) r.innerHTML = '<pre style="color:var(--danger);white-space:pre-wrap;font-family:monospace;font-size:13px">' + String(e && e.message || e).replace(/[&<>]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}) + '</pre>';
  }
})();
<\/script>`

export interface BuildReactSrcdocOpts {
  /** JSX source authored against window globals React / ReactDOM, defining a
   *  top-level `App` component. */
  jsx: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
}

/** Build the sandboxed iframe document for a dynamic React (kind:'react')
 *  artifact. Same security model as :func:`buildSrcdoc` (sandbox="allow-scripts"
 *  off a blob/null origin + strict CSP + sanitized theme vars); additionally
 *  loads React/ReactDOM UMD + Babel from the CSP-allowed CDNs and renders the
 *  agent's `App` inside an error boundary. The JSX is embedded as a
 *  `type="text/babel"` script — NOT eval'd in the parent — so it executes only
 *  inside the sandboxed frame. */
export function buildReactSrcdoc({ jsx, themeVars, mode }: BuildReactSrcdocOpts): string {
  // Defang any closing script tag in the agent JSX so it can't break out of the
  // text/babel <script> container (it still runs sandboxed regardless).
  const safeJsx = jsx.replace(/<\/script>/gi, '<\\/script>')
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'unsafe-inline' https://cdn.tailwindcss.com; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none';">
${TAILWIND_CDN}
${TAILWIND_CONFIG}
${REACT_CDN}
<style>
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
</style>
</head>
<body class="${mode}">
<div id="root"></div>
<script type="text/babel" data-presets="react">
${safeJsx}
<\/script>
${REACT_HARNESS}
</body>
</html>`
}
