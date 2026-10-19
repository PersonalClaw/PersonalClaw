const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

// The desktop app's rasters (its icon, its menu-bar images) and the brand PNG are rendered
// from the web app's mark by `docs/brand/render_marks.mjs`, never painted by hand. The
// script writes `docs/brand/marks.json`: the hash of the `claw.svg` and of the script it
// rendered with, and each output's path, size and hash. These tests hold the tree to that
// record, so a change to the mark without a re-render, or a raster edited by hand, fails.

const REPO = path.resolve(__dirname, "..", "..");
const MANIFEST = path.join(REPO, "docs", "brand", "marks.json");
const CLAW_SVG = path.join(REPO, "web", "public", "claw.svg");
const SCRIPT = path.join(REPO, "docs", "brand", "render_marks.mjs");
const LOADING = path.join(REPO, "desktop", "loading.html");

const sha256 = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");

/** Width and height from a PNG's IHDR chunk, which always follows the 8-byte signature. */
function pngSize(file) {
  const buf = fs.readFileSync(file);
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.ok(buf.subarray(0, 8).equals(signature), `${file} is not a PNG`);
  assert.equal(buf.toString("latin1", 12, 16), "IHDR", `${file} has no IHDR chunk first`);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

/** The mark's path data and its gradient stops (offset, colour), in document order. */
function markParts(svgText) {
  const paths = [...svgText.matchAll(/<path\b[^>]*\sd="([^"]+)"/g)].map((m) => m[1]);
  const stops = [...svgText.matchAll(/<stop\b([^>]*)\/?>/g)].map((m) => {
    const offset = /offset="([^"]+)"/.exec(m[1]);
    const color = /stop-color="([^"]+)"/.exec(m[1]);
    return { offset: offset ? Number(offset[1]) : 0, color: color ? color[1].toLowerCase() : null };
  });
  return { paths, stops };
}

describe("the brand rasters match what render_marks.mjs rendered", () => {
  it("the manifest exists and was rendered from today's claw.svg by today's script", () => {
    assert.ok(fs.existsSync(MANIFEST), "docs/brand/marks.json is missing: run node docs/brand/render_marks.mjs");
    const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
    assert.equal(manifest.source, "web/public/claw.svg");
    assert.equal(
      manifest.source_sha256,
      sha256(CLAW_SVG),
      "claw.svg changed since the rasters were rendered: run node docs/brand/render_marks.mjs",
    );
    assert.equal(
      manifest.script_sha256,
      sha256(SCRIPT),
      "render_marks.mjs changed since the rasters were rendered: run it again",
    );
  });

  it("every output exists with the recorded size and hash", () => {
    const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
    // Vacuity floor: an empty list would match nothing and pass.
    const expected = [
      "desktop/icon.png",
      "desktop/tray.png",
      "desktop/tray@2x.png",
      "desktop/trayTemplate.png",
      "desktop/trayTemplate@2x.png",
      "docs/brand/personalclaw-mark.png",
    ];
    assert.deepStrictEqual(manifest.outputs.map((o) => o.path).sort(), [...expected].sort());
    for (const out of manifest.outputs) {
      const file = path.join(REPO, out.path);
      assert.ok(fs.existsSync(file), `${out.path} is missing`);
      assert.deepStrictEqual(pngSize(file), { width: out.size, height: out.size }, `${out.path} size`);
      assert.equal(sha256(file), out.sha256, `${out.path} differs from what the script rendered`);
    }
  });

  it("the sizes are the ones the desktop app is built from", () => {
    const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
    const size = Object.fromEntries(manifest.outputs.map((o) => [o.path, o.size]));
    assert.equal(size["desktop/icon.png"], 1024);
    assert.equal(size["desktop/tray.png"], 18);
    assert.equal(size["desktop/tray@2x.png"], 36);
    assert.equal(size["desktop/trayTemplate.png"], 18);
    assert.equal(size["desktop/trayTemplate@2x.png"], 36);
    assert.equal(size["docs/brand/personalclaw-mark.png"], 512);
  });
});

describe("the loading window's mark is claw.svg's", () => {
  const claw = markParts(fs.readFileSync(CLAW_SVG, "utf8"));
  const loadingText = fs.readFileSync(LOADING, "utf8");
  const loading = markParts(loadingText);

  it("claw.svg has the one path and four stops this test compares (vacuity floor)", () => {
    assert.equal(claw.paths.length, 1);
    assert.equal(claw.stops.length, 4);
  });

  it("carries claw.svg's path", () => {
    assert.deepStrictEqual(loading.paths, claw.paths);
  });

  it("carries claw.svg's four gradient stops, and fills the path with them", () => {
    assert.deepStrictEqual(loading.stops, claw.stops);
    assert.match(loadingText, /<linearGradient\b/);
    assert.match(loadingText, /<path\b[^>]*fill="url\(#[^)]+\)"/);
  });

  it("the mark no longer uses the flat orange", () => {
    // Scoped to the mark's own <svg>: the page's background, text and spinner colours belong
    // to the shell pages' palette work, which this change leaves alone.
    const svg = /<svg\b[\s\S]*?<\/svg>/.exec(loadingText);
    assert.ok(svg, "the loading page's inline mark must be findable");
    assert.doesNotMatch(svg[0], /#f97316/i);
  });
});
