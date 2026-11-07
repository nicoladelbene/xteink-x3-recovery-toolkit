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
