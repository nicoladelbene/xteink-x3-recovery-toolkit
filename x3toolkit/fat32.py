"""Read-only FAT32 parser for raw disk or partition images.

Why not just mount the image? On a card that returns silent bit flips, the
partition table, the FAT copies and directory entries can all be damaged, and
the OS will either refuse the volume or "repair" it. This parser:

* never writes to the image (it is opened read-only);
* takes the partition start explicitly, so a corrupted MBR does not matter;
* follows a chosen FAT copy and cross-checks every chain against the other;
* reports every irregularity as an anomaly instead of skipping it;
* preserves exact long file names (VFAT LFN), attributes and timestamps;
* can map every 512-byte sector of a file to its absolute image offset, which
  is what multi-read sector reconstruction needs.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator

SECTOR = 512
EOC_MIN = 0x0FFFFFF8
BAD_CLUSTER = 0x0FFFFFF7


class Fat32Error(Exception):
    """The image does not contain a usable FAT32 volume at the given offset."""


@dataclass
class Entry:
    path: str
    is_dir: bool
    size: int
    first_cluster: int
    short_name: str
    attr: int
    mtime: _dt.datetime | None
    clusters: list[int] = field(default_factory=list)
    chain_ok: bool = True
    sha256: str = ""

    @property
    def hidden(self) -> bool:
        return bool(self.attr & 0x02)

    @property
    def system(self) -> bool:
        return bool(self.attr & 0x04)


def _fat_datetime(d: int, t: int = 0) -> _dt.datetime | None:
    try:
        return _dt.datetime(1980 + (d >> 9), (d >> 5) & 15 or 1, d & 31 or 1,
                            t >> 11, (t >> 5) & 63, min((t & 31) * 2, 59))
    except ValueError:
        return None


def lfn_checksum(short: bytes) -> int:
    s = 0
    for b in short:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


class Fat32Volume:
    """A FAT32 volume inside an image file (or any seekable binary stream)."""

    def __init__(self, f: BinaryIO, partition_lba: int = 0, follow_fat: int = 1):
        self.f = f
        self.base = partition_lba * SECTOR
        v = self._read(self.base, SECTOR)
        if v[510:512] != b"\x55\xaa" or v[82:90] != b"FAT32   ":
            raise Fat32Error(f"no FAT32 boot sector at LBA {partition_lba}")
        self.bytes_per_sector = struct.unpack_from("<H", v, 11)[0]
        self.sectors_per_cluster = v[13]
        self.reserved = struct.unpack_from("<H", v, 14)[0]
        self.num_fats = v[16]
        total = struct.unpack_from("<I", v, 32)[0]
        self.fat_sectors = struct.unpack_from("<I", v, 36)[0]
        self.root_cluster = struct.unpack_from("<I", v, 44)[0]
        if self.bytes_per_sector != SECTOR or self.sectors_per_cluster == 0 or self.num_fats == 0:
            raise Fat32Error("unsupported FAT32 geometry")
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster
        self.data_offset = self.base + (self.reserved + self.num_fats * self.fat_sectors) * SECTOR
        self.num_clusters = (total - self.reserved - self.num_fats * self.fat_sectors) // self.sectors_per_cluster
        self._geometry: list[str] = []
        fat_capacity = self.fat_sectors * SECTOR // 4 - 2  # a FAT cannot describe more clusters than it has entries
        if self.num_clusters > fat_capacity:
            self._geometry.append(f"boot sector claims {self.num_clusters} clusters, FAT holds {fat_capacity}; "
                                  "using the FAT size")
            self.num_clusters = max(0, fat_capacity)
        if follow_fat not in range(1, self.num_fats + 1):
            raise ValueError(f"follow_fat must be 1..{self.num_fats}")
        self.fats = [self._load_fat(i) for i in range(self.num_fats)]
        self.fat = self.fats[follow_fat - 1]
        self.other = self.fats[2 - follow_fat] if self.num_fats > 1 else self.fat
        self.anomalies: list[str] = []
        self.deleted: list[tuple[str, str, int]] = []
        self._owner: dict[int, str] = {}

    @classmethod
    def open(cls, path: str, partition_lba: int = 0, follow_fat: int = 1) -> "Fat32Volume":
        return cls(open(path, "rb"), partition_lba, follow_fat)

    # -- low level -----------------------------------------------------------
    def _read(self, off: int, n: int) -> bytes:
        self.f.seek(off)
        d = self.f.read(n)
        if len(d) != n:
            raise Fat32Error(f"short read at offset {off} (image truncated?)")
        return d

    def _load_fat(self, i: int) -> list[int]:
        n = self.num_clusters + 2
        raw = self._read(self.base + (self.reserved + i * self.fat_sectors) * SECTOR, n * 4)
        return [x & 0x0FFFFFFF for x in struct.unpack(f"<{n}I", raw)]

    def cluster_offset(self, c: int) -> int:
        return self.data_offset + (c - 2) * self.cluster_size

    def chain(self, first: int, path: str = "?") -> list[int]:
        out, c, seen = [], first, set()
        while 2 <= c < EOC_MIN:
            if c >= self.num_clusters + 2:
                self.anomalies.append(f"{path}: chain points past end (0x{c:x})")
                break
            if c in seen:
                self.anomalies.append(f"{path}: chain loop at cluster {c}")
                break
            seen.add(c)
            out.append(c)
            if self.fat[c] != self.other[c]:
                self.anomalies.append(f"{path}: FAT copies disagree at cluster {c}")
            prev = self._owner.get(c)
            if prev is not None and prev != path:
                self.anomalies.append(f"{path}: cluster {c} cross-linked with {prev}")
            self._owner[c] = path
            c = self.fat[c]
        if c in (0, 1) or c == BAD_CLUSTER:
            self.anomalies.append(f"{path}: chain ends with invalid marker 0x{c:x}")
        return out

    def read_clusters(self, clusters: list[int], size: int | None = None) -> bytes:
        data = b"".join(self._read(self.cluster_offset(c), self.cluster_size) for c in clusters)
        return data if size is None else data[:size]

    # -- directory walk ------------------------------------------------------
    def _dir_entries(self, cluster: int, rel: str) -> Iterator[Entry]:
        raw = self.read_clusters(self.chain(cluster, rel or "/"))
        lfn: dict[int, bytes] = {}
        lfn_sum = None
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if e[0] == 0x00:
                break
            attr = e[11]
            if e[0] == 0xE5 and attr == 0x0F:  # deleted long-name fragment
                lfn = {}
                continue
            if attr == 0x0F:
                seq = e[0] & 0x1F
                if e[0] & 0x40:
                    lfn, lfn_sum = {}, e[13]
                lfn[seq] = e[1:11] + e[14:26] + e[28:32]
                continue
            name83 = e[0:11]
            if e[0] == 0xE5:
                self.deleted.append((rel or "/", name83[1:].decode("latin-1").strip(),
                                     struct.unpack_from("<I", e, 28)[0]))
                lfn = {}
                continue
            if attr & 0x08:  # volume label
                lfn = {}
                continue
            long_name = None
            if lfn and lfn_sum == lfn_checksum(name83):
                u = b"".join(lfn[k] for k in sorted(lfn))
                long_name = u.decode("utf-16-le", "replace").split("\x00")[0]
            elif lfn:
                self.anomalies.append(f"{rel}/: LFN checksum mismatch for {name83!r}")
            lfn = {}
            base = name83[0:8].decode("latin-1").rstrip()
            ext = name83[8:11].decode("latin-1").rstrip()
            if e[12] & 0x08:
                base = base.lower()
            if e[12] & 0x10:
                ext = ext.lower()
            short = base + ("." + ext if ext else "")
            if short in (".", ".."):
                continue
            name = long_name or short
            if "/" in name or "\\" in name or name in ("", ".", ".."):
                self.anomalies.append(f"{rel}/: unsafe name {name!r} skipped")
                continue
            first = (struct.unpack_from("<H", e, 20)[0] << 16) | struct.unpack_from("<H", e, 26)[0]
            yield Entry(path=f"{rel}/{name}", is_dir=bool(attr & 0x10),
                        size=struct.unpack_from("<I", e, 28)[0], first_cluster=first,
                        short_name=short, attr=attr,
                        mtime=_fat_datetime(struct.unpack_from("<H", e, 24)[0],
                                            struct.unpack_from("<H", e, 22)[0]))

    def walk(self, hash_files: bool = True) -> list[Entry]:
        """Return every file and directory, depth first, in on-disk order."""
        self.anomalies[:] = list(self._geometry)
        self.deleted.clear()
        self._owner.clear()
        out: list[Entry] = []
        seen_dirs = {self.root_cluster}  # a damaged entry can point a directory at an ancestor

        def rec(cluster: int, rel: str) -> None:
            for ent in self._dir_entries(cluster, rel):
                if ent.is_dir:
                    out.append(ent)
                    if not ent.first_cluster:
                        self.anomalies.append(f"{ent.path}: directory with cluster 0")
                    elif ent.first_cluster in seen_dirs:
                        self.anomalies.append(
                            f"{ent.path}: directory cluster {ent.first_cluster} already walked (cycle); not followed")
                    else:
                        seen_dirs.add(ent.first_cluster)
                        rec(ent.first_cluster, ent.path)
                    continue
                ent.clusters = self.chain(ent.first_cluster, ent.path) if ent.first_cluster else []
                need = -(-ent.size // self.cluster_size)
                if len(ent.clusters) != need:
                    ent.chain_ok = False
                    self.anomalies.append(
                        f"{ent.path}: chain has {len(ent.clusters)} clusters, size needs {need}")
                if hash_files:
                    ent.sha256 = self.hash_file(ent)
                out.append(ent)

        rec(self.root_cluster, "")
        return out

    def read_file(self, ent: Entry) -> bytes:
        return self.read_clusters(ent.clusters, ent.size) if ent.clusters else b""

    def hash_file(self, ent: Entry) -> str:
        """SHA-256 of a file's content, read one cluster at a time (constant memory)."""
        h, left = hashlib.sha256(), ent.size
        for c in ent.clusters:
            if left <= 0:
                break
            h.update(self._read(self.cluster_offset(c), self.cluster_size)[:left])
            left -= self.cluster_size
        return h.hexdigest()

    def sector_offsets(self, ent: Entry) -> list[int]:
        """Absolute image offset of every 512-byte sector of a file, in file order."""
        offs: list[int] = []
        for c in ent.clusters:
            base = self.cluster_offset(c)
            offs.extend(base + k for k in range(0, self.cluster_size, SECTOR))
        return offs[:-(-ent.size // SECTOR)]


def compare_fat_copies(vol: Fat32Volume) -> list[int]:
    """Cluster numbers whose FAT entries differ between copy 1 and copy 2."""
    if vol.num_fats < 2:
        return []
    a, b = vol.fats[0], vol.fats[1]
    return [i for i in range(2, len(a)) if a[i] != b[i]]


def used_region_end(vol: Fat32Volume) -> int:
    """Byte offset (from the start of the image) just past the highest allocated cluster.

    Everything the file system references lives below this offset, so snapshotting
    ``[0, used_region_end)`` captures the metadata and every allocated cluster. Both FAT
    copies are consulted, so a cluster allocated in only one of them is still covered.
    Free clusters above it are not captured; restoring the snapshot makes them free again.
    """
    last = max((i for fat in vol.fats for i in range(2, len(fat)) if fat[i]), default=1)
    return vol.data_offset + max(0, last - 1) * vol.cluster_size
