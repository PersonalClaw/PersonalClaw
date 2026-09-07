#!/bin/sh
# PersonalClaw bootstrap installer — the `curl -fsSL https://personalclaw.dev/install | sh` one-liner.
#
# Plan 34 (DISTRIBUTION) §E / T2.2. THIS FILE IS THE SOURCE OF TRUTH for the bytes
# served at https://personalclaw.dev/install. It is staged here — rather than living only
# in the website repo (personalclaw.dev, plan 36 owns) — because the rail below is here.
# The website repo's public/install is a MIRROR: re-apply it whenever this file changes,
# and update install.sh.sha256 in the same commit (deploy/website/README.md §1).
#
# What CI actually does with this file. Every claim in this block is asserted by
# tests/test_website_installer.py, which reds if the job it names stops existing or
# stops naming this file — an earlier version of this header advertised a weekly
# full.yml smoke that had never been written, and nothing noticed:
#   · ci.yml   `lint`          shellcheck -s sh + sh -n + dash -n on this file (every PR)
#   · ci.yml   `test`          offline contract: --help / --container / unknown-arg, the
#                              truncation-safe structure, staged-vs-pinned digest
#   · full.yml `install-smoke` runs BOTH this file AND the bytes actually served at
#                              /install, in a bare ubuntu container (push to main +
#                              nightly), and reds when the two disagree. A fetch that
#                              fails reports `unproven` and reds; it never goes green.
#
# What it does (idempotent — re-running upgrades):
#   1. `--container` → print the Docker Compose snippet and exit (no install).
#   2. Ensure `uv` is present (installs via the official installer if missing).
#   3. `uv tool install --upgrade personalclaw>=$PC_MIN_VERSION` (uv brings its own Python
#      3.12). The floor bounds a downgrade attack without giving up --upgrade — see
#      PC_MIN_VERSION below for why it lags the current release by one.
#   4. Print next steps and offer to run `personalclaw setup`.
#
# POSIX sh only (no bashisms) so it runs under dash/ash/sh on Linux + macOS.

set -eu

PC_PACKAGE="personalclaw"
# Downgrade floor for the PyPI install below, and it is deliberately the PREVIOUS release
# rather than the version this tree builds.
#
# WHAT IT BUYS. `--upgrade` already resolves to the newest release, so on an honest index the
# floor forbids nothing (measured 2026-09-07, uv 0.12.5: `>=0.1.2` + `--upgrade` installs
# 0.1.3). Its whole job is the dishonest case: a rolled-back or yanked-and-replaced index
# that offers only old code. Unfloored, that installs silently — measured `+ personalclaw==
# 0.1.0`, exit 0. Floored, uv exits 1 with "unsatisfiable". Loud beats quiet.
#
# WHY NOT THE CURRENT VERSION. A floor equal to pyproject's version is unsatisfiable for as
# long as it takes that release to reach PyPI and its mirrors — measured, `>=0.1.4` exits 1
# today with "only personalclaw<=0.1.3 is available". That window would break every install
# AND full.yml's `install-smoke`, which runs this file for real on every push to main. One
# release behind has always shipped already, so the floor can never demand a version that
# does not exist yet. The cost is one unguarded step: a rollback to the immediately-previous
# release still installs, which is the attacker's weakest move anyway.
#
# WHY IT CANNOT ROT. A hand-typed version in a shell script is the defect in #2554 (two
# copies drifted three weeks). tests/test_website_installer.py pins this constant to
# CHANGELOG.md's second-newest release heading, so it reds the release after it goes stale.
PC_MIN_VERSION="0.1.2"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

# ── tiny output helpers ──────────────────────────────────────────────────────
if [ -t 1 ]; then
    _bold=$(printf '\033[1m')
    _dim=$(printf '\033[2m')
    _reset=$(printf '\033[0m')
else
    _bold=''
    _dim=''
    _reset=''
fi
say() { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$_bold" "$_reset" "$*"; }
warn() { printf '%swarning:%s %s\n' "$_bold" "$_reset" "$*" >&2; }
die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

# ── --container: print the compose snippet and exit ──────────────────────────
print_container() {
    cat <<'EOF'
Run PersonalClaw with Docker Compose (self-hosters / Windows):

  cp .env.example .env        # optional: set provider keys / options
  docker compose -f deploy/compose/compose.yaml up -d

The dashboard comes up on http://127.0.0.1:10000 with a persistent volume.
Details, backups, and updates: https://github.com/PersonalClaw/PersonalClaw/blob/main/docs/guides/containers.md
EOF
}

# ── OS / arch detection (informational + guard rails) ────────────────────────
detect_platform() {
    os=$(uname -s 2>/dev/null || echo unknown)
    arch=$(uname -m 2>/dev/null || echo unknown)
    case "$os" in
        Linux | Darwin) : ;;
        *)
            warn "unsupported OS '$os' — the uv path targets Linux and macOS."
            warn "On Windows, use the Docker Compose path:  sh install.sh --container"
            ;;
    esac
    say "${_dim}Detected platform: $os $arch${_reset}"
}

# ── ensure uv is installed ───────────────────────────────────────────────────
ensure_uv() {
    if have uv; then
        say "${_dim}uv already installed: $(uv --version 2>/dev/null || echo present)${_reset}"
        return 0
    fi
    step "Installing uv (Astral's Python package/tool manager)…"
    # The trust chain for THIS fetch is TLS to astral.sh plus Astral's release hygiene, and
    # nothing else. We do not verify these bytes: no digest, no version pin, and astral.sh
    # redirects — so a compromised host, a mis-issued certificate or a hostile redirect
    # would execute here. uv's installer does checksum-pin the uv BINARIES it goes on to
    # fetch, but that is a different download and says nothing about the script we pipe into
    # sh. (An earlier version of this comment claimed otherwise — see #2582.) This is the
    # same posture as rustup, nvm and uv's own documented bootstrap: chosen, not overlooked.
    # A user who wants a verified fetch can check THIS file's digest against the copy committed
    # in the GitHub repo first; that path covers our bytes, not Astral's, and is written up
    # under "Verify the one-liner" in docs/guides/getting-started.md §1.
    # Prefer curl, fall back to wget.
    if have curl; then
        curl -fsSL "$UV_INSTALLER_URL" | sh
    elif have wget; then
        wget -qO- "$UV_INSTALLER_URL" | sh
    else
        die "need curl or wget to install uv. Install one, or install uv manually: https://docs.astral.sh/uv/"
    fi
    # The installer drops uv in ~/.local/bin (or $XDG_BIN_HOME / ~/.cargo/bin);
    # make it visible to THIS shell so the tool-install below finds it.
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "${XDG_BIN_HOME:-}"; do
        [ -n "$d" ] && [ -d "$d" ] && case ":$PATH:" in
            *":$d:"*) : ;;
            *) PATH="$d:$PATH" ;;
        esac
    done
    export PATH
    have uv || die "uv was installed but is not on PATH. Open a new shell and re-run, or add ~/.local/bin to PATH."
}

# ── install / upgrade personalclaw ───────────────────────────────────────────
install_personalclaw() {
    step "Installing $PC_PACKAGE with uv (this brings its own Python 3.12)…"
    # --upgrade makes re-runs idempotent: a fresh install the first time, an
    # in-place upgrade to the latest release afterwards. The >= floor does not narrow that —
    # --upgrade still resolves to the newest release; it only refuses an index that offers
    # nothing at or above PC_MIN_VERSION. See PC_MIN_VERSION at the top of this file.
    uv tool install --upgrade "$PC_PACKAGE>=$PC_MIN_VERSION"
    have personalclaw || {
        warn "personalclaw installed but not yet on PATH."
        warn "Run 'uv tool update-shell' (or open a new shell), then 'personalclaw setup'."
        return 0
    }
}

# ── offer to run setup ───────────────────────────────────────────────────────
offer_setup() {
    have personalclaw || return 0
    say ""
    step "Installed. Next steps:"
    say "  personalclaw setup      # configure your workspace directory + timezone"
    say "  personalclaw gateway    # start the dashboard on http://localhost:10000"
    say ""
    # Only prompt when we have a real TTY (piped `curl | sh` has none — don't hang).
    if [ -t 0 ]; then
        # The backticks below are prose the USER reads — they quote the command name in the
        # prompt. Single quotes are deliberate: no expansion is wanted, and double quotes
        # would turn `personalclaw setup` into a real command substitution. SC2016 flags the
        # shape without knowing that, so it is silenced on the next line only.
        # NOTE: a shellcheck directive must be a line of its own — trailing prose after the
        # key=value is parsed as another pair and errors with SC1125.
        # shellcheck disable=SC2016
        printf 'Run `personalclaw setup` now? [y/N] '
        read -r reply || reply=n
        case "$reply" in
            [Yy] | [Yy][Ee][Ss]) exec personalclaw setup ;;
            *) say "Skipped. Run 'personalclaw setup' when you're ready." ;;
        esac
    else
        say "${_dim}(non-interactive install — run 'personalclaw setup' yourself.)${_reset}"
    fi
}

main() {
    for arg in "$@"; do
        case "$arg" in
            --container)
                print_container
                exit 0
                ;;
            -h | --help)
                say "Usage: install.sh [--container]"
                say "  (no args)    install personalclaw via uv"
                say "  --container  print the Docker Compose snippet instead"
                exit 0
                ;;
            *) die "unknown argument: $arg (try --help)" ;;
        esac
    done

    say "${_bold}PersonalClaw installer${_reset}"
    detect_platform
    ensure_uv
    install_personalclaw
    offer_setup
}

main "$@"
