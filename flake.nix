# Nix flake — the `nix run` / `nix profile install` convenience channel (DISTRIBUTION T5.2).
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
# pinned by `flake.lock`, so two builds of the same lock produce the same closure.
{
  description = "PersonalClaw - self-hosted personal AI agent (chat via Slack, dashboard, or CLI)";

  inputs = {
    # A RELEASE branch, not `nixos-unstable`, and the choice is load-bearing. The packaged
    # artifact is a released wheel whose `Requires-Dist` bounds are frozen at release time,
    # so the package set has to be one those bounds accept. Measured 2026-09-23:
    # `nixos-unstable` carries reportlab 5.0.1 against the wheel's `reportlab<5`, and
    # `nixos-25.11` carries tree-sitter-language-pack 0.10.0 against its `>=1.0` — the two
    # failures point in opposite directions, so "just track unstable" and "just pin older"
    # are both wrong. `nixos-26.05` satisfies all 21 unconditional requirements.
    #
    # Consequence for a release bump: re-check this pin against the NEW wheel's metadata.
    # `pythonRuntimeDepsCheckHook` fails the build and names the offending specifier, so the
    # answer is measured rather than guessed — see nix/personalclaw.nix.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      # Linux ARM is first because it is what this project's fresh-install validation runs on
      # (scripts/fresh_install_validate.sh, in a Finch container).
      systems = [
        "aarch64-linux"
        "x86_64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
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
          };
          default = personalclaw;
        }
      );

      # `nix flake check` runs the atom's own acceptance clause rather than only type-checking
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
        }
      );
    };
}
