#!/usr/bin/env bash
# Writes a spoken test sentence as a mono 16-bit WAV using espeak-ng (sudo apt install espeak-ng),
# for: python -m talk_to_mic --transcribe <file>. The Linux counterpart of make-sample-wav.ps1;
# espeak-ng writes 22.05 kHz, which talk-to-mic resamples to 16 kHz when it loads the file.
# Usage: scripts/make-sample-wav.sh [PATH] [TEXT]
set -euo pipefail

path="${1:-sample.wav}"
text="${2:-Um, so I think we should, uh, meet on Tuesday. No wait, I meant Wednesday.}"

command -v espeak-ng >/dev/null || { echo "espeak-ng is missing: sudo apt install espeak-ng" >&2; exit 1; }
mkdir -p "$(dirname "$path")"
espeak-ng -s 150 -w "$path" "$text"
echo "Wrote $path"
