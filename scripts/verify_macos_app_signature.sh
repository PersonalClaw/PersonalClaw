#!/usr/bin/env bash
#
# verify_macos_app_signature.sh — fail the build unless a macOS .app is ad-hoc signed with a
# VALID seal over the bundle we actually built, and carries no third-party signing authority.
# Usage: scripts/verify_macos_app_signature.sh <path-to-.app>
#
# WHY THIS EXISTS — two defects in two days, and the second was caused by the fix for the
# first. The project ships the desktop app unsigned by owner ruling (2026-09-22: producing the
# installer does not require signing, and a real signature needs a paid Apple Developer
# account deliberately not bought). Twice the build produced something else and nothing noticed:
#
#   1. FOREIGN AUTHORITY. electron-builder AUTO-DISCOVERS an identity from the build machine's
#      login keychain. A local build shipped PersonalClaw.app signed
#      `Authority=MeetNote Developer` — an unrelated third party's identity on what would have
#      been a public release artifact — while the Makefile comment claimed it was unsigned.
#   2. DAMAGED SEAL. Fixing (1) with `"identity": null` disabled signing, which did NOT leave
#      the bundle unsigned: it left the STOCK ELECTRON LINKER SIGNATURE in place, and that
#      seal does not describe the bundle we built. Measured on the 21:09Z 0.2.0 build:
#      `Identifier=Electron`, `flags=0x20002(adhoc,linker-signed)`, `Info.plist=not bound`,
#      `Sealed Resources=none`, no `Contents/_CodeSignature`, and
#      `codesign --verify --deep --strict` exit 1 — "code has no resources but signature
#      indicates they must be present". It sealed NONE of Frameworks, the four helper .apps,
#      app.asar or the PyInstaller backend while declaring that resources must be present.
#      macOS refuses a DAMAGED signature more firmly than an absent one, so this build failed
#      to install HARDER than the foreign-signed one, and presented with no name because macOS
#      will not trust an Info.plist behind a broken seal.
#
# So the intended state is not "no signature". On Apple silicon every arm64 executable must
# carry at least an ad-hoc signature to execute at all, so a genuinely stripped bundle would
# install and then refuse to launch. The intended state is: **ad-hoc signed over the whole
# bundle, seal valid, no named authority** — produced by `desktop/afterPack.js`, which runs
# after electron-builder packs and before the dmg is assembled.
#
# WHY EVERY CHEAP GATE HERE IS VACUOUS, each for a different reason:
#
#   * `codesign -dv … | grep -v Authority` — on a genuinely unsigned app codesign EXITS
#     NON-ZERO ("code object is not signed at all") and prints no report, so the pipeline
#     passes on the good and the bad artifact alike.
#   * `codesign -dv …` AT ALL — `-dv` is too quiet to print `Authority=` even for a signed
#     app. Measured on the MeetNote bundle: `-dv` exits 0 and prints Identifier/Format/
#     CodeDirectory/Signature-size/TeamIdentifier and NO Authority line. `-dvv` is the
#     minimum verbosity that names a signer, so a gate written with `-dv` reports CLEAN on an
#     app that is signed.
#   * ANY AUTHORITY-ABSENCE CHECK — this is what let defect (2) ship, including an earlier
#     revision of this very script. The damaged bundle has no `Authority=` line and reports
#     `Signature=adhoc`, so a gate that fails only on a named authority PASSES it. Authority
#     absence is necessary and NOT sufficient.
#   * `codesign -dvv | grep 'Sealed Resources'` alone — codesign spells that key TWO ways and
#     matching one spelling silently inverts the test. Measured:
#         properly sealed : `Sealed Resources version=2 rules=13 files=4844`
#         damaged         : `Sealed Resources=none`
#     Likewise `Info.plist entries=32` versus `Info.plist=not bound`. Each check below demands
#     the GOOD spelling positively rather than passing on "did not say the bad thing".
#
# THE PRIMITIVE IS SEAL VALIDITY read from EXIT STATUS: `codesign --verify --deep --strict`,
# the one check that exits non-zero on all three bad states.
#
# GATEKEEPER IS DELIBERATELY NOT ASSERTED. `spctl -a -t install` exits 3 ("rejected") on a
# KNOWN-GOOD unnotarized bundle — measured on the working installed app — because Gatekeeper
# wants Developer ID + notarization, which the owner ruling rules out buying. Gating on spctl
# would gate on notarization and would fail a perfectly good artifact. The user-facing cost of
# that rejection is documented in docs/guides/desktop.md (drag to Applications, approve once
# under System Settings -> Privacy & Security), not enforced here.
#
# THE INTENDED AUTHORITY SET IS EMPTY. If the ruling is ever revisited and a real Developer ID
# is bought, this script is the single place that encodes the rule — allow the new authority
# here rather than deleting the gate.

set -euo pipefail

#: The bundle identifier every artifact of this project must claim. Asserted literally *and*
#: against the bundle's own Info.plist: the literal catches an appId change, the cross-check
#: catches the stale-linker state (which reports `Identifier=Electron` while the plist is
#: still correct). Either alone would miss one of the two.
EXPECTED_IDENTIFIER="io.personalclaw.app"

if [ "$#" -ne 1 ]; then
	echo "usage: $0 <path-to-.app>" >&2
	exit 2
fi

APP="$1"

if [ ! -d "$APP" ]; then
	echo "verify_macos_app_signature: not a bundle directory: $APP" >&2
	exit 2
fi

failed=0

fail() {
	echo "  FAIL      $1" >&2
	shift
	if [ "$#" -gt 0 ]; then
		printf '%s\n' "$@" | sed 's/^/            /' >&2
	fi
	failed=$((failed + 1))
}

# Capture output AND status without `|| true`, which is exactly what makes these checks
# vacuous. `set +e` around the single call is the deliberate handling.
run_capture() {
	set +e
	# The space after `$(` is required: `$((` parses as arithmetic expansion (SC1102).
	RUN_OUT="$( ("$@") 2>&1 )"
	RUN_STATUS=$?
	set -e
}

report_of() {
	run_capture codesign -dvv "$1"
	printf '%s\n' "$RUN_OUT"
}

echo "verify_macos_app_signature: $APP"

# ── 1. THE PRIMITIVE — the seal is valid over this bundle ──────────────────────────────────
run_capture codesign --verify --deep --strict "$APP"
if [ "$RUN_STATUS" -ne 0 ]; then
	fail "SEAL IS INVALID — codesign --verify --deep --strict exited $RUN_STATUS" "$RUN_OUT"
else
	echo "  ok        seal verifies (codesign --verify --deep --strict, exit 0)"
fi

top_report="$(report_of "$APP")"

# ── 2. no named signing authority, anywhere in the bundle tree ─────────────────────────────
# Nested code is checked too: electron-builder signs the helper apps and bundled frameworks
# separately, and on the MeetNote artifact all NINE bundles carried the foreign authority. A
# partial re-sign would otherwise hide in a framework.
authority_seen=0
for bundle in "$APP" "$APP"/Contents/Frameworks/*.app "$APP"/Contents/Frameworks/*.framework; do
	[ -e "$bundle" ] || continue
	bundle_report="$(report_of "$bundle")"
	if printf '%s\n' "$bundle_report" | grep -q '^Authority='; then
		authority_seen=1
		fail "SIGNED BY A CODE-SIGNING AUTHORITY: $bundle" \
			"$(printf '%s\n' "$bundle_report" | grep -E '^(Identifier|Authority|TeamIdentifier)=')"
	fi
done
[ "$authority_seen" -eq 0 ] && echo "  ok        no named signing authority in the bundle tree"

# ── 3. it is ad-hoc, and NOT the inherited Electron linker seal ────────────────────────────
if printf '%s\n' "$top_report" | grep -q '^Signature=adhoc'; then
	echo "  ok        ad-hoc signature (Signature=adhoc)"
elif printf '%s\n' "$top_report" | grep -qF 'code object is not signed at all'; then
	fail "NOT SIGNED AT ALL — an arm64 bundle must be at least ad-hoc signed to launch" "$top_report"
else
	fail "signature is present but is NOT ad-hoc — the project ships ad-hoc only" "$top_report"
fi

# `flags=0x20002(adhoc,linker-signed)` is the inherited-Electron-seal tell; a properly
# re-signed bundle reads `flags=0x2(adhoc)`. A sharper discriminator than the identifier,
# because it is what the linker seal IS rather than what it happens to be named.
if printf '%s\n' "$top_report" | grep -q 'linker-signed'; then
	fail "CodeDirectory is linker-signed — this is Electron's stock seal, not a signature over our bundle" \
		"$(printf '%s\n' "$top_report" | grep '^CodeDirectory')"
else
	echo "  ok        not linker-signed ($(printf '%s\n' "$top_report" | grep '^CodeDirectory' | sed 's/.*\(flags=[^ ]*\).*/\1/'))"
fi

# ── 4. the seal actually covers the bundle's resources and Info.plist ──────────────────────
if printf '%s\n' "$top_report" | grep -q '^Sealed Resources version='; then
	sealed_line="$(printf '%s\n' "$top_report" | grep '^Sealed Resources version=')"
	sealed_files="$(printf '%s\n' "$sealed_line" | sed -n 's/.*files=\([0-9]\{1,\}\).*/\1/p')"
	if [ -z "$sealed_files" ]; then
		fail "could not read a files= count from '$sealed_line' — the seal-coverage check would pass vacuously"
	elif [ "$sealed_files" -le 0 ]; then
		fail "seal covers files=$sealed_files — a signature sealing nothing is the damaged state"
	else
		echo "  ok        seal covers the bundle ($sealed_line)"
	fi
elif printf '%s\n' "$top_report" | grep -q '^Sealed Resources=none'; then
	fail "Sealed Resources=none — the CodeDirectory claims sealed resources that do not exist (the damaged state)"
else
	fail "codesign reported no recognisable 'Sealed Resources' line — cannot confirm the seal covers the bundle" "$top_report"
fi

if printf '%s\n' "$top_report" | grep -q '^Info\.plist entries='; then
	echo "  ok        Info.plist bound ($(printf '%s\n' "$top_report" | grep '^Info\.plist entries='))"
elif printf '%s\n' "$top_report" | grep -q '^Info\.plist=not bound'; then
	fail "Info.plist=not bound — macOS will not trust the bundle's metadata, which is why a damaged build presents with no name"
else
	fail "codesign reported no recognisable 'Info.plist' binding line — cannot confirm the plist is sealed" "$top_report"
fi

# ── 5. the signing identifier names THIS app ───────────────────────────────────────────────
sig_identifier="$(printf '%s\n' "$top_report" | sed -n 's/^Identifier=\(.*\)$/\1/p')"
if [ "$sig_identifier" = "$EXPECTED_IDENTIFIER" ]; then
	echo "  ok        signing identifier is $EXPECTED_IDENTIFIER"
else
	fail "signing identifier is '$sig_identifier', expected '$EXPECTED_IDENTIFIER' (the inherited Electron seal reports 'Electron')"
fi

plist_identifier=""
if [ -f "$APP/Contents/Info.plist" ]; then
	set +e
	plist_identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null)"
	set -e
fi
if [ -z "$plist_identifier" ]; then
	fail "could not read CFBundleIdentifier from $APP/Contents/Info.plist — the cross-check would pass vacuously"
elif [ "$sig_identifier" != "$plist_identifier" ]; then
	fail "signature and bundle disagree: codesign says '$sig_identifier', Info.plist says '$plist_identifier'"
else
	echo "  ok        signature agrees with the bundle's own CFBundleIdentifier"
fi

if [ "$failed" -ne 0 ]; then
	cat >&2 <<EOF

verify_macos_app_signature: FAILED — $failed check(s) failed.

This build is NOT the artifact the project ships, and a user would not be able to install it.
Do NOT fix it by obtaining, borrowing or configuring a real signing identity, and do not
notarize (owner ruling 2026-09-22). The intended state is an ad-hoc signature over the WHOLE
bundle with a valid seal. Confirm all three guards and rebuild:

  * CSC_IDENTITY_AUTO_DISCOVERY=false  in the Makefile's desktop-dist recipe
  * "identity": null                   under build.mac in desktop/package.json
  * "afterPack": "./afterPack.js"       in desktop/package.json's build block — the hook that
                                       ad-hoc re-signs the packed bundle BEFORE the dmg is
                                       assembled. Without it the bundle keeps Electron's
                                       stock linker seal, which seals none of our files.
EOF
	exit 1
fi

echo "verify_macos_app_signature: OK — ad-hoc signed, seal valid over the bundle, no third-party authority."
