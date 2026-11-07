"""Multi-read reconstruction on a *simulated* failing card.

The simulation mirrors what was measured on the real card: a few "weak"
sectors return different random bit flips on each read, with no I/O error.
"""
import io
import random

from fatbuilder import Fat32Builder
from x3toolkit.epub import check_epub
from x3toolkit.fat32 import Fat32Volume
from x3toolkit.reconstruct import (assemble, bytes_wrong, differing_sectors, majority_vote,
                                   repair_zip, sector_versions)


def card_with(epub):
    b = Fat32Builder()
    b.add_file("/book.epub", epub)
    img = b.build()
    vol = Fat32Volume(io.BytesIO(img))
    ent = next(e for e in vol.walk() if e.path == "/book.epub")
    return img, vol.sector_offsets(ent), ent.size


def flip(buf, off, nbits, rnd):
    for _ in range(nbits):
        i = off + rnd.randrange(512)
        buf[i] ^= 1 << rnd.randrange(8)


def simulate_reads(img, weak, n_reads, rnd, clean_reads_per_sector=1, bits=30):
    """Each weak sector is correct in `clean_reads_per_sector` random reads, flipped elsewhere."""
    reads = [bytearray(img) for _ in range(n_reads)]
    for off in weak:
        clean = set(rnd.sample(range(n_reads), clean_reads_per_sector))
        for k in range(n_reads):
            if k not in clean:
                flip(reads[k], off, bits, rnd)
    return [bytes(r) for r in reads]


def test_crc_guided_reconstruction_recovers_exact_file(epub_bytes):
    rnd = random.Random(7)
    img, offs, size = card_with(epub_bytes)
    weak = [offs[i] for i in (3, 9, 14)]  # inside deflated chapters
    reads = simulate_reads(img, weak, 7, rnd)
    first = assemble(sector_versions(reads[:1], offs, size))
    assert not check_epub(bytes(first)).ok          # read 1 alone is damaged
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "repaired"
    assert res.data == epub_bytes                    # byte-exact original


def test_majority_vote_can_be_wrong_where_crc_search_is_right(epub_bytes):
    """Biased flips: the same bit is wrong in most reads, so voting locks in the error."""
    img, offs, size = card_with(epub_bytes)
    off = offs[5]
    reads = [bytearray(img) for _ in range(7)]
    for k in range(1, 6):        # 5 of 7 reads agree on the SAME wrong bit
        reads[k][off + 100] ^= 0x04
    reads[0][off + 300] ^= 0x80  # read 1 has unrelated noise; read 7 happens to be clean
    reads = [bytes(r) for r in reads]
    voted, stats = majority_vote(reads)
    assert stats.voted_sectors == 1
    assert voted[off + 100] != img[off + 100]        # the vote kept the majority's error
    vote_file = b"".join(voted[o:o + 512] for o in offs)[:size]
    assert not check_epub(vote_file).ok
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "repaired" and res.data == epub_bytes


def test_stable_damage_is_reported_not_guessed(epub_bytes):
    img, offs, size = card_with(epub_bytes)
    bad = bytearray(img)
    bad[offs[9] + 50] ^= 0xFF
    reads = [bytes(bad)] * 5                         # every read returns the same wrong data
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "unrepaired" and res.data is None
    assert {m.status for m in res.members} == {"stable-damage"}


def test_no_correct_version_ever_read(epub_bytes):
    rnd = random.Random(3)
    img, offs, size = card_with(epub_bytes)
    reads = simulate_reads(img, [offs[9]], 5, rnd, clean_reads_per_sector=0)
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "unrepaired"
    assert res.members[0].status in ("no-solution", "exhausted")


def test_budget_limits_search(epub_bytes):
    rnd = random.Random(11)
    img, offs, size = card_with(epub_bytes)
    reads = simulate_reads(img, [offs[i] for i in range(2, 12)], 7, rnd)
    res = repair_zip(sector_versions(reads, offs, size), budget=5)
    assert res.status in ("unrepaired", "partial")
    assert any(m.status == "exhausted" for m in res.members)


def test_partial_rereads_only_vote_where_they_cover(epub_bytes):
    rnd = random.Random(5)
    img, offs, size = card_with(epub_bytes)
    reads = simulate_reads(img, [offs[3]], 5, rnd)
    cut = offs[3] + 512                               # extra reads stop right after the weak sector
    reads = [reads[0]] + [r[:cut] for r in reads[1:]]
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "repaired" and res.data == epub_bytes


def test_differing_sectors_and_bytes_wrong(epub_bytes):
    rnd = random.Random(1)
    img, offs, _ = card_with(epub_bytes)
    reads = simulate_reads(img, [offs[2], offs[6]], 3, rnd)
    secs = differing_sectors(reads)
    assert {offs[2] // 512, offs[6] // 512} <= set(secs)
    assert bytes_wrong(img, img) == 0 and bytes_wrong(reads[1], img) >= 0


def test_damaged_header_in_shared_sector_is_fixed_with_its_neighbour(epub_bytes):
    """Regression (found on real data): one bad sector holds the tail of member A and the
    local header of member B. Only an alternate read of that sector fixes both."""
    from x3toolkit.epub import central_directory, member_data_range
    img, offs, size = card_with(epub_bytes)
    ms = central_directory(epub_bytes)
    b = next(m for m in ms if m.name == "images/noise.bin")       # header straddles sector 14/15
    a_end = member_data_range(epub_bytes, ms[ms.index(b) - 1])[1]
    sector = b.local_offset // 512
    assert a_end // 512 == sector
    primary = bytearray(img)
    base = offs[sector]
    primary[base + (b.local_offset % 512) + 26] ^= 0x01             # B's name length
    primary[base + (a_end % 512) - 3] ^= 0x20                       # A's last deflate bytes
    reads = [bytes(primary), bytes(primary), img]
    res = repair_zip(sector_versions(reads, offs, size))
    assert res.status == "repaired" and res.data == epub_bytes


# --- regressions for defects found in the 2026-11 audit -------------------------------------
def test_member_running_past_end_of_file_is_reported_not_raised(epub_bytes):
    import struct
    buf = bytearray(epub_bytes)
    p = buf.find(b"PK\x01\x02")
    struct.pack_into("<I", buf, p + 20, 10 ** 7)        # compressed size far beyond the file
    versions = [[bytes(buf[i:i + 512])] for i in range(0, len(buf), 512)]
    versions[0].append(bytes(512))                      # give the search something to try
    res = repair_zip(versions)                          # previously: IndexError
    assert res.status in ("partial", "unrepaired") and res.data is None
    assert any(m.status == "header-damaged" for m in res.members)
