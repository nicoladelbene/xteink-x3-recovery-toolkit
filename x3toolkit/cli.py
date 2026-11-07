"""Command-line interface: ``python -m x3toolkit <command> ...``.

Every command here works on *image files* and is read-only with respect to its
inputs. Nothing in this CLI opens a block device for writing; writing to a card
is done only by the guarded shell scripts in ``scripts/macos``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mmap
import os
import sys

from . import __version__
from .bmp import validate_sleep_bmp
from .epub import check_epub
from .esp_image import CHIP_IDS, validate_file
from .fat32 import Fat32Error, Fat32Volume, compare_fat_copies, used_region_end
from .reconstruct import differing_sectors, majority_vote, repair_zip, sector_versions
from .verify import Plan, compare, walk_both_fats

CHIP_BY_NAME = {v.lower().replace("-", ""): k for k, v in CHIP_IDS.items()}


def _refuse_device(path: str) -> None:
    if path.startswith("/dev/"):
        sys.exit(f"refusing to open {path}: this tool works on image files only")


def _out_path(path: str, force: bool) -> None:
    if os.path.exists(path) and not force:
        sys.exit(f"refusing to overwrite {path} (use --force)")


def cmd_fat_ls(a) -> int:
    _refuse_device(a.image)
    vol = Fat32Volume.open(a.image, a.lba, a.fat)
    ents = vol.walk(hash_files=not a.no_hash)
    diff = compare_fat_copies(vol)
    rows = [{"path": e.path, "type": "dir" if e.is_dir else "file", "size": e.size,
             "hidden": int(e.hidden), "system": int(e.system), "first_cluster": e.first_cluster,
             "chain_ok": int(e.chain_ok), "mtime": e.mtime.isoformat() if e.mtime else "",
             "sha256": e.sha256} for e in ents]
    if a.csv:
        _out_path(a.csv, a.force)
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, list(rows[0]) if rows else ["path"])
            w.writeheader()
            w.writerows(rows)
    else:
        for r in rows:
            print(f"{r['type']:4} {r['size']:>12} {r['path']}")
    files = [e for e in ents if not e.is_dir]
    print(f"# FAT{a.fat}: {len(ents) - len(files)} dirs, {len(files)} files, "
          f"{sum(e.size for e in files)} bytes, {len(vol.anomalies)} anomalies, "
          f"{len(diff)} FAT entries differ between copies", file=sys.stderr)
    for x in vol.anomalies[:50]:
        print(f"# ANOMALY {x}", file=sys.stderr)
    return 1 if vol.anomalies else 0


def cmd_fat_extract(a) -> int:
    _refuse_device(a.image)
    if os.path.exists(a.outdir) and os.listdir(a.outdir):
        sys.exit(f"{a.outdir} is not empty")
    vol = Fat32Volume.open(a.image, a.lba, a.fat)
    real_out = os.path.realpath(a.outdir)
    n = 0
    for e in vol.walk(hash_files=False):
        dest = os.path.realpath(os.path.join(a.outdir, e.path.lstrip("/")))
        if not dest.startswith(real_out + os.sep):
            print(f"skip unsafe path {e.path!r}", file=sys.stderr)
            continue
        if e.is_dir:
            os.makedirs(dest, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(vol.read_file(e))
        if e.mtime:
            os.utime(dest, (e.mtime.timestamp(), e.mtime.timestamp()))
        n += 1
    print(f"extracted {n} files, {len(vol.anomalies)} anomalies")
    return 1 if vol.anomalies else 0


def cmd_epub_check(a) -> int:
    fails = 0
    for p in a.files:
        r = check_epub(open(p, "rb").read())
        fails += not r.ok
        print(f"{'PASS' if r.ok else 'FAIL'}  {p}  {r.detail}")
        for m in r.bad_members[:10]:
            print(f"      bad member: {m}")
    print(f"# {len(a.files) - fails}/{len(a.files)} pass", file=sys.stderr)
    return 1 if fails else 0


def _load_reads(paths, max_bytes=None):
    """Memory-map raw reads read-only (full card images can be tens of GB)."""
    out = []
    for p in paths:
        _refuse_device(p)
        with open(p, "rb") as f:
            m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        out.append(m[:max_bytes] if max_bytes else m)
    return out


def cmd_reads_diff(a) -> int:
    reads = _load_reads(a.reads, a.mib * 1048576 if a.mib else None)
    n = min(len(r) for r in reads)
    base = reads[0]
    for p, r in zip(a.reads[1:], reads[1:]):
        secs = differing_sectors([base, r], 0, n)
        print(f"{p}: {len(secs)} sectors differ from {a.reads[0]}")
    allsec = differing_sectors([r[:n] for r in reads])
    print(f"sectors where not all {len(reads)} reads agree: {len(allsec)}")
    return 0


def cmd_reads_vote(a) -> int:
    _out_path(a.out, a.force)
    reads = _load_reads(a.reads, a.mib * 1048576 if a.mib else None)
    res, st = majority_vote(reads)
    with open(a.out, "wb") as f:
        f.write(res)
    print(json.dumps(st.__dict__))
    return 0


def cmd_epub_reconstruct(a) -> int:
    _out_path(a.out, a.force)
    reads = _load_reads([a.image] + a.reads)
    vol = Fat32Volume.open(a.image, a.lba, 2 if a.fat == 2 else 1)
    ents = {e.path: e for e in vol.walk(hash_files=False)}
    if a.path not in ents:
        sys.exit(f"{a.path} not found in the image")
    e = ents[a.path]
    vers = sector_versions(reads, vol.sector_offsets(e), e.size)
    multi = sum(len(v) > 1 for v in vers)
    res = repair_zip(vers, budget=a.budget, max_combos=a.max_combos)
    print(f"{res.status}: {len(vers)} sectors, {multi} with >1 version across {len(reads)} reads")
    for m in res.members:
        print(f"  {m.status:15} {m.name}  {m.detail}")
    if res.data is not None:
        with open(a.out, "wb") as f:
            f.write(res.data)
        print(f"wrote {a.out} sha256 {hashlib.sha256(res.data).hexdigest()}")
        return 0
    return 1


def cmd_bmp_validate(a) -> int:
    bad = 0
    for p in a.files:
        v = validate_sleep_bmp(open(p, "rb").read())
        bad += not v.ok
        tag = "OK " if v.ok else "BAD"
        extra = "direct-on-2.0.0" if v.direct_on_2_0 else "converted-on-2.0.0"
        info = f"{v.info.width}x{v.info.height} {v.info.bpp}bpp" if v.info else ""
        print(f"{tag} {p} {info} {extra if v.ok else ''}")
        for x in v.errors + (v.warnings if a.verbose else []):
            print(f"    {x}")
    return 1 if bad else 0


def cmd_bmp_convert(a) -> int:
    from .wallpaper import encode_x3, make_master, preview, quantize
    os.makedirs(a.out, exist_ok=True)
    for p in a.sources:
        stem = os.path.splitext(os.path.basename(p))[0]
        dest = os.path.join(a.out, f"{stem}.bmp")
        _out_path(dest, a.force)
        master = make_master(p)
        data = encode_x3(master, a.preset)
        with open(dest, "wb") as f:
            f.write(data)
        if a.preview:
            preview(quantize(master)).save(os.path.join(a.out, f"{stem}.preview.png"))
        v = validate_sleep_bmp(data)
        print(f"{'OK ' if v.ok else 'BAD'} {dest} ({len(data)} bytes)")
    return 0


def cmd_firmware_check(a) -> int:
    chip = CHIP_BY_NAME.get(a.chip.lower().replace("-", "")) if a.chip else None
    if a.chip and chip is None:
        sys.exit(f"unknown chip {a.chip}; known: {', '.join(CHIP_IDS.values())}")
    r = validate_file(a.file, expected_chip=chip, max_size=a.max_size)
    if a.sha256 and r.sha256 != a.sha256.lower().removeprefix("sha256:"):
        r.errors.append(f"sha256 {r.sha256} does not match the published digest")
    print(f"file      {a.file}\nsize      {r.size}\nsha256    {r.sha256}\nchip      {r.chip_name}\n"
          f"project   {r.project_name}\nversion   {r.app_version}\nsegments  {len(r.segments)}\n"
          f"checksum  {'ok' if r.checksum_ok else 'BAD'}\nsha trail {r.sha_ok}")
    for e in r.errors:
        print(f"ERROR     {e}")
    print("RESULT    " + ("PASS" if r.ok else "FAIL"))
    return 0 if r.ok else 1


def cmd_verify(a) -> int:
    for p in (a.before, a.after):
        _refuse_device(p)
    plan = Plan.from_manifest(open(a.manifest).read(), removable=tuple(a.remove),
                              ordered_dirs=tuple(a.ordered))
    with open(a.before, "rb") as fb, open(a.after, "rb") as fa:
        before, pb = walk_both_fats(fb, a.lba)
        after, pa = walk_both_fats(fa, a.lba)
    rep = compare(before, after, plan)
    rep.problems = [f"before: {x}" for x in pb] + [f"after: {x}" for x in pa] + rep.problems
    print(f"unchanged {rep.unchanged}, added {rep.added}, removed {rep.removed}")
    for x in rep.problems[:60]:
        print(f"  {x}")
    print("RESULT: " + ("PASS" if rep.ok else "FAIL"))
    return 0 if rep.ok else 1


def cmd_fat_used(a) -> int:
    """Print the MiB to snapshot: FAT metadata + every allocated cluster, + 1 MiB margin."""
    with open(a.image, "rb") as f:
        try:
            vol = Fat32Volume(f, a.lba)
        except Fat32Error as e:
            sys.exit(str(e))
        end = used_region_end(vol)
    print((end + (1 << 20) - 1) // (1 << 20) + 1)
    return 0


def cmd_precheck(a) -> int:
    """Pre-write gate on a snapshot: both FATs agree, no anomalies, no planned path exists yet."""
    _refuse_device(a.snapshot)
    plan = Plan.from_manifest(open(a.manifest).read())
    with open(a.snapshot, "rb") as f:
        ents, problems = walk_both_fats(f, a.lba)
    paths = {e.path for e in ents}
    clash = [p for p in plan.add if p in paths]
    if clash:
        problems.append(f"{len(clash)} planned destination(s) already exist, e.g. {clash[0]}")
    for req in a.require:
        if req not in paths:
            problems.append(f"required path missing: {req}")
    files = [e for e in ents if not e.is_dir]
    print(f"snapshot: {len(ents) - len(files)} dirs, {len(files)} files; planned additions {len(plan.add)}")
    for x in problems:
        print(f"  {x}")
    print("PRE-WRITE CHECK: " + ("PASS" if not problems else "FAIL"))
    return 0 if not problems else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="x3toolkit", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"x3toolkit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def img(p):
        p.add_argument("--lba", type=int, default=0,
                       help="partition start in 512-byte sectors (0 for a partition image)")
        p.add_argument("--fat", type=int, default=1, choices=(1, 2), help="FAT copy to follow")

    p = sub.add_parser("fat-ls", help="list a FAT32 image (read-only)")
    p.add_argument("image"); img(p)
    p.add_argument("--csv"); p.add_argument("--no-hash", action="store_true")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_fat_ls)

    p = sub.add_parser("fat-extract", help="copy every file out of a FAT32 image")
    p.add_argument("image"); p.add_argument("outdir"); img(p); p.set_defaults(fn=cmd_fat_extract)

    p = sub.add_parser("epub-check", help="ZIP CRC-32 + container check")
    p.add_argument("files", nargs="+"); p.set_defaults(fn=cmd_epub_check)

    p = sub.add_parser("reads-diff", help="count sectors that differ between raw reads")
    p.add_argument("reads", nargs="+"); p.add_argument("--mib", type=int)
    p.set_defaults(fn=cmd_reads_diff)

    p = sub.add_parser("reads-vote", help="bitwise majority vote (for comparison)")
    p.add_argument("out"); p.add_argument("reads", nargs="+"); p.add_argument("--mib", type=int)
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_reads_vote)

    p = sub.add_parser("epub-reconstruct", help="CRC-guided multi-read sector reconstruction of one EPUB")
    p.add_argument("image", help="primary read (full image)"); img(p)
    p.add_argument("--reads", nargs="+", required=True, help="further raw reads (may be partial)")
    p.add_argument("--path", required=True, help="file path inside the volume, e.g. /book.epub")
    p.add_argument("--out", required=True); p.add_argument("--budget", type=int, default=400_000)
    p.add_argument("--max-combos", type=int, default=50_000)
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_epub_reconstruct)

    p = sub.add_parser("bmp-validate", help="check sleep-screen BMPs against CrossPoint rules")
    p.add_argument("files", nargs="+"); p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_bmp_validate)

    p = sub.add_parser("bmp-convert", help="convert images to X3 sleep screens (needs Pillow)")
    p.add_argument("sources", nargs="+"); p.add_argument("--out", required=True)
    p.add_argument("--preset", choices=("2bit", "24bit"), default="2bit")
    p.add_argument("--preview", action="store_true"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_bmp_convert)

    p = sub.add_parser("firmware-check", help="offline ESP32 app-image validation")
    p.add_argument("file"); p.add_argument("--chip", help="e.g. ESP32-C3")
    p.add_argument("--sha256", help="published digest to compare against")
    p.add_argument("--max-size", type=lambda s: int(s, 0), help="OTA slot size, e.g. 0x640000")
    p.set_defaults(fn=cmd_firmware_check)

    p = sub.add_parser("verify", help="prove a card update changed only what was planned")
    p.add_argument("before"); p.add_argument("after"); p.add_argument("--lba", type=int, default=0)
    p.add_argument("--manifest", required=True, help="CSV with dest,sha256 columns")
    p.add_argument("--remove", action="append", default=[], help="path prefix allowed to disappear")
    p.add_argument("--ordered", action="append", default=[], help="directory whose entry order must hold")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("fat-used", help="MiB of an image/device head needed to cover all allocated data")
    p.add_argument("image"); p.add_argument("--lba", type=int, default=0); p.set_defaults(fn=cmd_fat_used)

    p = sub.add_parser("precheck", help="pre-write gate on a card snapshot")
    p.add_argument("snapshot"); p.add_argument("--lba", type=int, default=0)
    p.add_argument("--manifest", required=True)
    p.add_argument("--require", action="append", default=[], help="path that must exist (repeatable)")
    p.set_defaults(fn=cmd_precheck)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)
