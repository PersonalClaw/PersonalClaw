const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

describe("electron-builder files list", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const bundledFiles = pkg.build.files;

  it("includes every local require() from main.js", () => {
    const main = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
    const localRequires = [...main.matchAll(/require\("\.\/([^"]+)"\)/g)].map(m => m[1] + ".js");

    const missing = localRequires.filter(f => !bundledFiles.includes(f));
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("does not reference files that no longer exist", () => {
    const stale = bundledFiles.filter(f => !fs.existsSync(path.join(ROOT, f)));
    assert.deepStrictEqual(stale, [], `Stale entries in build.files: ${stale.join(", ")}`);
  });
});

describe("electron-builder Linux target (DC-6)", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const linux = pkg.build.linux;

  it("declares exactly the AppImage + deb targets the ruling shipped", () => {
    // Exact-match on purpose: a target added here silently changes what the
    // release attaches, and Windows (nsis/portable) is DEFERRED — see
    // docs/roadmap/atomic/DC.md `DC-6`. Widening this list is a ruling, not a tweak.
    assert.deepStrictEqual(linux.target, ["AppImage", "deb"]);
  });

  it("points its icon at a file that exists", () => {
    assert.ok(linux.icon, "linux.icon missing");
    assert.ok(fs.existsSync(path.join(ROOT, linux.icon)), `linux icon not found: ${linux.icon}`);
  });

  it("carries the maintainer contact the deb target requires", () => {
    // electron-builder refuses to build a .deb without a maintainer email
    // (falls back to package.json `author`, which this package does not set).
    assert.match(linux.maintainer || "", /<[^@\s]+@[^@\s]+>/,
      "linux.maintainer must be 'Name <email>' — the deb control file requires it");
  });

  it("has a dist:linux script that builds --linux with the pinned electron version", () => {
    const script = pkg.scripts["dist:linux"];
    assert.ok(script, "scripts['dist:linux'] missing");
    assert.match(script, /--linux/);
    // Same guard the mac script carries: electron is hoisted to the workspace
    // root, so electron-builder cannot always detect the version on its own.
    assert.match(script, /--config\.electronVersion=/);
  });

  it("does not carry a mac-only package name into the deb Package field", () => {
    // electron-builder derives the deb's Package: field from package.json `name`
    // (appInfo.linuxPackageName), so a name claiming one OS ships a lie to dpkg.
    assert.strictEqual(pkg.name, "personalclaw-desktop");
  });
});
