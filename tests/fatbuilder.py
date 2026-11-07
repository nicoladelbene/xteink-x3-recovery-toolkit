"""Tiny FAT32 image writer for tests (synthetic fixtures only).

Produces a small but structurally valid FAT32 volume: boot sector, FSInfo,
two FAT copies, a root directory at cluster 2, subdirectories, and VFAT long
file names for every entry. Optionally wraps it in an MBR at a given LBA.
It is deliberately simple (contiguous allocation, 1 sector per cluster by
default) and is only meant to feed the read-only parser.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

SECTOR = 512


def lfn_checksum(short: bytes) -> int:
    s = 0
    for b in short:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


@dataclass
class _Node:
    name: str
    is_dir: bool
    data: bytes = b""
    children: list["_Node"] = field(default_factory=list)
    first: int = 0
    short: bytes = b""
    attr: int = 0


class Fat32Builder:
    def __init__(self, clusters: int = 4096, sectors_per_cluster: int = 1, reserved: int = 32):
        self.n_clusters = clusters
        self.spc = sectors_per_cluster
        self.reserved = reserved
        self.root = _Node("", True)
        self._counter = 0

    # -- tree ---------------------------------------------------------------
    def _dir(self, path: str) -> _Node:
        node = self.root
        for part in [p for p in path.split("/") if p]:
            nxt = next((c for c in node.children if c.name == part), None)
            if nxt is None:
                nxt = _Node(part, True, attr=0x10)
                node.children.append(nxt)
            node = nxt
        return node

    def add_dir(self, path: str, hidden: bool = False) -> None:
        d = self._dir(path)
        if hidden:
            d.attr |= 0x02

    def add_file(self, path: str, data: bytes, hidden: bool = False) -> None:
        parent, _, name = path.rpartition("/")
        self._dir(parent).children.append(_Node(name, False, data, attr=0x20 | (0x02 if hidden else 0)))

    # -- layout -------------------------------------------------------------
    def _short(self) -> bytes:
        self._counter += 1
        return f"F{self._counter:07d}".encode() + b"BIN"

    @staticmethod
    def _lfn_entries(name: str, short: bytes) -> list[bytes]:
        u = name.encode("utf-16-le")
        chars = [u[i:i + 2] for i in range(0, len(u), 2)]
        chars.append(b"\x00\x00")
        while len(chars) % 13:
            chars.append(b"\xff\xff")
        parts = [chars[i:i + 13] for i in range(0, len(chars), 13)]
        if len(chars) > 13 and all(c == b"\xff\xff" for c in parts[-1]):
            parts = parts[:-1]
        cs = lfn_checksum(short)
        out = []
        for seq in range(len(parts), 0, -1):
            p = b"".join(parts[seq - 1])
            first = seq | (0x40 if seq == len(parts) else 0)
            out.append(bytes([first]) + p[0:10] + bytes([0x0F, 0, cs]) + p[10:22] + b"\x00\x00" + p[22:26])
        return out

    def _entries_needed(self, d: _Node) -> int:
        n = 0 if d is self.root else 2
        for c in d.children:
            n += 1 + -(-(len(c.name) + 1) // 13)
        return n + 1  # end marker

    def build(self, partition_lba: int = 0) -> bytes:
        cs = SECTOR * self.spc
        fat_sectors = -(-(self.n_clusters + 2) * 4 // SECTOR)
        fat = [0x0FFFFFF8, 0x0FFFFFFF] + [0] * self.n_clusters
        nxt = [2]

        def alloc(nbytes: int) -> int:
            n = max(1, -(-nbytes // cs))
            first = nxt[0]
            if first + n > self.n_clusters + 2:
                raise ValueError("synthetic volume too small")
            for i in range(first, first + n - 1):
                fat[i] = i + 1
            fat[first + n - 1] = 0x0FFFFFFF
            nxt[0] += n
            return first

        def assign(d: _Node) -> None:
            d.first = alloc(self._entries_needed(d) * 32)
            for c in d.children:
                c.short = self._short()
                if c.is_dir:
                    assign(c)
                elif c.data:
                    c.first = alloc(len(c.data))
        self.root.first = 0
        assign(self.root)

        total_sectors = self.reserved + 2 * fat_sectors + self.n_clusters * self.spc
        vol = bytearray(total_sectors * SECTOR)
        data_off = (self.reserved + 2 * fat_sectors) * SECTOR

        def coff(c: int) -> int:
            return data_off + (c - 2) * cs

        def dirent(short: bytes, attr: int, first: int, size: int) -> bytes:
            return (short + bytes([attr, 0, 0]) + struct.pack("<HHHHHHHI", 0, 0x5A21, 0x5A21,
                    first >> 16, 0x6000, 0x5A21, first & 0xFFFF, size))

        def write_dir(d: _Node, parent_first: int) -> None:
            raw = bytearray()
            if d is not self.root:
                raw += dirent(b".          ", 0x10, d.first, 0)
                raw += dirent(b"..         ", 0x10, parent_first if parent_first != self.root.first else 0, 0)
            for c in d.children:
                for e in self._lfn_entries(c.name, c.short):
                    raw += e
                raw += dirent(c.short, c.attr, c.first, 0 if c.is_dir else len(c.data))
                if c.is_dir:
                    write_dir(c, d.first)
                elif c.data:
                    o = coff(c.first)
                    vol[o:o + len(c.data)] = c.data
            o = coff(d.first)
            vol[o:o + len(raw)] = raw

        write_dir(self.root, 0)

        bs = bytearray(SECTOR)
        bs[0:3] = b"\xEB\x58\x90"
        bs[3:11] = b"SYNTHFAT"
        struct.pack_into("<HBHBHHBHHHII", bs, 11, SECTOR, self.spc, self.reserved, 2, 0, 0, 0xF8, 0,
                         63, 255, partition_lba, total_sectors)
        struct.pack_into("<IHHIHH", bs, 36, fat_sectors, 0, 0, self.root.first, 1, 6)
        bs[64] = 0x80
        bs[66] = 0x29
        bs[67:71] = b"\x12\x34\x56\x78"
        bs[71:82] = b"NO NAME    "
        bs[82:90] = b"FAT32   "
        bs[510:512] = b"\x55\xaa"
        vol[0:SECTOR] = bs
        vol[6 * SECTOR:7 * SECTOR] = bs  # backup boot sector
        fsinfo = bytearray(SECTOR)
        fsinfo[0:4] = b"RRaA"
        fsinfo[484:488] = b"rrAa"
        struct.pack_into("<II", fsinfo, 488, self.n_clusters + 2 - nxt[0], nxt[0])
        fsinfo[510:512] = b"\x55\xaa"
        vol[SECTOR:2 * SECTOR] = fsinfo
        raw_fat = struct.pack(f"<{len(fat)}I", *fat)
        for i in range(2):
            o = (self.reserved + i * fat_sectors) * SECTOR
            vol[o:o + len(raw_fat)] = raw_fat

        if not partition_lba:
            return bytes(vol)
        mbr = bytearray(partition_lba * SECTOR)
        struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\0\0\0", 0x0C, b"\0\0\0", partition_lba, total_sectors)
        mbr[510:512] = b"\x55\xaa"
        return bytes(mbr) + bytes(vol)


def data_offset(img: bytes, partition_lba: int = 0) -> tuple[int, int]:
    """(data region offset, cluster size) of an image built above — handy for corrupting it."""
    p = partition_lba * SECTOR
    rs = struct.unpack_from("<H", img, p + 14)[0]
    fz = struct.unpack_from("<I", img, p + 36)[0]
    return p + (rs + 2 * fz) * SECTOR, img[p + 13] * SECTOR
