"use strict";

/**
 * How a still orb reads against the menu bar or the dock it sits on (#3896): its PNG decoded, and
 * the contrast of each of its glyph's pixels against a plain background, by WCAG's formula.
 *
 * A glyph pixel is one at least half opaque: the faint edge of a dot is not what a reader tells
 * the state by. Each is composited onto the background as the system draws it (a template's own
 * ink is whatever the system paints it in), and its contrast with the background is counted. WCAG
 * asks 3:1 of the parts of a graphic needed to understand it (non-text contrast, 1.4.11).
 */

const fs = require("node:fs");
const zlib = require("node:zlib");

/** WCAG's non-text contrast. */
const NON_TEXT_CONTRAST = 3;
/** The share of a glyph's pixels that must hold it. */
const GLYPH_SHARE = 0.9;
/** A pixel at least this opaque is the glyph's. */
const GLYPH_ALPHA = 0.5;

/** An 8-bit RGBA, non-interlaced PNG as `{width, height, rgba}`; any other kind is refused. */
function decodePng(file) {
  const buf = fs.readFileSync(file);
  if (buf.toString("latin1", 1, 4) !== "PNG") throw new Error(`${file} is not a PNG`);
  let at = 8;
  let width = 0;
  let height = 0;
  const idat = [];
  while (at < buf.length) {
    const len = buf.readUInt32BE(at);
    const type = buf.toString("latin1", at + 4, at + 8);
    const data = buf.subarray(at + 8, at + 8 + len);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      if (data[8] !== 8 || data[9] !== 6 || data[12] !== 0) throw new Error(`${file} is not 8-bit RGBA without interlace`);
    } else if (type === "IDAT") idat.push(data);
    at += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * 4;
  const rgba = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const row = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const left = x >= 4 ? rgba[y * stride + x - 4] : 0;
      const up = y ? rgba[(y - 1) * stride + x] : 0;
      const corner = x >= 4 && y ? rgba[(y - 1) * stride + x - 4] : 0;
      let v = row[x];
      if (filter === 1) v += left;
      else if (filter === 2) v += up;
      else if (filter === 3) v += (left + up) >> 1;
      else if (filter === 4) {
        const p = left + up - corner;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - corner);
        v += pa <= pb && pa <= pc ? left : pb <= pc ? up : corner;
      }
      rgba[y * stride + x] = v & 255;
    }
  }
  return { width, height, rgba };
}

const channel = (c) => {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
/** WCAG's relative luminance of an sRGB colour `[r, g, b]`, 0–255. */
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
/** WCAG's contrast ratio of two colours. */
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

/**
 * The glyph of *image* against *background*: how many pixels it has, the lowest contrast among
 * them, and the share at `NON_TEXT_CONTRAST` or more. *ink*, when given, paints every pixel in one
 * colour at its own opacity, as the system draws a template image.
 */
function glyphContrast(image, background, ink = null) {
  const ratios = [];
  for (let i = 0; i < image.width * image.height; i++) {
    const alpha = image.rgba[i * 4 + 3] / 255;
    if (alpha < GLYPH_ALPHA) continue;
    const colour = ink || [image.rgba[i * 4], image.rgba[i * 4 + 1], image.rgba[i * 4 + 2]];
    ratios.push(contrast(colour.map((c, k) => c * alpha + background[k] * (1 - alpha)), background));
  }
  const holding = ratios.filter((r) => r >= NON_TEXT_CONTRAST).length;
  return { pixels: ratios.length, lowest: ratios.length ? Math.min(...ratios) : 0, share: ratios.length ? holding / ratios.length : 0 };
}

/** Whether a glyph reads against its background: it has pixels, and enough of them hold 3:1. */
const readsAgainst = (measured) => measured.pixels > 0 && measured.share >= GLYPH_SHARE;

module.exports = { GLYPH_ALPHA, GLYPH_SHARE, NON_TEXT_CONTRAST, contrast, decodePng, glyphContrast, readsAgainst };
