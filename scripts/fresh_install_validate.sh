#!/usr/bin/env bash
# Fresh-installation validation in throwaway containers (DISTRIBUTION V5 / DIST-12).
#
# Every install path this project advertises is a claim about a machine we do not own: a
# clean one. A dev box cannot test that claim — Homebrew, pip, uv and nix are all already
# there, and a passing check on a machine that already has the dependency proves nothing.
# So each path is exercised in a container that is destroyed afterwards, and the container
# runtime here is Finch (`finch vm stop && finch vm start` is a factory reset of the whole
# substrate, so a poisoned VM is never something you debug).
#
# Usage:
#   scripts/fresh_install_validate.sh                   # every leg
#   scripts/fresh_install_validate.sh pip nix           # only these legs
#   VERSION=0.2.0 scripts/fresh_install_validate.sh pip
#   CONTAINER_CLI=docker scripts/fresh_install_validate.sh
#
# Legs:
#   pip   `pip install personalclaw==VERSION` in a bare python:3.13-slim. The path most
#         users actually take, and the one that catches a wheel missing its package-data.
#   brew  `brew tap PersonalClaw/tap && brew install personalclaw/tap/personalclaw`, with
#         Homebrew installed from scratch in a bare Debian. See the ARCHITECTURE note below.
#   nix   `nix run .#personalclaw -- --version` against THIS checkout's flake, with nix
#         installed from scratch in a bare Debian.
#
# ARCHITECTURE NOTE — the brew leg runs on Linux (linuxbrew), not macOS. A container cannot
# run macOS, so this is not bare-metal coverage of the formula on a Mac; the tap repository's
# own CI covers that on a clean GitHub-hosted macOS runner, and that is the evidence for the
# macOS row. Do not read a green brew leg here as a green Mac.
#
# It also runs on the HOST's architecture. Do not reach for `--platform linux/amd64` to test
# x86_64: under QEMU-user emulation on Apple Silicon, Homebrew refuses to run at all —
# "Error: Homebrew's x86_64 support on Linux requires a CPU with SSSE3 support!" — because
# the check reads /proc/cpuinfo, which shows the host's ARM CPU. `QEMU_CPU=max` does not
# help (measured). x86_64 Linux coverage needs a real x86_64 machine.
#
# Each leg prints its own commands and output and ends in a single PASS/FAIL line. The script
# exits nonzero if any leg failed. Exit status is written explicitly at every step rather
# than read from `$?` after a pipeline, because `$?` after a pipe is the wrong command's
# status and that is exactly how a validation script comes to report a green it never ran.

set -uo pipefail

VERSION="${VERSION:-0.1.3}"
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.13-slim}"
DEBIAN_IMAGE="${DEBIAN_IMAGE:-public.ecr.aws/docker/library/debian:stable-slim}"
TAP="${TAP:-PersonalClaw/tap}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── container runtime ─────────────────────────────────────────────────────────────────────
pick_cli() {
  if [[ -n "${CONTAINER_CLI:-}" ]]; then
    command -v "$CONTAINER_CLI" >/dev/null 2>&1 || die "CONTAINER_CLI=$CONTAINER_CLI not found"
    printf '%s' "$CONTAINER_CLI"
    return
  fi
  local candidate
  for candidate in finch docker nerdctl podman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return
    fi
  done
  die "no container runtime found (looked for finch, docker, nerdctl, podman)"
}

die() {
  printf '\nfresh-install: %s\n' "$1" >&2
  exit 2
}

say() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

FAILED=()
PASSED=()
SKIPPED=()

record() { # record <leg> <exit status>
  if [[ "$2" -eq 0 ]]; then
    PASSED+=("$1")
    printf '\nfresh-install: %s PASS\n' "$1"
  else
    FAILED+=("$1")
    printf '\nfresh-install: %s FAIL (exit %s)\n' "$1" "$2"
  fi
}

# ── leg: pip ──────────────────────────────────────────────────────────────────────────────
leg_pip() {
  say "pip install personalclaw==$VERSION (clean $PYTHON_IMAGE)"
  "$CLI" run --rm -i "$PYTHON_IMAGE" bash -s "$VERSION" <<'GUEST'
set -euxo pipefail
version="$1"
uname -m
python --version
# A console script already on PATH would make the assertion below vacuous.
if command -v personalclaw; then echo "personalclaw ALREADY PRESENT - not a clean image" >&2; exit 3; fi
pip install --no-cache-dir --quiet "personalclaw==${version}"
command -v personalclaw
personalclaw --version | tee /tmp/v
grep -qx "personalclaw ${version}" /tmp/v
# The dashboard ships as package data inside the wheel. `--version` passes without it, so a
# wheel that lost its static tree would look healthy here unless this is asserted too.
python - <<'PY'
import pathlib
import personalclaw
dist = pathlib.Path(personalclaw.__file__).parent / "static" / "dist"
files = list(dist.rglob("*")) if dist.exists() else []
print(f"SPA files: {len(files)}")
assert files, f"no dashboard SPA bundled at {dist}"
PY
GUEST
  record pip "$?"
}

# ── leg: brew ─────────────────────────────────────────────────────────────────────────────
leg_brew() {
  say "brew tap $TAP && brew install personalclaw/tap/personalclaw (clean $DEBIAN_IMAGE, linuxbrew)"
  "$CLI" run --rm -i "$DEBIAN_IMAGE" bash -s "$VERSION" "$TAP" <<'GUEST'
set -euxo pipefail
version="$1"
tap="$2"
uname -m
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update >/dev/null
# Homebrew's own Linux requirements: git, curl, file, procps, and a compiler for any formula
# without a bottle for this architecture.
apt-get -qq install -y curl ca-certificates git procps file gcc g++ make >/dev/null

# Homebrew refuses to run as root, and its installer needs sudo unless the prefix already
# exists and is owned by the installing user.
useradd -m -s /bin/bash lb
mkdir -p /home/linuxbrew/.linuxbrew
chown -R lb:lb /home/linuxbrew

install -m 0755 /dev/stdin /home/lb/leg.sh <<'INNER'
set -euxo pipefail
version="$1"
tap="$2"
NONINTERACTIVE=1 /bin/bash -c \
  "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv bash)"
brew --version
if command -v personalclaw; then echo "personalclaw ALREADY PRESENT" >&2; exit 3; fi
brew tap "$tap"
brew install personalclaw/tap/personalclaw
command -v personalclaw
personalclaw --version | tee /tmp/v
grep -qx "personalclaw ${version}" /tmp/v
brew test personalclaw
INNER
chown lb:lb /home/lb/leg.sh
su lb -c "/home/lb/leg.sh '$version' '$tap'"
GUEST
  record brew "$?"
}

# ── leg: nix ──────────────────────────────────────────────────────────────────────────────
# This one needs the checkout inside the container, so it uses a named container plus `cp`
# rather than a one-shot `run`. The container is removed on the way out, pass or fail.
leg_nix() {
  say "nix run .#personalclaw -- --version (clean $DEBIAN_IMAGE, this checkout's flake)"
  local name="pclaw-freshinstall-nix-$$"
  local staged
  staged="$(mktemp -d)"

  # Ship the flake and its derivation, not the whole tree: `nix run .` copies the flake
  # directory into the store, and .git plus a populated node_modules/.venv turns a 2-second
  # copy into minutes. Nothing else in the repo is a flake input.
  mkdir -p "$staged/checkout"
  cp "$REPO_ROOT/flake.nix" "$staged/checkout/"
  cp -R "$REPO_ROOT/nix" "$staged/checkout/"
  [[ -f "$REPO_ROOT/flake.lock" ]] && cp "$REPO_ROOT/flake.lock" "$staged/checkout/"

  cat >"$staged/leg.sh" <<'GUEST'
set -euxo pipefail
uname -m
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update >/dev/null
apt-get -qq install -y curl xz-utils ca-certificates git >/dev/null

# A single-user install as root: /nix must exist (the installer would shell out to sudo,
# which a slim image does not have), and build-users-group must be empty or nix refuses to
# start over a missing 'nixbld' group.
mkdir -m 0755 -p /nix /etc/nix
printf 'build-users-group =\nexperimental-features = nix-command flakes\nfilter-syscalls = false\nsandbox = false\n' >/etc/nix/nix.conf
curl -sSL https://nixos.org/nix/install -o /tmp/nix-install.sh
sh /tmp/nix-install.sh --no-daemon --no-channel-add >/tmp/nix-install.log 2>&1 \
  || { tail -20 /tmp/nix-install.log; exit 1; }
nix=/root/.nix-profile/bin/nix
"$nix" --version

cd /checkout
"$nix" flake metadata --json >/dev/null
"$nix" run .#personalclaw -- --version | tee /tmp/v
grep -qx "personalclaw ${VERSION}" /tmp/v
GUEST

  local status=0
  "$CLI" rm -f "$name" >/dev/null 2>&1
  "$CLI" run -d --name "$name" -e "VERSION=$VERSION" "$DEBIAN_IMAGE" sleep infinity >/dev/null || status=$?
  if [[ $status -eq 0 ]]; then
    "$CLI" cp "$staged/checkout" "$name:/checkout" || status=$?
  fi
  if [[ $status -eq 0 ]]; then
    "$CLI" cp "$staged/leg.sh" "$name:/leg.sh" || status=$?
  fi
  if [[ $status -eq 0 ]]; then
    "$CLI" exec "$name" bash /leg.sh
    status=$?
  fi
  "$CLI" rm -f "$name" >/dev/null 2>&1
  rm -rf "$staged"
  record nix "$status"
}

# ── main ──────────────────────────────────────────────────────────────────────────────────
CLI="$(pick_cli)"

legs=("$@")
if [[ ${#legs[@]} -eq 0 ]]; then
  legs=(pip brew nix)
fi

printf 'fresh-install: runtime=%s version=%s legs=%s\n' "$CLI" "$VERSION" "${legs[*]}"
if [[ "$CLI" == "finch" ]]; then
  "$CLI" vm status || die "finch VM is not running — start it with 'finch vm start'"
fi

for leg in "${legs[@]}"; do
  case "$leg" in
    pip) leg_pip ;;
    brew) leg_brew ;;
    nix) leg_nix ;;
    *) SKIPPED+=("$leg"); printf '\nfresh-install: unknown leg %s — skipped\n' "$leg" ;;
  esac
done

say "summary"
printf 'passed:  %s\n' "${PASSED[*]:-none}"
printf 'failed:  %s\n' "${FAILED[*]:-none}"
printf 'skipped: %s\n' "${SKIPPED[*]:-none}"

# A run that exercised nothing must not read as success — that is the same defect as a
# pytest run reporting exit 0 after collecting zero items.
if [[ ${#PASSED[@]} -eq 0 && ${#FAILED[@]} -eq 0 ]]; then
  die "no leg ran"
fi
[[ ${#FAILED[@]} -eq 0 ]] || exit 1
