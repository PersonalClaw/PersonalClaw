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

  it("includes every local require() from the modules main.js pulls in, transitively", () => {
    // `main.js`'s own requires were covered; a module IT requires can require a third, and that
    // third one is just as fatal to a packaged app. Walked rather than listed so the ratchet does
    // not need editing every time a module gains a dependency. (CA-8 added a four-module chain:
    // main → connectDialog → connectMode → gatewayUrl / endpointRegistry.)
    const seen = new Set();
    const queue = ["main.js"];
    const missing = [];
    while (queue.length) {
      const file = queue.shift();
      if (seen.has(file)) continue;
      seen.add(file);
      const full = path.join(ROOT, file);
      if (!fs.existsSync(full)) continue;
      for (const m of fs.readFileSync(full, "utf8").matchAll(/require\("\.\/([^"]+)"\)/g)) {
        const dep = m[1].endsWith(".js") ? m[1] : `${m[1]}.js`;
        if (!bundledFiles.includes(dep)) missing.push(`${file} → ${dep}`);
        queue.push(dep);
      }
    }
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("includes every sibling file loaded by path rather than by require()", () => {
    // A preload and an HTML document are referenced as `path.join(__dirname, "x")`, which the
    // require scan above cannot see. Leaving one out of `build.files` does not crash the app — it
    // ships a window that loads nothing, which is worse, because it looks like a feature that
    // silently does not work. `connectDialog.html` and `connectPreload.js` are exactly that shape.
    const jsFiles = fs.readdirSync(ROOT).filter((f) => f.endsWith(".js") && fs.statSync(path.join(ROOT, f)).isFile());
    const missing = [];
    for (const file of jsFiles) {
      const src = fs.readFileSync(path.join(ROOT, file), "utf8");
      for (const m of src.matchAll(/path\.join\(__dirname,\s*"([^"]+)"\)/g)) {
        const ref = m[1];
        if (!fs.existsSync(path.join(ROOT, ref))) {
          missing.push(`${file} → ${ref} (file does not exist)`);
        } else if (!bundledFiles.includes(ref)) {
          missing.push(`${file} → ${ref}`);
        }
      }
    }
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("the scanners above are not vacuous — they find the files that ARE there", () => {
    // A floor. Both scans are regex-driven, so a syntax change in the source they read (a single
    // quote instead of a double, say) would silently make them match nothing and pass forever.
    const main = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
    assert.ok([...main.matchAll(/require\("\.\/([^"]+)"\)/g)].length >= 5, "the require scan found almost nothing");
    const dialog = fs.readFileSync(path.join(ROOT, "connectDialog.js"), "utf8");
    assert.ok([...dialog.matchAll(/path\.join\(__dirname,\s*"([^"]+)"\)/g)].length >= 2, "the path-join scan found almost nothing");
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
    // the DC plan (internal, not in this repo) `DC-6`. Widening this list is a ruling, not a tweak.
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
