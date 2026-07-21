#!/bin/sh
# PersonalClaw bootstrap installer — the `curl -fsSL https://personalclaw.dev/install | sh` one-liner.
#
# Plan 34 (DISTRIBUTION) §E / T2.2. THIS FILE BELONGS IN THE WEBSITE REPO
# (personalclaw.dev, plan 36 owns) — it is staged here under deploy/website/ so it
# is version-controlled, shellcheck-clean, and CI-smoke-tested (plan 33 full.yml
# runs it in a bare ubuntu container weekly). Serve it at https://personalclaw.dev/install.
#
# What it does (idempotent — re-running upgrades):
#   1. `--container` → print the Docker Compose snippet and exit (no install).
#   2. Ensure `uv` is present (installs via the official installer if missing).
#   3. `uv tool install --upgrade personalclaw` (uv provides its own Python 3.12).
#   4. Print next steps and offer to run `personalclaw setup`.
#
# POSIX sh only (no bashisms) so it runs under dash/ash/sh on Linux + macOS.

set -eu

PC_PACKAGE="personalclaw"
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
    # The official installer is served over TLS from astral.sh and verifies its
    # own downloads (checksum-pinned per release). Prefer curl, fall back to wget.
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
    # in-place upgrade to the latest release afterwards.
    uv tool install --upgrade "$PC_PACKAGE"
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
    say "  personalclaw setup      # configure your name + first model provider"
    say "  personalclaw gateway    # start the dashboard on http://localhost:10000"
    say ""
    # Only prompt when we have a real TTY (piped `curl | sh` has none — don't hang).
    if [ -t 0 ]; then
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
