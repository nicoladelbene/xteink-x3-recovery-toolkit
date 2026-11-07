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
    """Parse the ZIP central directory directly (no zipfile module)."""
    e = buf.rfind(b"PK\x05\x06")
    if e < 0 or e + 22 > len(buf):
        raise ValueError("no end-of-central-directory record")
    count, _cd_size, cd_off = struct.unpack_from("<HII", buf, e + 10)
    out, p = [], cd_off
    for _ in range(count):
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
    if buf[lo:lo + 4] != b"PK\x03\x04":
        return None
    nl, xl = struct.unpack_from("<HH", buf, lo + 26)
    start = lo + 30 + nl + xl
    return start, start + m.compressed_size


def member_ok(buf: bytes, m: Member) -> bool:
    """True if the member decompresses completely and matches its stored CRC-32."""
    r = member_data_range(buf, m)
    if r is None:
        return False
    data = bytes(buf[r[0]:r[1]])
    try:
        if m.method == 8:
            d = zlib.decompressobj(-15)
            raw = d.decompress(data) + d.flush()
            if not d.eof:
                return False
        elif m.method == 0:
            raw = data
        else:
            return False
    except zlib.error:
        return False
    return (zlib.crc32(raw) & 0xFFFFFFFF) == m.crc32


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
