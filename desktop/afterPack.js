// electron-builder afterPack hook — ad-hoc sign the packed macOS bundle.
//
// WHY THIS EXISTS. The project ships the desktop app unsigned by owner ruling (2026-09-22:
// producing the installer does not require signing, and a real signature needs a paid Apple
// Developer account deliberately not bought). `mac.identity: null` plus
// `CSC_IDENTITY_AUTO_DISCOVERY=false` stop electron-builder discovering a Developer identity
// from the build machine's login keychain — which it once did, shipping an app signed by an
// unrelated third party. Those two guards stay, and this hook is NOT a reversal of them: an
// ad-hoc signature (`--sign -`) needs no identity, no keychain and no Apple account.
//
// WHAT IT FIXES. Disabling signing does not leave the bundle unsigned. It leaves the STOCK
// ELECTRON LINKER SIGNATURE in place, and that seal does not describe the bundle we built:
// it declares that sealed resources must be present while sealing none of Frameworks, the
// four helper .apps, app.asar or the PyInstaller backend. Measured on the 21:09Z 0.2.0 build:
//
//   Identifier=Electron                       (not io.personalclaw.app)
//   flags=0x20002(adhoc,linker-signed)         (the inherited-seal tell)
//   Info.plist=not bound · Sealed Resources=none · no Contents/_CodeSignature
//   codesign --verify --deep --strict -> exit 1
//     "code has no resources but signature indicates they must be present"
//
// macOS refuses a DAMAGED signature more firmly than an absent one, so that build failed to
// install harder than the foreign-signed one did, and presented with no name because macOS
// will not trust an Info.plist behind a broken seal. Stripping the signature instead is NOT
// an option: on Apple silicon every arm64 executable must carry at least an ad-hoc signature
// to execute, so a stripped bundle would install and then refuse to launch.
//
// WHY afterPack AND NOT A POST-BUILD STEP. This runs after the .app is packed and BEFORE the
// dmg is assembled, so the dmg is built from the re-signed bundle. A step that ran after
// `npm run dist` would be useless — the dmg would already contain the bad app. It also lives
// in the manifest rather than the Makefile, so it survives someone invoking the build another
// way (npm directly, CI, a future script): the same belt-and-braces reasoning that put
// `identity: null` beside the environment variable.
//
// The hook VERIFIES its own work and throws, so a bundle that cannot be sealed fails the
// build here rather than becoming a dmg nobody can install.

"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

module.exports = async function afterPack(context) {
  // Linux/Windows have no codesign and no OS-level signing gate; nothing to do.
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  const appName = `${context.packager.appInfo.productFilename}.app`;
  const appPath = path.join(context.appOutDir, appName);

  if (!fs.existsSync(appPath)) {
    throw new Error(
      `afterPack: expected a packed bundle at ${appPath} and found none — ` +
        "refusing to report the bundle as signed without signing it.",
    );
  }

  // `--sign -` is the ad-hoc pseudo-identity: no certificate, no keychain, no Apple account.
  // `--timestamp=none` because a trusted timestamp is a signing-authority service and means
  // nothing for an ad-hoc signature. No `--options runtime`: hardened runtime's library
  // validation refuses to load ad-hoc-signed frameworks, which would make the app install
  // and then fail to launch. The target state is plain `flags=0x2(adhoc)`.
  console.log(`  • ad-hoc signing ${appName} (no identity, no notarization)`);
  execFileSync(
    "codesign",
    ["--force", "--deep", "--sign", "-", "--timestamp=none", appPath],
    { stdio: "inherit" },
  );

  // Verify HERE so a bad seal can never reach the dmg. Exit status is the signal — this is
  // the check that the previous build lacked, and the reason it shipped uninstallable.
  execFileSync("codesign", ["--verify", "--deep", "--strict", appPath], {
    stdio: "inherit",
  });
  console.log(`  • ad-hoc signature verified for ${appName}`);
};
