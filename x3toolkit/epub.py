"""EPUB (ZIP) integrity checks that do not trust the file system.

An EPUB is a ZIP archive. Every member carries a CRC-32 of its uncompressed
content in the central directory, so a ZIP is its own checksum manifest: a
flipped bit inside a deflate stream either breaks decompression or changes the
CRC. That is what makes silent corruption *detectable* without a clean copy,
and what lets ``x3toolkit.reconstruct`` tell a correct sector combination from
a wrong one.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Member:
    name: str
    local_offset: int
    compressed_size: int
    uncompressed_size: int
    crc32: int
    method: int  # 0 = stored, 8 = deflate


def central_directory(buf: bytes) -> list[Member]:
    """Parse the ZIP central directory directly (no zipfile module).

    Raises ValueError for anything malformed or truncated. ZIP64 archives are not
    supported (EPUBs are far below the 4 GiB limit that needs them).
    """
    e = buf.rfind(b"PK\x05\x06")
    if e < 0 or e + 22 > len(buf):
        raise ValueError("no end-of-central-directory record")
    count, _cd_size, cd_off = struct.unpack_from("<HII", buf, e + 10)
    out, p = [], cd_off
    for _ in range(count):
        if p + 46 > len(buf):
            raise ValueError(f"central directory truncated at offset {p}")
        if buf[p:p + 4] != b"PK\x01\x02":
            raise ValueError(f"bad central directory entry at offset {p}")
        method = struct.unpack_from("<H", buf, p + 10)[0]
        crc, csz, usz = struct.unpack_from("<III", buf, p + 16)
        nl, xl, cl = struct.unpack_from("<HHH", buf, p + 28)
        lo = struct.unpack_from("<I", buf, p + 42)[0]
        name = buf[p + 46:p + 46 + nl].decode("utf-8", "replace")
        out.append(Member(name, lo, csz, usz, crc, method))
        p += 46 + nl + xl + cl
    return out


def member_data_range(buf: bytes, m: Member) -> tuple[int, int] | None:
    """Byte range of a member's (compressed) data, or None if the local header is damaged."""
    lo = m.local_offset
    if lo + 30 > len(buf) or buf[lo:lo + 4] != b"PK\x03\x04":
        return None
    nl, xl = struct.unpack_from("<HH", buf, lo + 26)
    start = lo + 30 + nl + xl
    if start + m.compressed_size > len(buf):
        return None
    return start, start + m.compressed_size


def member_ok(buf: bytes, m: Member) -> bool:
    """True if the member's data decodes completely to exactly its declared size and CRC-32.

    Checked: the deflate stream is valid and ends exactly at the member's compressed
    end, the output has the declared uncompressed size, and its CRC-32 matches. Not
    checked (no checksum covers them): local-header fields other than the lengths,
    the central-directory entry itself, and whether the content is semantically right.
    """
    r = member_data_range(buf, m)
    if r is None:
        return False
    data = bytes(buf[r[0]:r[1]])
    try:
        if m.method == 8:
            d = zlib.decompressobj(-15)
            raw = d.decompress(data, m.uncompressed_size + 1)  # bounded: no decompression bomb
            if not d.eof or d.unconsumed_tail or d.unused_data:
                return False
        elif m.method == 0:
            raw = data
        else:
            return False
    except zlib.error:
        return False
    return len(raw) == m.uncompressed_size and (zlib.crc32(raw) & 0xFFFFFFFF) == m.crc32


@dataclass
class CheckResult:
    ok: bool
    detail: str
    bad_members: list[str]
    member_count: int


def check_epub(buf: bytes) -> CheckResult:
    """Full integrity check of an EPUB held in memory."""
    try:
        members = central_directory(buf)
    except ValueError as e:
        return CheckResult(False, f"central directory unreadable: {e}", [], 0)
    bad = [m.name for m in members if not member_ok(buf, m)]
    names = {m.name for m in members}
    if bad:
        return CheckResult(False, f"{len(bad)} member(s) fail CRC/inflate", bad, len(members))
    if "META-INF/container.xml" not in names:
        return CheckResult(False, "no META-INF/container.xml", [], len(members))
    # cross-check with the standard library's reader as a second opinion
    try:
        with zipfile.ZipFile(io.BytesIO(buf)) as z:
            if z.testzip() is not None:
                return CheckResult(False, "zipfile.testzip reported an error", [], len(members))
    except zipfile.BadZipFile as e:
        return CheckResult(False, f"zipfile: {e}", [], len(members))
    return CheckResult(True, "", [], len(members))
