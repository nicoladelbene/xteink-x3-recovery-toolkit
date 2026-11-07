#!/bin/bash
# make-manifest.sh STAGE_DIR DEST_PREFIX — write STAGE_DIR/stage-manifest.csv for every file under
# STAGE_DIR, mapping STAGE_DIR/<rel> to DEST_PREFIX/<rel> on the card. Read-only except that one file.
#   examples/make-manifest.sh ./stage /
set -euo pipefail
STAGE="${1:?usage: make-manifest.sh STAGE_DIR DEST_PREFIX}"; PREFIX="${2:?}"; PREFIX="${PREFIX%/}"
cd "$STAGE"
TMP=$(mktemp)
sha() { if command -v shasum >/dev/null; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1; }
{
  echo "source,dest,size,sha256"
  find . -type f ! -name stage-manifest.csv ! -name '.DS_Store' ! -name '._*' | sed 's|^\./||' | LC_ALL=C sort |
  while IFS= read -r rel; do
    case "$rel" in *,*|*\"*) echo "refusing name with comma/quote: $rel" >&2; exit 2;; esac
    echo "$rel,$PREFIX/$rel,$(wc -c < "$rel" | tr -d ' '),$(sha "$rel")"
  done
} > "$TMP"
mv "$TMP" stage-manifest.csv
echo "wrote $STAGE/stage-manifest.csv ($(($(wc -l < stage-manifest.csv) - 1)) files)"
