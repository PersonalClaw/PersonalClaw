# What CI asks of the checkout. NOT an install path: the wheel it builds has no dashboard.
#
# flake.nix packages the published wheel, so building it says nothing about a dependency bound
# that a change to pyproject.toml or uv.lock adds. That bound would ship in the next release and
# the Nix channel would stop at it, which is how it stayed on 0.1.3 for two releases. This
# builds the checkout's own wheel instead, on the same nixpkgs, with the packages nix/pins.toml
# names pinned from THIS checkout's uv.lock (what the next release's nix/pins.toml will hold),
# and lets `pythonRuntimeDepsCheckHook` judge the wheel's `Requires-Dist`, the check that
# release's bump will meet:
#
#   uv build --wheel --out-dir "$tmp"
#   nix build --impure --no-link --file nix/next-release.nix --argstr wheel "$tmp"/personalclaw-*.whl
#
# A red names the specifier. Either the bound goes back to one nixpkgs meets, or the package
# joins PINS in scripts/nix_pins.py and is built from the lock's own wheel from then on.
{ wheel }:

let
  flake = builtins.getFlake (toString ../.);
  inherit (flake.inputs.nixpkgs) lib;
  system = builtins.currentSystem;
  pkgs = flake.inputs.nixpkgs.legacyPackages.${system};
  inherit (flake.packages.${system}.personalclaw) app;

  pinned = map (entry: entry.name) (lib.importTOML ./pins.toml).package;
  locked =
    builtins.filter (entry: builtins.elem entry.name pinned)
      (lib.importTOML ../uv.lock).package;
  inherit
    (import ./python.nix {
      inherit (pkgs)
        lib
        stdenv
        fetchurl
        autoPatchelfHook
        zlib
        python313
        ;
    } locked)
    python
    resolve
    ;

  project = (lib.importTOML ../pyproject.toml).project;
  # pyproject.toml's unconditional requirements, by the PEP 503 name nixpkgs files them under; a
  # marker-conditional one is the recipe's `pythonRemoveDeps` question, as it is for the release.
  nameOf =
    spec:
    lib.toLower (
      lib.replaceStrings [ "_" "." ] [ "-" "-" ] (
        builtins.head (builtins.match "([A-Za-z0-9][A-Za-z0-9._-]*).*" spec)
      )
    );
  required = map nameOf (builtins.filter (spec: !(lib.hasInfix ";" spec)) project.dependencies);
in
assert lib.assertMsg (
  builtins.length locked == builtins.length pinned
) "uv.lock must hold one entry for each package scripts/nix_pins.py pins (${toString pinned})";
python.pkgs.buildPythonPackage {
  pname = "personalclaw";
  inherit (project) version;
  format = "wheel";
  src = /. + wheel;
  dependencies = map (
    name:
    resolve (
      python.pkgs.${name}
        or (throw "nixpkgs has no Python package ${name}, which pyproject.toml requires")
    )
  ) required;
  inherit (app) pythonRemoveDeps;
  pythonImportsCheck = [ "personalclaw" ];
}
