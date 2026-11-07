import zipfile
import io

from x3toolkit.epub import central_directory, check_epub, member_data_range, member_ok


def test_clean_epub_passes(epub_bytes):
    r = check_epub(epub_bytes)
    assert r.ok and r.member_count == 7


def test_single_bit_flip_in_deflate_member_is_detected(epub_bytes):
    members = central_directory(epub_bytes)
    m = next(m for m in members if m.name == "text/ch1.xhtml")
    ds, de = member_data_range(epub_bytes, m)
    bad = bytearray(epub_bytes)
    bad[(ds + de) // 2] ^= 0x10
    r = check_epub(bytes(bad))
    assert not r.ok and "text/ch1.xhtml" in r.bad_members
    assert member_ok(bytes(bad), members[0])  # other members still verify


def test_flip_in_stored_member_is_detected(epub_bytes):
    m = next(m for m in central_directory(epub_bytes) if m.name == "images/noise.bin")
    ds, _ = member_data_range(epub_bytes, m)
    bad = bytearray(epub_bytes)
    bad[ds + 100] ^= 1
    assert check_epub(bytes(bad)).bad_members == ["images/noise.bin"]


def test_missing_container_and_garbage():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    assert "container.xml" in check_epub(buf.getvalue()).detail
    assert not check_epub(b"not a zip at all").ok


# --- regressions for defects found in the 2026-11 audit -------------------------------------
def test_declared_size_must_match_not_only_crc(epub_bytes):
    import struct
    buf = bytearray(epub_bytes)
    p = buf.find(b"PK\x01\x02")                         # first central-directory entry
    usz = struct.unpack_from("<I", buf, p + 24)[0]
    struct.pack_into("<I", buf, p + 24, usz + 7)        # wrong uncompressed size, CRC untouched
    assert not check_epub(bytes(buf)).ok


def test_truncated_or_mutated_archives_never_raise(epub_bytes):
    """Property: any truncation or byte mutation yields a verdict, never an exception."""
    import random
    rnd = random.Random(2026)
    e = epub_bytes.rfind(b"PK\x05\x06")
    cd = int.from_bytes(epub_bytes[e + 16:e + 20], "little")
    samples = [epub_bytes[:cd + k] + epub_bytes[e:] for k in range(0, e - cd, 7)]  # cut inside the CD
    samples += [epub_bytes[:n] for n in range(0, len(epub_bytes), 97)]
    for _ in range(300):
        b = bytearray(epub_bytes)
        for _ in range(rnd.randint(1, 6)):
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        samples.append(bytes(b))
    for s in samples:
        assert isinstance(check_epub(s).ok, bool)
