const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// The shell's own pages (the loading page, the gateway switcher, the rename-tab sheet) and the
// window's first paint use the dashboard's colours: the values of the shared colour tokens in
// `web/src/design/tokens.css`. The shell cannot read the web app's CSS, so it carries the values;
// these tests fail when a shell colour is not one of them, so the two cannot drift apart.

const ROOT = path.resolve(__dirname, "..");
const TOKENS = fs.readFileSync(path.join(ROOT, "..", "web", "src", "design", "tokens.css"), "utf8");

/** The `--name: value;` declarations inside the first block that opens with `opener`. */
function declarations(opener) {
  const at = TOKENS.indexOf(opener);
  assert.ok(at >= 0, `tokens.css no longer has a ${opener} block`);
  const end = TOKENS.indexOf("\n}", at);
  const out = new Map();
  for (const m of TOKENS.slice(at, end).matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
}

/** The palette steps (`:root`, layer 1) the colour tokens are written in. */
const PALETTE = declarations(":root {");

/**
 * Each `--color-*` token of the block that opens with `opener`, resolved to its hex value: a token
 * is a hex, a palette step (`var(--gray-168)`) or another colour token of the same mode
 * (`var(--color-ok)`), which falls back to the dark block for a mode that does not reassign it.
 */
function colourBlock(opener, fallback = new Map()) {
  const own = declarations(opener);
  const resolve = (value, depth = 0) => {
    if (depth > 8) return null;
    if (/^#[0-9a-fA-F]{3,8}$/.test(value)) return value.toLowerCase();
    const ref = /^var\(--([a-z0-9-]+)\)$/.exec(value);
    if (!ref) return null;
    const name = ref[1];
    if (PALETTE.has(name)) return resolve(PALETTE.get(name), depth + 1);
    const colour = name.replace(/^color-/, "");
    if (name.startsWith("color-") && own.has(name)) return resolve(own.get(name), depth + 1);
    if (name.startsWith("color-") && fallback.has(colour)) return fallback.get(colour);
    return null;
  };
  const values = new Map(fallback);
  for (const [name, value] of own) {
    if (!name.startsWith("color-")) continue;
    const hex = resolve(value);
    if (hex) values.set(name.slice("color-".length), hex);
  }
  return values;
}

const DARK = colourBlock("@theme {");
const LIGHT = colourBlock(".light {", DARK);
const hexes = (text) => [...text.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toLowerCase());
const notTokens = (text, palette) => {
  const allowed = new Set(palette.values());
  return [...new Set(hexes(text))].filter((h) => !allowed.has(h));
};

/** A file's text with its inline brand mark taken out: the mark's gradient is the brand's own. */
const withoutMark = (text) => text.replace(/<svg\b[\s\S]*?<\/svg>/g, "");

/** The properties that paint a colour. */
const COLOUR_PROPERTY = /^(color|background(-color)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|fill|stroke|caret-color|accent-color|text-decoration(-color)?|column-rule(-color)?|(box|text)-shadow)$/;
/** The words a colour declaration may hold beside a token's hex: no colour of their own. */
const NOT_A_COLOUR = new Set([
  "transparent", "currentcolor", "inherit", "initial", "unset", "none",
  "solid", "dashed", "dotted", "double", "hidden", "thin", "medium", "thick", "inset",
]);
const LENGTH = /^-?(\d+|\d*\.\d+)(px|em|rem|%|vh|vw)?$/;

/**
 * Every value in a colour-painting declaration of `css` (rule bodies and `style` attributes) that
 * is neither a hex nor a word with no colour: an `rgb()`, an `hsl()`, a colour by name. Hexes are
 * checked against the tokens by `notTokens`.
 */
function nonHexColours(css) {
  const bodies = [...css.matchAll(/\{([^{}]*)\}/g), ...css.matchAll(/\bstyle="([^"]*)"/g)].map((m) => m[1]);
  const out = [];
  for (const body of bodies) {
    for (const decl of body.split(";")) {
      const colon = decl.indexOf(":");
      if (colon < 0) continue;
      const property = decl.slice(0, colon).trim().toLowerCase();
      if (!COLOUR_PROPERTY.test(property)) continue;
      const value = decl.slice(colon + 1).replace(/!important/i, "").trim();
      for (const [part] of value.matchAll(/[a-z-]+\([^)]*\)|[^\s,]+/gi)) {
        if (/^#[0-9a-f]{3,8}$/i.test(part) || LENGTH.test(part) || NOT_A_COLOUR.has(part.toLowerCase())) continue;
        out.push(`${property}: ${part}`);
      }
    }
  }
  return out;
}

/** The CSS a shell page carries: its `<style>` blocks and its `style` attributes. */
const pageCSS = (text) => [
  ...[...text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]),
  ...[...text.matchAll(/\bstyle="[^"]*"/g)].map((m) => `{${m[0].slice(7, -1)}}`),
].join("\n");

describe("the shell pages use the shared tokens' values", () => {
  it("reads both palettes from tokens.css (vacuity floor)", () => {
    for (const palette of [DARK, LIGHT]) {
      for (const name of ["canvas", "surface", "surface-container", "on-surface", "on-surface-low", "primary", "on-primary"]) {
        assert.ok(palette.has(name), `--color-${name} not found`);
      }
    }
  });

  it("finds a colour written other than as a hex, and lets a colourless word through", () => {
    const sample = `body { background: rgb(15, 17, 23); color: #e3e3e3; }
      .a { border: 1px solid orange; outline: none; } .b { fill: hsl(0 0% 0%); border-color: transparent; }
      .c { border-top: 1px solid currentColor; background-color: inherit; box-shadow: 0 0 2px RED; }`;
    assert.deepStrictEqual(nonHexColours(sample), [
      "background: rgb(15, 17, 23)", "border: orange", "fill: hsl(0 0% 0%)", "box-shadow: RED",
    ]);
    assert.deepStrictEqual(nonHexColours(pageCSS('<p style="color: teal">x</p>')), ["color: teal"]);
  });

  for (const file of ["loading.html", "connectDialog.html"]) {
    it(`${file} uses only dark token values`, () => {
      const text = withoutMark(fs.readFileSync(path.join(ROOT, file), "utf8"));
      assert.ok(hexes(text).length >= 3, `${file}: found almost no colours`);
      assert.deepStrictEqual(notTokens(text, DARK), [], `${file} carries colours that are no token's value`);
    });

    it(`${file} writes every colour as a token's hex, never as rgb(), hsl() or a name`, () => {
      const css = pageCSS(withoutMark(fs.readFileSync(path.join(ROOT, file), "utf8")));
      assert.ok(css.length > 200, `${file}: found almost no styles`);
      assert.deepStrictEqual(nonHexColours(css), [], `${file} carries a colour that is not a token's hex`);
    });
  }

  it("the window and the gateway switcher open on the canvas colour", () => {
    // The window's colour is windowChrome.js's opaque fallback, which makeWindow spreads in
    // (windowChrome.test.js holds that it sets no colour of its own).
    const chrome = fs.readFileSync(path.join(ROOT, "windowChrome.js"), "utf8");
    assert.match(chrome, new RegExp(`const OPAQUE_BACKGROUND = "${DARK.get("canvas")}";`, "i"));
    const dialog = fs.readFileSync(path.join(ROOT, "connectDialog.js"), "utf8");
    assert.match(dialog, new RegExp(`backgroundColor: "${DARK.get("canvas")}"`, "i"));
  });

  it("the rename-tab sheet's fallback styles use each mode's own token values", () => {
    const main = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
    const src = /function modalCSSForMode\(dark\) \{[\s\S]*?\n\}/.exec(main);
    assert.ok(src, "modalCSSForMode must be findable");
    const modalCSSForMode = vm.runInNewContext(`(${src[0]})`);
    assert.deepStrictEqual(notTokens(modalCSSForMode(true), DARK), [], "dark sheet");
    assert.deepStrictEqual(notTokens(modalCSSForMode(false), LIGHT), [], "light sheet");
    assert.deepStrictEqual(nonHexColours(modalCSSForMode(true)), [], "dark sheet");
    assert.deepStrictEqual(nonHexColours(modalCSSForMode(false)), [], "light sheet");
  });
});
