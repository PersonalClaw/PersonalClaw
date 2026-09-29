// ── React inside a react artifact's frame, from the copy this app runs on ──────────────────────
//
// A react artifact is authored against the `React` / `ReactDOM` globals, and its frame used to
// fetch them from a CDN as UMD bundles. React no longer ships UMD bundles, and a local-first app
// fetches nothing from a third party to draw what it was given. So the frame gets the production
// builds of the react, react-dom and scheduler packages installed here, linked by a loader small
// enough to read: each file is the CommonJS module it was published as, `require`d by name.
//
// Imported by relative path because the packages' `exports` maps do not list their `cjs/` files;
// the path is still inside this repository's one `node_modules`, so the build's licence census
// attributes every byte to the package it came from.
import react from '../../../../node_modules/react/cjs/react.production.js?raw'
import reactDom from '../../../../node_modules/react-dom/cjs/react-dom.production.js?raw'
import reactDomClient from '../../../../node_modules/react-dom/cjs/react-dom-client.production.js?raw'
import scheduler from '../../../../node_modules/scheduler/cjs/scheduler.production.js?raw'

/** A module's source as the body of a CommonJS wrapper, made safe to sit inside a `<script>`:
 *  `<\/script` is the same text to JavaScript and cannot end the element. */
const wrap = (source: string) =>
  `function (module, exports, require) {\n${source.replace(/<\/script/gi, '<\\/script')}\n}`

/** The frame's runtime, as script text: defines `React`, `ReactDOM` (react-dom and
 *  react-dom/client together, as the one global always carried both `createPortal` and
 *  `createRoot`) and `require`, for a component that imports them by name. */
export const REACT_FRAME_RUNTIME = `(function () {
  var sources = {
    'react': ${wrap(react)},
    'scheduler': ${wrap(scheduler)},
    'react-dom': ${wrap(reactDom)},
    'react-dom/client': ${wrap(reactDomClient)}
  };
  var loaded = {};
  function require(name) {
    if (!Object.prototype.hasOwnProperty.call(sources, name)) {
      throw new Error('This preview can import react and react-dom, not "' + name + '".');
    }
    if (!loaded[name]) {
      var module = { exports: {} };
      loaded[name] = module;
      sources[name](module, module.exports, require);
    }
    return loaded[name].exports;
  }
  window.React = require('react');
  window.ReactDOM = Object.assign({}, require('react-dom'), require('react-dom/client'));
  window.require = require;
  // The component is compiled as a CommonJS module, so an \`export default\` lands here.
  window.module = { exports: {} };
  window.exports = window.module.exports;
})();`
