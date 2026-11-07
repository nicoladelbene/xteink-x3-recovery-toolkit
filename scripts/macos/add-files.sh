#!/bin/bash
# add-files.sh — add planned files to a FAT32 card WITHOUT rewriting anything already on it.
#
# Default is a DRY RUN: it identifies the target and prints the plan, and writes nothing.
#
#   Dry run (device):     scripts/macos/add-files.sh --disk disk4 --expect-size 63998787584 \
#                           --volume-name "NO NAME" --partition-offset 4194304 --stage ./stage
#   Rehearsal (image):    scripts/macos/add-files.sh --image card.img --partition-offset 4194304 \
#                           --stage ./stage --backup ./snapshots --execute
#   Real write (device):  sudo scripts/macos/add-files.sh --disk disk4 ... --backup ./snapshots \
#                           --execute --confirm "WRITE disk4"
#
# The stage directory holds the files plus stage-manifest.csv (columns source,dest,size,sha256;
# source relative to the stage dir, dest an absolute path on the card).
#
# What --execute does, in order (any failure stops it; the card is ejected ONLY on PASS):
#   1. identity: whole disk, external, removable, exact byte size, FAT32 partition at the given
#      offset with the given volume name, not mounted (device mode)
#   2. rollback snapshot of the used region; second read must match (device mode)
#   3. pre-write gate: snapshot parses via both FATs with 0 anomalies; no planned path exists
#   4. identity re-check, then additions with `mcopy -D s` (skip, never overwrite); optional
#      removal of macOS metadata dirs (/.Spotlight-V100 /.fseventsd /.Trashes) only
#   5. after-snapshot, fsck -n (read-only) gate, x3toolkit verify (only planned changes), eject
# It never partitions, formats, relabels, or writes raw sectors.
set -Eeuo pipefail

usage() { sed -n '2,25p' "$0"; exit 2; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DISK="" IMAGE="" EXPECT="" VOLNAME="" POFF="" STAGE="" BACKUP="" EXECUTE=0 CONFIRM="" RMMETA=0
PY="${X3_PYTHON:-python3}"
while (($#)); do
  case "$1" in
    --disk) DISK="$2"; shift 2;;
    --image) IMAGE="$2"; shift 2;;
    --expect-size) EXPECT="$2"; shift 2;;
    --volume-name) VOLNAME="$2"; shift 2;;
    --partition-offset) POFF="$2"; shift 2;;
    --stage) STAGE="$2"; shift 2;;
    --backup) BACKUP="$2"; shift 2;;
    --python) PY="$2"; shift 2;;
    --remove-macos-metadata) RMMETA=1; shift;;
    --execute) EXECUTE=1; shift;;
    --confirm) CONFIRM="$2"; shift 2;;
    -h|--help) usage;;
    *) echo "unknown argument: $1"; exit 2;;
  esac
done

die() { echo "ABORT: $*"; exit 3; }
[[ -n "$DISK" || -n "$IMAGE" ]] || { echo "need --disk diskN or --image FILE"; exit 2; }
[[ -n "$DISK" && -n "$IMAGE" ]] && { echo "use either --disk or --image, not both"; exit 2; }
[[ "$POFF" =~ ^[0-9]+$ && $((POFF % 512)) -eq 0 ]] || { echo "--partition-offset must be a multiple of 512"; exit 2; }
[[ -f "$STAGE/stage-manifest.csv" ]] || { echo "--stage must contain stage-manifest.csv"; exit 2; }
LBA=$((POFF / 512))
awk -F, 'NR > 1 && NF != 4 { exit 1 }' "$STAGE/stage-manifest.csv" \
  || { echo "stage-manifest.csv: every row needs exactly 4 fields (no commas or quotes in names)"; exit 2; }
sha256() { if command -v shasum >/dev/null; then shasum -a 256 | cut -d' ' -f1; else sha256sum | cut -d' ' -f1; fi; }
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -c 'import x3toolkit' 2>/dev/null || { echo "cannot import x3toolkit with $PY (use --python)"; exit 2; }

if [[ -n "$DISK" ]]; then
  [[ "$DISK" =~ ^disk[0-9]+$ ]] || { echo "--disk must look like disk4"; exit 2; }
  [[ "$EXPECT" =~ ^[0-9]+$ ]] || { echo "--expect-size (bytes) is required with --disk"; exit 2; }
  [[ -n "$VOLNAME" ]] || { echo "--volume-name is required with --disk"; exit 2; }
  command -v diskutil >/dev/null || { echo "device mode needs macOS diskutil"; exit 2; }
  TARGET="/dev/${DISK}s1"; RAW="/dev/r${DISK}"
else
  [[ -f "$IMAGE" && ! "$IMAGE" =~ ^/dev/ ]] || { echo "--image must be a regular file"; exit 2; }
  TARGET="$IMAGE@@$POFF"; RAW="$IMAGE"
fi

check_identity() {
  [[ -z "$DISK" ]] && { echo "target: image file $IMAGE (rehearsal)"; return 0; }
  local INFO P1 SIZE
  INFO=$(diskutil info "$DISK") || die "diskutil cannot see $DISK"
  P1=$(diskutil info "${DISK}s1") || die "no ${DISK}s1"
  SIZE=$(sed -nE 's/.*Disk Size:.*\(([0-9]+) Bytes\).*/\1/p' <<<"$INFO")
  grep -qE 'Whole: +Yes' <<<"$INFO" || die "$DISK is not a whole disk"
  grep -qE 'Device Location: +External' <<<"$INFO" || die "$DISK is not external"
  grep -qE 'Removable Media: +Removable' <<<"$INFO" || die "$DISK is not removable media"
  [[ "$SIZE" == "$EXPECT" ]] || die "size ${SIZE:-?} != expected $EXPECT"
  grep -qE 'File System Personality: +MS-DOS FAT32' <<<"$P1" || die "${DISK}s1 is not FAT32"
  grep -qE "Partition Offset: +$POFF Bytes" <<<"$P1" || die "${DISK}s1 is not at offset $POFF"
  grep -qF "Volume Name:" <<<"$P1" && grep -qE "Volume Name: +${VOLNAME}\$" <<<"$P1" || die "volume name is not '$VOLNAME'"
  if mount | grep -qE "^/dev/${DISK}(s[0-9]+)? "; then die "$DISK is mounted (run: diskutil unmountDisk $DISK)"; fi
  echo "identity OK: /dev/$DISK external removable $SIZE B, FAT32 '$VOLNAME' at offset $POFF, not mounted"
}

echo "== target"; check_identity
N=$(($(wc -l < "$STAGE/stage-manifest.csv") - 1))
echo "== plan: add $N files (never overwrite)$( ((RMMETA)) && echo ', remove macOS metadata dirs')"
tail -n +2 "$STAGE/stage-manifest.csv" | head -5 | sed 's/^/   /'; ((N > 5)) && echo "   ... ($N total)"

if ((EXECUTE == 0)); then
  echo "== DRY RUN: nothing was written. Add --backup DIR --execute$([[ -n "$DISK" ]] && echo " --confirm \"WRITE $DISK\" (as root)") to apply."
  exit 0
fi
[[ -n "$BACKUP" ]] || { echo "--backup DIR is required with --execute"; exit 2; }
if [[ -n "$DISK" ]]; then
  [[ "$CONFIRM" == "WRITE $DISK" ]] || { echo "--confirm must be exactly \"WRITE $DISK\""; exit 2; }
  [[ $EUID -eq 0 ]] || { echo "device writes need root (sudo)"; exit 2; }
fi
command -v mcopy >/dev/null || { echo "mtools is required (brew install mtools)"; exit 2; }

OUT="$BACKUP/session-$(date -u +%Y%m%dT%H%M%SZ)"
[[ -e "$OUT" ]] && die "$OUT exists"
mkdir -p "$OUT"
exec > >(tee -a "$OUT/session.log") 2>&1
trap 'echo "== FAILED at line $LINENO: NOT verified, NOT ejected. Rollback snapshot (if taken): $OUT/before.bin"' ERR

snap() {  # $1 output file
  local m h1 h2
  dd if="$RAW" of="$1.head" bs=1048576 count=64 2>/dev/null
  m=$("$PY" -m x3toolkit fat-used "$1.head" --lba "$LBA"); rm -f "$1.head"
  [[ "$m" =~ ^[0-9]+$ && "$m" -ge 1 ]] || die "cannot size the used region"
  if [[ -n "$IMAGE" ]]; then
    local have=$(( ($(wc -c < "$IMAGE") + 1048575) / 1048576 ))
    (( m > have )) && m=$have
  fi
  dd if="$RAW" of="$1" bs=1048576 count="$m" 2>/dev/null
  h1=$(sha256 < "$1")
  h2=$(dd if="$RAW" bs=1048576 count="$m" 2>/dev/null | sha256)
  [[ "$h1" == "$h2" ]] || die "snapshot differs from a second read"
  echo "$h1  $(basename "$1")" > "$1.sha256"
  echo "snapshot $(basename "$1"): $m MiB, verified by a second read"
}

echo "== rollback snapshot"; snap "$OUT/before.bin"
echo "== pre-write gate"; "$PY" -m x3toolkit precheck "$OUT/before.bin" --lba "$LBA" --manifest "$STAGE/stage-manifest.csv"
check_identity
export MTOOLS_SKIP_CHECK=1 LC_ALL="${LC_ALL:-en_US.UTF-8}"
T=(-i "$TARGET")
echo "== WRITES START"
REMOVE=()
if ((RMMETA)); then
  for d in .Spotlight-V100 .fseventsd .Trashes; do
    if mdir "${T[@]}" -a -b :: | grep -qxE "::/$d/?"; then mdeltree "${T[@]}" "::/$d"; echo "removed /$d"; fi
    REMOVE+=(--remove "/$d")
  done
fi
tail -n +2 "$STAGE/stage-manifest.csv" | while IFS=, read -r src dest size sha; do
  dir="${dest%/*}"
  if [[ -n "$dir" ]]; then  # create parent directories one level at a time (skip if present)
    p=""; IFS=/ read -ra parts <<<"${dir#/}"
    for part in "${parts[@]}"; do p="$p/$part"; mmd "${T[@]}" -D s "::$p" 2>/dev/null || true; done
  fi
  mcopy "${T[@]}" -D s -m -Q "$STAGE/$src" "::$dest"
done
sync
echo "== after snapshot"; snap "$OUT/after.bin"

if [[ -n "$DISK" ]] || command -v fsck_msdos >/dev/null; then
  FSCKT="${DISK:+/dev/r${DISK}s1}"
  if [[ -n "$FSCKT" ]]; then
    FSCK=$(fsck_msdos -n "$FSCKT" 2>&1 || true); echo "$FSCK"
    # mtools leaves the FSInfo free-cluster hint stale; that warning is benign
    UNKNOWN=$(grep -vE '^\*\* (/dev/|Phase [1-5])|Free space in FSInfo block|^Fix\? no|files, [0-9]+ KiB free|^$' <<<"$FSCK" || true)
    [[ -z "$UNKNOWN" ]] || die "fsck reported: $UNKNOWN"
  fi
fi

echo "== verify"
"$PY" -m x3toolkit verify "$OUT/before.bin" "$OUT/after.bin" --lba "$LBA" \
  --manifest "$STAGE/stage-manifest.csv" ${REMOVE[@]+"${REMOVE[@]}"}
if [[ -n "$DISK" ]]; then sync; diskutil eject "$DISK"; echo "EJECTED"; fi
echo "== DONE: PASS"
