# The Python nix/personalclaw.nix builds with: nixpkgs' CPython 3.13 and its package set, as
# nixpkgs built them, except for the packages nixpkgs cannot supply at a version the release
# accepts. Those come from the wheels a uv.lock pins for them.
#
# `pins` is a list of uv.lock `[[package]]` entries: nix/pins.toml (the packaged release's own
# entries) for the package, and the checkout's uv.lock for nix/next-release.nix. Each becomes the
# one wheel it lists that loads on this platform, at the lock's version and checked against the
# lock's sha256, so it is the very file `uv sync --locked` installs. Its dependencies are the
# lock's, by name.
#
# This returns `{ python, resolve }`, and `resolve` is how a pin reaches the package: it maps a
# package of `python.pkgs` to what to install. A pinned package becomes its wheel. A package
# that depends on one at run time, at any depth (trafilatura on urllib3), is rebuilt against it,
# so the closure holds one copy (`pythonCatchConflictsHook` refuses two). Anything else is
# nixpkgs' own build, from its binary cache.
#
# NOT `packageOverrides`, and measured: replacing urllib3 for the whole set changes every
# package whose tests reach it, and in nixpkgs they reach far. A build of this package then had
# 242 derivations to build, torch, scipy and nodejs among them, and 3.9 GiB to download, where
# the other three pins alone cost 8. So only what the package runs is rebuilt against a pin (for
# urllib3: trafilatura, courlan and htmldate), from the source nixpkgs builds it from, without
# the test suite that reached the rest.
#
# These are binary builds by construction: a lock entry names published files. The platform
# wheels (sqlite-vec, tree-sitter-language-pack) carry compiled extensions, which
# `autoPatchelfHook` points at this closure's C libraries on Linux, and they say so in
# `meta.sourceProvenance`.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  python313,
}:

pins:

let
  python = python313;
  inherit (python) pkgs;
  inherit (stdenv.hostPlatform) system;

  # The platform tags (PEP 425) a wheel for this system carries, as a pattern over one tag of
  # the `.`-separated set in its file name.
  platformTag =
    {
      x86_64-linux = "manylinux(1|2010|2014|_[0-9]+_[0-9]+)_x86_64";
      aarch64-linux = "manylinux(2014|_[0-9]+_[0-9]+)_aarch64";
      x86_64-darwin = "macosx_[0-9]+_[0-9]+_(x86_64|intel|universal2)";
      aarch64-darwin = "macosx_[0-9]+_[0-9]+_(arm64|universal2)";
    }
    .${system} or (throw "nix/python.nix knows no wheel platform tag for ${system}");

  cpython = "cp" + lib.replaceStrings [ "." ] [ "" ] python.pythonVersion;
  minor = lib.toInt (lib.versions.minor python.pythonVersion);

  # A wheel file name is `{name}-{version}(-{build})?-{python}-{abi}-{platform}.whl`, and each of
  # the last three may be a `.`-separated set.
  tagsOf =
    url:
    let
      parts = lib.splitString "-" (lib.removeSuffix ".whl" (baseNameOf url));
      at = n: lib.splitString "." (builtins.elemAt parts (builtins.length parts - n));
    in
    {
      python = at 3;
      abi = at 2;
      platform = at 1;
    };

  pure = wheel: builtins.elem "any" (tagsOf wheel.url).platform;

  # An abi3 wheel built for CPython 3.N loads on every later 3.x.
  stableAbiFor = tag: lib.toInt (builtins.head (builtins.match "cp3([0-9]+)" tag)) <= minor;

  loads =
    wheel:
    let
      tags = tagsOf wheel.url;
      has = builtins.elem;
      onPlatform =
        has "any" tags.platform || lib.any (tag: builtins.match platformTag tag != null) tags.platform;
      onPython =
        (has "none" tags.abi && (has "py3" tags.python || has cpython tags.python))
        || (has cpython tags.abi && has cpython tags.python)
        || (
          has "abi3" tags.abi && lib.any stableAbiFor (builtins.filter (lib.hasPrefix "cp3") tags.python)
        );
    in
    onPlatform && onPython;

  # The lock's wheel for this platform. A platform wheel wins over a pure one, as with pip; two
  # left over is a choice this file will not make for you.
  wheelOf =
    pin:
    let
      fits = builtins.filter loads (pin.wheels or [ ]);
      native = builtins.filter (wheel: !pure wheel) fits;
      chosen = if native != [ ] then native else fits;
    in
    if builtins.length chosen == 1 then
      builtins.head chosen
    else
      throw "uv.lock lists ${toString (builtins.length chosen)} wheels of ${pin.name} ${pin.version} that load on ${system} (${cpython}), and nix/python.nix builds from exactly one: ${
        lib.concatMapStringsSep ", " (wheel: baseNameOf wheel.url) chosen
      }";

  fromWheel =
    pin:
    let
      wheel = wheelOf pin;
      patch = !pure wheel && stdenv.hostPlatform.isLinux;
    in
    pkgs.buildPythonPackage {
      pname = pin.name;
      inherit (pin) version;
      format = "wheel";
      src = fetchurl {
        inherit (wheel) url;
        # uv writes the digest PyPI published as `sha256:<hex>`.
        sha256 = lib.removePrefix "sha256:" wheel.hash;
      };
      # A marker-conditional dependency is the wheel's own runtime-deps check to judge: it names
      # one that applies here and is missing.
      dependencies = map (dependency: resolve pkgs.${dependency.name}) (
        builtins.filter (dependency: !(dependency ? marker)) (pin.dependencies or [ ])
      );
      # The extras nixpkgs declares for the package this replaces, which other packages name
      # (`urllib3.optional-dependencies.socks`); nothing installs one unless asked.
      optional-dependencies = pkgs.${pin.name}.optional-dependencies or { };
      nativeBuildInputs = lib.optionals patch [ autoPatchelfHook ];
      buildInputs = lib.optionals patch [ stdenv.cc.cc.lib ];
      pythonImportsCheck = [ (lib.replaceStrings [ "-" ] [ "_" ] pin.name) ];
      meta = lib.optionalAttrs (!pure wheel) {
        sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
      };
    };

  # uv.lock names a package as PEP 503 does; nixpkgs' `pname` is the distribution's own name.
  normalize = name: lib.toLower (lib.replaceStrings [ "_" "." ] [ "-" "-" ] name);
  pinned = lib.listToAttrs (map (pin: lib.nameValuePair pin.name (fromWheel pin)) pins);
  pinOf = drv: if drv ? pname then pinned.${normalize drv.pname} or null else null;
  holdsAPin = drv: lib.any (module: pinOf module != null) (pkgs.requiredPythonModules [ drv ]);

  resolve =
    drv:
    if pinOf drv != null then
      pinOf drv
    else if drv ? overridePythonAttrs && holdsAPin drv then
      drv.overridePythonAttrs (old: {
        dependencies = map resolve (old.dependencies or [ ]);
        propagatedBuildInputs = map resolve (old.propagatedBuildInputs or [ ]);
        doCheck = false;
      })
    else
      drv;
in
{
  inherit python resolve;
}
