#!/bin/sh
# Download English auto-subtitles for a YouTube URL as VTT into a temp dir and print the path.
set -e
url="$1"
[ -n "$url" ] || { echo "usage: transcript.sh <url>" >&2; exit 2; }
out=$(mktemp -d)
yt-dlp --skip-download --write-auto-subs --sub-langs en --sub-format vtt -o "$out/%(id)s" "$url" >/dev/null
ls "$out"/*.vtt
