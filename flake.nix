# Nix flake — the `nix run` / `nix profile install` convenience channel.
#
#     nix run .#personalclaw -- --version
#     nix profile install github:PersonalClaw/PersonalClaw#personalclaw
#
# WHAT THIS PACKAGES — the PUBLISHED RELEASE, not this working tree. That is deliberate and
# it is the same choice the Homebrew formula makes (PersonalClaw/homebrew-tap): a
# convenience channel exists to hand someone a working install of a release, and this
# repository's sources alone cannot produce one. The dashboard SPA lives in the wheel, built
# by `make web-build` from `web/` with Node; a Python build of the bare checkout produces a
# gateway that serves no dashboard. Packaging the wheel means the SPA arrives with it.
#
# So `nix run .#personalclaw` runs the version in `nix/personalclaw.nix`, NOT your checkout.
# To run a checkout, use the development install (`pip install -e ".[dev]"`, then
# `make serve`) — see CONTRIBUTING.md. There is deliberately no second flake output that
# half-builds the tree; one path that works beats two where one is a trap.
#
# Unlike the Homebrew formula, this IS hermetic: every dependency is a nixpkgs derivation
# pinned by `flake.lock`, or, for the few nixpkgs cannot supply at a version the release
# accepts, the very wheel the release's `uv.lock` pins, checked against its sha256
# (nix/pins.toml). So two builds of the same tree produce the same closure.
{
  description = "PersonalClaw - self-hosted personal AI agent (chat via Slack, dashboard, or CLI)";

  inputs = {
    # A RELEASE branch, not `nixos-unstable`, and the choice is load-bearing. The packaged
    # artifact is a released wheel whose `Requires-Dist` bounds are frozen at release time,
    # so the package set has to be one those bounds accept. Measured 2026-09-23:
    # `nixos-unstable` carries reportlab 5.0.1 against the wheel's `reportlab<5`, and
    # `nixos-25.11` carries tree-sitter-language-pack 0.10.0 against its `>=1.0` — the two
    # failures point in opposite directions, so "just track unstable" and "just pin older"
    # are both wrong. `nixos-26.05` satisfied all 21 of 0.1.3's unconditional requirements;
    # for 0.2.1's 30 it meets all but four, and those four are built from the release's own
    # wheels instead (nix/pins.toml says why for each), never relaxed.
    #
    # Consequence for a release bump: re-check this pin against the NEW wheel's metadata.
    # `pythonRuntimeDepsCheckHook` fails the build and names the offending specifier, so the
    # answer is measured rather than guessed — see nix/personalclaw.nix. The `nix` workflow
    # asks the same question of every change to pyproject.toml or uv.lock, before a release.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      # Linux ARM is first because it is what this project's fresh-install validation runs on
      # (scripts/fresh_install_validate.sh, in a Finch container).
      #
      # No x86_64-darwin, measured: nixpkgs 26.05 refuses to evaluate this package there,
      # because pdfplumber's tests need pyarrow, which it marks broken on that platform, and
      # 26.05 is the last nixpkgs release that supports it at all. 0.1.3's package did not
      # evaluate there either; listing it offered Intel Macs an install that could not start.
      systems = [
        "aarch64-linux"
        "x86_64-linux"
        "aarch64-darwin"
      ];

      forEachSystem =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f {
            inherit system;
            pkgs = nixpkgs.legacyPackages.${system};
          }
        );
    in
    {
      packages = forEachSystem (
        { pkgs, ... }:
        rec {
          personalclaw = pkgs.callPackage ./nix/personalclaw.nix { };
          default = personalclaw;
        }
      );

      # Declared explicitly rather than left to `nix run`'s fallback onto `packages`, so the
      # entry point is a stated contract instead of a guess about `meta.mainProgram`.
      apps = forEachSystem (
        { system, ... }:
        rec {
          personalclaw = {
            type = "app";
            program = "${self.packages.${system}.personalclaw}/bin/personalclaw";
            inherit (self.packages.${system}.personalclaw) meta;
          };
          default = personalclaw;
        }
      );

      # `nix flake check` runs the change's own acceptance clause rather than only type-checking
      # the outputs, so the flake cannot pass its gate while failing the thing it exists for.
      checks = forEachSystem (
        { system, pkgs, ... }:
        let
          personalclaw = self.packages.${system}.personalclaw;
        in
        {
          version-prints = pkgs.runCommand "personalclaw-version-prints" { } ''
            ${personalclaw}/bin/personalclaw --version | tee "$out"
            grep -qx 'personalclaw ${personalclaw.version}' "$out"
          '';

          # The install works on a scratch home: the doctor finds what PersonalClaw's own
          # Python children import, the gateway boots and serves the dashboard, and the agent's
          # own MCP server comes up. scripts/nix_smoke.py says what each step asserts and why.
          # Its report goes to the build log; the output is only the mark that it passed, so
          # it is the same on every run (the report names the gateway's ephemeral port).
          serves = pkgs.runCommand "personalclaw-serves" { } ''
            export HOME="$TMPDIR"
            ${pkgs.python313.interpreter} ${./scripts/nix_smoke.py} \
              ${personalclaw}/bin/personalclaw ${personalclaw.version}
            touch "$out"
          '';
        }
      );
    };
}
