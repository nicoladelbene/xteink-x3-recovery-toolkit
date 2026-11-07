import hashlib
import io
import struct

import pytest

from fatbuilder import Fat32Builder, data_offset
from x3toolkit.fat32 import Fat32Error, Fat32Volume, compare_fat_copies, used_region_end

NAMES = {
    "/The Book’s Title, Vol. 1 - A. Author.epub": b"E" * 5000,
    "/Città e caffè.epub": b"C" * 1200,
    "/.crosspoint/epub_123456/progress.bin": bytes.fromhex("0f0019002b0073330000"),
    "/.crosspoint/settings.json": b'{"sleepScreen":2}',
    "/.sleep/IMG_0001.bmp": b"B" * 777,
    "/empty.txt": b"",
}


def build(lba=8):
    b = Fat32Builder()
    b.add_dir("/.sleep", hidden=True)
    for p, d in NAMES.items():
        b.add_file(p, d)
    return b.build(partition_lba=lba)


def test_walk_preserves_names_sizes_and_content():
    img = build()
    vol = Fat32Volume(io.BytesIO(img), partition_lba=8)
    ents = {e.path: e for e in vol.walk()}
    for p, d in NAMES.items():
        assert p in ents, p
        assert ents[p].size == len(d)
        assert ents[p].sha256 == hashlib.sha256(d).hexdigest()
    assert ents["/.sleep"].is_dir and ents["/.sleep"].hidden
    assert vol.anomalies == []
    assert compare_fat_copies(vol) == []


def test_follow_either_fat_gives_same_tree():
    img = build()
    t1 = [(e.path, e.sha256) for e in Fat32Volume(io.BytesIO(img), 8, 1).walk()]
    t2 = [(e.path, e.sha256) for e in Fat32Volume(io.BytesIO(img), 8, 2).walk()]
    assert t1 == t2


def test_sector_offsets_map_file_bytes():
    img = build(lba=0)
    vol = Fat32Volume(io.BytesIO(img))
    e = next(e for e in vol.walk() if e.path.endswith("Vol. 1 - A. Author.epub"))
    offs = vol.sector_offsets(e)
    assert len(offs) == -(-e.size // 512)
    joined = b"".join(img[o:o + 512] for o in offs)[:e.size]
    assert joined == NAMES[e.path]


def _fat_entry_offset(img, lba, copy, cluster):
    p = lba * 512
    rs = struct.unpack_from("<H", img, p + 14)[0]
    fz = struct.unpack_from("<I", img, p + 36)[0]
    return p + (rs + copy * fz) * 512 + cluster * 4


def test_fat_copy_disagreement_is_reported():
    img = bytearray(build(lba=0))
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    e = next(e for e in vol.walk() if e.size > 2000)
    c = e.clusters[0]
    struct.pack_into("<I", img, _fat_entry_offset(img, 0, 1, c), 0x0FFFFFFF)  # damage FAT2 only
    vol = Fat32Volume(io.BytesIO(bytes(img)), follow_fat=1)
    vol.walk()
    assert any("FAT copies disagree" in a for a in vol.anomalies)
    assert c in compare_fat_copies(vol)


def test_chain_loop_terminates_and_is_reported():
    img = bytearray(build(lba=0))
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    e = next(e for e in vol.walk() if len(e.clusters) > 3)
    for copy in (0, 1):  # last cluster points back at the first
        struct.pack_into("<I", img, _fat_entry_offset(img, 0, copy, e.clusters[-1]), e.clusters[0])
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    vol.walk()
    assert any("chain loop" in a for a in vol.anomalies)


def test_rejects_non_fat32_and_truncated_images():
    with pytest.raises(Fat32Error):
        Fat32Volume(io.BytesIO(b"\0" * 4096))
    img = build(lba=0)
    with pytest.raises(Fat32Error):
        Fat32Volume(io.BytesIO(img[:20000])).walk()


def test_used_region_covers_all_files():
    img = build(lba=0)
    vol = Fat32Volume(io.BytesIO(img))
    end = used_region_end(vol)
    for e in vol.walk():
        for c in e.clusters:
            assert vol.cluster_offset(c) + vol.cluster_size <= end + vol.cluster_size
    off, cs = data_offset(img)
    assert end >= off


# --- regressions for defects found in the 2026-11 audit -------------------------------------
def _patch_dir_entry(img, vol, parent_cluster, target_first, new_first):
    off = vol.cluster_offset(parent_cluster)
    for i in range(off, off + vol.cluster_size, 32):
        if img[i + 11] & 0x10 and img[i] not in (0x00, 0x2E, 0xE5) and \
                struct.unpack_from("<H", img, i + 26)[0] == target_first:
            struct.pack_into("<H", img, i + 26, new_first & 0xFFFF)
            struct.pack_into("<H", img, i + 20, new_first >> 16)
            return
    raise AssertionError("entry not found")


def test_directory_cycle_is_reported_not_infinite():
    b = Fat32Builder(clusters=300)
    b.add_file("/d/e/x.txt", b"hello")
    img = bytearray(b.build())
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    ents = {e.path: e for e in vol.walk()}
    # a bit flip makes /d/e point back at /d (previously: RecursionError)
    _patch_dir_entry(img, vol, ents["/d"].first_cluster, ents["/d/e"].first_cluster, ents["/d"].first_cluster)
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    paths = [e.path for e in vol.walk()]
    assert paths == ["/d", "/d/e"]
    assert any("cycle" in a for a in vol.anomalies)


def test_corrupt_total_sector_count_is_bounded_by_fat_size():
    img = bytearray(Fat32Builder(clusters=300).build())
    struct.pack_into("<I", img, 32, 0xFFFFFFF0)       # flipped high bits in BPB_TotSec32
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    vol.walk()
    assert vol.num_clusters <= vol.fat_sectors * 512 // 4 - 2
    assert any("boot sector claims" in a for a in vol.anomalies)


def test_used_region_counts_clusters_allocated_in_either_fat():
    img = bytearray(build(lba=0))
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    hi = vol.num_clusters                              # a free cluster near the end of the volume
    fat2 = (vol.reserved + vol.fat_sectors) * 512
    struct.pack_into("<I", img, fat2 + hi * 4, 0x0FFFFFFF)  # allocated in FAT2 only
    vol = Fat32Volume(io.BytesIO(bytes(img)))
    assert used_region_end(vol) >= vol.cluster_offset(hi) + vol.cluster_size
