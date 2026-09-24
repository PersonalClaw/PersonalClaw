# The `personalclaw` derivation for flake.nix. Read the header comment there first — in
# particular, this packages the PUBLISHED WHEEL, not the surrounding checkout.
#
# A release bump changes `version`, the `hash` beside it, AND possibly this dependency list:
# it must match the *released* wheel's `Requires-Dist`, which is not the same thing as the
# bounds currently in `pyproject.toml`. Measured on 0.1.3: the wheel asks for `croniter<7`
# and `reportlab<5` where today's pyproject says `croniter<3` and `reportlab<6`, and the
# wheel does not declare `sqlite-vec`, `cryptography` or `jsonschema` at all — all three
# became declared dependencies after the release. Read the artifact, not the tree:
#
#   nix store prefetch-file --json <wheel url>          # the hash
#   unzip -p <wheel> personalclaw-<V>.dist-info/METADATA | grep '^Requires-Dist'
#
# `pythonRuntimeDepsCheckHook` enforces the agreement, so a missed bound is a build failure
# naming the offending specifier, not a silently wrong install.
{
  lib,
  fetchurl,
  python313,
  python313Packages,
}:

python313Packages.buildPythonApplication {
  pname = "personalclaw";
  version = "0.1.3";
  format = "wheel";

  src = fetchurl {
    url = "https://files.pythonhosted.org/packages/b7/16/15f5d4ef217f327cb119a2e033f408ff7839473807ff818b35671ba6135f/personalclaw-0.1.3-py3-none-any.whl";
    hash = "sha256-Vy5oVsPk1egjIvYFHhUVQ4qIh0qgt3iV4nXtMvNdibI=";
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
  # impossible to read.
  propagatedBuildInputs = with python313Packages; [
    aiohttp
    websockets
    cron-descriptor
    croniter
    numpy
    snowballstemmer
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
    argon2-cffi
  ];

  pythonImportsCheck = [ "personalclaw" ];

  # Two assertions `--version` cannot make:
  #   * the dashboard SPA really travelled inside the wheel. A gateway serving no dashboard
  #     is the exact failure that made this flake package the wheel instead of the tree, and
  #     it is invisible to every CLI check.
  #   * the interpreter's SQLite has FTS5 — the premise of dropping `pysqlite3-binary`
  #     above. Without it the knowledge and memory search paths degrade to table scans.
  postInstall = ''
    spa="$out/${python313.sitePackages}/personalclaw/static/dist"
    count="$(find "$spa" -type f 2>/dev/null | wc -l)"
    echo "SPA files: $count"
    if [ "$count" -eq 0 ]; then
      echo "no dashboard SPA bundled at $spa" >&2
      exit 1
    fi

    ${python313.interpreter} -c \
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
}
