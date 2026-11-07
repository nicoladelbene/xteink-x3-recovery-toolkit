#!/bin/bash
# image-card.sh — read-only, resumable block image of a (possibly failing) card with GNU ddrescue,
# plus optional extra raw reads of the card's first N MiB for multi-read reconstruction.
#
# The card is only ever READ. Default is a DRY RUN that identifies the card and prints the plan.
#
#   scripts/macos/image-card.sh --disk disk4 --expect-size 31268536320 --out ./backup
#   sudo scripts/macos/image-card.sh --disk disk4 --expect-size 31268536320 --out ./backup --execute
#   sudo scripts/macos/image-card.sh --disk disk4 --expect-size 31268536320 --out ./backup \
#        --extra-reads 6 --extra-mib 96 --execute
#
# Guards: whole disk, external, removable, size equals --expect-size exactly, nothing mounted,
# --not-size (e.g. your replacement card's size) refused, output directory on INTERNAL storage
# with >= 3x the card size free, never overwrites an image that has no ddrescue mapfile.
# Pass 1 copies everything readable without retries (-n); a retry pass (-r2) runs only if pass 1
# left less than 0.5% unread — hammering a dying card with retries can make it worse.
# Tip: run scripts/macos/mount-blocker first so macOS never mounts (and writes to) the card.
set -Eeuo pipefail
usage() { sed -n '2,19p' "$0"; exit 2; }
DISK="" EXPECT="" OUT="" NOTSIZE="" EXECUTE=0 EXTRA=0 EXTRA_MIB=96 PAUSE=60
while (($#)); do
  case "$1" in
    --disk) DISK="$2"; shift 2;;
    --expect-size) EXPECT="$2"; shift 2;;
    --not-size) NOTSIZE="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --extra-reads) EXTRA="$2"; shift 2;;
    --extra-mib) EXTRA_MIB="$2"; shift 2;;
    --pause) PAUSE="$2"; shift 2;;
    --execute) EXECUTE=1; shift;;
    -h|--help) usage;;
    *) echo "unknown argument: $1"; exit 2;;
  esac
done
die() { echo "ABORT: $*"; exit 3; }
[[ "$DISK" =~ ^disk[0-9]+$ ]] || { echo "--disk must look like disk4"; exit 2; }
[[ "$EXPECT" =~ ^[0-9]+$ ]] || { echo "--expect-size BYTES is required"; exit 2; }
[[ -n "$OUT" ]] || { echo "--out DIR is required"; exit 2; }
[[ "$EXTRA" =~ ^[0-9]+$ && "$EXTRA" -le 10 && "$EXTRA_MIB" =~ ^[0-9]+$ && "$PAUSE" =~ ^[0-9]+$ ]] \
  || { echo "bad --extra-reads (0..10) / --extra-mib / --pause"; exit 2; }
command -v diskutil >/dev/null || { echo "macOS only (diskutil)"; exit 2; }

check_identity() {
  local INFO SIZE
  INFO=$(diskutil info "$DISK") || die "diskutil cannot see $DISK"
  SIZE=$(sed -nE 's/.*Disk Size:.*\(([0-9]+) Bytes\).*/\1/p' <<<"$INFO")
  grep -qE 'Whole: +Yes' <<<"$INFO" || die "$DISK is not a whole disk"
  grep -qE 'Device Location: +External' <<<"$INFO" || die "$DISK is not external"
  grep -qE 'Removable Media: +Removable' <<<"$INFO" || die "$DISK is not removable media"
  [[ "$SIZE" == "$EXPECT" ]] || die "size ${SIZE:-?} != expected $EXPECT"
  [[ -z "$NOTSIZE" || "$SIZE" != "$NOTSIZE" ]] || die "this card has the --not-size size"
  if mount | grep -qE "^/dev/${DISK}(s[0-9]+)? "; then die "part of $DISK is mounted"; fi
  echo "identity OK: /dev/$DISK external removable $SIZE B, not mounted"
}
check_identity
mkdir -p "$OUT"
OUTDEV=$(df "$OUT" | awk 'NR==2{print $1}')
diskutil info "$OUTDEV" | grep -qE 'Device Location: +Internal' || die "$OUT is not on internal storage"
AVAIL=$(df -k "$OUT" | awk 'NR==2{print $4*1024}')
(( AVAIL > EXPECT * 3 )) || die "need 3x the card size free in $OUT"
IMG="$OUT/card.img"; MAP="$OUT/card.map"
[[ -e "$IMG" && ! -e "$MAP" ]] && die "$IMG exists without a mapfile; refusing to overwrite"
echo "== plan: ddrescue /dev/r$DISK -> $IMG (mapfile $MAP)$( ((EXTRA)) && echo "; then $EXTRA extra reads of the first $EXTRA_MIB MiB")"
if ((EXECUTE == 0)); then echo "== DRY RUN: nothing was read or written. Add --execute (as root)."; exit 0; fi
[[ $EUID -eq 0 ]] || { echo "reading /dev/r$DISK needs root (sudo)"; exit 2; }
command -v ddrescue >/dev/null || { echo "GNU ddrescue is required (brew install ddrescue)"; exit 2; }

LOG="$OUT/imaging-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
trap 'chown -R "${SUDO_USER:-root}" "$OUT" 2>/dev/null || true' EXIT
diskutil list "$DISK" > "$OUT/partition-table.txt"
echo "== pass 1: everything readable, no scraping, no retries"
ddrescue -n -b 512 -s "$EXPECT" -T 5m "/dev/r$DISK" "$IMG" "$MAP"
UNREC=$(ddrescuelog -s "$EXPECT" -l'?*/-' -b 512 "$MAP" | wc -l | awk '{print $1*512}')
echo "unread after pass 1: $UNREC bytes"
if (( UNREC > 0 )); then
  (( UNREC * 1000000 / EXPECT > 5000 )) && { echo "== STOP: >0.5% unread. Reassess before retrying."; exit 10; }
  echo "== pass 2: scrape + at most 2 retries"
  ddrescue -r2 -b 512 -s "$EXPECT" -T 5m "/dev/r$DISK" "$IMG" "$MAP"
fi
ddrescuelog -t "$MAP"
[[ $(wc -c < "$IMG") -eq $EXPECT ]] || echo "WARNING: image size differs from the card size"
( cd "$OUT" && shasum -a 256 card.img | tee card.img.sha256 )
chmod a-w "$IMG"

# Zero read errors do NOT mean correct data: a degraded card can return silently different
# bytes on each read. Extra reads expose that (compare with: python -m x3toolkit reads-diff).
mkdir -p "$OUT/reads"
for ((k = 2; k <= EXTRA + 1; k++)); do
  check_identity
  R=$(printf "%s/reads/read-%02d.bin" "$OUT" "$k")
  [[ -e "$R" ]] && die "$R exists"
  echo "-- extra read $k: first $EXTRA_MIB MiB"
  dd if="/dev/r$DISK" of="$R" bs=1048576 count="$EXTRA_MIB"
  [[ $(wc -c < "$R") -eq $((EXTRA_MIB * 1048576)) ]] || die "short read"
  ( cd "$OUT/reads" && shasum -a 256 "$(basename "$R")" | tee "$(basename "$R").sha256" )
  (( k <= EXTRA )) && { echo "   cooling ${PAUSE}s"; sleep "$PAUSE"; }
done
echo "== done. The card was only read."
