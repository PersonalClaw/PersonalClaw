const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { TRAY_STILLS } = require("../trayPresence");
const { GLYPH_SHARE, NON_TEXT_CONTRAST, decodePng, glyphContrast, readsAgainst } = require("./helpers/stillContrast");

// ── The still orbs read in high contrast (#3896) ──────────────────────────────────────────────
//
// Each still the tray and the dock load, measured from the PNG itself against a pure white and a
// pure black bar: at least `GLYPH_SHARE` of its glyph's pixels hold WCAG's 3:1 non-text contrast.
// A colour still is measured on its own appearance's bar (a light still on white, a dark one on
// black). A template is measured as macOS draws it, its black recoloured to the bar's ink, on both;
// macOS draws template images for Increase Contrast itself. The families are told apart by their
// badge and their dots' density, not by colour (`tests/rails/test_buddy_design_matrix.py`).

const BUDDY = path.join(__dirname, "..", "buddy");
const WHITE = [255, 255, 255];
const BLACK = [0, 0, 0];
const BAR = { light: WHITE, dark: BLACK };
const still = (name) => decodePng(path.join(BUDDY, name));

function holds(name, background, ink = null) {
  const measured = glyphContrast(still(name), background, ink);
  assert.ok(
    readsAgainst(measured),
    `${name} on ${background === WHITE ? "white" : "black"}: ${Math.round(measured.share * 100)}% of ${measured.pixels} glyph pixels hold ${NON_TEXT_CONTRAST}:1 (lowest ${measured.lowest.toFixed(2)}:1), under ${GLYPH_SHARE * 100}%`,
  );
}

describe("the still orbs hold 3:1 against the bar they sit on", () => {
  it("each Linux tray still, at 18 and 36 px, on its own appearance's bar", () => {
    for (const s of TRAY_STILLS) {
      for (const mode of ["light", "dark"]) {
        holds(`tray-${s}-${mode}.png`, BAR[mode]);
        holds(`tray-${s}-${mode}@2x.png`, BAR[mode]);
      }
    }
  });

  it("each macOS template, as the system draws it, on a white bar and on a black one", () => {
    for (const s of TRAY_STILLS) {
      for (const size of ["", "@2x"]) {
        holds(`tray-${s}Template${size}.png`, WHITE, BLACK);
        holds(`tray-${s}Template${size}.png`, BLACK, WHITE);
      }
    }
  });

  it("the dock's still, on its own appearance's dock", () => {
    holds("dock-needs-you-light.png", WHITE);
    holds("dock-needs-you-dark.png", BLACK);
  });
});

describe("the measure can fail", () => {
  /** A square still of one ink at one opacity: the planted low-contrast glyph. */
  const flat = (rgb, alpha, size = 18) => {
    const rgba = Buffer.alloc(size * size * 4);
    for (let i = 0; i < size * size; i++) rgba.set([...rgb, Math.round(alpha * 255)], i * 4);
    return { width: size, height: size, rgba };
  };

  it("a light grey still on a white bar does not read", () => {
    const measured = glyphContrast(flat([0xc4, 0xc7, 0xc5], 1), WHITE);
    assert.ok(measured.lowest < NON_TEXT_CONTRAST, `measured ${measured.lowest}`);
    assert.equal(readsAgainst(measured), false);
  });

  it("a dark ink at half opacity on a white bar does not read either", () => {
    assert.equal(readsAgainst(glyphContrast(flat([0x5f, 0x63, 0x68], 0.55), WHITE)), false);
  });

  it("a light appearance's still on a black bar does not read: the bar it was drawn for matters", () => {
    assert.equal(readsAgainst(glyphContrast(still("tray-idle-light.png"), BLACK)), false);
  });

  it("a still with no glyph does not read", () => {
    assert.equal(readsAgainst(glyphContrast(flat([0, 0, 0], 0.2), WHITE)), false, "nothing at least half opaque is no glyph");
  });

  it("black on white is WCAG's 21:1", () => {
    assert.equal(glyphContrast(flat(BLACK, 1), WHITE).lowest.toFixed(2), "21.00");
  });
});
