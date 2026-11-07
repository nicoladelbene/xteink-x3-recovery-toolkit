import hashlib
import struct

from x3toolkit.esp_image import validate_image


def make_image(chip=0x0005, segments=((0x3C000020, 300), (0x40380000, 1000)), version="2.0.0",
               project="demo-app"):
    hdr = bytearray(24)
    hdr[0], hdr[1] = 0xE9, len(segments)
    struct.pack_into("<I", hdr, 4, 0x40380000)  # entry point
    struct.pack_into("<H", hdr, 12, chip)
    hdr[23] = 1                                  # hash appended
    body = bytearray()
    cks = 0xEF
    for n, (addr, length) in enumerate(segments):
        data = bytearray((i * 7 + n) & 0xFF for i in range(length))
        if n == 0:  # esp_app_desc_t at the start of the first segment
            struct.pack_into("<I", data, 0, 0xABCD5432)
            data[16:48] = version.encode().ljust(32, b"\0")
            data[48:80] = project.encode().ljust(32, b"\0")
        body += struct.pack("<II", addr, length) + data
        for b in data:
            cks ^= b
    img = hdr + body
    pad = (len(img) + 16) & ~15
    img += bytes(pad - len(img) - 1) + bytes([cks])
    return bytes(img) + hashlib.sha256(img).digest()


def test_valid_image():
    r = validate_image(make_image(), expected_chip=0x0005, max_size=0x640000)
    assert r.ok, r.errors
    assert (r.chip_name, r.app_version, r.project_name) == ("ESP32-C3", "2.0.0", "demo-app")
    assert r.checksum_ok and r.sha_ok and len(r.segments) == 2


def test_wrong_chip_is_rejected():
    r = validate_image(make_image(chip=0x0009), expected_chip=0x0005)
    assert not r.ok and "ESP32-S3" in r.errors[0]


def test_corruption_and_truncation_are_detected():
    img = bytearray(make_image())
    img[200] ^= 0x01
    r = validate_image(bytes(img))
    assert not r.ok and not r.checksum_ok
    assert not validate_image(make_image()[:-10]).ok
    assert not validate_image(b"\x00" * 64).ok


def test_oversize_for_partition():
    assert not validate_image(make_image(), max_size=100).ok
