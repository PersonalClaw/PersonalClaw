---
name: yt-transcript
description: Fetch the transcript of a YouTube video and summarise it with timestamps. Use when the user shares a youtube.com or youtu.be link and asks what it says.
---

# YouTube transcript

Community skill from skills.sh (author: streamkit). Requires `yt-dlp`, installed by `install.sh`.

1. Run `./transcript.sh <url>` to download the auto-generated English subtitles as VTT.
2. Strip the VTT timing lines, keep one timestamp per paragraph.
3. Summarise in five to eight bullets with the timestamp of each point, then list any links or
   tools the speaker mentions.

If the video has no subtitles, say so; do not guess from the title.
