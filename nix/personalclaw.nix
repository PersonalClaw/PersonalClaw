# The `personalclaw` derivation for flake.nix. Read the header comment there first — in
# particular, this packages the PUBLISHED WHEEL, not the surrounding checkout.
#
# A release bump changes `version`, the `hash` beside it, nix/pins.toml, AND possibly this
# dependency list: it must match the *released* wheel's `Requires-Dist`, which is not the same
# thing as the bounds currently in `pyproject.toml`. Measured on 0.1.3: the wheel asks for
# `croniter<7` and `reportlab<5` where today's pyproject says `croniter<3` and `reportlab<6`,
# and the wheel does not declare `sqlite-vec`, `cryptography` or `jsonschema` at all — all
# three became declared dependencies after the release. Read the artifact, not the tree:
#
#   nix store prefetch-file --json <wheel url>          # the hash
#   unzip -p <wheel> personalclaw-<V>.dist-info/METADATA | grep '^Requires-Dist'
#   python3 scripts/nix_pins.py                         # nix/pins.toml, from the tag's uv.lock
#
# `pythonRuntimeDepsCheckHook` enforces the agreement, so a missed bound is a build failure
# naming the offending specifier, not a silently wrong install, and the `nix` workflow builds
# this on every change that could move one.
{
  lib,
  stdenv,
  fetchurl,
  runCommand,
  autoPatchelfHook,
  python313,
}:

let
  # nixpkgs' python313, with the packages nixpkgs cannot supply at a version this wheel accepts
  # built from the wheels the release's uv.lock pins: croniter, sqlite-vec,
  # tree-sitter-language-pack and urllib3, each for the reason nix/pins.toml gives beside it.
  # Pinned that way they ARE the release's dependencies, the same files `uv sync --locked`
  # installs, never a newer or older version that happens to satisfy the bounds; the bounds
  # themselves are load-bearing (pyproject.toml says why for each), so relaxing one with
  # `pythonRelaxDeps` would install a combination the release was never tested with.
  inherit
    (import ./python.nix {
      inherit
        lib
        stdenv
        fetchurl
        autoPatchelfHook
        python313
        ;
    } (lib.importTOML ./pins.toml).package)
    python
    resolve
    ;

  app = python.pkgs.buildPythonPackage {
    pname = "personalclaw";
    version = "0.2.1";
    format = "wheel";

    src = fetchurl {
      url = "https://files.pythonhosted.org/packages/eb/bf/c151e5151e184607a17083a2065d1b1b3d732ee1b63a1a1c637766fdbf49/personalclaw-0.2.1-py3-none-any.whl";
      hash = "sha256-kfCU6wCXdxSlknETdRsT8Dt1WLa6pnMfybKtG2s8W7g=";
    };

    # `pysqlite3-binary` is not in nixpkgs, and it is an OPTIONAL ACCELERATOR, not a
    # requirement: `src/personalclaw/sqlite_compat.py` imports it in a `try` and falls back to
    # the stdlib `sqlite3` when absent, reporting which driver resolved through the doctor. It
    # exists because some platforms' bundled SQLite has no FTS5/JSON1 — and nixpkgs' python313
    # links one that does, which `postInstall` below asserts rather than assumes. The wheel's
    # marker only requests it on linux/x86_64 in the first place.
    pythonRemoveDeps = [ "pysqlite3-binary" ];

    # Exactly the wheel's unconditional `Requires-Dist`, in its order, and nothing else.
    # Adding a package the artifact does not declare would give Nix users a capability no other
    # install path has — the kind of per-channel behaviour fork that makes a bug report
    # impossible to read. `resolve` swaps in the pins (nix/python.nix).
    propagatedBuildInputs = map resolve (
      with python.pkgs;
      [
        aiohttp
        websockets
        cron-descriptor
        croniter
        numpy
        snowballstemmer
        packaging
        pip
        sqlite-vec
        python-docx
        pdfplumber
        python-pptx
        html2text
        reportlab
        trafilatura
        nh3
        openpyxl
        pillow
        tree-sitter
        tree-sitter-language-pack
        httpx
        pyyaml
        python-dotenv
        tzdata
        zstandard
        argon2-cffi
        cryptography
        jsonschema
        mcp
        urllib3
      ]
    );

    pythonImportsCheck = [ "personalclaw" ];

    # Two assertions `--version` cannot make:
    #   * the dashboard SPA really travelled inside the wheel. A gateway serving no dashboard
    #     is the exact failure that made this flake package the wheel instead of the tree, and
    #     it is invisible to every CLI check.
    #   * the interpreter's SQLite has FTS5 — the premise of dropping `pysqlite3-binary`
    #     above. Without it the knowledge and memory search paths degrade to table scans.
    postInstall = ''
      spa="$out/${python.sitePackages}/personalclaw/static/dist"
      count="$(find "$spa" -type f 2>/dev/null | wc -l)"
      echo "SPA files: $count"
      if [ "$count" -eq 0 ]; then
        echo "no dashboard SPA bundled at $spa" >&2
        exit 1
      fi

      ${python.interpreter} -c \
        'import sqlite3; sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)"); print("sqlite", sqlite3.sqlite_version, "has FTS5")'
    '';

    meta = {
      description = "Self-hosted personal AI agent - chat via Slack, dashboard, or CLI";
      homepage = "https://personalclaw.dev";
      changelog = "https://github.com/PersonalClaw/PersonalClaw/blob/main/CHANGELOG.md";
      license = lib.licenses.mit;
      mainProgram = "personalclaw";
      platforms = lib.platforms.unix;
    };
  };

  # PersonalClaw starts Python children with `sys.executable` and they import what it imports:
  # each Python app backend, the agent's own MCP server (`python -m personalclaw...`), the
  # provider probes, and the app installer's `python -m pip`. nixpkgs' application wrapper puts
  # the dependencies on `sys.path` inside its one process and leaves `sys.executable` the bare
  # interpreter, so on 0.2.1 every such child failed with "No module named personalclaw" and
  # `personalclaw doctor` read the dependencies and pip as missing (0.1.3's doctor already did).
  # An environment of the package and its closure makes `sys.executable` that environment's
  # interpreter, the way a venv does for pip. Only `bin/personalclaw` is installed from it: the
  # environment's other programs (`python3.13`, `pip`, `uvicorn`, ...) would otherwise land on
  # the PATH of whoever installs this.
  env = python.withPackages (_: [ app ]);
in
runCommand "personalclaw-${app.version}"
  {
    inherit (app) version meta;
    passthru = { inherit app; };
  }
  ''
    mkdir -p "$out/bin"
    ln -s ${env}/bin/personalclaw "$out/bin/personalclaw"
  ''
