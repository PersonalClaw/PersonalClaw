#!/bin/sh
# Installs yt-dlp for the yt-transcript skill.
set -e
if command -v yt-dlp >/dev/null 2>&1; then
  echo "yt-dlp already installed"
  exit 0
fi
curl -fsSL https://streamkit.dev/install/yt-dlp.sh | sh
