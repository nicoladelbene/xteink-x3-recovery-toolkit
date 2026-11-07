"""Multi-read analysis and checksum-guided sector reconstruction.

The failure mode this targets: a flash card that returns *different wrong data*
on different reads of the same sector, without reporting any read error. Two
naive answers fail:

* "Use the first read" keeps whatever flips happened that time.
* "Majority-vote every bit across N reads" assumes flips are rare, independent
  and unbiased. On a degraded page they are none of those, and voting can
  produce a sector that no read ever returned (and that is still wrong).

Instead, for every 512-byte sector of a file we collect the distinct versions
seen across reads, then search for the combination that makes each damaged
ZIP member inflate cleanly *and* match its stored CRC-32. The archive's own
checksums are the oracle; nothing is accepted on probability alone.

All functions work on in-memory byte strings; nothing here touches a device.
"""
from __future__ import annotations

import itertools
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from .epub import Member, central_directory, member_data_range, member_ok

SECTOR = 512


# --------------------------------------------------------------------------- analysis
def differing_sectors(reads: Sequence[bytes], start: int = 0, length: int | None = None) -> list[int]:
    """Indices (relative to ``start``) of 512-byte sectors where not all reads agree."""
    n = min(len(r) for r in reads) - start if length is None else length
    out = []
    for s in range(n // SECTOR):
        o = start + s * SECTOR
        first = reads[0][o:o + SECTOR]
        if any(r[o:o + SECTOR] != first for r in reads[1:]):
            out.append(s)
    return out


def bytes_wrong(candidate: bytes, reference: bytes) -> int:
    """Number of byte positions where ``candidate`` differs from a known-good ``reference``."""
    return sum(1 for a, b in zip(candidate, reference) if a != b) + abs(len(candidate) - len(reference))


@dataclass
class VoteStats:
    reads: int
    voted_sectors: int
    bytes_changed_vs_primary: int
    tie_bits: int


def majority_vote(reads: Sequence[bytes]) -> tuple[bytes, VoteStats]:
    """Bitwise majority across equal-length reads; ties keep the primary (first) read.

    Provided for comparison: on the card this toolkit was written for it made the
    result *worse* than the best single read (see docs/recovery-method.md).
    """
    if len(reads) < 2:
        raise ValueError("need at least two reads")
    n = min(len(r) for r in reads)
    k = len(reads)
    res = bytearray(reads[0][:n])
    voted = changed = ties = 0
    for s in range(n // SECTOR):
        o = s * SECTOR
        blocks = [r[o:o + SECTOR] for r in reads]
        if all(b == blocks[0] for b in blocks[1:]):
            continue
        voted += 1
        for i in range(SECTOR):
            vals = [b[i] for b in blocks]
            if all(v == vals[0] for v in vals):
                continue
            byte = 0
            for bit in range(8):
                ones = sum((v >> bit) & 1 for v in vals)
                if ones * 2 > k:
                    byte |= 1 << bit
                elif ones * 2 == k:
                    ties += 1
                    byte |= ((vals[0] >> bit) & 1) << bit
            if byte != res[o + i]:
                changed += 1
            res[o + i] = byte
    return bytes(res), VoteStats(k, voted, changed, ties)


def sector_versions(reads: Sequence[bytes], offsets: Sequence[int], size: int) -> list[list[bytes]]:
    """For each sector of a file, the distinct versions seen across reads.

    Version 0 is always the primary read's. The rest are ordered by how often
    they were seen, so the search tries the most common alternatives first.
    The last sector is truncated to the file size.
    """
    out = []
    for idx, off in enumerate(offsets):
        keep = max(0, min(SECTOR, size - idx * SECTOR))
        # partial re-reads (e.g. only the first N MiB) simply do not vote on later sectors
        seen = [r[off:off + keep] for r in reads if len(r) >= off + keep]
        if not seen:
            raise ValueError(f"no read covers sector {idx} at offset {off}")
        counts = Counter(seen)
        primary = seen[0]
        rest = sorted((v for v in counts if v != primary), key=lambda v: -counts[v])
        out.append([primary] + rest)
    return out


def assemble(versions: Sequence[Sequence[bytes]], choice: dict[int, int] | None = None) -> bytearray:
    choice = choice or {}
    return bytearray(b"".join(v[choice.get(i, 0)] for i, v in enumerate(versions)))


# --------------------------------------------------------------------------- repair
@dataclass
class MemberOutcome:
    name: str
    status: str          # repaired | stable-damage | header-damaged | exhausted | no-solution
    detail: str = ""
    nodes: int = 0


@dataclass
class RepairResult:
    status: str                       # intact | repaired | partial | unrepaired | unreadable
    data: bytes | None                # full repaired file, only when every member passes
    members: list[MemberOutcome] = field(default_factory=list)
    chosen: dict[int, int] = field(default_factory=dict)  # sector -> version index used


def _dfs_deflate(versions, allowed: dict[int, list[int]], ds: int, de: int,
                 crc: int, budget: int, size: int | None = None) -> tuple[dict[int, int] | None, int]:
    """Depth-first search over sector versions with incremental inflate pruning.

    Iterative (explicit stack), because one member can span thousands of sectors.
    Each frame keeps a copy of the inflater state *before* its sector, so
    backtracking costs nothing but discarding frames. A branch is cut as soon
    as the deflate stream becomes invalid, ends early, or produces more than ``size``
    bytes; a full path is accepted only if the stream ends exactly at the member's end
    with exactly ``size`` bytes of output and the CRC-32 matches.
    Returns (sector -> version index, nodes examined); None if not found or the
    budget ran out (nodes > budget).
    """
    first, last = ds // SECTOR, (de - 1) // SECTOR

    def options(i: int) -> list[int]:
        return allowed[i]

    nodes = 0
    choice: dict[int, int] = {}
    # frame: [sector index, inflater before this sector, crc before, options, next option, bytes out]
    stack = [[first, zlib.decompressobj(-15), 0, options(first), 0, 0]]
    while stack:
        fr = stack[-1]
        i, dec, ccrc, opts, k, nout = fr
        if k >= len(opts):
            stack.pop()
            choice.pop(i, None)
            continue
        fr[4] += 1
        nodes += 1
        if nodes > budget:
            return None, nodes
        vi = opts[k]
        lo = max(ds, i * SECTOR) - i * SECTOR
        hi = min(de, (i + 1) * SECTOR) - i * SECTOR
        d2 = dec.copy()
        try:
            out = d2.decompress(versions[i][vi][lo:hi])
        except zlib.error:
            continue
        if d2.eof and i < last:
            continue  # stream claims to end before its declared size: wrong branch
        if d2.eof and d2.unused_data:
            continue  # stream ends before the member's compressed end
        nout2 = nout + len(out)
        if size is not None and nout2 > size:
            continue  # more output than the declared uncompressed size
        c2 = zlib.crc32(out, ccrc)
        choice[i] = vi
        if i == last:
            try:
                tail = d2.flush()
            except zlib.error:
                continue
            c2 = zlib.crc32(tail, c2)
            nout2 += len(tail)
            if d2.eof and (size is None or nout2 == size) and (c2 & 0xFFFFFFFF) == crc:
                return dict(choice), nodes
            continue
        stack.append([i + 1, d2, c2, options(i + 1), 0, nout2])
    return None, nodes


def _exhaustive(buf: bytearray, versions, allowed: dict[int, list[int]], m: Member,
                max_combos: int) -> tuple[dict[int, int] | None, int]:
    span = [i for i in sorted(allowed) if len(allowed[i]) > 1]
    space = 1
    for i in span:
        space *= len(allowed[i])
    if space > max_combos:
        return None, space
    trial = bytearray(buf)  # one working copy; every combination rewrites all span sectors
    for n, combo in enumerate(itertools.product(*[allowed[i] for i in span]), 1):
        for i, vi in zip(span, combo):
            trial[i * SECTOR:i * SECTOR + len(versions[i][vi])] = versions[i][vi]
        if member_ok(trial, m):
            return dict(zip(span, combo)), n
    return None, space


class _Pins:
    """Byte ranges that must keep their current content (good members, headers, directory).

    A sector version is *allowed* only if it agrees with the pinned bytes. This is
    finer than pinning whole sectors: a sector shared by a good member and a damaged
    one may still be swapped for a version that leaves the good member's bytes intact.
    """

    def __init__(self, versions):
        self.versions = versions
        self.ranges: dict[int, list[tuple[int, int, bytes]]] = {}

    def pin(self, buf: bytes, start: int, end: int) -> None:
        for i in range(start // SECTOR, (max(start, end) - 1) // SECTOR + 1):
            a = max(start, i * SECTOR) - i * SECTOR
            b = min(end, (i + 1) * SECTOR) - i * SECTOR
            if b > a:
                self.ranges.setdefault(i, []).append((a, b, bytes(buf[i * SECTOR + a:i * SECTOR + b])))

    def allowed(self, i: int) -> list[int]:
        cons = self.ranges.get(i, ())
        return [vi for vi, v in enumerate(self.versions[i]) if all(v[a:b] == req for a, b, req in cons)]


def repair_zip(versions: Sequence[Sequence[bytes]], budget: int = 400_000,
               max_combos: int = 50_000) -> RepairResult:
    """Choose per-sector versions so that every ZIP member passes CRC-32.

    ``versions`` comes from :func:`sector_versions`. The central directory and
    local headers are taken from the primary read and pinned; members whose
    headers are damaged are reported, not guessed.
    """
    buf = assemble(versions)
    try:
        members = central_directory(bytes(buf))
    except ValueError as e:
        return RepairResult("unreadable", None, [MemberOutcome("<central directory>", "header-damaged", str(e))])
    bad = [m for m in members if not member_ok(buf, m)]
    if not bad:
        return RepairResult("intact", bytes(buf))

    pins = _Pins(versions)
    ranges = {m: member_data_range(buf, m) for m in members}
    last_data_end = 0
    for m in members:
        r = ranges[m]
        if r is None:
            continue
        last_data_end = max(last_data_end, r[1])
        if m in bad:
            # A damaged member's own header may be part of the damage: never pin it.
            continue
        # Good members: pin their data, and from their local header only the bytes parsing
        # depends on (signature, name/extra lengths). Other header fields (times, flags) are
        # not covered by any checksum, so they cannot be verified and must not constrain
        # the choice of sector versions.
        pins.pin(buf, m.local_offset, m.local_offset + 4)
        pins.pin(buf, m.local_offset + 26, m.local_offset + 30)
        pins.pin(buf, r[0], r[1])
    pins.pin(buf, last_data_end, len(buf))           # central directory + EOCD

    outcomes, chosen = [], {}
    for m in bad:
        r = ranges[m]
        if r is None:
            outcomes.append(MemberOutcome(m.name, "header-damaged", "local header unreadable"))
            continue
        ds, de = r
        allowed = {i: pins.allowed(i) for i in range(ds // SECTOR, (de - 1) // SECTOR + 1)}
        if any(not a for a in allowed.values()):
            outcomes.append(MemberOutcome(m.name, "no-solution", "no read agrees with the pinned bytes"))
            continue
        if all(len(a) == 1 for a in allowed.values()):
            outcomes.append(MemberOutcome(m.name, "stable-damage",
                                          "every usable read returned the same (wrong) data"))
            continue
        if m.method == 8:
            ch, n = _dfs_deflate(versions, allowed, ds, de, m.crc32, budget, m.uncompressed_size)
            exhausted = ch is None and n > budget
        else:
            ch, n = _exhaustive(buf, versions, allowed, m, max_combos)
            exhausted = ch is None and n > max_combos
        if ch is None:
            outcomes.append(MemberOutcome(m.name, "exhausted" if exhausted else "no-solution",
                                          f"{n} candidates examined", n))
            continue
        for i, vi in ch.items():
            buf[i * SECTOR:i * SECTOR + len(versions[i][vi])] = versions[i][vi]
            if vi:
                chosen[i] = vi
        pins.pin(buf, ds, de)                        # later members must not undo this one
        outcomes.append(MemberOutcome(m.name, "repaired", f"{n} search nodes", n))

    # A member can be fixed as a side effect of repairing a neighbour that shares a sector.
    final = bytes(buf)
    for o, m in zip(outcomes, bad):  # one outcome per bad member, same order
        if o.status != "repaired" and member_ok(final, m):
            o.status, o.detail = "repaired", "fixed by a shared sector chosen for another member"
    all_ok = all(member_ok(final, m) for m in central_directory(final))
    fixed = sum(o.status == "repaired" for o in outcomes)
    status = "repaired" if all_ok else ("partial" if fixed else "unrepaired")
    return RepairResult(status, final if all_ok else None, outcomes, chosen)
