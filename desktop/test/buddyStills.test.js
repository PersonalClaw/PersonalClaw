const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { TRAY_STILLS } = require("../trayPresence");

// The buddy's still orbs the tray and the dock load (#3896) are rendered from the drawn stills in
// `docs/design/buddy/poses/` by `docs/design/buddy/render_stills.mjs`, never painted by hand. The
// script writes `docs/design/buddy/stills.json`: the hash of each drawing, of the contact sheet whose
// token values it paints with, and of the script, and each output's path, size and hash. These tests
// hold the tree to that record, so a drawing changed without a re-render, or a raster edited by
// hand, fails.

const REPO = path.resolve(__dirname, "..", "..");
const MANIFEST = path.join(REPO, "docs", "design", "buddy", "stills.json");
const SCRIPT = path.join(REPO, "docs", "design", "buddy", "render_stills.mjs");

const sha256 = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");

/** Width and height from a PNG's IHDR chunk, which always follows the 8-byte signature. */
function pngSize(file) {
  const buf = fs.readFileSync(file);
  assert.ok(buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])), `${file} is not a PNG`);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));

describe("the buddy's still orbs", () => {
  it("were rendered by today's script from today's drawings", () => {
    assert.equal(manifest.script_sha256, sha256(SCRIPT), "render_stills.mjs changed: run node docs/design/buddy/render_stills.mjs");
    assert.equal(manifest.sources.length, 8, "the six template drawings, the dock's colour drawing and the sheet");
    for (const src of manifest.sources) {
      assert.equal(sha256(path.join(REPO, src.path)), src.sha256, `${src.path} changed: run node docs/design/buddy/render_stills.mjs`);
    }
  });

  it("every output exists with the recorded size and hash", () => {
    for (const out of manifest.outputs) {
      const file = path.join(REPO, out.path);
      assert.ok(fs.existsSync(file), `${out.path} is missing`);
      assert.deepEqual(pngSize(file), { width: out.size, height: out.size }, `${out.path} size`);
      assert.equal(sha256(file), out.sha256, `${out.path} differs from what the script rendered`);
    }
  });

  it("are every still the tray shows, in each mode and as a template, at 18 with an @2x, and the dock's", () => {
    const expected = [];
    for (const still of TRAY_STILLS) {
      for (const mode of ["light", "dark"]) expected.push(`desktop/buddy/tray-${still}-${mode}.png`, `desktop/buddy/tray-${still}-${mode}@2x.png`);
      expected.push(`desktop/buddy/tray-${still}Template.png`, `desktop/buddy/tray-${still}Template@2x.png`);
    }
    expected.push("desktop/buddy/dock-needs-you-light.png", "desktop/buddy/dock-needs-you-dark.png");
    assert.deepEqual(manifest.outputs.map((o) => o.path).sort(), expected.sort());
    const onDisk = fs.readdirSync(path.join(REPO, "desktop", "buddy")).map((f) => `desktop/buddy/${f}`);
    assert.deepEqual(onDisk.sort(), expected.sort(), "no raster in desktop/buddy/ the script did not render");
    for (const out of manifest.outputs) {
      const want = out.path.startsWith("desktop/buddy/dock-") ? 512 : out.path.includes("@2x") ? 36 : 18;
      assert.equal(out.size, want, out.path);
    }
  });

  it("ship with the app: electron-builder copies the folder they are in", () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(REPO, "desktop", "package.json"), "utf8"));
    assert.ok(pkg.build.files.includes("buddy"), "desktop/package.json build.files lists the buddy/ folder");
  });

  it("each template image and each Linux tray still is drawn from the template drawing, and the dock's from the colour one", () => {
    for (const out of manifest.outputs) {
      const tray = out.path.startsWith("desktop/buddy/tray-");
      assert.equal(out.source.endsWith("-template.svg"), tray, out.path);
    }
  });

  it("each Linux tray still is the template painted in its appearance's own ink, and nothing else is (#3896)", () => {
    for (const out of manifest.outputs) {
      const mode = /^desktop\/buddy\/tray-[a-z-]+-(light|dark)(@2x)?\.png$/.exec(out.path)?.[1];
      assert.equal(out.ink, mode ? `--sheet-template (${mode})` : undefined, out.path);
    }
  });
});
