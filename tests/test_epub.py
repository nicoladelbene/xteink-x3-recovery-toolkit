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
