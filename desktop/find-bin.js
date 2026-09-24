/**
 * Locate the personalclaw backend binary by checking well-known paths in order.
 *
 * Returns the first executable candidate, or bare `"personalclaw"` as a PATH
 * fallback. Dependencies are injected so the function is pure and testable
 * without mocking globals.
 *
 * @param {typeof import("fs")} fs - Node fs module (needs `accessSync`, `constants.X_OK`)
 * @param {typeof import("os")} os - Node os module (needs `homedir()`)
 * @param {typeof import("path")} path - Node path module
 * @param {string|undefined} resourcesPath - `process.resourcesPath` (Electron only)
 * @param {string} dirname - `__dirname` of the calling module
 * @returns {string} Absolute path to the binary, or `"personalclaw"`
 */
function findPersonalclawBin(fs, os, path, resourcesPath, dirname) {
  const home = os.homedir();
  const candidates = [
    // 1. Bundled PyInstaller binary (inside .app or dev electron/backend-dist)
    path.join(resourcesPath || "", "backend-dist", "personalclaw-backend", "personalclaw-backend"),
    path.resolve(dirname, "backend-dist", "personalclaw-backend", "personalclaw-backend"),
    path.resolve(dirname, "..", "bin", "personalclaw"),
    // 2. Well-known install paths (pip install, venv, homebrew)
    path.join(home, ".local", "bin", "personalclaw"),
    path.join(home, ".personalclaw-app", ".venv", "bin", "personalclaw"),
  ];
  for (const bin of candidates) {
    try {
      fs.accessSync(bin, fs.constants.X_OK);
      return bin;
    } catch (e) {
      if (e.code !== "ENOENT") console.warn(`personalclaw candidate ${bin}: ${e.code}`);
    }
  }
  return "personalclaw"; // fall back to PATH
}

module.exports = { findPersonalclawBin };
