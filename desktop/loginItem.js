/**
 * "Open PersonalClaw at login" (DC-4 T4.3).
 *
 * A login item is a PERSISTENT change to the user's machine, so this module is
 * deliberately narrow and boring:
 *
 *  - **Opt-in.** Nothing here runs at boot. `makeLoginItem()` only reads; the sole
 *    write is `set()`, and `set()` is reachable only from an explicit user action
 *    (the tray's "Open at Login" checkbox, or Settings over the bridge).
 *    `test/loginItem.test.js` asserts construction performs ZERO writes.
 *  - **Reversible by the same control.** `set(false)` un-registers, and the user can
 *    also remove it in System Settings → General → Login Items without the app
 *    running. There is no other state to clean up.
 *  - **Idempotent.** `set()` reads the current registration first and returns
 *    `changed: false` without writing when it already matches, so enabling twice
 *    cannot produce two entries.
 *  - **Nothing outside the app's own domain.** We call Electron's
 *    `app.setLoginItemSettings()`, which on macOS registers THIS `.app` bundle with
 *    the per-user Login Items service (LaunchServices / `SMAppService`-backed;
 *    surfaced in System Settings → General → Login Items and recorded in the OS's
 *    own per-user background-task database). We do NOT write a
 *    `~/Library/LaunchAgents/*.plist`, we do not touch `/Library/*`, and nothing
 *    here needs elevated privileges.
 *
 * Electron is injected (`app`) rather than required, so the whole contract is
 * testable against a fake and no test can reach the real login-item registry.
 */

/** Platforms where Electron implements login-item settings. */
const SUPPORTED_PLATFORMS = ["darwin", "win32"];

/**
 * @param {object} deps
 * @param {{getLoginItemSettings: Function, setLoginItemSettings: Function}} deps.app
 * @param {string} [deps.platform] `process.platform`
 * @param {(msg: string) => void} [deps.log]
 */
function makeLoginItem({ app, platform = process.platform, log = () => {} } = {}) {
  const supported = SUPPORTED_PLATFORMS.includes(platform) && !!app;

  /** Current registration, or false when unsupported/unreadable. Never throws:
   * a tray menu that cannot render because a settings read failed would be a worse
   * bug than a checkbox that reads "off". */
  function isEnabled() {
    if (!supported) return false;
    try {
      const settings = app.getLoginItemSettings() || {};
      return Boolean(settings.openAtLogin);
    } catch (err) {
      log(`login item state unreadable: ${err.message}`);
      return false;
    }
  }

  /**
   * Register or un-register. Idempotent — a no-op write is skipped, not repeated.
   * @param {boolean} enabled
   * @returns {{ok: boolean, enabled: boolean, changed: boolean, supported: boolean,
   *            reason?: string}}
   */
  function set(enabled) {
    const want = Boolean(enabled);
    if (!supported) {
      return {
        ok: false,
        enabled: false,
        changed: false,
        supported: false,
        reason: `login items are not implemented on ${platform}`,
      };
    }
    const current = isEnabled();
    if (current === want) {
      // The idempotence guarantee: enabling an already-enabled item writes nothing,
      // so it cannot mint a second entry.
      return { ok: true, enabled: current, changed: false, supported: true };
    }
    try {
      // `openAsHidden` keeps a login launch from throwing a window in the user's face
      // at every boot — the point of the tray presence is to be quiet.
      app.setLoginItemSettings({ openAtLogin: want, openAsHidden: true });
    } catch (err) {
      log(`login item write failed: ${err.message}`);
      return {
        ok: false,
        enabled: current,
        changed: false,
        supported: true,
        reason: err.message,
      };
    }
    // Read back rather than trust the write: the OS is the authority on whether the
    // registration took, and a silent failure here would be an inert toggle.
    const after = isEnabled();
    return {
      ok: after === want,
      enabled: after,
      changed: after !== current,
      supported: true,
      ...(after === want ? {} : { reason: "the OS did not apply the change" }),
    };
  }

  /** What this touches, in one line, for logs and for the Settings UI. */
  function describe() {
    if (!supported) return `login items are not implemented on ${platform}`;
    if (platform === "darwin") {
      return "macOS Login Items (System Settings → General → Login Items) for this app bundle only";
    }
    return "the current user's Run registry key for this app only";
  }

  return { supported, isEnabled, set, describe };
}

/**
 * The bridge's main-process half for the login item.
 *
 * Registered on its OWN channels rather than folded into `registerCapabilityIpc`,
 * because the capability bridge's channel set is ratcheted to exactly
 * probe/request/snapshot (`test/capabilities.test.js`) — a login item is a
 * preference, not an OS permission, and does not belong in that vocabulary.
 *
 * The renderer can only ask for `true`/`false`; the coercion happens here, in the
 * process that owns the boundary, and not in the preload (which is a courtesy check).
 *
 * `onChanged` is how the OTHER surface finds out. The tray checkbox and this bridge
 * write the SAME registration, but the tray renders from a cached
 * `loginItemState` — so without this callback a flip in Settings left the menu-bar
 * checkbox showing the stale value until the next restart, and the user had two
 * controls disagreeing about one fact. It fires on every `set`, including a refused
 * or no-op one, because the point is to re-read the OS rather than to mirror the
 * request.
 *
 * @param {(result: object) => void} [onChanged]
 */
function registerLoginItemIpc(ipcMain, loginItem, channels, onChanged = () => {}) {
  ipcMain.handle(channels.loginItemGet, () => ({
    enabled: loginItem.isEnabled(),
    supported: loginItem.supported,
    describes: loginItem.describe(),
  }));
  ipcMain.handle(channels.loginItemSet, (_e, enabled) => {
    const result = loginItem.set(Boolean(enabled));
    onChanged(result);
    return result;
  });
}

module.exports = { makeLoginItem, registerLoginItemIpc, SUPPORTED_PLATFORMS };
